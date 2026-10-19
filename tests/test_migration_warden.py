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

    def test_completed_migration_is_active_without_restore_helpers(self):
        contract = yaml.safe_load((ROOT / 'tests/migrations/warden.yaml').read_text())
        flux = yaml.safe_load((ROOT / 'clusters/home/warden.yaml').read_text())
        self.assertEqual(contract['phase'], 'active')
        self.assertIs(flux['spec']['suspend'], False)
        self.assertIs(release('warden')['spec']['suspend'], False)
        self.assertFalse((APP / 'MIGRATION.md').exists())
        self.assertFalse(list(APP.glob('restore*.yaml')))

    def test_retained_storage_preserves_restored_vault_data(self):
        claim = load('storage.yaml')
        self.assertEqual(claim['metadata']['name'], 'warden-data')
        self.assertEqual(claim['metadata']['annotations']['kustomize.toolkit.fluxcd.io/prune'], 'disabled')
        self.assertEqual(claim['spec']['resources']['requests']['storage'], '10Gi')
        self.assertEqual(claim['spec']['storageClassName'], 'local-path')
        self.assertEqual(claim['spec']['accessModes'], ['ReadWriteOnce'])
        self.assertEqual(load('kustomization.yaml')['namespace'], 'warden')
        self.assertIn('storage.yaml', load('kustomization.yaml')['resources'])
        self.assertEqual(release('warden')['spec']['values']['storage']['existingVolumeClaim'],
                         {'claimName': claim['metadata']['name'], 'dataPath': '/data'})
