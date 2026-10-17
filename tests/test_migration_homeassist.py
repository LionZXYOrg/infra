from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/homeassist'
def load(name):
    return yaml.safe_load((APP / name).read_text())
def values():
    return load('helmrelease-homeassist.yaml')['spec']['values']

class MigrationTests(unittest.TestCase):
    def test_matter_discovery_and_hardware_stay_on_the_selected_node(self):
        v = values()
        for name, controller in v['controllers'].items():
            self.assertEqual(controller['pod']['nodeSelector'], {'home.kulikov.uk/automation': 'true'})
            if name != 'homeassistant':
                self.assertTrue(controller['pod']['hostNetwork'])
                self.assertEqual(controller['pod']['dnsPolicy'], 'ClusterFirstWithHostNet')
        self.assertTrue(v['controllers']['homeassistant']['containers']['main']['securityContext']['privileged'])
        self.assertEqual(v['persistence']['radio-0']['hostPathType'], 'CharDevice')
        self.assertEqual(v['persistence']['radio-1']['hostPathType'], 'CharDevice')
        self.assertEqual(v['controllers']['matter-hub']['containers']['main']['env']['HAMH_HOME_ASSISTANT_URL'], 'http://homeassist-homeassistant:8123/')

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_restore_rewrites_the_docker_only_matter_address(self):
        instructions = (APP / 'MIGRATION.md').read_text()
        self.assertIn('ws://host.docker.internal:5580/ws', instructions)
        self.assertIn('ws://homeassist-matter-server.homeassist.svc.cluster.local:5580/ws', instructions)
        self.assertIn('chown -R 1000:1000 /restore/homeassist-matter', instructions)
