import datetime
import os
from pathlib import Path
import tempfile
import unittest
os.environ['ADDRESS'] = 'Example address'
import bot
from greenwich_bins import AddressSchedule

class StorageTests(unittest.TestCase):
    def test_subscribers_survive_a_process_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            bot.SUBSCRIBERS_FILE = Path(directory) / 'subscribers.json'
            self.assertEqual(bot.load_subscribers(), set())
            bot.save_subscribers({2, 1, 2})
            self.assertEqual(bot.load_subscribers(), {1, 2})

    def test_cache_round_trip_and_reminder_timezone(self):
        with tempfile.TemporaryDirectory() as directory:
            bot.SCHEDULE_CACHE_FILE = Path(directory) / 'schedule.json'
            expected = AddressSchedule(address='Example address', day='Monday', frequency='weekly')
            bot.save_cached_schedule(expected)
            self.assertEqual(bot.load_cached_schedule(), expected)
        self.assertEqual(bot.parse_reminder_time('18:00').hour, 18)
        self.assertEqual(str(bot.parse_reminder_time('18:00').tzinfo), 'Europe/London')
