"""localcontentshare migration invariants from the source snapshot; no live credentials."""
from pathlib import Path
import shlex
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/localcontentshare'
def load(filename):
    return yaml.safe_load((APP / filename).read_text())
def release(name):
    return load('helmrelease-' + name + '.yaml')

class MigrationTests(unittest.TestCase):
    def test_storage_is_namespaced_and_retained(self):
        claim = load('storage.yaml')
        self.assertEqual(claim['metadata']['namespace'], 'localcontentshare')
        self.assertEqual(claim['metadata']['annotations']['kustomize.toolkit.fluxcd.io/prune'], 'disabled')
        self.assertEqual(claim['spec']['accessModes'], ['ReadWriteOnce'])

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_copy_uses_trusted_ssh_and_only_storage_commands(self):
        commands = (APP / 'MIGRATION.md').read_text()
        ssh_line = next(line for line in commands.splitlines() if line.startswith('SSH=('))
        self.assertEqual(shlex.split(ssh_line[len('SSH=('):-1]), [
            'ssh', '-n', '-T', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'UserKnownHostsFile=$KNOWN_HOSTS', '-o', 'HostKeyAlgorithms=ssh-ed25519',
            '-o', 'ConnectTimeout=10', 'root@192.168.1.109'])
        self.assertTrue(commands.startswith("```bash\nKUBECONFIG=\"${KUBECONFIG:?}\" bash <<'LOCALCONTENTSHARE_MIGRATION'\n"))
        self.assertIn('192.168.1.109 ssh-ed25519 ', commands)
        self.assertIn('tar -C /home/docker/app/localcontentshare/data -cpf - .', commands)
        self.assertIn('tar -xpf /tmp/localcontentshare-data.tar -C /restore/localcontentshare-data', commands)
        self.assertIn('test "$ARCHIVE_SHA" = "${POD_SHA%% *}"', commands)
        self.assertNotIn('exec -i restore', commands)
        self.assertIn('docker ps --filter name=local-content-share', commands)
        for line in commands.splitlines():
            if line.startswith('kubectl ') and ('storage.yaml' in line or 'restore-pod.yaml' in line):
                self.assertIn('-n localcontentshare apply', line)
        for forbidden in ['HostKeyAlias', 'home.kulikov.uk', 'lionzxy@', 'sudo ', 'docker stop', 'gh pr', 'git switch', 'flux ', 'sops ', 'age ']:
            self.assertNotIn(forbidden, commands)

    def test_restore_preserves_app_data_and_https_target(self):
        v = release('localcontentshare')['spec']['values']
        self.assertEqual(v['persistence']['data']['advancedMounts']['main']['main'][0]['path'], '/app/data')
        route = load('ingressroute.yaml')['spec']
        self.assertEqual(route['routes'][0]['services'], [{'name': 'localcontentshare', 'port': 8080}])
        self.assertEqual(route['entryPoints'], ['websecure'])

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_restore_helper_uses_only_declared_pvcs_and_no_cluster_credentials(self):
        helper = load('restore-pod.yaml')['spec']
        declared = {v['metadata']['name'] for v in yaml.safe_load_all((APP / 'storage.yaml').read_text())}
        self.assertEqual({v['persistentVolumeClaim']['claimName'] for v in helper['volumes']}, declared)
        self.assertFalse(helper['automountServiceAccountToken'])
        self.assertFalse(helper.get('hostNetwork', False))
