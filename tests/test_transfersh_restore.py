"""Restore generated transfer.sh uploads in disposable local containers."""
from pathlib import Path
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

HELPER_IMAGE = 'busybox:1.37.0@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e'


@unittest.skipUnless(all(shutil.which(tool) for tool in ('docker', 'helm', 'kubectl')),
                     'Docker and pinned chart tools required')
class TransfershRestoreTests(unittest.TestCase):
    def test_restored_upload_remains_readable_with_the_deployed_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            contract = migration.load(ROOT / 'tests/migrations/transfersh.yaml')
            objects = migration.render(ROOT, 'transfersh', contract,
                                       migration.definitions(ROOT, 'transfersh'), Path(directory))
            workload = next(item for item in objects if item['kind'] == 'Deployment')
            pod = workload['spec']['template']['spec']
            container = pod['containers'][0]
            self.assertEqual(container['volumeMounts'][0]['mountPath'], '/storage')
            self.assertEqual(pod['volumes'][0]['persistentVolumeClaim']['claimName'], 'transfersh-storage')
            marker = uuid.uuid4().hex
            names = ['infra-transfersh-' + marker + suffix for suffix in ('-source', '-target')]
            volumes = [name + '-data' for name in names]
            payload = ('restore-fixture-' + marker).encode() * 1024

            def docker(*args, timeout=180):
                result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
                self.assertEqual(result.returncode, 0,
                                 'Disposable transfer.sh fixture operation failed: ' + args[0])
                return result.stdout.strip()

            def start(name, volume):
                args = ['run', '--detach', '--name', name, '--cap-drop', 'ALL',
                        '--security-opt', 'no-new-privileges', '--publish', '127.0.0.1::8080',
                        '--mount', f'type=volume,src={volume},dst=/storage']
                for item in container['env']:
                    args += ['--env', item['name'] + '=' + item['value']]
                docker(*args, container['image'], *container['args'])
                port = docker('port', name, '8080/tcp').rsplit(':', 1)[-1]
                base = 'http://127.0.0.1:' + port
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    try:
                        with urllib.request.urlopen(base + '/health.html', timeout=3) as response:
                            self.assertEqual(response.status, 200)
                            return base
                    except (OSError, urllib.error.URLError):
                        time.sleep(1)
                self.fail('Disposable transfer.sh fixture did not become healthy')

            def download(base, path):
                with urllib.request.urlopen(base + path, timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    return response.read()

            try:
                for volume in volumes:
                    docker('volume', 'create', volume)
                base = start(names[0], volumes[0])
                request = urllib.request.Request(base + '/fixture.bin', data=payload, method='PUT')
                with urllib.request.urlopen(request, timeout=15) as response:
                    self.assertEqual(response.status, 200)
                    path = urllib.parse.urlsplit(response.read().decode().strip()).path
                self.assertTrue(path.endswith('/fixture.bin'))
                self.assertEqual(download(base, path), payload)
                docker('stop', '--time', '30', names[0])
                docker('run', '--rm', '--network', 'none',
                       '--mount', f'type=volume,src={volumes[0]},dst=/source,readonly',
                       '--mount', f'type=volume,src={volumes[1]},dst=/target',
                       HELPER_IMAGE, 'sh', '-ec',
                       'set -o pipefail; tar -C /source -cpf - . | tar -xpf - -C /target')
                base = start(names[1], volumes[1])
                self.assertEqual(download(base, path), payload)
                with self.assertRaises(urllib.error.HTTPError) as missing:
                    urllib.request.urlopen(base + '/missing/fixture.bin', timeout=10)
                self.assertEqual(missing.exception.code, 404)
                missing.exception.close()
            finally:
                for name in names:
                    subprocess.run(['docker', 'rm', '--force', name], capture_output=True)
                for volume in volumes:
                    subprocess.run(['docker', 'volume', 'rm', volume], capture_output=True)
