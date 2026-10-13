from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/portainer'
def load(name):
    return yaml.safe_load((APP / name).read_text())
def values():
    return load('helmrelease-portainer.yaml')['spec']['values']

class MigrationTests(unittest.TestCase):
    def test_cluster_management_does_not_mount_the_old_docker_socket(self):
        v = values()
        self.assertTrue(v['localMgmt'])
        self.assertEqual(v['image']['tag'].split('@')[0], '2.33.5')
        self.assertEqual(v['service']['type'], 'ClusterIP')
        self.assertEqual(v['persistence']['existingClaim'], 'portainer-data')
        self.assertNotIn('hostPath', str(v))
        self.assertIn('/home/docker/core/portainer-data', (APP / 'MIGRATION.md').read_text())
