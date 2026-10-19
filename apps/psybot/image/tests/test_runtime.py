"""Test the installed Telegram API without starting the bot."""
import importlib
import os
import unittest
from unittest.mock import patch

import telegram


class RuntimeTests(unittest.TestCase):
    def test_pinned_telegram_api_loads_without_credentials_or_startup(self):
        self.assertEqual(telegram.__version__, '13.15')
        with patch.dict(os.environ, {}, clear=True), patch.object(
                telegram, 'Bot', side_effect=AssertionError('Import must not start the bot')):
            module = importlib.import_module('main')
        self.assertTrue(callable(module.main))
        self.assertTrue(callable(module.echo))
        self.assertIsNone(module.update_id)


if __name__ == '__main__':
    unittest.main()
