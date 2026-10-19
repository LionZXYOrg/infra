"""warden migration invariants from the source snapshot; no live credentials."""
from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/warden'
def load(filename):
    return yaml.safe_load((APP / filename).read_text())
def release(name):
    return load('helmrelease-' + name + '.yaml')

class MigrationTests(unittest.TestCase):
    def test_public_domain_and_dedicated_certificate_match(self):
        hostname = 'warden.lionzxy.ru'
        self.assertEqual(release('warden')['spec']['values']['domain'], f'https://{hostname}')
        route = load('ingressroute.yaml')['spec']
        self.assertEqual(route['routes'][0]['match'], f'Host(`{hostname}`)')
        certificate = load('certificate.yaml')['spec']
        self.assertEqual(certificate['dnsNames'], [hostname])
        self.assertEqual(route['tls']['secretName'], certificate['secretName'])
        self.assertEqual(certificate['issuerRef'], {'kind': 'ClusterIssuer', 'name': 'letsencrypt-production'})
        self.assertIn('certificate.yaml', load('kustomization.yaml')['resources'])

    def test_chart_cannot_enable_signups_or_generate_credentials(self):
        v = release('warden')['spec']['values']
        self.assertFalse(v['signupsAllowed'])
        self.assertEqual(v['image']['tag'].split('@')[0], '1.37.2-alpine')
        self.assertEqual(v['adminToken']['existingSecret'], 'warden-config')
        for integration in ('smtp', 'pushNotifications', 'yubico', 'duo'):
            self.assertEqual(v[integration]['existingSecret'], 'warden-config')
        self.assertEqual(v['storage']['existingVolumeClaim']['dataPath'], '/data')

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_restore_helper_uses_only_declared_pvcs_and_no_cluster_credentials(self):
        helper = load('restore-pod.yaml')['spec']
        declared = {v['metadata']['name'] for v in yaml.safe_load_all((APP / 'storage.yaml').read_text())}
        self.assertEqual({v['persistentVolumeClaim']['claimName'] for v in helper['volumes']}, declared)
        self.assertFalse(helper['automountServiceAccountToken'])
        self.assertFalse(helper.get('hostNetwork', False))
