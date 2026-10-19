from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/immich'
def load(name):
    return yaml.safe_load((APP / name).read_text())
def values():
    return load('helmrelease-immich.yaml')['spec']['values']

class MigrationTests(unittest.TestCase):
    def test_restore_keeps_live_database_extensions_and_application_versions(self):
        v = load('helmrelease-immich-backend.yaml')['spec']['values']
        database = v['controllers']['database']['containers']['main']
        self.assertEqual(database['image']['repository'], 'ghcr.io/immich-app/postgres')
        self.assertEqual(database['image']['tag'], '14-vectorchord0.4.3-pgvectors0.2.0')
        self.assertEqual(database['args'], ['-c', 'config_file=/etc/postgresql/postgresql.conf'])
        self.assertEqual(database['env']['PGDATA'], '/var/lib/postgresql/data')
        self.assertEqual(v['controllers']['redis']['containers']['main']['image']['repository'], 'valkey/valkey')
        for part in ['server', 'machine-learning']:
            self.assertEqual(values()[part]['controllers']['main']['containers']['main']['image']['tag'], 'v3.3.1')
        self.assertFalse(values()['valkey']['enabled'])

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_media_capacity_and_logical_restore_prevent_old_snapshot_restore(self):
        claims = {d['metadata']['name']: d['spec']['resources']['requests']['storage']
                  for d in yaml.safe_load_all((APP / 'storage.yaml').read_text())}
        self.assertEqual(claims['immich-library'], '2Ti')
        instructions = (APP / 'MIGRATION.md').read_text()
        self.assertIn('tar -C /home/lionzxy/tmp-moving/immich -cpf - .', instructions)
        self.assertIn('ssh root@192.168.1.109', instructions)
        self.assertIn('pg_dump -U postgres -d immich -Fc', instructions)
        self.assertIn('pg_restore -U postgres -d immich', instructions)
        self.assertNotIn('tar -C /home/docker/app/immich/postgres', instructions)
        restore = load('restore-db.yaml')['spec']
        self.assertFalse(restore['automountServiceAccountToken'])
        self.assertNotIn('restore-db.yaml', load('kustomization.yaml')['resources'])
