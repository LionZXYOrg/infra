"""Keep the original public service names and their TLS coverage."""
import copy
from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import migration_check as migration

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = {
    'changedetection': 'cd.kulikov.uk',
    'copyparty': 'cp.kulikov.uk',
    'homeassist': 'ha.kulikov.uk',
    'immich': 'immich.kulikov.uk',
    'localcontentshare': 'share.kulikov.uk',
    'obsidian': 'obsidian.kulikov.uk',
    'portainer': 'portainer.lionzxy.ru',
    'qbittorrent': 'torrent.kulikov.uk',
    'speedtest': 'speedtest.kulikov.uk',
    'transfersh': 'transfer.kulikov.uk',
    'warden': 'warden.lionzxy.ru',
}


@unittest.skipUnless(shutil.which('kubectl'), 'kubectl needed for Kustomize rendering')
class OriginalHostnameTests(unittest.TestCase):
    def services(self):
        for name, hostname in ORIGINAL.items():
            if (ROOT / 'apps' / name / 'kustomization.yaml').exists():
                yield name, hostname, migration.definitions(ROOT, name)

    def test_rendered_routes_keep_only_the_original_https_hostname(self):
        for name, hostname, objects in self.services():
            with self.subTest(service=name):
                routes = [obj for obj in objects if obj['kind'] == 'IngressRoute']
                self.assertEqual(len(routes), 1)
                route = routes[0]
                self.assertEqual(route['spec']['entryPoints'], ['websecure'])
                self.assertIn('tls', route['spec'])
                self.assertEqual(len(route['spec']['routes']), 1)
                self.assertEqual(route['spec']['routes'][0]['match'], f'Host(`{hostname}`)')

    def test_renamed_hosts_and_extra_aliases_fail_validation(self):
        contracts = dict(migration.contracts(ROOT))
        for name, hostname, objects in self.services():
            invalid = [f'Host(`{hostname}`) || Host(`unapproved.kulikov.uk`)']
            if hostname != f'{name}.kulikov.uk':
                invalid.append(f'Host(`{name}.kulikov.uk`)')
            for match in invalid:
                with self.subTest(service=name, match=match):
                    changed = copy.deepcopy(objects)
                    route = next(obj for obj in changed if obj['kind'] == 'IngressRoute')
                    route['spec']['routes'][0]['match'] = match
                    with self.assertRaisesRegex(ValueError, 'route must use'):
                        migration.check_preparation(ROOT, name, contracts[name], changed)

    def test_other_domains_use_a_matching_production_certificate(self):
        for name, hostname, objects in self.services():
            if hostname.endswith('.kulikov.uk'):
                continue
            with self.subTest(service=name):
                route = next(obj for obj in objects if obj['kind'] == 'IngressRoute')
                secret = route['spec']['tls'].get('secretName')
                self.assertTrue(secret)
                certificates = [obj for obj in objects if obj['kind'] == 'Certificate'
                                and obj['spec']['secretName'] == secret]
                self.assertEqual(len(certificates), 1)
                self.assertEqual(certificates[0]['spec']['dnsNames'], [hostname])
                self.assertEqual(certificates[0]['spec']['issuerRef'],
                                 {'kind': 'ClusterIssuer', 'name': 'letsencrypt-production'})


if __name__ == '__main__':
    unittest.main()
