"""psybot migration invariants from the source snapshot; no live credentials."""
from pathlib import Path
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/psybot'
def load(filename):
    return yaml.safe_load((APP / filename).read_text())
def release(name):
    return load('helmrelease-' + name + '.yaml')

class MigrationTests(unittest.TestCase):
    def test_legacy_telegram_api_and_publication_prerequisite_are_explicit(self):
        self.assertEqual((APP / 'image/requirements.txt').read_text(), 'python-telegram-bot==13.15\n')
        contract = yaml.safe_load((ROOT / 'tests/migrations/psybot.yaml').read_text())
        self.assertEqual(len(contract['pendingImages']), 1)
        self.assertEqual(contract['pendingImages'][0], 'ghcr.io/lionzxyorg/psybot:snapshot-ccfc5ecd5433')
        self.assertEqual(contract['imageBuilds'], [{'context': 'apps/psybot/image'}])

class BotBehaviorTests(unittest.TestCase):
    def load_bot(self, text):
        import importlib.util
        import sys
        import types
        from unittest.mock import Mock, patch
        telegram = types.ModuleType('telegram')
        errors = types.ModuleType('telegram.error')
        errors.NetworkError = type('NetworkError', (Exception,), {})
        errors.Unauthorized = type('Unauthorized', (Exception,), {})
        message = types.SimpleNamespace(text=text, chat=types.SimpleNamespace(id=999))
        update = types.SimpleNamespace(update_id=7, message=message)
        fake = Mock()
        fake.get_updates.return_value = [update]
        spec = importlib.util.spec_from_file_location('psybot_under_test', APP / 'image/main.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'telegram': telegram, 'telegram.error': errors}):
            spec.loader.exec_module(module)
        return module, fake

    def test_start_replies_without_forwarding_a_question(self):
        module, fake = self.load_bot('/start')
        module.echo(fake)
        self.assertEqual(fake.send_message.call_count, 1)
        self.assertEqual(fake.send_message.call_args.kwargs['chat_id'], 999)
        self.assertEqual(module.update_id, 8)

    def test_question_is_forwarded_and_acknowledged_with_fake_clients(self):
        module, fake = self.load_bot('fixture question')
        module.echo(fake)
        self.assertEqual(fake.send_message.call_count, 2)
        calls = fake.send_message.call_args_list
        self.assertEqual(calls[0].kwargs['chat_id'], 203155380)
        self.assertEqual(calls[0].kwargs['text'], 'fixture question')
        self.assertEqual(calls[1].kwargs['chat_id'], 999)
        self.assertEqual(module.update_id, 8)
