from pathlib import Path
import shlex
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/birthday-reminder'
def load(name):
    return yaml.safe_load((APP / name).read_text())
def values():
    return load('helmrelease-birthday-reminder.yaml')['spec']['values']

class MigrationTests(unittest.TestCase):
    def test_activation_preserves_the_restored_claim_and_retention(self):
        claim = load('storage.yaml')
        self.assertEqual(claim['metadata']['name'], 'birthday-reminder-data')
        self.assertEqual(claim['metadata']['annotations']['kustomize.toolkit.fluxcd.io/prune'], 'disabled')
        self.assertEqual(claim['spec']['storageClassName'], 'local-path')
        self.assertEqual(claim['spec']['accessModes'], ['ReadWriteOnce'])
        self.assertEqual(claim['spec']['resources']['requests']['storage'], '1Gi')
        self.assertEqual(values()['persistence']['data']['existingClaim'], claim['metadata']['name'])

    def test_both_containers_use_the_same_published_image_and_timezone(self):
        containers = values()['controllers']['main']['containers']
        images = set()
        for container in containers.values():
            image = container['image']
            self.assertEqual(image['repository'], 'ghcr.io/lionzxyorg/birthday-reminder')
            self.assertRegex(image['digest'], r'^sha256:[0-9a-f]{64}$')
            images.add((image['repository'], image['tag'], image['digest']))
            self.assertEqual(container['env']['TZ'], 'Europe/Moscow')
        self.assertEqual(len(images), 1)

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_restore_copies_only_the_saved_csv_without_a_retired_container(self):
        instructions = (APP / 'MIGRATION.md').read_text()
        commands = [shlex.split(line) for line in instructions.splitlines() if line.startswith('ssh ')]
        self.assertEqual(len(commands), 1)
        source, destination = commands[0][:commands[0].index('|')], commands[0][commands[0].index('|') + 1:]
        self.assertEqual(source, [
            'ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'HostKeyAlias=home.kulikov.uk', 'lionzxy@192.168.1.109',
            'sudo -n tar -C /home/docker/app/personal_birthday_reminder -cpf - birthday.csv',
        ])
        self.assertEqual(destination[-5:], ['tar', '-xpf', '-', '-C', '/restore/birthday-reminder-data'])
        self.assertIn('restore', destination)
        self.assertNotIn('docker cp', instructions)
        self.assertNotIn('docker stop', instructions)

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
