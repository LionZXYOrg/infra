"""replacevowelbot migration invariants from the source snapshot; no live credentials."""
from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/replacevowelbot'
def load(filename):
    return yaml.safe_load((APP / filename).read_text())
def release(name):
    return load('helmrelease-' + name + '.yaml')

class MigrationTests(unittest.TestCase):
    def test_token_file_interface_is_preserved_and_never_becomes_public(self):
        v = release('replacevowelbot')['spec']['values']
        secret = v['persistence']['secret']
        self.assertEqual(secret['type'], 'secret')
        self.assertEqual(secret['name'], 'replacevowelbot-config')
        self.assertEqual(secret['advancedMounts']['main']['main'][0], {'path': '/app/secret.py', 'subPath': 'secret.py'})
        self.assertFalse((APP / 'secret.py').exists())
