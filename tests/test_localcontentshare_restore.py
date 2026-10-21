"""Restore generated LocalContentShare data in disposable local containers."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import migration_check as migration

HELPER_IMAGE = 'busybox:1.37.0@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e'


@unittest.skipUnless(all(shutil.which(tool) for tool in ('docker', 'helm', 'kubectl')),
                     'Docker and pinned chart tools required')
class LocalContentShareRestoreTests(unittest.TestCase):
    def test_files_snippets_and_notepad_survive_the_storage_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            contract = migration.load(ROOT / 'tests/migrations/localcontentshare.yaml')
            objects = migration.render(ROOT, 'localcontentshare', contract,
                                       migration.definitions(ROOT, 'localcontentshare'), Path(directory))
            pod = next(item for item in objects if item['kind'] == 'Deployment')['spec']['template']['spec']
            container = pod['containers'][0]
            self.assertEqual(container['volumeMounts'][0]['mountPath'], '/app/data')
            self.assertEqual(pod['volumes'][0]['persistentVolumeClaim']['claimName'], 'localcontentshare-data')
            for probe in ('startupProbe', 'readinessProbe', 'livenessProbe'):
                self.assertEqual(container[probe]['httpGet'], {'path': '/', 'port': 8080})
            marker = uuid.uuid4().hex
            names = ['infra-localcontentshare-' + marker + suffix for suffix in ('-source', '-target')]
            volumes = [name + '-data' for name in names]
            payload = ('restore-fixture-' + marker).encode() * 1024
            snippet = 'Saved snippet ' + marker
            note = '# Saved notepad\n' + marker

            def docker(*args):
                result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0,
                                 'Disposable LocalContentShare fixture operation failed: ' + args[0])
                return result.stdout.strip()

            def request(base, path, data=None, headers=None):
                request = urllib.request.Request(base + path, data=data, headers=headers or {})
                with urllib.request.urlopen(request, timeout=10) as response:
                    self.assertEqual(response.status, 200)
                    return response.read()

            def start(name, volume):
                docker('run', '--detach', '--name', name, '--cap-drop', 'ALL',
                       '--security-opt', 'no-new-privileges', '--publish', '127.0.0.1::8080',
                       '--mount', f'type=volume,src={volume},dst=/app/data',
                       container['image'], *container.get('args', []))
                base = 'http://127.0.0.1:' + docker('port', name, '8080/tcp').rsplit(':', 1)[-1]
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    try:
                        request(base, '/')
                        return base
                    except (OSError, urllib.error.URLError):
                        time.sleep(1)
                self.fail('Disposable LocalContentShare fixture did not become healthy')

            def submit(base, fields, file=None):
                boundary = 'fixture-' + uuid.uuid4().hex
                parts = []
                for name, value in fields.items():
                    parts.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="' + name
                                  + '"\r\n\r\n' + value + '\r\n').encode())
                if file is not None:
                    parts.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="file-upload"; '
                                  'filename="fixture.bin"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()
                                 + file + b'\r\n')
                parts.append(('--' + boundary + '--\r\n').encode())
                request(base, '/submit', b''.join(parts), {
                    'Content-Type': 'multipart/form-data; boundary=' + boundary,
                    'X-Requested-With': 'XMLHttpRequest'})

            def assert_restored_content(base):
                self.assertEqual(request(base, '/raw/text/fixture-note'), snippet.encode())
                self.assertEqual(request(base, '/download/files/fixture.bin'), payload)
                self.assertEqual(request(base, '/notepad/md.file'), note.encode())

            try:
                for volume in volumes:
                    docker('volume', 'create', volume)
                base = start(names[0], volumes[0])
                submit(base, {'type': 'text', 'content': snippet, 'name': 'fixture-note', 'expiry': 'Never'})
                submit(base, {'name': 'fixture.bin', 'expiry': 'Never'}, payload)
                request(base, '/notepad/md.file', note.encode())
                assert_restored_content(base)
                docker('stop', '--time', '30', names[0])
                docker('run', '--rm', '--network', 'none',
                       '--mount', f'type=volume,src={volumes[0]},dst=/source,readonly',
                       '--mount', f'type=volume,src={volumes[1]},dst=/target',
                       HELPER_IMAGE, 'sh', '-ec',
                       'tar -C /source -cpf /tmp/restore.tar .; '
                       'tar -tf /tmp/restore.tar >/dev/null; tar -xpf /tmp/restore.tar -C /target')
                base = start(names[1], volumes[1])
                assert_restored_content(base)
                request(base, '/notepad/md.file', b'Writable after restore')
                self.assertEqual(request(base, '/notepad/md.file'), b'Writable after restore')
            finally:
                for name in names:
                    subprocess.run(['docker', 'rm', '--force', name], capture_output=True)
                for volume in volumes:
                    subprocess.run(['docker', 'volume', 'rm', volume], capture_output=True)
