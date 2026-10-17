from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/birthday-reminder'
def load(name):
    return yaml.safe_load((APP / name).read_text())
def values():
    return load('helmrelease-birthday-reminder.yaml')['spec']['values']

class MigrationTests(unittest.TestCase):
    def test_bot_and_cron_share_restored_birthdays_but_keep_private_configuration(self):
        v = values()
        mounts = v['persistence']['data']['advancedMounts']['main']
        self.assertEqual(set(mounts), {'bot', 'cron'})
        for entries in mounts.values():
            self.assertEqual(entries[0]['subPath'], 'birthday.csv')
            self.assertEqual(entries[0]['path'], '/app/birthday.csv')
        private = v['persistence']['configuration']['advancedMounts']['main']
        for part in ['bot', 'cron']:
            self.assertEqual(private[part][0]['subPath'], part + '-secret.py')
        self.assertIn('cron_builder.py >', v['controllers']['main']['containers']['cron']['command'][-1])
        dockerfile = (APP / 'image/Dockerfile').read_text()
        self.assertNotIn('COPY secret.py', dockerfile)
        self.assertNotIn('COPY birthday.csv', dockerfile)
        self.assertNotIn('RUN python /app/cron_builder.py', dockerfile)
