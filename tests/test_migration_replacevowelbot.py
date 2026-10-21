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
    def test_merge_starts_the_stateless_bot_without_a_restore(self):
        self.assertIs(release('replacevowelbot')['spec']['suspend'], False)
        flux = yaml.safe_load((ROOT / 'clusters/home/replacevowelbot.yaml').read_text())
        self.assertIs(flux['spec']['suspend'], False)
        applications = yaml.safe_load((ROOT / 'clusters/home/kustomization.yaml').read_text())['resources']
        self.assertEqual(applications.count('replacevowelbot.yaml'), 1)
        contract = yaml.safe_load((ROOT / 'tests/migrations/replacevowelbot.yaml').read_text())
        self.assertEqual(contract['phase'], 'active')
        self.assertFalse((APP / 'MIGRATION.md').exists())
        self.assertFalse(list(APP.glob('restore*.yaml')))
        self.assertEqual(set(release('replacevowelbot')['spec']['values']['persistence']), {'secret'})

    def test_token_file_interface_is_preserved_and_never_becomes_public(self):
        v = release('replacevowelbot')['spec']['values']
        secret = v['persistence']['secret']
        self.assertEqual(secret['type'], 'secret')
        self.assertEqual(secret['name'], 'replacevowelbot-config')
        self.assertEqual(secret['defaultMode'], 0o400)
        self.assertEqual(secret['advancedMounts']['main']['main'][0], {'path': '/app/secret.py', 'subPath': 'secret.py'})
        self.assertFalse((APP / 'secret.py').exists())
