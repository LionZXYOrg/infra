"""Migration safety and wiring regression tests; no production access."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import migration_check as migration


class WorkloadContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = {'workloads': 1, 'secrets': ['config'], 'mounts': ['/data']}
        self.objects = [
            {'kind': 'PersistentVolumeClaim', 'metadata': {'name': 'data'}},
            {'kind': 'Deployment', 'metadata': {'name': 'app'}, 'spec': {
                'replicas': 1, 'strategy': {'type': 'Recreate'}, 'template': {
                    'metadata': {'labels': {'app': 'app'}}, 'spec': {
                        'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': 'data'}}],
                        'containers': [{'name': 'app', 'image': 'example@sha256:' + 'a' * 64,
                            'resources': {'requests': {'cpu': '10m'}},
                            'readinessProbe': {'httpGet': {'path': '/', 'port': 80}},
                            'livenessProbe': {'httpGet': {'path': '/', 'port': 80}},
                            'ports': [{'name': 'http', 'containerPort': 80}],
                            'envFrom': [{'secretRef': {'name': 'config'}}],
                            'volumeMounts': [{'name': 'data', 'mountPath': '/data'}]}]}}}},
            {'kind': 'Service', 'metadata': {'name': 'app'}, 'spec': {
                'selector': {'app': 'app'}, 'ports': [{'port': 80, 'targetPort': 'http'}]}},
            {'kind': 'IngressRoute', 'metadata': {'name': 'app'}, 'spec': {
                'routes': [{'services': [{'name': 'app', 'port': 80}]}]}}]

    def test_valid_single_writer_contract(self):
        migration.check_workloads('example', self.contract, self.objects)

    def test_broken_wiring_is_rejected(self):
        cases = [
            ('overlapping writers', lambda o: o[1]['spec']['strategy'].update(type='RollingUpdate')),
            ('wrong replica count', lambda o: o[1]['spec'].update(replicas=2)),
            ('missing PVC', lambda o: o[0]['metadata'].update(name='wrong')),
            ('ephemeral data', lambda o: o[1]['spec']['template']['spec']['volumes'][0].update(
                persistentVolumeClaim={'claimName': 'wrong'})),
            ('wrong service port', lambda o: o[2]['spec']['ports'][0].update(targetPort='wrong')),
            ('wrong selector', lambda o: o[2]['spec']['selector'].update(app='wrong')),
            ('wrong route', lambda o: o[3]['spec']['routes'][0]['services'][0].update(name='wrong')),
            ('missing probe', lambda o: o[1]['spec']['template']['spec']['containers'][0].pop('readinessProbe')),
            ('unmanaged secret', lambda o: o.append({'kind': 'Secret'})),
            ('floating image', lambda o: o[1]['spec']['template']['spec']['containers'][0].update(image='example:latest')),
        ]
        for label, mutate in cases:
            with self.subTest(label=label):
                objects = copy.deepcopy(self.objects)
                mutate(objects)
                with self.assertRaises(ValueError):
                    migration.check_workloads('example', self.contract, objects)

    def test_inline_password_is_rejected(self):
        self.objects[1]['spec']['template']['spec']['containers'][0]['env'] = [
            {'name': 'DB_PASSWORD', 'value': 'fixture-only'}]
        with self.assertRaisesRegex(ValueError, 'credential inline'):
            migration.check_workloads('example', self.contract, self.objects)

    def test_hardware_exceptions_require_exact_paths_and_a_selected_node(self):
        pod = self.objects[1]['spec']['template']['spec']
        pod['hostNetwork'] = True
        pod['dnsPolicy'] = 'ClusterFirstWithHostNet'
        pod['nodeSelector'] = {'home.kulikov.uk/automation': 'true'}
        pod['volumes'].append({'name': 'dbus', 'hostPath': {'path': '/run/dbus'}})
        self.contract.update(hostNetworkWorkloads=['app'], hostPaths={'app': ['/run/dbus']})
        migration.check_workloads('example', self.contract, self.objects)
        pod['volumes'][-1]['hostPath']['path'] = '/var/run/docker.sock'
        with self.assertRaisesRegex(ValueError, 'unapproved host path'):
            migration.check_workloads('example', self.contract, self.objects)
        pod['volumes'][-1]['hostPath']['path'] = '/run/dbus'
        pod.pop('nodeSelector')
        with self.assertRaisesRegex(ValueError, 'node must be selected'):
            migration.check_workloads('example', self.contract, self.objects)

    def test_shared_claim_must_be_declared_and_mounted(self):
        self.objects.pop(0)
        self.contract['sharedClaims'] = ['data']
        migration.check_workloads('example', self.contract, self.objects)
        self.contract['sharedClaims'] = ['wrong']
        with self.assertRaisesRegex(ValueError, 'dangling PVC'):
            migration.check_workloads('example', self.contract, self.objects)

    def test_new_exclusions_are_enforced(self):
        for name in ['lionbot', 'wireguard', 'midpass', 'samba', 'QBittorrentBot']:
            with self.subTest(service=name), self.assertRaisesRegex(ValueError, 'excluded service'):
                migration.check_preparation(migration.ROOT, name, {}, [])


class RepositoryMigrationTests(unittest.TestCase):
    def test_every_preparation_stays_inactive_and_registered(self):
        for name, contract in migration.contracts():
            with self.subTest(service=name):
                objects = migration.definitions(migration.ROOT, name)
                migration.check_preparation(migration.ROOT, name, contract, objects)


class ImageBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.context = self.root / 'apps/example/image'
        self.context.mkdir(parents=True)
        self.contract = {'imageBuilds': [{'context': 'apps/example/image'}]}

    def test_registered_image_runs_tests_without_networking(self):
        with patch.object(migration.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            migration.check_images(self.root, 'example', self.contract)
        build, test = run.call_args_list
        self.assertEqual(build.args[0], ['docker', 'build', '-t', 'infra-migration-example-0:test',
                                        '-f', str(self.context / 'Dockerfile'), str(self.context)])
        self.assertEqual(test.args[0], ['docker', 'run', '--rm', '--network', 'none',
                                       '--entrypoint', 'python', 'infra-migration-example-0:test',
                                       '-m', 'unittest', 'discover', '-s', 'tests', '-v'])

    def test_unregistered_images_are_not_built(self):
        with patch.object(migration.subprocess, 'run') as run:
            migration.check_images(self.root, 'example', {})
        run.assert_not_called()

    def test_image_paths_cannot_escape_service_source(self):
        cases = [
            ({'context': 'apps/other/image'}, 'inside the service source'),
            ({'context': 'apps/example/image', 'dockerfile': '../Dockerfile'}, 'escapes image context'),
        ]
        for build, message in cases:
            with self.subTest(build=build), patch.object(migration.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, message):
                    migration.check_images(self.root, 'example', {'imageBuilds': [build]})
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
