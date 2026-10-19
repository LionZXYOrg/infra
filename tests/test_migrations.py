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
    def test_home_assistant_route_rejects_the_old_hostname_and_aliases(self):
        contract = dict(migration.contracts())['homeassist']
        objects = migration.definitions(migration.ROOT, 'homeassist')
        migration.check_preparation(migration.ROOT, 'homeassist', contract, objects)
        route = next(obj for obj in objects if obj['kind'] == 'IngressRoute')
        for match in ['Host(`homeassist.kulikov.uk`)',
                      'Host(`ha.kulikov.uk`) || Host(`homeassist.kulikov.uk`)',
                      'Host(`ha.kulikov.uk`) && Host(`homeassist.kulikov.uk`)',
                      'Host(`ha.kulikov.uk`) || Host(`other.kulikov.uk`)']:
            with self.subTest(match=match), self.assertRaisesRegex(ValueError, 'route must use'):
                route['spec']['routes'][0]['match'] = match
                migration.check_preparation(migration.ROOT, 'homeassist', contract, objects)

    def test_every_preparation_stays_inactive_and_registered(self):
        for name, contract in migration.contracts():
            with self.subTest(service=name):
                objects = migration.definitions(migration.ROOT, name)
                migration.check_preparation(migration.ROOT, name, contract, objects)


class MigrationPhaseTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for directory in ['apps/example', 'clusters/home', 'infrastructure/namespaces']:
            (self.root / directory).mkdir(parents=True)
        self.contract = {'source': {'host': 'home.kulikov.uk', 'path': '/home/docker/app/example',
                                    'observedAt': '2026-10-13'},
                         'charts': {'example': {'chart': 'example', 'version': '1', 'sha256': 'a' * 64}}}
        self.objects = [{'kind': 'HelmRelease', 'metadata': {'name': 'example'}, 'spec': {
            'suspend': True, 'chart': {'spec': {'chart': 'example', 'version': '1'}}}}]
        self.flux = {'spec': {'suspend': True, 'path': './apps/example',
            'dependsOn': [{'name': 'infra-tls'}, {'name': 'cluster-secrets'}],
            'healthChecks': [{'kind': 'HelmRelease', 'name': 'example'}]}}
        (self.root / 'clusters/home/kustomization.yaml').write_text('resources: [example.yaml]\n')
        (self.root / 'infrastructure/namespaces/kustomization.yaml').write_text('resources: [example.yaml]\n')
        (self.root / 'apps/example/MIGRATION.md').write_text('```bash\n:\n```\n\n```bash\ngit status\n```\n')

    def check(self):
        import yaml
        (self.root / 'clusters/home/example.yaml').write_text(yaml.safe_dump(self.flux))
        migration.check_preparation(self.root, 'example', self.contract, self.objects)

    def test_inventory_cannot_depend_on_a_source_repository(self):
        self.check()
        for field, value in [('repository', 'https://example.invalid/source'), ('commit', 'a' * 40)]:
            with self.subTest(field=field):
                self.contract['source'][field] = value
                with self.assertRaisesRegex(ValueError, 'live inventory fields'):
                    self.check()
                del self.contract['source'][field]

    def test_live_inventory_still_requires_the_observed_host_path_and_date(self):
        cases = [('host', 'other.example.invalid'), ('path', '/tmp/example'),
                 ('path', 'relative/path'), ('observedAt', '2026-01-01')]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                original = self.contract['source'][field]
                self.contract['source'][field] = value
                with self.assertRaisesRegex(ValueError, 'live inventory changed'):
                    self.check()
                self.contract['source'][field] = original

    def test_preparation_cannot_enable_either_controller(self):
        self.check()
        self.flux['spec']['suspend'] = False
        with self.assertRaisesRegex(ValueError, 'migration phase'):
            self.check()
        self.flux['spec']['suspend'] = True
        self.objects[0]['spec']['suspend'] = False
        with self.assertRaisesRegex(ValueError, 'migration phase'):
            self.check()

    def test_completed_phase_requires_both_controls_and_cleanup(self):
        self.contract['phase'] = 'active'
        with self.assertRaisesRegex(ValueError, 'migration phase'):
            self.check()
        self.flux['spec']['suspend'] = False
        self.objects[0]['spec']['suspend'] = False
        with self.assertRaisesRegex(ValueError, 'remove completed migration'):
            self.check()
        (self.root / 'apps/example/MIGRATION.md').unlink()
        self.check()
        (self.root / 'apps/example/restore-pod.yaml').write_text('temporary')
        with self.assertRaisesRegex(ValueError, 'remove completed restore'):
            self.check()

    def test_old_test_hostname_is_rejected(self):
        self.objects.append({'kind': 'IngressRoute', 'spec': {'entryPoints': ['websecure'], 'tls': {},
            'routes': [{'match': 'Host(`example.test.kulikov.uk`)'}]}})
        with self.assertRaisesRegex(ValueError, 'example.kulikov.uk host'):
            self.check()


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
