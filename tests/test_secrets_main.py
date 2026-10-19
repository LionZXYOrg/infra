"""Exercise real commit ancestry, untrusted metadata, and the trusted workflow boundary."""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from test_ci_workflow import WorkflowLoader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import secrets_main as guard

HEAD = 'a' * 40
OTHER_HEAD = 'b' * 40
PIN = 'c' * 40
BLOB = 'd' * 40
MODULE = ('[submodule "secrets"]\n\tpath = secrets\n'
          '\turl = https://github.com/LionZXYOrg/infra-secrets.git\n\tbranch = main\n')


def metadata(raw=MODULE.encode(), pin=PIN):
    return {'truncated': False, 'tree': [
        {'path': 'secrets', 'mode': '160000', 'type': 'commit', 'sha': pin},
        {'path': '.gitmodules', 'mode': '100644', 'type': 'blob', 'sha': BLOB}]}, {
        'encoding': 'base64', 'size': len(raw), 'content': base64.b64encode(raw).decode()}


class MetadataTests(unittest.TestCase):
    def test_main_branch_and_known_repository_are_required(self):
        self.assertTrue(guard.module_is_main(MODULE.encode()))
        for old, new in [('branch = main', 'branch = feature'), ('branch = main', ''),
                         ('path = secrets', 'path = other'),
                         ('https://github.com/LionZXYOrg/infra-secrets.git', 'ssh://example.invalid/repo')]:
            with self.subTest(new=new):
                self.assertFalse(guard.module_is_main(MODULE.replace(old, new).encode()))

    def test_includes_commands_duplicate_and_extra_modules_are_rejected(self):
        for raw in (MODULE + '\tupdate = !echo sentinel\n', MODULE + '\tbranch = main\n',
                    MODULE + '[include]\npath = /tmp/sentinel\n',
                    MODULE + '[submodule "extra"]\npath = extra\n',
                    '[DEFAULT]\nbranch = main\n' + MODULE, MODULE + '\0', 'x' * 8193):
            with self.subTest(raw=raw[:30]):
                self.assertFalse(guard.module_is_main(raw.encode()))

    def test_only_the_requested_public_commit_and_gitmodules_blob_are_read(self):
        request = Mock(side_effect=metadata())
        self.assertEqual(guard.pointer(HEAD, request), PIN)
        self.assertEqual([call.args[0] for call in request.call_args_list], [
            f'repos/LionZXYOrg/infra/git/trees/{HEAD}', f'repos/LionZXYOrg/infra/git/blobs/{BLOB}'])

    def test_missing_module_symlink_and_incomplete_tree_fail(self):
        tree, blob = metadata()
        cases = [dict(tree, truncated=True), dict(tree, tree=[]), None]
        for position, field, value in ((0, 'mode', '040000'), (0, 'type', 'tree'),
                                       (1, 'mode', '120000'), (1, 'type', 'commit'),
                                       (0, 'sha', None)):
            changed = copy.deepcopy(tree)
            changed['tree'][position][field] = value
            cases.append(changed)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(guard.AuditError):
                guard.pointer(HEAD, Mock(side_effect=[case, blob]))

    def test_branch_override_and_invalid_blob_fail(self):
        tree, blob = metadata()
        bad_blob = dict(blob, content='not-base64!')
        for response in (bad_blob, dict(blob, encoding='raw'), dict(blob, size=9000), None):
            with self.subTest(response=response), self.assertRaises(guard.InvalidPointer):
                guard.pointer(HEAD, Mock(side_effect=[tree, response]))
        with self.assertRaises(guard.InvalidPointer):
            guard.pointer(HEAD, Mock(side_effect=metadata(MODULE.replace('main', 'feature').encode())))

    def test_prs_are_paginated_and_fork_heads_are_supported(self):
        record = {'number': 1, 'head': {'sha': HEAD, 'repo': {'full_name': 'example/fork'}},
                  'base': {'ref': 'master'}}
        request = Mock(side_effect=[[dict(record, number=n) for n in range(1, 101)], [dict(record, number=101)]])
        rows = guard.pull_requests(request)
        self.assertEqual(len(rows), 101)
        self.assertEqual(rows[-1], {'number': 101, 'head': HEAD})
        self.assertIn('page=2', request.call_args.args[0])
        for bad in ({}, dict(record, number=-1), dict(record, head={'sha': ';echo sentinel'}),
                    dict(record, base={'ref': 'other'})):
            with self.assertRaises(guard.AuditError):
                guard.pull_requests(Mock(return_value=[bad]))


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.remote = self.work / 'remote.git'
        self.source = self.work / 'source'
        self.environment = {'PATH': os.environ['PATH'], 'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
                            'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_ALLOW_PROTOCOL': 'file'}
        self.git('init', '--bare', str(self.remote))
        self.git('--git-dir', str(self.remote), 'config', 'uploadpack.allowFilter', 'true')
        self.git('init', '-b', 'main', str(self.source))
        self.git('-C', str(self.source), 'config', 'user.name', 'CI fixture')
        self.git('-C', str(self.source), 'config', 'user.email', 'fixture@example.invalid')
        self.old = self.commit('one')
        self.current = self.commit('two')
        self.git('-C', str(self.source), 'push', str(self.remote), 'main')
        self.git('-C', str(self.source), 'switch', '-c', 'unmerged')
        self.feature = self.commit('feature')
        self.git('-C', str(self.source), 'push', str(self.remote), 'unmerged')

    def git(self, *args):
        return subprocess.check_output(['git', *args], env=self.environment, stderr=subprocess.PIPE).decode().strip()

    def commit(self, value):
        (self.source / 'fixture.txt').write_text(value)
        self.git('-C', str(self.source), 'add', 'fixture.txt')
        self.git('-C', str(self.source), 'commit', '-m', 'Update disposable fixture')
        return self.git('-C', str(self.source), 'rev-parse', 'HEAD')

    def test_current_and_older_main_commits_pass_but_feature_commits_fail(self):
        directory = self.work / 'audit.git'
        commits = guard.fetch_main(directory, self.environment, self.remote.as_uri())
        self.assertEqual(commits, {self.old, self.current})
        self.assertNotIn(self.feature, commits)
        types = self.git('--git-dir', str(directory), 'cat-file', '--batch-all-objects',
                         '--batch-check=%(objecttype)').splitlines()
        self.assertEqual(set(types), {'commit'})
        self.assertFalse((directory / 'fixture.txt').exists())

    def test_tag_called_main_does_not_replace_the_branch(self):
        self.git('--git-dir', str(self.remote), 'update-ref', 'refs/tags/main', self.current)
        self.git('--git-dir', str(self.remote), 'update-ref', '-d', 'refs/heads/main')
        with self.assertRaises(guard.AuditError):
            guard.fetch_main(self.work / 'audit.git', self.environment, self.remote.as_uri())

    def test_fresh_history_rejects_a_commit_removed_from_main(self):
        self.git('--git-dir', str(self.remote), 'update-ref', 'refs/heads/main', self.old)
        commits = guard.fetch_main(self.work / 'audit.git', self.environment, self.remote.as_uri())
        self.assertEqual(commits, {self.old})
        self.assertNotIn(self.current, commits)


class StatusTests(unittest.TestCase):
    def test_policy_results_attach_to_each_exact_pr_head(self):
        calls = []
        def request(path, **kwargs):
            if kwargs:
                calls.append((path, kwargs['body']))
                return {}
            if '/trees/' in path:
                return metadata(pin=PIN if path.endswith(HEAD) else 'f' * 40)[0]
            return metadata()[1]
        rows = [{'head': HEAD}, {'head': OTHER_HEAD}]
        counts = guard.audit(rows, lambda: {PIN}, publish=True, request=request)
        self.assertEqual(counts, {'success': 1, 'failure': 1, 'error': 0})
        self.assertEqual([body['state'] for _, body in calls], ['pending', 'pending', 'success', 'failure'])
        self.assertTrue(calls[2][0].endswith(HEAD))
        self.assertTrue(calls[3][0].endswith(OTHER_HEAD))
        self.assertTrue(all(body['context'] == 'Secrets main' for _, body in calls))

    def test_private_access_failure_posts_errors_and_never_success(self):
        request = Mock(return_value={})
        with self.assertRaises(guard.AuditError):
            guard.audit([{'head': HEAD}], Mock(side_effect=guard.AuditError('fixed failure')),
                        publish=True, request=request)
        states = [call.kwargs['body']['state'] for call in request.call_args_list]
        self.assertEqual(states, ['pending', 'error'])

    def test_public_read_failure_posts_error(self):
        def request(path, **kwargs):
            if kwargs:
                return {}
            raise guard.AuditError('fixed failure')
        self.assertEqual(guard.audit([{'head': HEAD}], lambda: {PIN}, publish=True, request=request),
                         {'success': 0, 'failure': 0, 'error': 1})

    def test_dry_run_never_posts_and_empty_pr_list_never_reads_private_history(self):
        request = Mock(side_effect=metadata())
        self.assertEqual(guard.audit([{'head': HEAD}], lambda: {PIN}, request=request)['success'], 1)
        self.assertFalse(any(call.kwargs for call in request.call_args_list))
        read_main = Mock()
        self.assertEqual(guard.audit([], read_main), {'success': 0, 'failure': 0, 'error': 0})
        read_main.assert_not_called()

    def test_command_errors_suppress_underlying_output(self):
        result = subprocess.CompletedProcess([], 1, stdout=b'private-sentinel', stderr=b'credential-sentinel')
        with patch.object(guard.subprocess, 'run', return_value=result), self.assertRaises(guard.AuditError) as error:
            guard.command(['git', 'fetch'])
        self.assertNotIn('sentinel', str(error.exception))

    def test_key_permissions_host_pinning_environment_and_cleanup(self):
        seen = []
        def fetch(directory, environment):
            identity = directory.parent / 'identity'
            seen.append(identity)
            self.assertEqual(identity.stat().st_mode & 0o777, 0o600)
            self.assertEqual(identity.read_text(), 'disposable-key\n')
            self.assertIn('StrictHostKeyChecking=yes', environment['GIT_SSH_COMMAND'])
            self.assertIn('IdentitiesOnly=yes', environment['GIT_SSH_COMMAND'])
            self.assertEqual(environment['GIT_ALLOW_PROTOCOL'], 'ssh')
            self.assertNotIn('GH_TOKEN', environment)
            self.assertNotIn('SECRETS_MAIN_SSH_KEY', environment)
            return {PIN}
        request = Mock(return_value={'ssh_keys': ['ssh-ed25519 ZGlzcG9zYWJsZQ==']})
        with patch.object(guard, 'fetch_main', side_effect=fetch):
            self.assertEqual(guard.private_main('disposable-key', request), {PIN})
        self.assertFalse(seen[0].exists())
        with self.assertRaises(guard.AuditError):
            guard.private_main('', request)

    def test_key_leaves_the_environment_before_api_or_git_commands_run(self):
        def audit(prs, read_main, **kwargs):
            self.assertNotIn('SECRETS_MAIN_SSH_KEY', os.environ)
            self.assertEqual(read_main(), {PIN})
            return {'success': 1, 'failure': 0, 'error': 0}
        with patch.dict(os.environ, {'SECRETS_MAIN_SSH_KEY': 'disposable-key'}), \
                patch.object(sys, 'argv', ['secrets_main.py']), \
                patch.object(guard, 'pull_requests', return_value=[{'head': HEAD}]), \
                patch.object(guard, 'private_main', return_value={PIN}) as read_main, \
                patch.object(guard, 'audit', side_effect=audit), patch('builtins.print'):
            self.assertEqual(guard.main(), 0)
        read_main.assert_called_once_with('disposable-key')


class TrustedWorkflowTests(unittest.TestCase):
    def test_only_master_code_runs_with_the_protected_environment(self):
        workflow = yaml.load((ROOT / '.github/workflows/secrets-main.yaml').read_text(), Loader=WorkflowLoader)
        self.assertEqual(set(workflow['on']), {'pull_request_target', 'push', 'schedule', 'workflow_dispatch'})
        self.assertEqual(workflow['on']['pull_request_target']['branches'], ['master'])
        self.assertEqual(workflow['on']['push']['branches'], ['master'])
        self.assertEqual(workflow['on']['schedule'], [{'cron': '*/5 * * * *'}])
        self.assertEqual(workflow['permissions'], {'contents': 'read', 'pull-requests': 'read', 'statuses': 'write'})
        job = workflow['jobs']['audit']
        self.assertEqual(job['environment'], 'secrets-main')
        self.assertEqual(job['timeout-minutes'], 10)
        checkout, audit = job['steps']
        self.assertRegex(checkout['uses'], r'^actions/checkout@[0-9a-f]{40}$')
        self.assertEqual(checkout['with']['ref'], 'master')
        self.assertEqual(checkout['with']['repository'], 'LionZXYOrg/infra')
        self.assertIs(checkout['with']['persist-credentials'], False)
        self.assertIs(checkout['with']['submodules'], False)
        self.assertEqual(checkout['with']['sparse-checkout'], 'scripts/secrets_main.py')
        self.assertEqual(audit['run'], 'python3 scripts/secrets_main.py --publish')
        self.assertEqual(audit['env']['GH_TOKEN'], '${{ github.token }}')
        self.assertEqual(audit['env']['SECRETS_MAIN_SSH_KEY'], '${{ secrets.SECRETS_MAIN_SSH_KEY }}')


if __name__ == '__main__':
    unittest.main()
