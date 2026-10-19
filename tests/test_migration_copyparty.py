from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/copyparty'
def load(name):
    return yaml.safe_load((APP / name).read_text())
def values():
    return load('helmrelease-copyparty.yaml')['spec']['values']

class MigrationTests(unittest.TestCase):
    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_file_sharing_preserves_uid_and_shared_volume_identity(self):
        v = values()
        self.assertEqual(v['controllers']['main']['containers']['main']['securityContext']['runAsUser'], 1000)
        self.assertEqual(v['persistence']['data']['existingClaim'], 'copyparty-data')
        self.assertEqual(v['persistence']['data']['advancedMounts']['main']['main'][0]['path'], '/w')
        self.assertEqual(v['defaultPodOptions']['labels']['home.kulikov.uk/shared-data'], 'copyparty')
        self.assertIn('docker stop qbittorrent copyparty', (APP / 'MIGRATION.md').read_text())
