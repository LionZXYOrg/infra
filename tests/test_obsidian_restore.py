"""Restore generated CouchDB data in local, disposable containers."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import migration_check as migration


@unittest.skipUnless(all(shutil.which(tool) for tool in ('docker', 'helm', 'kubectl')),
                     'Docker and pinned chart tools required')
class ObsidianRestoreTests(unittest.TestCase):
    def test_physical_restore_preserves_the_standalone_node_and_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            contract = migration.load(ROOT / 'tests/migrations/obsidian.yaml')
            objects = migration.render(ROOT, 'obsidian', contract,
                                       migration.definitions(ROOT, 'obsidian'), work)
            workload = next(item for item in objects if item['kind'] == 'StatefulSet')
            pod = workload['spec']['template']['spec']
            container = pod['containers'][0]
            configuration = next(item['data'] for item in objects if item['kind'] == 'ConfigMap')
            environment = {item['name']: item for item in container['env']}
            self.assertEqual(environment['ERL_FLAGS']['value'].strip(), '')
            self.assertNotIn('NODENAME', environment)
            self.assertEqual(configuration['seedlistinifile'].strip(), '[cluster]\nseedlist =')
            for probe in ('readinessProbe', 'livenessProbe'):
                command = container[probe]['exec']['command'][-1]
                self.assertIn('${COUCHDB_USER}:${COUCHDB_PASSWORD}', command)
                self.assertIn('/_up', command)
            for name, key in [('COUCHDB_USER', 'adminUsername'), ('COUCHDB_PASSWORD', 'adminPassword'),
                              ('COUCHDB_SECRET', 'cookieAuthSecret'), ('COUCHDB_ERLANG_COOKIE', 'erlangCookie')]:
                self.assertEqual(environment[name]['valueFrom']['secretKeyRef'],
                                 {'name': 'obsidian-config', 'key': key})
            self.assertEqual(next(volume['persistentVolumeClaim']['claimName']
                                  for volume in pod['volumes'] if volume['name'] == 'database-storage'),
                             'obsidian-data')
            marker = uuid.uuid4().hex
            source, target = ('infra-obsidian-' + marker + suffix for suffix in ('-source', '-target'))
            volumes = [source + '-data', target + '-data', source + '-config', target + '-config']
            helper_image = next(item['image'] for item in pod['initContainers'] if item['name'] == 'init-copy')
            credentials = {'COUCHDB_USER': 'fixture-admin', 'COUCHDB_PASSWORD': uuid.uuid4().hex,
                           'COUCHDB_SECRET': uuid.uuid4().hex, 'COUCHDB_ERLANG_COOKIE': uuid.uuid4().hex}
            auth = credentials['COUCHDB_USER'] + ':' + credentials['COUCHDB_PASSWORD']

            def docker(*args, timeout=180, input_text=None):
                result = subprocess.run(['docker', *args], input=input_text, capture_output=True, text=True, timeout=timeout)
                self.assertEqual(result.returncode, 0,
                                 'Disposable CouchDB fixture operation failed: ' + args[0])
                return result.stdout

            def start(name, volume, config_volume, extra_environment=None):
                args = ['run', '--detach', '--network', 'none', '--name', name,
                        '--mount', f'type=volume,src={volume},dst=/opt/couchdb/data',
                        '--mount', f'type=volume,src={config_volume},dst=/opt/couchdb/etc/default.d']
                for key, value in {**credentials, **(extra_environment or {})}.items():
                    args += ['--env', key + '=' + value]
                docker(*args, container['image'])
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    result = subprocess.run(['docker', 'exec', name, 'curl', '--silent', '--fail',
                                             '--user', auth, 'http://127.0.0.1:5984/_up'],
                                            capture_output=True, timeout=10)
                    if result.returncode == 0:
                        self.assertEqual(json.loads(result.stdout)['status'], 'ok')
                        return
                    time.sleep(1)
                self.fail('Disposable CouchDB fixture did not become healthy')

            def request(name, path, *args):
                return json.loads(docker('exec', name, 'curl', '--silent', '--fail', '--user', auth,
                                         *args, 'http://127.0.0.1:5984/' + path))

            try:
                for volume in volumes:
                    docker('volume', 'create', volume)
                def write_configuration(volume, name, content):
                    docker('run', '--rm', '--interactive', '--network', 'none',
                           '--mount', f'type=volume,src={volume},dst=/configuration',
                           '--entrypoint', 'sh', helper_image, '-ec',
                           f'cat > /configuration/{name}', input_text=content)

                write_configuration(volumes[2], 'fixture.ini', '[couchdb]\nsingle_node = true\n')
                start(source, volumes[0], volumes[2])
                self.assertEqual(request(source, '_membership')['all_nodes'], ['nonode@nohost'])
                self.assertTrue(request(source, 'fixture', '--request', 'PUT')['ok'])
                self.assertTrue(request(source, 'fixture/kept', '--request', 'PUT',
                                        '--header', 'Content-Type: application/json',
                                        '--data-binary', '{"value":"retained"}')['ok'])
                docker('stop', '--time', '30', source)
                docker('run', '--rm', '--network', 'none',
                       '--mount', f'type=volume,src={volumes[0]},dst=/source,readonly',
                       '--mount', f'type=volume,src={volumes[1]},dst=/target',
                       '--entrypoint', 'sh', helper_image, '-ec',
                       'set -o pipefail; tar -C /source -cpf - . | tar -xpf - -C /target; chown -R 5984:5984 /target')
                write_configuration(volumes[3], 'chart.ini', configuration['inifile'])
                write_configuration(volumes[3], 'seedlist.ini', configuration['seedlistinifile'])
                start(target, volumes[1], volumes[3], {'ERL_FLAGS': environment['ERL_FLAGS']['value']})
                self.assertEqual(request(target, '_membership')['all_nodes'], ['nonode@nohost'])
                self.assertEqual(request(target, 'fixture/kept')['value'], 'retained')
                status = docker('exec', target, 'curl', '--silent', '--output', '/dev/null',
                                '--write-out', '%{http_code}', 'http://127.0.0.1:5984/fixture/kept')
                self.assertEqual(status, '401')
            finally:
                for name in (source, target):
                    subprocess.run(['docker', 'rm', '--force', name], capture_output=True)
                for volume in volumes:
                    subprocess.run(['docker', 'volume', 'rm', volume], capture_output=True)
