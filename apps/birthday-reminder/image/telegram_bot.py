import locale

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from common import parse_birthdays
from secret import TG_TOKEN


async def getall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    birthdays = parse_birthdays()
    birthdays.sort(key=lambda x: x.time)
    lines = str()
    for birthday_index in range(len(birthdays)):
        birthday = birthdays[birthday_index]
        lines += str(birthday_index + 1) + '\. '
        lines += birthday.name + ' \- '
        lines += '*' + birthday.time.strftime('%d %B') + '*\n'

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=lines,
        reply_to_message_id=update.effective_message.message_id,
        parse_mode='MarkdownV2'
    )


if __name__ == "__main__":
    locale.setlocale(locale.LC_ALL, 'ru_RU.UTF-8')
    application = ApplicationBuilder().token(TG_TOKEN).build()

    start_handler = CommandHandler('getall', getall)
    application.add_handler(start_handler)

    application.run_polling()
