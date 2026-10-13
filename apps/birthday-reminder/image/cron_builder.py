import os

from common import parse_birthdays


def print_birthday_as_cron():
    rootdir = os.path.abspath(os.path.dirname(__file__))
    birthdays = parse_birthdays()
    for birthday_index in range(len(birthdays)):
        birthday_time = birthdays[birthday_index].time
        cron_time = '0 7 ' + str(birthday_time.day) + ' ' + str(birthday_time.month) + ' *'
        cron_cmd = 'python ' + rootdir + '/telegram_send.py ' + str(birthday_index)
        print(cron_time + ' ' + cron_cmd)


if __name__ == "__main__":
    print_birthday_as_cron()
