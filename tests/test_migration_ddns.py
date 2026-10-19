"""ddns migration invariants from the source snapshot; no live credentials."""
from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/ddns'
def load(filename):
    return yaml.safe_load((APP / filename).read_text())
def release(name):
    return load('helmrelease-' + name + '.yaml')

class MigrationTests(unittest.TestCase):
    def test_probe_cannot_update_dns_and_configuration_is_private(self):
        v = release('ddns')['spec']['values']
        for probe in v['controllers']['main']['containers']['main']['probes'].values():
            self.assertEqual(probe['spec']['exec']['command'], ['/cloudflare-ddns', '--help'])
        self.assertFalse(v['defaultPodOptions'].get('hostNetwork', False))
        config = v['persistence']['config']
        self.assertEqual(config['type'], 'secret')
        self.assertEqual(config['advancedMounts']['main']['main'][0]['subPath'], 'config.json')
