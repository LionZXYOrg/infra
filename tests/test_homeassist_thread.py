"""Thread route learning uses the host interface without changing saved networks."""
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/homeassist'
SCRIPT = APP / 'matter-network.sh'


class ThreadRouteTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.interface = self.root / 'ens18'
        self.interface.mkdir()
        self.initial = {'disable_ipv6': '0', 'accept_ra': '1', 'accept_ra_rt_info_max_plen': '0'}
        for name, value in self.initial.items():
            (self.interface / name).write_text(value + '\n')
        (self.root / 'all').mkdir()
        (self.root / 'all/forwarding').write_text('0\n')

    def run_script(self, interface='ens18', *extra):
        env = os.environ.copy()
        env['MATTER_IPV6_CONF_ROOT'] = str(self.root)
        return subprocess.run(['bash', str(SCRIPT), interface, *extra], env=env, capture_output=True, timeout=5)

    def test_accepts_thread_routes_without_enabling_host_forwarding(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual((self.interface / 'accept_ra').read_text().strip(), '2')
        self.assertEqual((self.interface / 'accept_ra_rt_info_max_plen').read_text().strip(), '64')
        self.assertEqual((self.root / 'all/forwarding').read_text(), '0\n')
        self.assertEqual((self.interface / 'disable_ipv6').read_text(), '0\n')

    def test_repeated_setup_does_not_rewrite_correct_configuration(self):
        self.assertEqual(self.run_script().returncode, 0)
        previous = {p.name: p.stat().st_mtime_ns for p in self.interface.iterdir()}
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(previous, {p.name: p.stat().st_mtime_ns for p in self.interface.iterdir()})

    def test_border_router_explicitly_enables_ipv6_forwarding(self):
        result = self.run_script('ens18', '--router')
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual((self.root / 'all/forwarding').read_text().strip(), '1')
        self.assertEqual((self.interface / 'accept_ra').read_text().strip(), '2')

    def test_preserves_a_larger_existing_prefix_limit(self):
        (self.interface / 'accept_ra_rt_info_max_plen').write_text('128\n')
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertEqual((self.interface / 'accept_ra_rt_info_max_plen').read_text(), '128\n')

    def test_disabled_ipv6_fails_before_any_changes(self):
        (self.interface / 'disable_ipv6').write_text('1\n')
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'IPv6 is disabled', result.stderr)
        self.assertEqual((self.interface / 'accept_ra').read_text(), '1\n')
        self.assertEqual((self.interface / 'accept_ra_rt_info_max_plen').read_text(), '0\n')

    def test_invalid_or_missing_interface_cannot_change_another_path(self):
        for interface in ('../ens18', '/ens18', 'missing', 'all', 'default', 'a' * 16):
            with self.subTest(interface=interface):
                self.assertNotEqual(self.run_script(interface).returncode, 0)
        for name, value in self.initial.items():
            self.assertEqual((self.interface / name).read_text(), value + '\n')

    def test_invalid_prefix_or_missing_file_fails_before_writes(self):
        for value in ('129', 'invalid', '-2'):
            (self.interface / 'accept_ra_rt_info_max_plen').write_text(value + '\n')
            with self.subTest(value=value):
                self.assertNotEqual(self.run_script().returncode, 0)
                self.assertEqual((self.interface / 'accept_ra').read_text(), '1\n')
        (self.interface / 'accept_ra_rt_info_max_plen').unlink()
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual((self.interface / 'accept_ra').read_text(), '1\n')


class ThreadDeploymentTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('docker'), 'Docker is needed for the pinned OTBR image smoke test')
    def test_pinned_border_router_image_supports_its_commands_and_flags(self):
        values = yaml.safe_load((APP / 'helmrelease-homeassist.yaml').read_text())['spec']['values']
        main = values['controllers']['otbr']['containers']['main']
        image = main['image']
        reference = image['repository'] + '@' + image['digest']
        result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', '/bin/bash',
                                 reference, '-ec', 'test -x /usr/sbin/ot-ctl; exec /usr/sbin/otbr-agent --help'],
                                capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, 'Pinned OTBR image command smoke test failed')
        for argument in main['args']:
            if argument.startswith('--'):
                self.assertIn(argument.split('=')[0].encode(), result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which('docker'), 'Docker is needed for the configured OTBR startup test')
    def test_configured_border_router_command_reaches_radio_initialization(self):
        values = yaml.safe_load((APP / 'helmrelease-homeassist.yaml').read_text())['spec']['values']
        main = values['controllers']['otbr']['containers']['main']
        image = main['image']
        reference = image['repository'] + '@' + image['digest']
        # No host network, USB, or credentials. The missing radio is the expected stop.
        result = subprocess.run(['docker', 'run', '--rm', '--network', 'none',
                                 '--entrypoint', main['command'][0], reference, *main['args']],
                                capture_output=True, timeout=30)
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, 'A container without the radio must stop')
        self.assertIn(b'No such file or directory', output,
                      'The configured command must reach the intentionally missing radio')
        self.assertNotIn(b'must be set', output)
        self.assertNotIn(b'unrecognized option', output)

    def test_home_automation_memory_requests_fit_the_shared_node_budget(self):
        values = yaml.safe_load((APP / 'helmrelease-homeassist.yaml').read_text())['spec']['values']

        def memory(container):
            resources = container.get('resources', {})
            self.assertNotIn('memory', resources.get('limits', {}))
            value = resources.get('requests', {}).get('memory', '0Mi')
            match = re.fullmatch(r'(\d+)(Mi|Gi)', value)
            self.assertIsNotNone(match, 'The memory request must use Mi or Gi')
            return int(match[1]) * (1024 if match[2] == 'Gi' else 1)

        total = 0
        for controller in values['controllers'].values():
            running = sum(memory(c) for c in controller['containers'].values())
            initialization = max([memory(c) for c in controller.get('initContainers', {}).values()] + [0])
            total += max(running, initialization) * controller.get('replicas', 1)
        # Leave capacity for the other services on the shared 8 GiB node.
        self.assertLessEqual(total, 1280, 'Home automation must reserve at most 1.25 GiB')

    def test_border_router_owns_the_thread_radio_and_keeps_its_api_local(self):
        values = yaml.safe_load((APP / 'helmrelease-homeassist.yaml').read_text())['spec']['values']
        controller = values['controllers']['otbr']
        self.assertTrue(controller['pod']['hostNetwork'])
        self.assertEqual(controller['pod']['nodeSelector'], {'home.kulikov.uk/automation': 'true'})
        self.assertEqual(controller['strategy'], 'Recreate')
        main = controller['containers']['main']
        self.assertEqual(main['image']['repository'], 'openthread/border-router')
        self.assertRegex(main['image']['digest'], r'^sha256:[a-f0-9]{64}$')
        args = main['args']
        self.assertEqual(args[args.index('--rest-listen-address') + 1], '127.0.0.1')
        self.assertIn('--auto-attach=0', args)
        self.assertTrue(any('spinel+hdlc+uart:///dev/ttyACM0?uart-baudrate=460800' in a for a in args))
        self.assertEqual(values['persistence']['radio-0']['advancedMounts'],
                         {'otbr': {'main': [{'path': '/dev/ttyACM0'}]}})
        self.assertNotIn('otbr', values['service'])
        self.assertEqual(values['persistence']['homeassistant']['advancedMounts']['otbr']['restore'], [
            {'path': '/restore/thread.datasets', 'subPath': '.storage/thread.datasets', 'readOnly': True}])
        self.assertEqual(values['persistence']['otbr']['existingClaim'], 'homeassist-otbr')
        self.assertEqual(controller['initContainers']['network']['args'], ['ens18', '--router'])

    def test_initialization_uses_the_same_image_and_interface_as_matter(self):
        values = yaml.safe_load((APP / 'helmrelease-homeassist.yaml').read_text())['spec']['values']
        controller = values['controllers']['matter-server']
        init = controller['initContainers']['network']
        main = controller['containers']['main']
        self.assertTrue(controller['pod']['hostNetwork'])
        self.assertEqual(init['image'], main['image'])
        self.assertEqual(init['args'], [main['env']['PRIMARY_INTERFACE']])
        self.assertEqual(init['command'], ['/usr/bin/bash', '/opt/matter-network/matter-network.sh'])
        self.assertTrue(init['securityContext']['privileged'])
        self.assertEqual(init['securityContext']['runAsUser'], 0)
        self.assertEqual(main['securityContext']['runAsUser'], 1000)
        self.assertFalse(main['securityContext']['allowPrivilegeEscalation'])
        self.assertNotIn('privileged', main['securityContext'])

    def test_rendered_config_map_name_reaches_only_the_initialization_container(self):
        objects = list(yaml.safe_load_all(subprocess.check_output(['kubectl', 'kustomize', str(APP)], timeout=30)))
        config = next(o for o in objects if o['kind'] == 'ConfigMap' and 'matter-network.sh' in o.get('data', {}))
        self.assertEqual(config['data']['matter-network.sh'], SCRIPT.read_text())
        self.assertEqual(config['data']['restore-thread.py'], (APP / 'restore-thread.py').read_text())
        release = next(o for o in objects if o['kind'] == 'HelmRelease')
        volume = release['spec']['values']['persistence']['matter-network']
        self.assertEqual(volume['name'], config['metadata']['name'])
        self.assertNotEqual(config['metadata']['name'], 'homeassist-matter-network')
        self.assertEqual(volume['advancedMounts']['matter-server'], {'network': [
            {'path': '/opt/matter-network', 'readOnly': True}]})
        self.assertEqual(volume['advancedMounts']['otbr'], {
            'network': [{'path': '/opt/matter-network', 'readOnly': True}],
            'restore': [{'path': '/opt/matter-network', 'readOnly': True}]})


if __name__ == '__main__':
    unittest.main()
