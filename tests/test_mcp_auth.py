"""Credential creation uses SOPS; exported client credentials stay private."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

import bcrypt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import mcp_auth
import secretctl


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.root = self.work / 'repo'
        self.root.mkdir()
        self.path = self.root / 'mcp-auth.sops.yaml'
        self.enterContext(patch.object(secretctl, 'ROOT', self.root))

    def test_random_password_is_hashed_before_private_encryption(self):
        output = io.StringIO()
        def run(args, **kwargs):
            return str(self.work / 'secretctl.lock').encode()
        with patch.object(secretctl, 'require_private_module'), patch.object(secretctl, 'plaintext_guard'), \
                patch.object(secretctl, 'target', return_value=self.path), \
                patch.object(secretctl, 'run', side_effect=run), \
                patch.object(secretctl, 'encrypt', return_value=b'encrypted') as encrypt, \
                patch.object(secretctl, 'atomic_write') as write, \
                patch.object(secretctl, 'ensure_namespace'), patch.object(secretctl, 'sync_indexes'), \
                contextlib.redirect_stdout(output):
            mcp_auth.create()
        obj = encrypt.call_args.args[0]
        data = {key: base64.b64decode(value) for key, value in obj['data'].items()}
        self.assertGreaterEqual(len(data['password']), 32)
        username, hashed = data['users'].strip().split(b':', 1)
        self.assertEqual(username, data['username'])
        self.assertTrue(bcrypt.checkpw(data['password'], hashed))
        self.assertNotIn(data['password'].decode(), output.getvalue())
        write.assert_called_once_with(self.path, b'encrypted')

    def export(self, directory, password=b'fixture-password-with-at-least-32-bytes'):
        obj = {'data': {key: base64.b64encode(value).decode() for key, value in {
            'username': b'mcp', 'password': password}.items()}}
        certificate = {'data': {'tls.crt': base64.b64encode(b'-----BEGIN CERTIFICATE-----\nfixture').decode()}}
        def run(args, **kwargs):
            return json.dumps(certificate if args[0] == 'kubectl' else obj).encode()
        with patch.dict(os.environ, {'KUBECONFIG': '/fixture/admin'}), \
                patch.object(secretctl, 'require_private_module'), patch.object(secretctl, 'plaintext_guard'), \
                patch.object(secretctl, 'target', return_value=self.path), \
                patch.object(secretctl, 'identity_env', return_value=contextlib.nullcontext({})), \
                patch.object(secretctl, 'validate'), patch.object(secretctl, 'run', side_effect=run), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            mcp_auth.export_client('192.168.1.150', directory)
        return output.getvalue()

    def test_client_files_are_private_and_do_not_print_password(self):
        directory = self.work / 'client'
        output = self.export(directory)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for name in ('opencode.json', 'curl.conf', 'ca.crt'):
            self.assertEqual(stat.S_IMODE((directory / name).stat().st_mode), 0o600)
        entry = json.loads((directory / 'opencode.json').read_text())['mcp']['k3s-lan']
        self.assertEqual(entry['url'], 'https://192.168.1.150:8443/mcp')
        self.assertIs(entry['oauth'], False)
        self.assertTrue(entry['headers']['Authorization'].startswith('Basic '))
        self.assertNotIn('fixture-password', output)
        self.assertEqual(list(directory.glob('.mcp-*')), [])

    def test_refuses_public_address_before_reading_credentials(self):
        for address in ('178.62.76.162', '127.0.0.1', '0.0.0.0', 'not-an-ip'):
            with self.subTest(address=address), patch.object(secretctl, 'run') as run:
                with self.assertRaises(ValueError):
                    mcp_auth.export_client(address, self.work / 'client')
                run.assert_not_called()

    def test_refuses_repository_directory_and_symlinks(self):
        link = self.work / 'client-link'
        link.symlink_to(self.work / 'client')
        for directory in (self.root / 'client', link, self.work / 'bad"directory'):
            with self.subTest(directory=str(directory)):
                with self.assertRaises(ValueError):
                    self.export(directory)
        directory = self.work / 'client'
        directory.mkdir()
        target = self.work / 'sentinel'
        target.write_bytes(b'unchanged')
        (directory / 'opencode.json').symlink_to(target)
        with self.assertRaises(ValueError):
            self.export(directory)
        self.assertEqual(target.read_bytes(), b'unchanged')


if __name__ == '__main__':
    unittest.main()
