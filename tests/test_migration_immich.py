"""immich migration invariants from the source snapshot; no live credentials."""
from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/immich'
def load(filename):
    return yaml.safe_load((APP / filename).read_text())
def release(name):
    return load('helmrelease-' + name + '.yaml')

class MigrationTests(unittest.TestCase):
    def test_database_is_not_upgraded_and_server_waits_for_backend(self):
        backend = release('immich-backend')['spec']['values']
        image = backend['controllers']['database']['containers']['main']['image']
        self.assertEqual(image['repository'], 'docker.io/tensorchord/pgvecto-rs')
        self.assertEqual(image['tag'], 'pg14-v0.2.0')
        server = release('immich')['spec']
        self.assertEqual(server['dependsOn'], [{'name': 'immich-backend'}])
        self.assertFalse(server['values']['postgresql']['enabled'])
        self.assertFalse(server['values']['redis']['enabled'])
        self.assertEqual(server['values']['env']['DB_HOSTNAME'], 'immich-postgres')
        self.assertIn('v1.131.3@sha256:', server['values']['server']['image']['tag'])

    def test_restore_helper_uses_only_declared_pvcs_and_no_cluster_credentials(self):
        helper = load('restore-pod.yaml')['spec']
        declared = {v['metadata']['name'] for v in yaml.safe_load_all((APP / 'storage.yaml').read_text())}
        self.assertEqual({v['persistentVolumeClaim']['claimName'] for v in helper['volumes']}, declared)
        self.assertFalse(helper['automountServiceAccountToken'])
        self.assertFalse(helper.get('hostNetwork', False))
