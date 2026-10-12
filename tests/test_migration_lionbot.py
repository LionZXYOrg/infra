"""lionbot migration invariants from the source snapshot; no live credentials."""
from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/lionbot'
def load(filename):
    return yaml.safe_load((APP / filename).read_text())
def release(name):
    return load('helmrelease-' + name + '.yaml')

class MigrationTests(unittest.TestCase):
    def test_jdbc_and_postgres_share_a_private_credential_contract(self):
        bot = release('lionbot')['spec']
        self.assertEqual(bot['dependsOn'], [{'name': 'lionbot-db'}])
        c = bot['values']['controllers']['main']['containers']['main']
        self.assertEqual(c['env']['SERVER_DATABASE_URL'], 'jdbc:postgresql://lionbot-postgres:5432/postgres')
        self.assertEqual(c['envFrom'], [{'secretRef': {'name': 'lionbot-config'}}])
        db = release('lionbot-db')['spec']['values']['controllers']['main']['containers']['main']
        self.assertEqual(db['env']['POSTGRES_PASSWORD']['valueFrom']['secretKeyRef']['key'], 'POSTGRES_PASSWORD')
        self.assertEqual(bot['values']['defaultPodOptions']['nodeSelector'], {'kubernetes.io/arch': 'amd64'})

    def test_restore_helper_uses_only_declared_pvcs_and_no_cluster_credentials(self):
        helper = load('restore-pod.yaml')['spec']
        declared = {v['metadata']['name'] for v in yaml.safe_load_all((APP / 'storage.yaml').read_text())}
        self.assertEqual({v['persistentVolumeClaim']['claimName'] for v in helper['volumes']}, declared)
        self.assertFalse(helper['automountServiceAccountToken'])
        self.assertFalse(helper.get('hostNetwork', False))

    def test_database_entrypoint_can_initialize_volume_ownership(self):
        c = release('lionbot-db')['spec']['values']['controllers']['main']['containers']['main']
        self.assertEqual(c['securityContext']['capabilities']['drop'], ['NET_RAW'])
        self.assertFalse(c['securityContext']['allowPrivilegeEscalation'])
