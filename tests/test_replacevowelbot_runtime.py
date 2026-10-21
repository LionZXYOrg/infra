"""Start the pinned bot offline with generated credentials and a fake Telegram client."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import migration_check as migration


@unittest.skipUnless(all(shutil.which(tool) for tool in ('docker', 'helm', 'kubectl')),
                     'Docker and pinned chart tools required')
class ReplaceVowelBotRuntimeTests(unittest.TestCase):
    def test_bot_loads_the_secret_and_starts_polling_without_network_access(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            contract = migration.load(ROOT / 'tests/migrations/replacevowelbot.yaml')
            objects = migration.render(ROOT, 'replacevowelbot', contract,
                                       migration.definitions(ROOT, 'replacevowelbot'), work)
            pod = next(item for item in objects if item['kind'] == 'Deployment')['spec']['template']['spec']
            container = pod['containers'][0]
            self.assertEqual(container['command'], ['python', '/app/main.py'])
            self.assertTrue(container['image'].startswith('lionzxy/replace_vowel_bot:2@sha256:'))
            self.assertFalse(pod['automountServiceAccountToken'])
            self.assertFalse(any('persistentVolumeClaim' in volume for volume in pod['volumes']))
            secret_mount = next(item for item in container['volumeMounts'] if item['mountPath'] == '/app/secret.py')
            secret_volume = next(item for item in pod['volumes'] if item['name'] == secret_mount['name'])
            self.assertEqual(secret_volume['secret']['secretName'], 'replacevowelbot-config')
            self.assertEqual(secret_volume['secret']['defaultMode'], 0o400)
            token = '123456789:' + uuid.uuid4().hex
            secret = work / 'secret.py'
            secret.write_text('token = ' + repr(token) + '\n')
            secret.chmod(0o444)
            name = 'infra-replacevowelbot-' + uuid.uuid4().hex
            runner = textwrap.dedent('''
                from pathlib import Path
                import runpy
                import time
                from unittest.mock import patch
                from telegram.ext import InlineQueryHandler
                import secret

                with patch('telegram.ext.Updater') as client:
                    def idle():
                        args, kwargs = client.call_args
                        assert kwargs.get('token', args[0] if args else None) == secret.token
                        updater = client.return_value
                        assert updater.start_polling.call_count == 1
                        handlers = [call[0][0] for call in updater.dispatcher.add_handler.call_args_list]
                        assert len(handlers) == 1 and isinstance(handlers[0], InlineQueryHandler)
                        assert callable(handlers[0].callback)
                        Path('/tmp/fixture-ready').write_text('ready')
                        while True:
                            time.sleep(1)
                    client.return_value.idle.side_effect = idle
                    runpy.run_path('/app/main.py', run_name='__main__')
            ''')

            def docker(*args):
                result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=180)
                self.assertEqual(result.returncode, 0,
                                 'Disposable ReplaceVowelBot fixture operation failed: ' + args[0])
                return result.stdout

            try:
                docker('run', '--detach', '--name', name, '--network', 'none',
                       '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                       '--mount', f'type=bind,src={secret},dst=/app/secret.py,readonly',
                       '--entrypoint', container['command'][0], container['image'], '-c', runner)
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    status = subprocess.run(['docker', 'exec', name, 'test', '-f', '/tmp/fixture-ready'],
                                            capture_output=True, timeout=10)
                    if status.returncode == 0:
                        break
                    running = docker('inspect', '--format', '{{.State.Running}}', name).strip()
                    self.assertEqual(running, 'true', 'Offline bot exited before polling initialized')
                    time.sleep(1)
                else:
                    self.fail('Offline bot did not initialize polling')
                for probe in ('startupProbe', 'readinessProbe', 'livenessProbe'):
                    command = container[probe]['exec']['command']
                    self.assertEqual(command, ['/bin/sh', '-c', 'kill -0 1'])
                    docker('exec', name, *command)
            finally:
                subprocess.run(['docker', 'rm', '--force', name], capture_output=True)
