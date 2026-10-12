#!/usr/bin/env python3
"""Provision Linux locally or over SSH, then bootstrap Flux from this workstation.
Only the public provision script is copied over SSH. No GitHub token is copied.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import resource
import shlex
import subprocess
import sys
import tempfile

import yaml
import secretctl

ROOT = Path(__file__).resolve().parent.parent


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def capture(args: list[str], *, data: bytes | None = None, env: dict | None = None) -> bytes:
    # Never surface stdout/stderr from commands which could contain credentials.
    p = subprocess.run(args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    require(p.returncode == 0, f'{args[0]} failed; output suppressed. Check access and configuration.')
    return p.stdout


def visible(args: list[str], *, env: dict | None = None) -> None:
    # Use only for commands whose ordinary output contains no secret values.
    p = subprocess.run(args, env=env)
    require(p.returncode == 0, f'{args[0]} failed; see the output above.')


def decrypt_recovery() -> bytes:
    secretctl.plaintext_guard()
    # age reads the passphrase from the terminal; plaintext goes only to this process.
    p = subprocess.run(['age', '--decrypt', str(ROOT / 'bootstrap/sops-age.age')],
                       stdout=subprocess.PIPE)
    require(p.returncode == 0, 'Could not unlock the recovery identity.')
    identity = p.stdout
    recipient = capture(['age-keygen', '-y'], data=identity).decode().strip()
    rules = yaml.safe_load((ROOT / '.sops.yaml').read_text())['creation_rules']
    require(any(recipient in [x.strip() for x in str(rule.get('age', '')).split(',')] for rule in rules),
            'Recovery key does not match .sops.yaml.')
    env = secretctl.key_env(identity)
    for path in secretctl.check_indexes():
        obj = secretctl.load_yaml(capture(['sops', '--decrypt', str(path)], env=env))
        secretctl.validate(obj, path, encrypted=False)
    return identity


def normalize_kubeconfig(raw: bytes, ip: str) -> bytes:
    # Reconstruct only the expected embedded certificate credentials. Never import exec plugins.
    try:
        d = yaml.safe_load(raw)
        require(len(d['clusters']) == 1 and len(d['users']) == 1, 'Unexpected kubeconfig shape.')
        cluster, user = d['clusters'][0]['cluster'], d['users'][0]['user']
        ca = cluster['certificate-authority-data']
        cert, key = user['client-certificate-data'], user['client-key-data']
        require(all(isinstance(x, str) and x for x in (ca, cert, key)), 'Missing embedded credentials.')
        require('exec' not in user and 'auth-provider' not in user, 'Dynamic credentials are not allowed.')
    except (KeyError, TypeError, yaml.YAMLError):
        raise ValueError('Invalid K3s kubeconfig; contents suppressed.') from None
    out = {'apiVersion': 'v1', 'kind': 'Config',
           'clusters': [{'name': 'home-test', 'cluster': {
               'server': f'https://{ip}:6443', 'certificate-authority-data': ca}}],
           'users': [{'name': 'home-test-admin', 'user': {
               'client-certificate-data': cert, 'client-key-data': key}}],
           'contexts': [{'name': 'home-test', 'context': {
               'cluster': 'home-test', 'user': 'home-test-admin'}}],
           'current-context': 'home-test'}
    return yaml.safe_dump(out, sort_keys=False).encode()


def obtain_kubeconfig(ssh: str | None, ip: str, node: str, version: str,
                      *, fetch_only: bool) -> Path:
    kube_dir = Path.home() / '.kube'
    require(not kube_dir.is_symlink(), '~/.kube must not be a symlink for this helper.')
    kube_dir.mkdir(mode=0o700, exist_ok=True)
    kube_dir.chmod(0o700)
    dest = kube_dir / 'home-k3s.yaml'
    require(not dest.is_symlink(), 'Refusing a symlink at the target kubeconfig path.')
    # This intentionally replaces only this guide's dedicated kubeconfig, never ~/.kube/config.
    with tempfile.TemporaryDirectory(prefix='.home-gitops-', dir=kube_dir) as work:
        raw_file = Path(work) / 'received.yaml'
        if ssh:
            remote = capture(['ssh', ssh, 'umask 077; mktemp -d /tmp/home-gitops.XXXXXXXX']).decode().strip()
            require(re.fullmatch(r'/tmp/home-gitops\.[A-Za-z0-9]+', remote) is not None,
                    'Unexpected SSH output while creating the temporary directory.')
            try:
                remote_file = remote + '/kubeconfig'
                if fetch_only:
                    copy_cmd = ('install -m 0600 -o "$(id -u)" -g "$(id -g)" '
                                '/etc/rancher/k3s/k3s.yaml ' + shlex.quote(remote_file))
                    command = ('umask 077; if [ "$(id -u)" -eq 0 ]; then '
                               + copy_cmd + '; else sudo -v && sudo ' + copy_cmd + '; fi')
                else:
                    visible(['scp', str(ROOT / 'scripts/provision-server.sh'),
                             f'{ssh}:{remote}/provision-server.sh'])
                    command = shlex.join(['bash', remote + '/provision-server.sh',
                                          ip, node, version, remote_file])
                # A TTY lets sudo prompt without passing passwords through arguments/pipes.
                visible(['ssh', '-t', ssh, command])
                visible(['scp', f'{ssh}:{remote_file}', str(raw_file)])
            finally:
                p = subprocess.run(['ssh', '-o', 'BatchMode=yes', ssh,
                                    shlex.join(['rm', '-rf', '--', remote])],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                if p.returncode:
                    print(f'WARNING: remove the private temporary directory on the server: {remote}',
                          file=sys.stderr)
        else:
            require(sys.platform == 'linux', 'On macOS pass --ssh USER@SERVER. K3s cannot be installed here.')
            if fetch_only:
                privilege = [] if os.geteuid() == 0 else ['sudo']
                visible(privilege + ['install', '-m', '0600', '-o', str(os.getuid()),
                                     '-g', str(os.getgid()),
                                     '/etc/rancher/k3s/k3s.yaml', str(raw_file)])
            else:
                visible(['bash', str(ROOT / 'scripts/provision-server.sh'), ip, node, version, str(raw_file)])
        encoded = normalize_kubeconfig(raw_file.read_bytes(), ip)
        tmp = Path(work) / 'normalized.yaml'
        tmp.write_bytes(encoded)
        tmp.chmod(0o600)
        os.replace(tmp, dest)
    return dest


def bootstrap_flux(kubeconfig: Path, identity: bytes, owner: str, repo: str, version: str) -> None:
    env = os.environ.copy()
    env['KUBECONFIG'] = str(kubeconfig)
    # No secret env is passed to arbitrary git/ssh processes.
    env.pop('SOPS_AGE_KEY', None)
    env.pop('GITHUB_TOKEN', None)
    kube = ['kubectl', '--kubeconfig', str(kubeconfig), '--context', 'home-test']
    visible(['flux', 'check', '--pre'], env=env)
    namespace = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'flux-system'}}
    apply = kube + ['apply', '--server-side', '--field-manager=home-bootstrap', '-f', '-']
    capture(apply, data=json.dumps(namespace).encode(), env=env)
    existing = capture(kube + ['-n', 'flux-system', 'get', 'secret', 'sops-age',
                               '--ignore-not-found', '-o', 'json'], env=env)
    if existing.strip():
        old = json.loads(existing).get('data', {}).get('identity.agekey', '')
        require(base64.b64decode(old, validate=True) == identity,
                'A different SOPS identity already exists in this cluster; inspect it first.')
    secret = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
              'metadata': {'name': 'sops-age', 'namespace': 'flux-system'},
              'data': {'identity.agekey': base64.b64encode(identity).decode()}}
    # No --from-literal secret on argv, no plaintext temporary manifest.
    capture(apply, data=json.dumps(secret).encode(), env=env)
    token = capture(['gh', 'auth', 'token', '--hostname', 'github.com']).decode().strip()
    flux_env = {**env, 'GITHUB_TOKEN': token}
    visible(['flux', 'bootstrap', 'github', f'--owner={owner}', f'--repository={repo}',
             '--personal=true', '--private=true', '--branch=main', '--path=clusters/home',
             f'--version={version}', '--interval=1m', '--token-auth=false',
             '--read-write-key=false', '--ssh-key-algorithm=ed25519'], env=flux_env)
    del flux_env, token
    visible(['git', 'pull', '--ff-only', 'origin', 'main'], env=env)
    visible(['flux', 'reconcile', 'kustomization', 'flux-system', '--with-source', '--timeout=20m'], env=env)
    visible(kube + ['-n', 'flux-system', 'wait', 'kustomizations.kustomize.toolkit.fluxcd.io',
                    '--all', '--for=condition=Ready', '--timeout=30m'], env=env)
    visible(['flux', 'get', 'kustomizations', '-A'], env=env)


def main() -> None:
    os.chdir(ROOT)
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh', metavar='USER@SERVER', help='Linux host, or a trusted SSH config alias.')
    parser.add_argument('--fetch-kubeconfig', action='store_true',
                        help='Refresh only the dedicated admin kubeconfig; no K3s/Flux/secret changes.')
    args = parser.parse_args()
    require(sys.platform in {'linux', 'darwin'}, 'Supported workstations: Linux and macOS.')
    require(not (sys.platform == 'darwin' and os.getuid() == 0),
            'On macOS run as your normal user; root is supported on Linux.')
    require(args.ssh is not None or sys.platform == 'linux', 'On macOS supply --ssh USER@SERVER.')
    if args.ssh:
        require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]*', args.ssh) is not None,
                'Invalid SSH target. Use USER@IPv4 or an SSH config alias, not shell options.')
    ip = os.environ['SERVER_IP']
    ipaddress.IPv4Address(ip)
    node, version = os.environ['NODE_NAME'], os.environ['K3S_VERSION']
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}', node) is not None, 'Invalid node name.')
    require(re.fullmatch(r'v\d+\.\d+\.\d+\+k3s\d+', version) is not None, 'Invalid K3s version.')
    identity = None
    if not args.fetch_kubeconfig:
        owner, repo = os.environ['GITHUB_OWNER'], os.environ['GITHUB_REPOSITORY']
        require(owner != 'YOUR_GITHUB_LOGIN' and re.fullmatch(r'[A-Za-z0-9-]+', owner) is not None,
                'Set GITHUB_OWNER in bootstrap/settings.env.')
        require(re.fullmatch(r'[A-Za-z0-9._-]+', repo) is not None, 'Invalid repository name.')
        require(capture(['git', 'branch', '--show-current']).strip() == b'main', 'Check out main.')
        require(not capture(['git', 'status', '--porcelain']).strip(), 'Commit and push all changes first.')
        info = json.loads(capture(['gh', 'api', f'repos/{owner}/{repo}']))
        require(info.get('permissions', {}).get('admin') is True, 'GitHub repository admin access is required.')
        require(info.get('private') is True, 'This guide expects a private GitHub repository.')
        origin = capture(['git', 'remote', 'get-url', 'origin']).decode().strip()
        require(origin in {f'https://github.com/{owner}/{repo}', f'https://github.com/{owner}/{repo}.git',
                           f'git@github.com:{owner}/{repo}.git', f'ssh://git@github.com/{owner}/{repo}.git'},
                'origin does not match bootstrap/settings.env.')
        capture(['git', 'fetch', 'origin', 'main'])
        require(capture(['git', 'rev-parse', 'HEAD']) == capture(['git', 'rev-parse', 'origin/main']),
                'Local main differs from origin/main.')
        visible(['bash', 'scripts/check.sh'])
        # Unlock and validate before making changes to the target host.
        identity = decrypt_recovery()
    kubeconfig = obtain_kubeconfig(args.ssh, ip, node, version, fetch_only=args.fetch_kubeconfig)
    kube = ['kubectl', '--kubeconfig', str(kubeconfig), '--context', 'home-test']
    obj = json.loads(capture(kube + ['get', 'node', node, '-o', 'json']))
    require(any(a.get('address') == ip and a.get('type') == 'InternalIP'
                for a in obj.get('status', {}).get('addresses', [])), 'Node InternalIP does not match SERVER_IP.')
    if identity is not None:
        bootstrap_flux(kubeconfig, identity, owner, repo, os.environ['FLUX_VERSION'])
    print('\nReady. In your current terminal run:')
    print('export KUBECONFIG="$HOME/.kube/home-k3s.yaml"')
    print('kubectl config current-context  # expected: home-test')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, yaml.YAMLError) as exc:
        # KeyError identifies missing settings; parser errors are deliberately not printed.
        message = str(exc) if isinstance(exc, (ValueError, OSError, KeyError)) else 'Invalid YAML.'
        print(f'ERROR: {message}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('Cancelled.', file=sys.stderr)
        sys.exit(130)
