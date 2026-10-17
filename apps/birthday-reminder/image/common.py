import os
from datetime import datetime
from pathlib import Path

import dateparser


class Birthday:
    def __init__(self, time: datetime, name, nickname):
        self.time = time
        self.name = name
        self.nickname = nickname


def parse_birthdays():
    rootdir = os.path.abspath(os.path.dirname(__file__))
    birthday_raw = Path(rootdir + '/birthday.csv').read_text().split('\n')
    birthday_rows = [row.split(',') for row in birthday_raw if len(row) >= 2]
    return [
        Birthday(
            time=dateparser.parse(row[0]),
            name=row[1].strip(),
            nickname=row[2].strip() if len(row) > 2 else None
        )
        for row in birthday_rows
    ]
