"""Migration safety and wiring regression tests; no production access."""
import copy
from pathlib import Path
import sys
import unittest

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


class RepositoryMigrationTests(unittest.TestCase):
    def test_every_preparation_stays_inactive_and_registered(self):
        for name, contract in migration.contracts():
            with self.subTest(service=name):
                objects = migration.definitions(migration.ROOT, name)
                migration.check_preparation(migration.ROOT, name, contract, objects)


if __name__ == '__main__':
    unittest.main()
