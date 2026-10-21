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
    def test_file_sharing_preserves_uid_and_shared_volume_identity(self):
        v = values()
        self.assertEqual(v['controllers']['main']['containers']['main']['securityContext']['runAsUser'], 1000)
        self.assertEqual(v['controllers']['main']['containers']['main']['securityContext']['runAsGroup'], 1000)
        self.assertEqual(v['controllers']['main']['strategy'], 'Recreate')
        self.assertEqual(v['persistence']['data']['existingClaim'], 'copyparty-data')
        self.assertEqual(v['persistence']['data']['advancedMounts']['main']['main'][0]['path'], '/w')
        self.assertNotIn('subPath', v['persistence']['data']['advancedMounts']['main']['main'][0])
        self.assertEqual(v['defaultPodOptions']['labels']['home.kulikov.uk/shared-data'], 'copyparty')

    def test_configuration_keeps_its_restored_volume(self):
        configuration = values()['persistence']['configuration']
        self.assertEqual(configuration['type'], 'persistentVolumeClaim')
        self.assertEqual(configuration['existingClaim'], 'copyparty-config')
        self.assertEqual(configuration['advancedMounts']['main']['main'], [{'path': '/cfg'}])

    def test_activation_preserves_both_claims_and_retention(self):
        claims = {item['metadata']['name']: item for item in yaml.safe_load_all((APP / 'storage.yaml').read_text())}
        expected = {'copyparty-data': '512Gi', 'copyparty-config': '2Gi'}
        self.assertEqual(set(claims), set(expected))
        for name, size in expected.items():
            claim = claims[name]
            self.assertEqual(claim['spec']['storageClassName'], 'local-path')
            self.assertEqual(claim['spec']['accessModes'], ['ReadWriteOnce'])
            self.assertEqual(claim['spec']['resources']['requests']['storage'], size)
            self.assertEqual(claim['metadata']['annotations']['kustomize.toolkit.fluxcd.io/prune'], 'disabled')

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_restore_stops_both_writers_before_copy(self):
        self.assertIn('docker stop qbittorrent copyparty', (APP / 'MIGRATION.md').read_text())
