"""qbittorrent migration invariants from the source snapshot; no live credentials."""
from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/qbittorrent'
def load(filename):
    return yaml.safe_load((APP / filename).read_text())
def release(name):
    return load('helmrelease-' + name + '.yaml')

class MigrationTests(unittest.TestCase):
    def test_peer_ports_are_exposed_separately_from_webui(self):
        v = release('qbittorrent')['spec']['values']
        peer = v['service']['peer']
        self.assertEqual(peer['type'], 'LoadBalancer')
        self.assertEqual({(p['port'], p['protocol']) for p in peer['ports'].values()}, {(6881, 'TCP'), (6881, 'UDP')})
        self.assertEqual(v['service']['main']['type'], 'ClusterIP')
        self.assertEqual(v['persistence']['downloads']['advancedMounts']['main']['main'][0]['path'], '/downloads')
        self.assertEqual(v['controllers']['main']['containers']['main']['env']['TZ'], 'Europe/London')

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_restore_helper_uses_only_declared_pvcs_and_no_cluster_credentials(self):
        helper = load('restore-pod.yaml')['spec']
        declared = {v['metadata']['name'] for v in yaml.safe_load_all((APP / 'storage.yaml').read_text())}
        self.assertEqual({v['persistentVolumeClaim']['claimName'] for v in helper['volumes']}, declared)
        self.assertFalse(helper['automountServiceAccountToken'])
        self.assertFalse(helper.get('hostNetwork', False))

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_downloads_share_copyparty_claim_and_node(self):
        v = release('qbittorrent')['spec']['values']
        self.assertEqual(v['controllers']['main']['containers']['main']['image']['repository'], 'ghcr.io/lionzxy/qbittorrent-noupload')
        self.assertEqual(load('kustomization.yaml')['namespace'], 'copyparty')
        self.assertEqual(v['persistence']['downloads']['existingClaim'], 'copyparty-data')
        self.assertEqual(v['persistence']['downloads']['advancedMounts']['main']['main'][0]['subPath'], 'downloads')
        affinity = v['defaultPodOptions']['affinity']['podAffinity']['requiredDuringSchedulingIgnoredDuringExecution'][0]
        self.assertEqual(affinity['topologyKey'], 'kubernetes.io/hostname')
        self.assertEqual(affinity['labelSelector']['matchLabels'], {'home.kulikov.uk/shared-data': 'copyparty'})
        self.assertNotIn('samba', (APP / 'MIGRATION.md').read_text())
