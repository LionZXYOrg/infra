import contextlib
import datetime
import io
import types
import sys
import unittest
from unittest.mock import patch
sys.modules['secret'] = types.SimpleNamespace(TG_TOKEN='fixture', GIPHY_TOKEN='fixture', CHAT_ID=1)
import common
import cron_builder
import telegram_bot
import telegram_send

class BirthdayTests(unittest.TestCase):
    def test_russian_locale_used_by_reminders_is_available(self):
        import locale
        locale.setlocale(locale.LC_ALL, "ru_RU.UTF-8")

    def test_restored_csv_preserves_names_dates_and_optional_nicknames(self):
        with patch.object(common.Path, 'read_text', return_value='01 January 2000,Example,example\n02 February 2000,Other\n'):
            entries = common.parse_birthdays()
        self.assertEqual(len(entries), 2)
        self.assertEqual((entries[0].time.month, entries[0].time.day), (1, 1))
        self.assertEqual(entries[0].nickname, 'example')
        self.assertIsNone(entries[1].nickname)

    def test_cron_is_generated_from_restored_data_without_sending_messages(self):
        record = common.Birthday(datetime.datetime(2000, 2, 3), 'Example', None)
        output = io.StringIO()
        with patch.object(cron_builder, 'parse_birthdays', return_value=[record]), contextlib.redirect_stdout(output):
            cron_builder.print_birthday_as_cron()
        self.assertIn('0 7 3 2 *', output.getvalue())
        self.assertIn('/telegram_send.py 0', output.getvalue())
