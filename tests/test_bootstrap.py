"""Bootstrap unit tests. No GitHub, SSH, Kubernetes, or secret values are needed.

Run: python3 -m unittest discover -s tests -v
"""
from pathlib import Path
import base64
import json
import shutil
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bootstrap


class RepositoryTargetTests(unittest.TestCase):
    def test_default_branch_and_owner_type(self):
        for owner_type, branch, personal in [
            ('Organization', 'master', False),
            ('User', 'main', True),
            ('Organization', 'release/home', False),
        ]:
            with self.subTest(owner_type=owner_type, branch=branch):
                info = {'owner': {'type': owner_type}, 'default_branch': branch}
                with patch.object(bootstrap, 'capture', return_value=b'') as capture:
                    self.assertEqual(bootstrap.repository_target(info), (branch, personal))
                capture.assert_called_once_with(['git', 'check-ref-format', f'refs/heads/{branch}'])

    def test_branch_override(self):
        info = {'owner': {'type': 'Organization'}, 'default_branch': 'master'}
        with patch.object(bootstrap, 'capture', return_value=b'') as capture:
            self.assertEqual(bootstrap.repository_target(info, 'staging/home'), ('staging/home', False))
        capture.assert_called_once_with(['git', 'check-ref-format', 'refs/heads/staging/home'])

    def test_missing_or_unsafe_branch(self):
        for branch in (None, '', 123, '--all', 'HEAD'):
            with self.subTest(branch=branch):
                info = {'owner': {'type': 'Organization'}, 'default_branch': branch}
                with patch.object(bootstrap, 'capture') as capture:
                    with self.assertRaises(ValueError):
                        bootstrap.repository_target(info)
                    capture.assert_not_called()

    def test_invalid_git_ref_stops_bootstrap(self):
        info = {'owner': {'type': 'Organization'}, 'default_branch': 'bad..branch'}
        with patch.object(bootstrap, 'capture', side_effect=ValueError('git failed')):
            with self.assertRaises(ValueError):
                bootstrap.repository_target(info)

    def test_unknown_owner_type(self):
        for owner_type in (None, 'Bot'):
            with self.subTest(owner_type=owner_type):
                info = {'owner': {'type': owner_type}, 'default_branch': 'master'}
                with patch.object(bootstrap, 'capture', return_value=b''):
                    with self.assertRaisesRegex(ValueError, 'owner type'):
                        bootstrap.repository_target(info)


class CheckoutTests(unittest.TestCase):
    @staticmethod
    def outputs(branch='master'):
        return {
            ('git', 'branch', '--show-current'): (branch + '\n').encode(),
            ('git', 'status', '--porcelain'): b'',
            ('git', 'remote', 'get-url', 'origin'): b'https://github.com/LionZXYOrg/infra.git\n',
            ('git', 'fetch', 'origin', f'refs/heads/{branch}'): b'',
            ('git', 'rev-parse', 'HEAD'): b'same-commit\n',
            ('git', 'rev-parse', 'FETCH_HEAD'): b'same-commit\n',
        }

    def test_fetch_uses_selected_branch(self):
        for branch in ('master', 'main', 'release/home'):
            with self.subTest(branch=branch):
                outputs = self.outputs(branch)
                with patch.object(bootstrap, 'capture', side_effect=lambda args: outputs[tuple(args)]) as capture:
                    bootstrap.check_checkout('LionZXYOrg', 'infra', branch)
                capture.assert_any_call(['git', 'fetch', 'origin', f'refs/heads/{branch}'])
                capture.assert_any_call(['git', 'rev-parse', 'FETCH_HEAD'])

    def test_wrong_branch_or_detached_head(self):
        for current in (b'main\n', b''):
            with self.subTest(current=current):
                outputs = self.outputs()
                outputs[('git', 'branch', '--show-current')] = current
                with patch.object(bootstrap, 'capture', side_effect=lambda args: outputs[tuple(args)]) as capture:
                    with self.assertRaisesRegex(ValueError, 'Check out master'):
                        bootstrap.check_checkout('LionZXYOrg', 'infra', 'master')
                    self.assertEqual(capture.call_count, 1)

    def test_dirty_checkout(self):
        outputs = self.outputs()
        outputs[('git', 'status', '--porcelain')] = b' M README.md\n'
        with patch.object(bootstrap, 'capture', side_effect=lambda args: outputs[tuple(args)]) as capture:
            with self.assertRaisesRegex(ValueError, 'Commit and push'):
                bootstrap.check_checkout('LionZXYOrg', 'infra', 'master')
            self.assertEqual(capture.call_count, 2)

    def test_wrong_origin(self):
        outputs = self.outputs()
        outputs[('git', 'remote', 'get-url', 'origin')] = b'https://github.com/other/infra.git\n'
        with patch.object(bootstrap, 'capture', side_effect=lambda args: outputs[tuple(args)]) as capture:
            with self.assertRaisesRegex(ValueError, 'origin does not match'):
                bootstrap.check_checkout('LionZXYOrg', 'infra', 'master')
            self.assertEqual(capture.call_count, 3)

    def test_local_commit_differs_from_remote(self):
        outputs = self.outputs()
        outputs[('git', 'rev-parse', 'HEAD')] = b'local-only-commit\n'
        with patch.object(bootstrap, 'capture', side_effect=lambda args: outputs[tuple(args)]):
            with self.assertRaisesRegex(ValueError, 'Local master differs'):
                bootstrap.check_checkout('LionZXYOrg', 'infra', 'master')


class FluxArgumentsTests(unittest.TestCase):
    def test_mcp_configuration_rejects_addresses_outside_private_lan(self):
        for address in ('178.62.76.162', '127.0.0.1', '0.0.0.0', 'not-an-ip'):
            with self.subTest(address=address):
                with self.assertRaises(ValueError):
                    bootstrap.cluster_settings(address)
        self.assertEqual(bootstrap.cluster_settings('192.168.1.150')['data']['SERVER_IP'], '192.168.1.150')

    def test_branch_and_owner_reach_flux_and_git(self):
        for branch, personal in [('master', False), ('main', True), ('release/home', False)]:
            with self.subTest(branch=branch, personal=personal):
                def capture_output(args, **kwargs):
                    if args[:3] == ['gh', 'auth', 'token']:
                        return b'unit-test-token'
                    return b''

                with patch.object(bootstrap, 'capture', side_effect=capture_output) as capture, \
                        patch.object(bootstrap, 'visible') as visible, \
                        patch.object(bootstrap, 'ensure_secrets_access', create=True):
                    bootstrap.bootstrap_flux(Path('/tmp/test-kubeconfig'), b'test-identity',
                                             'example-owner', 'infra', 'v2.9.6',
                                             branch=branch, personal=personal, private=False,
                                             secrets_owner='example-owner', secrets_repo='infra-secrets',
                                             server_ip='192.168.1.150')
                calls = visible.call_args_list
                flux_call = next(call for call in calls if call.args[0][:3] == ['flux', 'bootstrap', 'github'])
                self.assertIn(f'--branch={branch}', flux_call.args[0])
                self.assertIn(f'--personal={str(personal).lower()}', flux_call.args[0])
                self.assertIn('--read-write-key=false', flux_call.args[0])
                self.assertIn('--private=false', flux_call.args[0])
                self.assertEqual(flux_call.kwargs['env']['GITHUB_TOKEN'], 'unit-test-token')
                git_call = next(call for call in calls if call.args[0][:2] == ['git', 'pull'])
                self.assertEqual(git_call.args[0], ['git', 'pull', '--ff-only', 'origin', f'refs/heads/{branch}'])
                self.assertNotIn('GITHUB_TOKEN', git_call.kwargs['env'])
                applied = [json.loads(call.kwargs['data']) for call in capture.call_args_list
                           if call.args[0][0] == 'kubectl' and 'apply' in call.args[0]]
                settings = next(obj for obj in applied if obj['kind'] == 'ConfigMap')
                self.assertEqual(settings['metadata'], {'name': 'cluster-settings', 'namespace': 'flux-system'})
                self.assertEqual(settings['data'], {'SERVER_IP': '192.168.1.150'})


class PrivateSourceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ssh-keygen'), 'OpenSSH tools required')
    def test_real_ed25519_key_is_reused(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / 'key'
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(path)], check=True)
            public = path.with_suffix('.pub').read_text().strip()
            secret = {'data': {k: base64.b64encode(v).decode() for k, v in {
                'identity': path.read_bytes(), 'identity.pub': public.encode(),
                'known_hosts': b'github.com fixture'}.items()}}
            real_capture = bootstrap.capture
            def fake_capture(args, **kwargs):
                if args[:4] == ['flux', 'create', 'secret', 'git']:
                    filename = next(x.split('=', 1)[1] for x in args if x.startswith('--private-key-file='))
                    self.assertEqual(Path(filename).stat().st_mode & 0o777, 0o600)
                    derived = real_capture(['ssh-keygen', '-y', '-f', filename])
                    return json.dumps({'stringData': {'identity.pub': derived.decode()}}).encode()
                if args[:2] == ['gh', 'api']:
                    if args[-1].endswith('/keys'):
                        return json.dumps([[{'key': public, 'read_only': True}]]).encode()
                    return b'{"private":true,"permissions":{"admin":true}}'
                if 'get' in args:
                    return json.dumps(secret).encode()
                self.fail('An existing valid credential must not be replaced')
            with patch.object(bootstrap, 'capture', side_effect=fake_capture):
                bootstrap.ensure_secrets_access(['kubectl'], {}, 'example-owner', 'infra-secrets')

    def test_public_secrets_repository_is_rejected(self):
        with patch.object(bootstrap, 'capture', return_value=b'{"private":false,"permissions":{"admin":true}}'):
            with self.assertRaisesRegex(ValueError, 'private'):
                bootstrap.private_repository('example-owner', 'infra-secrets')

    def test_existing_key_is_reused_and_read_only_is_required(self):
        public = 'ssh-ed25519 dGVzdC1wdWJsaWMta2V5 fixture'
        credentials = {'identity': 'fixture-private-key', 'identity.pub': public,
                       'known_hosts': 'github.com ssh-ed25519 fixture-host-key'}
        secret = {'data': {k: base64.b64encode(v.encode()).decode() for k, v in credentials.items()}}
        for read_only in (True, False):
            with self.subTest(read_only=read_only):
                writes = []
                def fake_capture(args, **kwargs):
                    if args[:4] == ['flux', 'create', 'secret', 'git']:
                        return json.dumps({'stringData': {'identity.pub': public}}).encode()
                    if args[:2] == ['gh', 'api']:
                        if args[-1].endswith('/keys'):
                            return json.dumps([[{'key': public, 'read_only': read_only}]]).encode()
                        return b'{"private":true,"permissions":{"admin":true}}'
                    if 'get' in args:
                        return json.dumps(secret).encode()
                    writes.append(args)
                    return b''
                with patch.object(bootstrap, 'capture', side_effect=fake_capture):
                    if read_only:
                        bootstrap.ensure_secrets_access(['kubectl'], {}, 'example-owner', 'infra-secrets')
                    else:
                        with self.assertRaisesRegex(ValueError, 'read.only'):
                            bootstrap.ensure_secrets_access(['kubectl'], {}, 'example-owner', 'infra-secrets')
                self.assertEqual(writes, [])

    def test_mismatched_private_and_public_credentials_are_rejected(self):
        secret = {'data': {k: base64.b64encode(v.encode()).decode() for k, v in {
            'identity': 'private-key', 'identity.pub': 'ssh-ed25519 registered-key',
            'known_hosts': 'github.com fixture'}.items()}}
        def fake_capture(args, **kwargs):
            if args[:4] == ['flux', 'create', 'secret', 'git']:
                return b'{"stringData":{"identity.pub":"ssh-ed25519 different-key"}}'
            if args[:2] == ['gh', 'api']:
                if args[-1].endswith('/keys'):
                    return b'[[{"key":"ssh-ed25519 registered-key","read_only":true}]]'
                return b'{"private":true,"permissions":{"admin":true}}'
            return json.dumps(secret).encode()
        with patch.object(bootstrap, 'capture', side_effect=fake_capture):
            with self.assertRaisesRegex(ValueError, 'key pair'):
                bootstrap.ensure_secrets_access(['kubectl'], {}, 'example-owner', 'infra-secrets')

    def test_new_key_is_registered_read_only_and_applied_without_argv_secrets(self):
        writes = []
        def fake_capture(args, **kwargs):
            if args[:2] == ['gh', 'api']:
                if '--method' in args:
                    body = json.loads(kwargs['data'])
                    self.assertTrue(body['read_only'])
                    self.assertEqual(body['key'], 'ssh-ed25519 fixture-public-key')
                    return b'{}'
                return b'{"private":true,"permissions":{"admin":true}}'
            if args[:4] == ['flux', 'create', 'secret', 'git']:
                return (b'apiVersion: v1\nkind: Secret\nmetadata:\n  name: infra-secrets\n'
                        b'  namespace: flux-system\nstringData:\n  identity: fixture-private-key\n'
                        b'  identity.pub: ssh-ed25519 fixture-public-key\n  known_hosts: github.com fixture\n')
            if 'get' in args:
                return b''
            writes.append((args, kwargs.get('data')))
            return b''
        with patch.object(bootstrap, 'capture', side_effect=fake_capture):
            bootstrap.ensure_secrets_access(['kubectl'], {}, 'example-owner', 'infra-secrets')
        self.assertEqual(len(writes), 1)
        self.assertNotIn('fixture-private-key', ' '.join(writes[0][0]))
        self.assertIn(b'fixture-private-key', writes[0][1])


if __name__ == '__main__':
    unittest.main()
