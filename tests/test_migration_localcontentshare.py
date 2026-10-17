"""localcontentshare migration invariants from the source snapshot; no live credentials."""
from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/localcontentshare'
def load(filename):
    return yaml.safe_load((APP / filename).read_text())
def release(name):
    return load('helmrelease-' + name + '.yaml')

class MigrationTests(unittest.TestCase):
    def test_restore_preserves_app_data_and_https_target(self):
        v = release('localcontentshare')['spec']['values']
        self.assertEqual(v['persistence']['data']['advancedMounts']['main']['main'][0]['path'], '/app/data')
        route = load('ingressroute.yaml')['spec']
        self.assertEqual(route['routes'][0]['services'], [{'name': 'localcontentshare', 'port': 8080}])
        self.assertEqual(route['entryPoints'], ['websecure'])

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_restore_helper_uses_only_declared_pvcs_and_no_cluster_credentials(self):
        helper = load('restore-pod.yaml')['spec']
        declared = {v['metadata']['name'] for v in yaml.safe_load_all((APP / 'storage.yaml').read_text())}
        self.assertEqual({v['persistentVolumeClaim']['claimName'] for v in helper['volumes']}, declared)
        self.assertFalse(helper['automountServiceAccountToken'])
        self.assertFalse(helper.get('hostNetwork', False))
