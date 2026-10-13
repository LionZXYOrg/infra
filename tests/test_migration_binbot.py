from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/binbot'
def load(name):
    return yaml.safe_load((APP / name).read_text())
def values():
    return load('helmrelease-binbot.yaml')['spec']['values']

class MigrationTests(unittest.TestCase):
    def test_subscribers_and_address_are_not_baked_into_the_image(self):
        v = values()
        c = v['controllers']['main']['containers']['main']
        self.assertEqual(c['envFrom'], [{'secretRef': {'name': 'binbot-config'}}])
        self.assertEqual(c['env']['SUBSCRIBERS_FILE'], '/app/data/subscribers.json')
        self.assertEqual(v['persistence']['data']['existingClaim'], 'binbot-data')
        self.assertIn("os.environ['ADDRESS']", (APP / 'image/bot.py').read_text())
        dockerfile = (APP / 'image/Dockerfile').read_text()
        self.assertNotIn('COPY .', dockerfile)
        self.assertNotIn('COPY .env', dockerfile)
