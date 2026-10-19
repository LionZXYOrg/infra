import telegram
from telegram.error import NetworkError, Unauthorized
from time import sleep
import os

update_id = None


def main():
    global update_id
    bot = telegram.Bot(os.environ['TELEGRAM_TOKEN'])

    print("Run psybot")
    # get the first pending update_id, this is so we can skip over it in case
    # we get an "Unauthorized" exception.
    try:
        update_id = bot.get_updates()[0].update_id
    except IndexError:
        update_id = None
    while True:
        try:
            echo(bot)
        except NetworkError:
            sleep(1)
        except Unauthorized:
            # The user has removed or blocked the bot.
            update_id += 1


def echo(bot):
    global update_id
    # Request updates after the last update_id
    for update in bot.get_updates(offset=update_id, timeout=10):
        update_id = update.update_id + 1

        if update.message:
            input_msg = update.message

            if input_msg.text.startswith('/start'):
                bot.send_message(chat_id=input_msg.chat.id,
                                 text='Привет. Сюда ты можешь писать вопросы и они полностью анонимно будут '
                                      'перенаправлены @akpsy')
                continue

            bot.send_message(chat_id=203155380,
                             text=input_msg.text)
            bot.send_message(chat_id=input_msg.chat.id,
                             text='Ваш вопрос анонимно перенаправлен @akpsy')


if __name__ == '__main__':
    main()
