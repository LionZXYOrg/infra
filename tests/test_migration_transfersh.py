"""transfersh migration invariants from the source snapshot; no live credentials."""
from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/transfersh'
def load(filename):
    return yaml.safe_load((APP / filename).read_text())
def release(name):
    return load('helmrelease-' + name + '.yaml')

class MigrationTests(unittest.TestCase):
    def test_local_provider_and_expiry_are_preserved(self):
        v = release('transfersh')['spec']['values']
        c = v['controllers']['main']['containers']['main']
        self.assertEqual(c['args'], ['--provider=local', '--listener=:8080'])
        self.assertEqual(c['env']['PURGE_DAYS'], '1')
        self.assertEqual(c['env']['PURGE_INTERVAL'], '24')
        self.assertEqual(c['probes']['readiness']['spec']['httpGet']['path'], '/health.html')
        self.assertEqual(v['persistence']['data']['existingClaim'], 'transfersh-storage')

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_restore_helper_uses_only_declared_pvcs_and_no_cluster_credentials(self):
        helper = load('restore-pod.yaml')['spec']
        declared = {v['metadata']['name'] for v in yaml.safe_load_all((APP / 'storage.yaml').read_text())}
        self.assertEqual({v['persistentVolumeClaim']['claimName'] for v in helper['volumes']}, declared)
        self.assertFalse(helper['automountServiceAccountToken'])
        self.assertFalse(helper.get('hostNetwork', False))
