"""Upgrade generated vault data with the chart configuration and pinned images."""
import base64
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import migration_check as migration

PREVIOUS_IMAGE = ('vaultwarden/server:1.37.2-alpine@sha256:'
                  'd16a304c2476fd787634ddde2c00be8fdfa1a72539438c626ece3686b5b88ab4')


@unittest.skipUnless(all(shutil.which(tool) for tool in ('docker', 'helm', 'kubectl')),
                     'Docker and pinned chart tools required')
class WardenUpgradeTests(unittest.TestCase):
    def test_existing_account_and_vault_item_survive_the_upgrade_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            contract = migration.load(ROOT / 'tests/migrations/warden.yaml')
            objects = migration.render(ROOT, 'warden', contract,
                                       migration.definitions(ROOT, 'warden'), Path(directory))
            workload = next(item for item in objects if item['kind'] == 'Deployment')
            pod = workload['spec']['template']['spec']
            container = pod['containers'][0]
            self.assertEqual(workload['spec']['strategy']['type'], 'Recreate')
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertEqual(container['volumeMounts'], [{'mountPath': '/data', 'name': 'vaultwarden-data'}])
            volume = next(item for item in pod['volumes'] if item['name'] == 'vaultwarden-data')
            self.assertEqual(volume['persistentVolumeClaim']['claimName'], 'warden-data')
            self.assertEqual(container['env'], [{'name': 'ADMIN_TOKEN', 'valueFrom': {
                'secretKeyRef': {'key': 'ADMIN_TOKEN', 'name': 'warden-config'}}}])
            self.assertEqual(container['envFrom'], [{'configMapRef': {'name': 'warden'}}])
            environment = next(item['data'] for item in objects
                               if item['kind'] == 'ConfigMap' and item['metadata']['name'] == 'warden')
            self.assertEqual(environment['SIGNUPS_ALLOWED'], 'false')
            self.assertEqual(environment['DOMAIN'], 'https://warden.lionzxy.ru')
            environment = {**environment, 'DOMAIN': 'https://warden.fixture.invalid',
                           'ADMIN_TOKEN': secrets.token_urlsafe(32)}
            marker = uuid.uuid4().hex
            names = ['infra-warden-' + marker + suffix for suffix in ('-before', '-after', '-restart')]
            data_volume = 'infra-warden-data-' + marker
            email = marker + '@example.invalid'
            password_hash = base64.b64encode(secrets.token_bytes(32)).decode()
            device = str(uuid.uuid4())

            def encrypted_value():
                return '2.' + '|'.join(base64.b64encode(secrets.token_bytes(size)).decode()
                                       for size in (16, 32, 32))

            account_key = encrypted_value()
            item = {'type': 2, 'name': encrypted_value(), 'notes': encrypted_value(),
                    'secureNote': {'type': 0}, 'favorite': False}

            def docker(*args):
                result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0,
                                 'Disposable Warden fixture operation failed: ' + args[0])
                return result.stdout.strip()

            def request(base, path, method='GET', data=None, token=None, form=False):
                headers = {}
                if data is not None:
                    data = (urllib.parse.urlencode(data) if form else json.dumps(data)).encode()
                    headers['Content-Type'] = ('application/x-www-form-urlencoded' if form
                                               else 'application/json')
                if token:
                    headers['Authorization'] = 'Bearer ' + token
                req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
                try:
                    response = urllib.request.urlopen(req, timeout=15)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    return response.status, response.read()

            def api(base, path, method='GET', data=None, token=None, form=False):
                status, body = request(base, path, method, data, token, form)
                self.assertEqual(status, 200, 'Disposable Warden API request failed: ' + path)
                return json.loads(body)

            def start(name, image, seed=False):
                env = {**environment, 'SIGNUPS_ALLOWED': 'true'} if seed else environment
                args = ['run', '--detach', '--name', name, '--cap-drop', 'ALL',
                        '--security-opt', 'no-new-privileges', '--publish', '127.0.0.1::8080',
                        '--mount', f'type=volume,src={data_volume},dst=/data']
                for key, value in env.items():
                    args += ['--env', key + '=' + value]
                docker(*args, image)
                base = 'http://127.0.0.1:' + docker('port', name, '8080/tcp').rsplit(':', 1)[-1]
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    try:
                        if request(base, '/alive')[0] == 200:
                            return base
                    except (OSError, urllib.error.URLError):
                        pass
                    self.assertEqual(docker('inspect', '--format', '{{.State.Running}}', name), 'true',
                                     'Disposable Warden fixture exited during startup')
                    time.sleep(1)
                self.fail('Disposable Warden fixture did not become healthy')

            def login(base, password=password_hash):
                return request(base, '/identity/connect/token', 'POST', {
                    'grant_type': 'password', 'username': email, 'password': password,
                    'scope': 'api offline_access', 'client_id': 'web',
                    'deviceIdentifier': device, 'deviceName': 'fixture', 'deviceType': '10'}, form=True)

            def authenticate(base):
                status, body = login(base)
                self.assertEqual(status, 200, 'Generated account cannot log in')
                payload = json.loads(body)
                self.assertEqual(payload['Key'], account_key)
                return payload['access_token']

            try:
                docker('volume', 'create', data_volume)
                base = start(names[0], PREVIOUS_IMAGE, seed=True)
                self.assertEqual(api(base, '/api/version'), '1.37.2')
                api(base, '/identity/accounts/register', 'POST', {
                    'email': email, 'name': 'Upgrade fixture', 'masterPasswordHash': password_hash,
                    'key': account_key, 'kdf': 0, 'kdfIterations': 600000})
                token = authenticate(base)
                item['encryptedFor'] = api(base, '/api/accounts/profile', token=token)['id']
                cipher = api(base, '/api/ciphers', 'POST', item, token)
                path = '/api/ciphers/' + cipher['id']
                docker('stop', '--time', '30', names[0])

                for name in names[1:]:
                    base = start(name, container['image'])
                    self.assertEqual(api(base, '/api/version'), '1.37.4')
                    self.assertIn(b'<html', request(base, '/')[1].lower())
                    token = authenticate(base)
                    saved = api(base, path, token=token)
                    for key in ('name', 'notes', 'secureNote', 'type'):
                        self.assertEqual(saved[key], item[key])
                    self.assertEqual(request(base, path)[0], 401)
                    self.assertEqual(login(base, secrets.token_urlsafe(32))[0], 400)
                    self.assertEqual(request(base, '/identity/accounts/register/send-verification-email',
                                             'POST', {'email': uuid.uuid4().hex + '@example.invalid'})[0], 400)
                    item['notes'] = encrypted_value()
                    api(base, path, 'PUT', item, token)
                    self.assertEqual(api(base, path, token=token)['notes'], item['notes'])
                    docker('stop', '--time', '30', name)
            finally:
                for name in names:
                    subprocess.run(['docker', 'rm', '--force', name], capture_output=True, timeout=60)
                subprocess.run(['docker', 'volume', 'rm', data_volume], capture_output=True, timeout=60)
