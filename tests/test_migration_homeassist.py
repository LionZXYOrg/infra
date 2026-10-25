from pathlib import Path
import shutil
import subprocess
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/homeassist'
def load(name):
    return yaml.safe_load((APP / name).read_text())
def values():
    return load('helmrelease-homeassist.yaml')['spec']['values']

class MigrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('kubectl'), 'kubectl needed for Kustomize rendering')
    def test_only_ha_hostname_reaches_the_existing_https_service(self):
        rendered = subprocess.check_output(['kubectl', 'kustomize', str(APP)], timeout=30)
        route = next(obj for obj in yaml.safe_load_all(rendered) if obj['kind'] == 'IngressRoute')
        self.assertEqual(route['metadata']['namespace'], 'homeassist')
        self.assertEqual(route['spec']['entryPoints'], ['websecure'])
        self.assertEqual(route['spec']['tls'], {})
        rules = route['spec']['routes']
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]['match'], 'Host(`ha.kulikov.uk`)')
        self.assertEqual(rules[0]['services'], [{'name': 'homeassist-homeassistant', 'port': 8123}])

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

    def test_radios_keep_the_paths_in_the_restored_configuration(self):
        persistence = values()['persistence']
        self.assertEqual(persistence['radio-0']['advancedMounts']['otbr']['main'], [{'path': '/dev/ttyACM0'}])
        self.assertEqual(persistence['radio-1']['advancedMounts']['homeassistant']['main'], [{'path': '/dev/ttyUSB0'}])

    def test_matter_uses_the_lan_interface_on_the_target_host(self):
        controller = values()['controllers']['matter-server']
        self.assertEqual(controller['containers']['main']['env']['PRIMARY_INTERFACE'], 'ens18')
        self.assertTrue(controller['pod']['hostNetwork'])

    def test_activation_keeps_all_restored_claims_and_retention(self):
        claims = {d['metadata']['name']: d for d in yaml.safe_load_all((APP / 'storage.yaml').read_text())}
        expected = {'homeassist-config': '10Gi', 'homeassist-matter': '2Gi', 'homeassist-hub': '2Gi', 'homeassist-otbr': '1Gi'}
        self.assertEqual(set(claims), set(expected))
        for name, size in expected.items():
            claim = claims[name]
            self.assertEqual(claim['spec']['storageClassName'], 'local-path')
            self.assertEqual(claim['spec']['accessModes'], ['ReadWriteOnce'])
            self.assertEqual(claim['spec']['resources']['requests']['storage'], size)
            self.assertEqual(claim['metadata']['annotations']['kustomize.toolkit.fluxcd.io/prune'], 'disabled')
        references = {entry['existingClaim'] for entry in values()['persistence'].values()
                      if entry['type'] == 'persistentVolumeClaim'}
        self.assertEqual(references, set(expected))

    @unittest.skipUnless((APP / 'MIGRATION.md').exists(), 'Migration completed')
    def test_restore_rewrites_the_docker_only_matter_address(self):
        instructions = (APP / 'MIGRATION.md').read_text()
        self.assertIn('ws://host.docker.internal:5580/ws', instructions)
        self.assertIn('ws://homeassist-matter-server.homeassist.svc.cluster.local:5580/ws', instructions)
        self.assertIn('chown -R 1000:1000 /restore/homeassist-matter', instructions)
