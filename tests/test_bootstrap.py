"""Bootstrap unit tests. No GitHub, SSH, Kubernetes, or secret values are needed.

Run: python3 -m unittest discover -s tests -v
"""
from pathlib import Path
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
    def test_branch_and_owner_reach_flux_and_git(self):
        for branch, personal in [('master', False), ('main', True), ('release/home', False)]:
            with self.subTest(branch=branch, personal=personal):
                def capture_output(args, **kwargs):
                    if args[:3] == ['gh', 'auth', 'token']:
                        return b'unit-test-token'
                    return b''

                with patch.object(bootstrap, 'capture', side_effect=capture_output), \
                        patch.object(bootstrap, 'visible') as visible:
                    bootstrap.bootstrap_flux(Path('/tmp/test-kubeconfig'), b'test-identity',
                                             'example-owner', 'infra', 'v2.9.6',
                                             branch=branch, personal=personal)
                calls = visible.call_args_list
                flux_call = next(call for call in calls if call.args[0][:3] == ['flux', 'bootstrap', 'github'])
                self.assertIn(f'--branch={branch}', flux_call.args[0])
                self.assertIn(f'--personal={str(personal).lower()}', flux_call.args[0])
                self.assertIn('--read-write-key=false', flux_call.args[0])
                self.assertEqual(flux_call.kwargs['env']['GITHUB_TOKEN'], 'unit-test-token')
                git_call = next(call for call in calls if call.args[0][:2] == ['git', 'pull'])
                self.assertEqual(git_call.args[0], ['git', 'pull', '--ff-only', 'origin', f'refs/heads/{branch}'])
                self.assertNotIn('GITHUB_TOKEN', git_call.kwargs['env'])


if __name__ == '__main__':
    unittest.main()
