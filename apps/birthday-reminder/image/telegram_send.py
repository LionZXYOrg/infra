import asyncio
import locale
import sys

import requests
from telegram import Bot

from common import parse_birthdays
from secret import TG_TOKEN, GIPHY_TOKEN, CHAT_ID


def random_birthday_gif():
    gif = requests.get(
        url='https://api.giphy.com/v1/gifs/random',
        params={
            'api_key': GIPHY_TOKEN,
            'tag': 'birthday'
        }
    ).json()['data']
    return gif['images']['original']['url']


async def congratulations(number):
    locale.setlocale(locale.LC_ALL, 'ru_RU.UTF-8')
    birthday = parse_birthdays()[int(number)]
    bot = Bot(TG_TOKEN)
    birthday_text = "Сегодня *" + birthday.time.strftime('%d %B') \
                    + "*, а это значит что у кого\-то 👀 сегодня день рождения\!🎉\n" \
                      "Поздравляем, " + birthday.name
    if birthday.nickname is not None:
        birthday_text += " \(" + birthday.nickname.replace('_', '\_') + "\)"

    birthday_text += "\!"

    gif_url = random_birthday_gif()
    await bot.send_document(
        chat_id=CHAT_ID,
        document=gif_url,
        caption=birthday_text,
        parse_mode='MarkdownV2'
    )


if __name__ == "__main__":
    asyncio.run(congratulations(sys.argv[1]))
