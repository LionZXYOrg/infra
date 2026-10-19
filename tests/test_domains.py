"""Production routes and certificate issuers cover the public hostnames."""
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class DomainTests(unittest.TestCase):
    def test_issuers_and_default_certificate_cover_public_routes(self):
        certificate = yaml.safe_load((ROOT / 'infrastructure/tls/certificate.yaml').read_text())
        store = yaml.safe_load((ROOT / 'infrastructure/tls/tlsstore.yaml').read_text())
        self.assertEqual(set(certificate['spec']['dnsNames']), {'kulikov.uk', '*.kulikov.uk'})
        self.assertEqual(store['spec']['defaultCertificate']['secretName'], certificate['spec']['secretName'])
        issuers = yaml.safe_load_all((ROOT / 'infrastructure/issuers/clusterissuers.yaml').read_text())
        for issuer in issuers:
            self.assertEqual(issuer['spec']['acme']['solvers'][0]['selector']['dnsZones'],
                             ['kulikov.uk', 'lionzxy.ru'])
        for route in (ROOT / 'apps').glob('*/ingressroute.yaml'):
            self.assertNotIn('.test.kulikov.uk', route.read_text())
            hostname = {'warden': 'warden.lionzxy.ru', 'homeassist': 'ha.kulikov.uk'}.get(
                route.parent.name, f'{route.parent.name}.kulikov.uk')
            definition = yaml.safe_load(route.read_text())
            self.assertEqual(definition['spec']['routes'][0]['match'], f'Host(`{hostname}`)')
