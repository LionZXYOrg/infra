#!/usr/bin/env python3
"""Publish a PR status from private main history without running PR code."""
import argparse
import base64
import binascii
import configparser
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile

PUBLIC_REPOSITORY = 'LionZXYOrg/infra'
PRIVATE_REPOSITORY = 'git@github.com:LionZXYOrg/infra-secrets.git'
CONTEXT = 'Secrets main'
SHA = re.compile(r'[0-9a-f]{40}')
MODULE_URLS = {'https://github.com/LionZXYOrg/infra-secrets.git',
               'https://github.com/LionZXYOrg/infra-secrets',
               'git@github.com:LionZXYOrg/infra-secrets.git',
               'ssh://git@github.com/LionZXYOrg/infra-secrets.git'}


class AuditError(RuntimeError):
    """Only fixed, credential-free messages leave this script."""


class InvalidPointer(AuditError):
    pass


def is_sha(value):
    return isinstance(value, str) and bool(SHA.fullmatch(value))


def command(args, *, data=None, env=None, timeout=60):
    try:
        result = subprocess.run(args, input=data, capture_output=True, env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise AuditError('The audit command failed. Retry the audit.') from None
    if result.returncode:
        raise AuditError('The audit command failed. Retry the audit.')
    return result.stdout


def api(path, *, body=None):
    args = ['gh', 'api', '--hostname', 'github.com', path]
    data = None
    if body is not None:
        args += ['--method', 'POST', '--input', '-']
        data = json.dumps(body).encode()
    try:
        return json.loads(command(args, data=data))
    except (ValueError, UnicodeError):
        raise AuditError('The GitHub response is invalid. Retry the audit.') from None


def pull_requests(request=api):
    records = []
    for page in range(1, 101):
        batch = request(f'repos/{PUBLIC_REPOSITORY}/pulls?state=open&base=master&per_page=100&page={page}')
        if not isinstance(batch, list):
            raise AuditError('The PR list is invalid. Retry the audit.')
        for pr in batch:
            if (not isinstance(pr, dict) or type(pr.get('number')) is not int or pr['number'] <= 0
                    or not isinstance(pr.get('head'), dict) or not is_sha(pr['head'].get('sha'))
                    or not isinstance(pr.get('base'), dict)
                    or pr.get('base', {}).get('ref') != 'master'):
                raise AuditError('The PR metadata is invalid. Retry the audit.')
            records.append({'number': pr['number'], 'head': pr['head']['sha']})
        if len(batch) < 100:
            return records
    raise AuditError('The PR list is too large. Retry the audit.')


def module_is_main(raw):
    if len(raw) > 8192 or b'\0' in raw:
        return False
    parser = configparser.RawConfigParser(strict=True, empty_lines_in_values=False)
    parser.optionxform = str
    try:
        parser.read_file(io.StringIO(raw.decode('utf-8')))
        if parser.defaults() or parser.sections() != ['submodule "secrets"']:
            return False
        values = dict(parser['submodule "secrets"'])
    except (UnicodeError, configparser.Error):
        return False
    return (set(values) == {'path', 'url', 'branch'} and values['path'] == 'secrets'
            and values['branch'] == 'main' and values['url'] in MODULE_URLS)


def pointer(head, request=api):
    if not is_sha(head):
        raise InvalidPointer('The public commit is invalid.')
    tree = request(f'repos/{PUBLIC_REPOSITORY}/git/trees/{head}')
    if (not isinstance(tree, dict) or tree.get('truncated') is not False
            or not isinstance(tree.get('tree'), list)
            or not all(isinstance(entry, dict) for entry in tree['tree'])):
        raise AuditError('The public tree is incomplete. Retry the audit.')
    entries = {entry.get('path'): entry for entry in tree['tree']}
    module, configuration = entries.get('secrets', {}), entries.get('.gitmodules', {})
    if (module.get('mode') != '160000' or module.get('type') != 'commit'
            or not is_sha(module.get('sha'))
            or configuration.get('mode') != '100644' or configuration.get('type') != 'blob'
            or not is_sha(configuration.get('sha'))):
        raise InvalidPointer('Keep secrets as the expected Git submodule.')
    blob = request(f'repos/{PUBLIC_REPOSITORY}/git/blobs/{configuration["sha"]}')
    try:
        if (not isinstance(blob, dict) or blob.get('encoding') != 'base64'
                or not isinstance(blob.get('size'), int) or not 0 < blob['size'] <= 8192):
            raise InvalidPointer('Use the expected secrets repository and branch main.')
        raw = base64.b64decode(''.join(blob['content'].split()), validate=True)
    except (KeyError, ValueError, TypeError, binascii.Error):
        raise InvalidPointer('Use the expected secrets repository and branch main.') from None
    if not module_is_main(raw):
        raise InvalidPointer('Use the expected secrets repository and branch main.')
    return module['sha']


def fetch_main(directory, environment, repository=PRIVATE_REPOSITORY):
    git = ['git', '-c', 'core.hooksPath=/dev/null', '--git-dir', str(directory)]
    command([*git, 'init', '--bare'], env=environment)
    # The literal branch ref rejects a tag called main. The filter excludes trees/blobs.
    command([*git, 'fetch', '--filter=tree:0', '--no-tags', repository,
             '+refs/heads/main:refs/heads/main'], env=environment, timeout=180)
    commits = command([*git, 'rev-list', 'refs/heads/main'], env=environment).decode().splitlines()
    if not commits or not all(is_sha(commit) for commit in commits):
        raise AuditError('Private main history is invalid. Retry the audit.')
    return set(commits)


def private_main(key, request=api):
    if not key:
        raise AuditError('The private read-only deploy key is missing.')
    keys = request('meta').get('ssh_keys', [])
    host_keys = [value for value in keys if isinstance(value, str)
                 and re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+', value)]
    if not host_keys:
        raise AuditError('The GitHub SSH host key is unavailable.')
    with tempfile.TemporaryDirectory(prefix='secrets-main-') as directory:
        root = Path(directory)
        identity, known_hosts = root / 'identity', root / 'known_hosts'
        for path, value in ((identity, key.rstrip() + '\n'),
                            (known_hosts, ''.join(f'github.com {value}\n' for value in host_keys))):
            with open(path, 'x', opener=lambda name, flags: os.open(name, flags, 0o600)) as target:
                target.write(value)
        ssh = ['ssh', '-F', '/dev/null', '-i', str(identity), '-o', 'BatchMode=yes',
               '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'HostKeyAlgorithms=ssh-ed25519', '-o', f'UserKnownHostsFile={known_hosts}']
        environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LC_ALL': 'C',
                       'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                       'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': 'ssh',
                       'GIT_SSH_COMMAND': shlex.join(ssh)}
        return fetch_main(root / 'private.git', environment)


def status(head, state, description, request=api):
    body = {'state': state, 'context': CONTEXT, 'description': description}
    run_id = os.environ.get('GITHUB_RUN_ID', '')
    if run_id.isdigit():
        body['target_url'] = f'https://github.com/{PUBLIC_REPOSITORY}/actions/runs/{run_id}'
    request(f'repos/{PUBLIC_REPOSITORY}/statuses/{head}', body=body)


def audit(prs, read_main, *, publish=False, request=api):
    def report(head, state, description):
        if publish:
            status(head, state, description, request)

    for pr in prs:
        report(pr['head'], 'pending', 'Compare the secrets commit with private main.')
    if not prs:
        return {'success': 0, 'failure': 0, 'error': 0}
    try:
        commits = read_main()
    except AuditError:
        for pr in prs:
            report(pr['head'], 'error', 'Private main could not be read. Retry this audit.')
        raise
    counts = {'success': 0, 'failure': 0, 'error': 0}
    for pr in prs:
        try:
            pinned = pointer(pr['head'], request)
            if pinned in commits:
                state, message = 'success', 'The secrets commit belongs to private main.'
            else:
                state, message = 'failure', 'Merge the private secrets PR into main, then update this pointer.'
        except InvalidPointer as error:
            state, message = 'failure', str(error)
        except AuditError:
            state, message = 'error', 'The public metadata could not be read. Retry this audit.'
        report(pr['head'], state, message)
        counts[state] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true', help='Post the required status to each open PR head.')
    args = parser.parse_args()
    # Do not pass the private key to gh or any other child process through its environment.
    key = os.environ.pop('SECRETS_MAIN_SSH_KEY', '')
    try:
        counts = audit(pull_requests(), lambda: private_main(key), publish=args.publish)
        print(f'Secrets main: {counts["success"]} passed, {counts["failure"]} blocked, {counts["error"]} errors.')
        # Expected policy failures live on the PR statuses, not on the scheduled audit job.
        return int(bool(counts['error']))
    except AuditError:
        print('Secrets main audit failed. Details and credentials are suppressed.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
