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
    secretctl.require_private_module()
    secretctl.plaintext_guard()
    # An explicit/local identity avoids a recovery password prompt during restores.
    with secretctl.identity_env() as key_env:
        identity = (key_env['SOPS_AGE_KEY'].encode() if key_env.get('SOPS_AGE_KEY')
                    else Path(key_env['SOPS_AGE_KEY_FILE']).read_bytes())
    recipient = capture(['age-keygen', '-y'], data=identity).decode().strip()
    rules = secretctl.load_yaml(secretctl.SOPS_CONFIG.read_bytes())['creation_rules']
    require(any(recipient in [x.strip() for x in str(rule.get('age', '')).split(',')] for rule in rules),
            'Recovery key does not match secrets/.sops.yaml.')
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


def repository_target(info: dict, branch_override: str = '') -> tuple[str, bool]:
    """Use one branch and owner type for both Git checks and Flux bootstrap."""
    branch = branch_override or info.get('default_branch')
    require(isinstance(branch, str) and bool(branch)
            and not branch.startswith('-') and branch != 'HEAD',
            'Set GITHUB_BRANCH or provide a repository with a default branch.')
    # Validate the literal ref. Do not expand checkout shorthand or accept options.
    capture(['git', 'check-ref-format', f'refs/heads/{branch}'])
    owner_type = info.get('owner', {}).get('type')
    require(owner_type in {'User', 'Organization'}, 'Unsupported GitHub repository owner type.')
    return branch, owner_type == 'User'


def check_checkout(owner: str, repo: str, branch: str) -> None:
    """Reject the wrong checkout before decrypting secrets or changing the host."""
    current = capture(['git', 'branch', '--show-current']).decode().strip()
    require(current == branch, f'Check out {branch} before bootstrap.')
    require(not capture(['git', 'status', '--porcelain']).strip(), 'Commit and push all changes first.')
    origin = capture(['git', 'remote', 'get-url', 'origin']).decode().strip()
    require(origin in {f'https://github.com/{owner}/{repo}', f'https://github.com/{owner}/{repo}.git',
                       f'git@github.com:{owner}/{repo}.git', f'ssh://git@github.com/{owner}/{repo}.git'},
            'origin does not match bootstrap/settings.env.')
    # Fetch an explicit branch ref, not a tag or a stale remote-tracking ref.
    capture(['git', 'fetch', 'origin', f'refs/heads/{branch}'])
    require(capture(['git', 'rev-parse', 'HEAD']) == capture(['git', 'rev-parse', 'FETCH_HEAD']),
            f'Local {branch} differs from the fetched origin branch.')


def private_repository(owner: str, repo: str) -> dict:
    info = json.loads(capture(['gh', 'api', '--hostname', 'github.com', f'repos/{owner}/{repo}']))
    require(info.get('private') is True, 'The secrets repository must be private.')
    require(info.get('permissions', {}).get('admin') is True,
            'GitHub admin access to the private secrets repository is required for deploy keys.')
    return info


def check_private_checkout(owner: str, repo: str) -> None:
    secretctl.require_private_module()
    git = ['git', '-C', str(secretctl.SECRET_DIR)]
    origin = capture(git + ['remote', 'get-url', 'origin']).decode().strip()
    require(origin in {f'https://github.com/{owner}/{repo}', f'https://github.com/{owner}/{repo}.git',
                       f'git@github.com:{owner}/{repo}.git', f'ssh://git@github.com/{owner}/{repo}.git'},
            'Private module origin does not match the secrets repository settings.')
    require(not capture(git + ['status', '--porcelain']).strip(),
            'Commit and push private module changes before bootstrap.')
    capture(git + ['fetch', 'origin', 'refs/heads/main'])
    require(capture(git + ['rev-parse', 'HEAD']) == capture(git + ['rev-parse', 'FETCH_HEAD']),
            'Private module differs from origin/main; update the module and its parent pointer.')
    source = secretctl.load_yaml((ROOT / 'clusters/home/secrets-source.yaml').read_bytes())
    require(source.get('spec', {}).get('url') == f'ssh://git@github.com/{owner}/{repo}',
            'Update clusters/home/secrets-source.yaml to match the secrets repository settings.')


def ensure_secrets_access(kube: list[str], env: dict[str, str], owner: str, repo: str) -> None:
    private_repository(owner, repo)
    raw = capture(kube + ['-n', 'flux-system', 'get', 'secret', 'infra-secrets',
                          '--ignore-not-found', '-o', 'json'], env=env)
    if raw.strip():
        try:
            values = {k: base64.b64decode(v, validate=True).decode()
                      for k, v in json.loads(raw).get('data', {}).items()}
        except (ValueError, TypeError, UnicodeError):
            raise ValueError('Invalid Git authentication Secret; contents suppressed.') from None
        require(all(values.get(k) for k in ('identity', 'identity.pub', 'known_hosts')),
                'Existing infra-secrets credentials are incomplete; inspect them before retrying.')
        public = values['identity.pub']
        # Flux emits PKCS8 Ed25519 keys, which OpenSSH cannot import on all hosts.
        # Use Flux's own parser; --export captures its output without applying it.
        with tempfile.TemporaryDirectory(prefix='.infra-git-key-') as work:
            key = Path(work) / 'identity'
            key.write_bytes(values['identity'].encode())
            key.chmod(0o600)
            parsed = secretctl.load_yaml(capture(['flux', 'create', 'secret', 'git', 'infra-secrets',
                f'--url=ssh://git@github.com/{owner}/{repo}', f'--private-key-file={key}',
                '--namespace=flux-system', '--export'], env=env))
            derived = parsed.get('stringData', {}).get('identity.pub', '')
        require(isinstance(derived, str) and len(public.split()) >= 2
                and derived.split()[:2] == public.split()[:2],
                'Existing infra-secrets credentials have a mismatched key pair; inspect them first.')
        pages = json.loads(capture(['gh', 'api', '--hostname', 'github.com', '--paginate', '--slurp',
                                    f'repos/{owner}/{repo}/keys']))
        matches = [key for page in pages for key in page
                   if key.get('key', '').split()[:2] == derived.split()[:2]]
        require(len(matches) == 1 and matches[0].get('read_only') is True,
                'Existing infra-secrets key must be registered read-only on the private repository.')
        return
    raw = capture(['flux', 'create', 'secret', 'git', 'infra-secrets',
                   f'--url=ssh://git@github.com/{owner}/{repo}', '--ssh-key-algorithm=ed25519',
                   '--namespace=flux-system', '--export'], env=env)
    generated = secretctl.load_yaml(raw)
    public = generated.get('stringData', {}).get('identity.pub')
    require(isinstance(public, str) and public.startswith('ssh-ed25519 '),
            'Flux did not generate the expected public deploy key.')
    payload = {'title': 'infra-secrets ' + public.split()[1][:16], 'key': public, 'read_only': True}
    capture(['gh', 'api', '--hostname', 'github.com', '--method', 'POST',
             f'repos/{owner}/{repo}/keys', '--input', '-'], data=json.dumps(payload).encode())
    capture(kube + ['apply', '--server-side', '--field-manager=home-bootstrap', '-f', '-'],
            data=raw, env=env)


def cluster_settings(server_ip: str) -> dict:
    address = ipaddress.IPv4Address(server_ip)
    networks = map(ipaddress.ip_network, ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    require(any(address in network for network in networks), 'SERVER_IP must be a private LAN address for MCP.')
    return {'apiVersion': 'v1', 'kind': 'ConfigMap',
            'metadata': {'name': 'cluster-settings', 'namespace': 'flux-system'},
            'data': {'SERVER_IP': server_ip}}


def bootstrap_flux(kubeconfig: Path, identity: bytes, owner: str, repo: str, version: str,
                   *, branch: str, personal: bool, private: bool,
                   secrets_owner: str, secrets_repo: str, server_ip: str) -> None:
    settings = cluster_settings(server_ip)
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
    # Flux substitutes this address into the MCP IP certificate and Service.
    capture(apply, data=json.dumps(settings).encode(), env=env)
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
    ensure_secrets_access(kube, env, secrets_owner, secrets_repo)
    token = capture(['gh', 'auth', 'token', '--hostname', 'github.com']).decode().strip()
    flux_env = {**env, 'GITHUB_TOKEN': token}
    visible(['flux', 'bootstrap', 'github', f'--owner={owner}', f'--repository={repo}',
             f'--personal={str(personal).lower()}', f'--private={str(private).lower()}', f'--branch={branch}',
             '--path=clusters/home', f'--version={version}', '--interval=1m', '--token-auth=false',
             '--read-write-key=false', '--ssh-key-algorithm=ed25519'], env=flux_env)
    del flux_env, token
    visible(['git', 'pull', '--ff-only', 'origin', f'refs/heads/{branch}'], env=env)
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
        info = json.loads(capture(['gh', 'api', '--hostname', 'github.com', f'repos/{owner}/{repo}']))
        require(info.get('permissions', {}).get('admin') is True, 'GitHub repository admin access is required.')
        require(isinstance(info.get('private'), bool), 'Cannot determine infrastructure repository visibility.')
        secrets_owner, secrets_repo = os.environ['SECRETS_GITHUB_OWNER'], os.environ['SECRETS_GITHUB_REPOSITORY']
        require(re.fullmatch(r'[A-Za-z0-9-]+', secrets_owner) is not None
                and re.fullmatch(r'[A-Za-z0-9._-]+', secrets_repo) is not None,
                'Invalid private secrets repository settings.')
        private_repository(secrets_owner, secrets_repo)
        branch, personal = repository_target(info, os.environ.get('GITHUB_BRANCH', ''))
        check_checkout(owner, repo, branch)
        check_private_checkout(secrets_owner, secrets_repo)
        visible(['bash', 'scripts/check.sh'])
        # Unlock and validate before making changes to the target host.
        identity = decrypt_recovery()
    kubeconfig = obtain_kubeconfig(args.ssh, ip, node, version, fetch_only=args.fetch_kubeconfig)
    kube = ['kubectl', '--kubeconfig', str(kubeconfig), '--context', 'home-test']
    obj = json.loads(capture(kube + ['get', 'node', node, '-o', 'json']))
    require(any(a.get('address') == ip and a.get('type') == 'InternalIP'
                for a in obj.get('status', {}).get('addresses', [])), 'Node InternalIP does not match SERVER_IP.')
    if identity is not None:
        bootstrap_flux(kubeconfig, identity, owner, repo, os.environ['FLUX_VERSION'],
                       branch=branch, personal=personal, private=info['private'],
                       secrets_owner=secrets_owner, secrets_repo=secrets_repo, server_ip=ip)
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
