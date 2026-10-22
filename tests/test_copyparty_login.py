"""Test browser login through a trusted proxy with the deployed image and chart."""
import http.cookies
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import migration_check as migration


@unittest.skipUnless(all(shutil.which(tool) for tool in ('docker', 'helm', 'kubectl')),
                     'Docker and pinned chart tools required')
class CopypartyLoginTests(unittest.TestCase):
    def test_https_login_requires_a_trusted_proxy_and_matching_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            contract = migration.load(ROOT / 'tests/migrations/copyparty.yaml')
            objects = migration.render(ROOT, 'copyparty', contract,
                                       migration.definitions(ROOT, 'copyparty'), work)
            pod = next(item for item in objects if item['kind'] == 'Deployment')['spec']['template']['spec']
            container = pod['containers'][0]
            self.assertNotIn('command', container)
            route = next(item for item in objects if item['kind'] == 'IngressRoute')
            self.assertEqual(route['spec']['routes'][0]['match'], 'Host(`cp.kulikov.uk`)')
            marker = uuid.uuid4().hex
            username, password = 'fixture', uuid.uuid4().hex
            configuration = work / 'configuration'
            configuration.mkdir(mode=0o777)
            configuration.chmod(0o777)
            conf = configuration / 'fixture.conf'
            conf.write_text('[global]\n  usernames\n  rproxy: 0\n  xff-src: 127.0.0.0/8\n'
                            '[accounts]\n  ' + username + ': ' + password + '\n'
                            '[/]\n  /w\n  accs:\n    r: ' + username + '\n')
            conf.chmod(0o444)
            network = 'infra-copyparty-' + marker
            names = [network + suffix for suffix in ('-before', '-after')]

            def docker(*args):
                result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0,
                                 'Disposable Copyparty fixture operation failed: ' + args[0])
                return result.stdout.strip()

            # Requests run inside the fixture. The pod address is trusted, but loopback is not.
            request_program = '''
import http.client, json, sys
target, method, path, headers, body = json.loads(sys.argv[1])
connection = http.client.HTTPConnection(target, 3923, timeout=10)
connection.request(method, path, body=body, headers=headers)
response = connection.getresponse()
payload = response.read()
print(json.dumps({'status': response.status, 'body': payload.decode(errors='replace'),
                  'cookies': response.headers.get_all('Set-Cookie', [])}))
connection.close()
'''

            def request(name, address, method='GET', path='/', origin=None, cookie=None,
                        forwarded=True, login_password=None):
                headers = {'Host': 'cp.kulikov.uk'}
                if forwarded:
                    # Traefik appends the actual peer, after any client-supplied address.
                    headers.update({'X-Forwarded-For': '198.51.100.12, 203.0.113.9',
                                    'X-Forwarded-Host': 'cp.kulikov.uk',
                                    'X-Forwarded-Proto': 'https'})
                if origin:
                    headers['Origin'] = origin
                if cookie:
                    headers['Cookie'] = cookie
                body = None
                if method == 'POST':
                    boundary = 'fixture-' + marker
                    fields = {'act': 'login', 'uname': username,
                              'cppwd': password if login_password is None else login_password}
                    body = ''.join('--' + boundary + '\r\nContent-Disposition: form-data; name="' + key
                                   + '"\r\n\r\n' + value + '\r\n' for key, value in fields.items())
                    body += '--' + boundary + '--\r\n'
                    headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
                return json.loads(docker('exec', name, 'python3', '-c', request_program,
                                         json.dumps([address, method, path, headers, body])))

            def start(name, args):
                security = container['securityContext']
                docker('run', '--detach', '--name', name, '--network', network,
                       '--user', str(security['runAsUser']) + ':' + str(security['runAsGroup']),
                       '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                       '--tmpfs', '/w:uid=1000,gid=1000', '--tmpfs', '/state:uid=1000,gid=1000',
                       '--mount', f'type=bind,src={configuration},dst=/cfg', container['image'], *args)
                address = json.loads(docker('inspect', name))[0]['NetworkSettings']['Networks'][network]['IPAddress']
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    result = subprocess.run(['docker', 'exec', name, 'python3', '-c',
                                             'import socket; socket.create_connection(("127.0.0.1",3923),1).close()'],
                                            capture_output=True, timeout=10)
                    if result.returncode == 0:
                        return address
                    self.assertEqual(docker('inspect', '--format', '{{.State.Running}}', name), 'true',
                                     'Disposable Copyparty fixture exited during startup')
                    time.sleep(1)
                self.fail('Disposable Copyparty fixture did not become healthy')

            try:
                # Keep the fixture inside the production pod range, without an external route.
                subnet = '10.42.' + str(128 + int(marker[:2], 16) % 126) + '.0/24'
                docker('network', 'create', '--internal', '--subnet', subnet, network)
                before = start(names[0], [])
                rejected = request(names[0], before, 'POST', origin='https://cp.kulikov.uk')
                self.assertEqual(rejected['status'], 403)
                self.assertIn('rejected by cors-check', rejected['body'])
                docker('rm', '--force', names[0])

                after = start(names[1], container['args'])
                self.assertEqual(request(names[1], '127.0.0.1', forwarded=False)['status'], 200)
                anonymous = request(names[1], after, path='/?ls')
                self.assertEqual(anonymous['status'], 200)
                self.assertEqual(json.loads(anonymous['body'])['acct'], '*')
                accepted = request(names[1], after, 'POST', origin='https://cp.kulikov.uk')
                self.assertEqual(accepted['status'], 200)
                self.assertIn('hi ' + username, accepted['body'])
                cookies = http.cookies.SimpleCookie()
                for value in accepted['cookies']:
                    cookies.load(value)
                self.assertIn('cppws', cookies)
                self.assertTrue(cookies['cppws']['secure'])
                self.assertTrue(cookies['cppws']['httponly'])
                cookie = 'cppws=' + cookies['cppws'].value
                listing = request(names[1], after, path='/?ls', cookie=cookie)
                self.assertEqual(listing['status'], 200)
                self.assertEqual(json.loads(listing['body'])['acct'], username)

                invalid = request(names[1], after, 'POST', origin='https://cp.kulikov.uk',
                                  login_password=uuid.uuid4().hex)
                self.assertEqual(invalid['status'], 200)
                self.assertNotIn('hi ' + username, invalid['body'])
                self.assertIn('naw dude', invalid['body'])
                for origin in ('https://cors-test.invalid', 'http://cp.kulikov.uk', 'null'):
                    rejected = request(names[1], after, 'POST', origin=origin, cookie=cookie)
                    self.assertEqual(rejected['status'], 403)
                    self.assertIn('rejected by cors-check', rejected['body'])
                # Forged forwarded headers from an address outside the pod range cannot enable login.
                rejected = request(names[1], '127.0.0.1', 'POST', origin='https://cp.kulikov.uk')
                self.assertEqual(rejected['status'], 403)
                self.assertIn('rejected by cors-check', rejected['body'])
            finally:
                for name in names:
                    subprocess.run(['docker', 'rm', '--force', name], capture_output=True, timeout=60)
                subprocess.run(['docker', 'network', 'rm', network], capture_output=True, timeout=60)
