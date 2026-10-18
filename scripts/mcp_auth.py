#!/usr/bin/env python3
"""Create encrypted MCP credentials or export private client files."""
import argparse
import base64
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import secrets
import tempfile

import bcrypt
import secretctl


def create():
    secretctl.require_private_module()
    secretctl.plaintext_guard()
    path = secretctl.target('k3s-mcp', 'mcp-http-auth')
    lock_path = Path(secretctl.run(['git', 'rev-parse', '--git-path', 'secretctl.lock']).decode().strip())
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        secretctl.require(not path.exists(), 'MCP credentials already exist.')
        username, password = b'mcp', secrets.token_urlsafe(32).encode()
        users = username + b':' + bcrypt.hashpw(password, bcrypt.gensalt(rounds=12)) + b'\n'
        obj = {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
               'metadata': {'name': 'mcp-http-auth', 'namespace': 'k3s-mcp'},
               'data': {key: base64.b64encode(value).decode() for key, value in {
                   'username': username, 'password': password, 'users': users}.items()}}
        secretctl.atomic_write(path, secretctl.encrypt(obj, path))
        secretctl.ensure_namespace('k3s-mcp')
        secretctl.sync_indexes()
    print('Created encrypted MCP credentials. Commit and push the private repository first.')


def secure_write(path: Path, data: bytes):
    secretctl.require(not path.is_symlink(), 'Refusing a client file symlink.')
    fd, temporary = tempfile.mkstemp(prefix='.mcp-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def export_client(server_ip: str, directory: Path):
    address = ipaddress.IPv4Address(server_ip)
    networks = map(ipaddress.ip_network, ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
    secretctl.require(any(address in network for network in networks), 'Use the private SERVER_IP address.')
    # Reject symlinks before resolve, including any symlinked parent directory.
    directory = directory.expanduser().absolute()
    secretctl.require(not any(path.is_symlink() for path in (directory, *directory.parents)),
                      'Refusing a client directory symlink.')
    directory = directory.resolve()
    secretctl.require(not directory.is_relative_to(secretctl.ROOT),
                      'Client credentials must stay outside this repository.')
    secretctl.require(not any(character in str(directory) for character in '\n\r"\\'),
                      'Unsupported client directory characters.')
    secretctl.require(bool(os.environ.get('KUBECONFIG')), 'Set the administrator KUBECONFIG first.')
    secretctl.require_private_module()
    secretctl.plaintext_guard()
    path = secretctl.target('k3s-mcp', 'mcp-http-auth')
    with secretctl.identity_env() as env:
        obj = secretctl.load_yaml(secretctl.run(['sops', '--decrypt', str(path)], env=env))
    secretctl.validate(obj, path, encrypted=False)
    data = {key: base64.b64decode(obj['data'][key], validate=True) for key in ('username', 'password')}
    secretctl.require(data['username'] == b'mcp' and len(data['password']) >= 32,
                      'Invalid MCP credentials; contents suppressed.')
    certificate = json.loads(secretctl.run(['kubectl', '--kubeconfig', os.environ['KUBECONFIG'],
        '-n', 'k3s-mcp', 'get', 'secret', 'mcp-ca', '-o', 'json']))
    ca = base64.b64decode(certificate['data']['tls.crt'], validate=True)
    secretctl.require(ca.startswith(b'-----BEGIN CERTIFICATE-----'), 'Invalid MCP CA certificate.')
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    authorization = 'Basic ' + base64.b64encode(data['username'] + b':' + data['password']).decode()
    url = f'https://{address}:8443/mcp'
    configuration = {'mcp': {'k3s-lan': {'type': 'remote', 'url': url,
                                      'headers': {'Authorization': authorization}}}}
    secure_write(directory / 'ca.crt', ca)
    secure_write(directory / 'opencode.json', (json.dumps(configuration, indent=2) + '\n').encode())
    curl = f'url = "{url}"\ncacert = "{directory / "ca.crt"}"\nheader = "Authorization: {authorization}"\n'
    secure_write(directory / 'curl.conf', curl.encode())
    print('Wrote private client files. Credential values are suppressed.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('create')
    client = commands.add_parser('client')
    client.add_argument('--server-ip', default=os.environ.get('SERVER_IP'), required=not os.environ.get('SERVER_IP'))
    client.add_argument('--output-dir', type=Path, default=Path.home() / '.config/k3s-mcp')
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'create':
        create()
    else:
        export_client(args.server_ip, args.output_dir)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, KeyError, OSError):
        raise SystemExit('MCP credential operation failed; sensitive output is suppressed.') from None
