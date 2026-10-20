"""obsidian migration invariants from the source snapshot; no live credentials."""
from pathlib import Path
import shlex
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/obsidian'
def load(filename):
    return yaml.safe_load((APP / filename).read_text())
def release(name):
    return load('helmrelease-' + name + '.yaml')

class MigrationTests(unittest.TestCase):
    def test_livesync_remains_authenticated_single_node_with_cors(self):
        v = release('obsidian')['spec']['values']
        self.assertEqual(v['clusterSize'], 1)
        self.assertFalse(v['allowAdminParty'])
        self.assertFalse(v['createAdminSecret'])
        self.assertEqual(v['extraSecretName'], 'obsidian-config')
        self.assertEqual(v['couchdbConfig']['chttpd']['require_valid_user'], 'true')
        self.assertEqual(v['couchdbConfig']['cors']['credentials'], 'true')
        self.assertIn('app://obsidian.md', v['couchdbConfig']['cors']['origins'])
        self.assertFalse(v['persistentVolume']['enabled'])
        self.assertIn('claimName: obsidian-data', release('obsidian')['spec']['postRenderers'][0]['kustomize']['patches'][0]['patch'])

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_restore_helper_uses_only_declared_pvcs_and_no_cluster_credentials(self):
        helper = load('restore-pod.yaml')['spec']
        declared = {v['metadata']['name'] for v in yaml.safe_load_all((APP / 'storage.yaml').read_text())}
        self.assertEqual({v['persistentVolumeClaim']['claimName'] for v in helper['volumes']}, declared)
        self.assertFalse(helper['automountServiceAccountToken'])
        self.assertFalse(helper.get('hostNetwork', False))

    def test_retained_storage_keeps_the_saved_database_and_target_capacity(self):
        claim = load('storage.yaml')
        self.assertEqual(claim['metadata']['name'], 'obsidian-data')
        self.assertEqual(claim['metadata']['annotations']['kustomize.toolkit.fluxcd.io/prune'], 'disabled')
        self.assertEqual(claim['spec']['storageClassName'], 'local-path')
        self.assertEqual(claim['spec']['accessModes'], ['ReadWriteOnce'])
        self.assertEqual(claim['spec']['resources']['requests']['storage'], '10Gi')

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_restore_uses_saved_data_without_a_retired_container_or_root_ssh(self):
        instructions = (APP / 'MIGRATION.md').read_text()
        commands = [shlex.split(line) for line in instructions.splitlines() if line.startswith('ssh ')]
        self.assertEqual(len(commands), 1)
        source = commands[0][:commands[0].index('|')]
        self.assertEqual(source, [
            'ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'HostKeyAlias=home.kulikov.uk', 'lionzxy@192.168.1.109',
            'sudo -n tar -C /home/docker/app/obsidian/couchdb-data -cpf - .',
        ])
        destination = commands[0][commands[0].index('|') + 1:]
        self.assertEqual(destination[-5:], ['tar', '-xpf', '-', '-C', '/restore/obsidian-data'])
        self.assertIn('chown -R 5984:5984 /restore/obsidian-data', instructions)
        for forbidden in ['docker stop', 'docker cp', 'root@', 'gh pr', 'git switch', 'flux ', 'sops ', 'age ']:
            self.assertNotIn(forbidden, instructions)
