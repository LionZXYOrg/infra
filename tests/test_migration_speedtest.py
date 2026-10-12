"""speedtest migration invariants from the source snapshot; no live credentials."""
from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/speedtest'
def load(filename):
    return yaml.safe_load((APP / filename).read_text())
def release(name):
    return load('helmrelease-' + name + '.yaml')

class MigrationTests(unittest.TestCase):
    def test_standalone_mode_has_no_accidental_database_or_volume(self):
        v = release('speedtest')['spec']['values']
        self.assertEqual(v['controllers']['main']['containers']['main']['env']['MODE'], 'standalone')
        self.assertNotIn('persistence', v)
        self.assertEqual(v['service']['main']['ports']['http']['port'], 8080)
