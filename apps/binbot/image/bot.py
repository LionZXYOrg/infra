from __future__ import annotations

import datetime as dt
import asyncio
import json
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from greenwich_bins import (
    AddressSchedule,
    collections_due_on,
    fetch_bank_holiday_revisions,
    fetch_schedule,
    schedule_to_json,
    upcoming_collections,
)


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
LOGGER = logging.getLogger("greenwich-bin-bot")

ADDRESS = os.environ['ADDRESS']
TIMEZONE = ZoneInfo(os.getenv("TIMEZONE", "Europe/London"))
REMINDER_TIME = os.getenv("REMINDER_TIME", "18:00")
REMIND_DAYS_BEFORE = int(os.getenv("REMIND_DAYS_BEFORE", "1"))
SUBSCRIBERS_FILE = Path(os.getenv("SUBSCRIBERS_FILE", "subscribers.json"))
SCHEDULE_CACHE_FILE = Path(os.getenv("SCHEDULE_CACHE_FILE", "schedule_cache.json"))

BIN_LABELS = {
    "green top": "зеленый бак: еда и садовые отходы",
    "blue top": "синий бак: переработка",
    "black top": "черный бак: общий мусор",
}

WEEKDAY_LABELS = {
    0: "понедельник",
    1: "вторник",
    2: "среда",
    3: "четверг",
    4: "пятница",
    5: "субботу",
    6: "воскресенье",
}

MONTH_LABELS = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


def load_subscribers() -> set[int]:
    if not SUBSCRIBERS_FILE.exists():
        return set()
    return set(json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8")))


def save_subscribers(chat_ids: set[int]) -> None:
    SUBSCRIBERS_FILE.write_text(
        json.dumps(sorted(chat_ids), indent=2),
        encoding="utf-8",
    )


def load_cached_schedule() -> AddressSchedule | None:
    if not SCHEDULE_CACHE_FILE.exists():
        return None
    payload = json.loads(SCHEDULE_CACHE_FILE.read_text(encoding="utf-8"))
    return AddressSchedule(
        address=payload["address"],
        day=payload["day"],
        frequency=payload["frequency"],
    )


def save_cached_schedule(schedule: AddressSchedule) -> None:
    SCHEDULE_CACHE_FILE.write_text(schedule_to_json(schedule), encoding="utf-8")


def get_schedule(refresh: bool = False) -> AddressSchedule:
    if not refresh:
        cached = load_cached_schedule()
        if cached:
            return cached

    schedule = fetch_schedule(ADDRESS)
    save_cached_schedule(schedule)
    return schedule


def get_revisions(today: dt.date) -> dict[dt.date, dt.date]:
    try:
        return fetch_bank_holiday_revisions(today.year)
    except Exception:
        LOGGER.warning("Could not load bank holiday revisions", exc_info=True)
        return {}


def format_date_ru(value: dt.date, with_weekday: bool = True) -> str:
    weekday = WEEKDAY_LABELS[value.weekday()]
    month = MONTH_LABELS[value.month]
    if not with_weekday:
        return f"{value.day} {month} {value.year}"
    return f"{weekday}, {value.day} {month} {value.year}"


def relative_date_label(value: dt.date, today: dt.date) -> str:
    if value == today:
        return "сегодня"
    if value == today + dt.timedelta(days=1):
        return "завтра"
    return format_date_ru(value)


def format_bins_ru(bins: tuple[str, ...]) -> list[str]:
    return [BIN_LABELS.get(bin_name, bin_name) for bin_name in bins]


def format_collection_ru(collection, today: dt.date) -> str:
    date_text = format_date_ru(collection.date)
    bins = "; ".join(format_bins_ru(collection.bins))
    line = f"{date_text}: {bins}"
    if collection.was_revised:
        line += f" (⚠️ перенос с {format_date_ru(collection.original_date)})"
    return line


def next_collections_message(schedule: AddressSchedule) -> str:
    today = dt.datetime.now(TIMEZONE).date()
    revisions = get_revisions(today)
    collections = upcoming_collections(schedule, today=today, revisions=revisions)
    lines = [
        "Ближайшие вывозы мусора",
        "",
        f"Обычный день: {WEEKDAY_LABELS[schedule.weekday]}",
        f"Черный бак: {schedule.frequency}",
        "",
        "Что и когда выставлять:",
    ]
    lines.extend(
        f"- {format_collection_ru(collection, today)}"
        for collection in collections
    )
    lines.extend(
        [
            "",
            "Баки нужно выставить к 6:00 в день вывоза.",
            "Праздничные переносы проверяются по сайту Royal Greenwich автоматически.",
        ]
    )
    return "\n".join(lines)


def reminder_message(schedule: AddressSchedule, due) -> str:
    today = dt.datetime.now(TIMEZONE).date()
    collection = due[0]
    when = relative_date_label(collection.date, today)
    bins = "\n".join(f"- {bin_label}" for bin_label in format_bins_ru(collection.bins))
    moved = ""
    if collection.was_revised:
        moved = f"\n\n⚠️ Перенос: {format_date_ru(collection.original_date, with_weekday=False)}."

    return (
        f"Напоминание: {when} вывоз мусора\n\n"
        f"Дата: {format_date_ru(collection.date)}\n"
        f"Нужно выставить:\n{bins}\n"
        f"{moved}\n\n"
        "Выставить баки к 6:00 утра в день вывоза."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return

    chat_ids = load_subscribers()
    chat_ids.add(update.effective_chat.id)
    save_subscribers(chat_ids)

    schedule = get_schedule(refresh=True)
    await update.message.reply_text(
        "Готово, я буду присылать напоминания.\n\n"
        + next_collections_message(schedule)
    )


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or not update.message:
        return

    chat_ids = load_subscribers()
    chat_ids.discard(update.effective_chat.id)
    save_subscribers(chat_ids)
    await update.message.reply_text("Ок, напоминания для этого чата выключены.")


async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    await update.message.reply_text(next_collections_message(get_schedule()))


async def check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    schedule = get_schedule(refresh=True)
    await update.message.reply_text(
        "Обновил данные с Royal Greenwich.\n\n" + next_collections_message(schedule)
    )


async def send_due_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    today = dt.datetime.now(TIMEZONE).date()
    target_date = today + dt.timedelta(days=REMIND_DAYS_BEFORE)
    schedule = get_schedule()
    due = collections_due_on(schedule, target_date, revisions=get_revisions(today))
    if not due:
        return

    message = reminder_message(schedule, due)

    for chat_id in load_subscribers():
        try:
            await context.bot.send_message(chat_id=chat_id, text=message)
        except Exception:
            LOGGER.warning("Could not send reminder to chat %s", chat_id, exc_info=True)


def parse_reminder_time(value: str) -> dt.time:
    hour_text, minute_text = value.split(":", maxsplit=1)
    return dt.time(int(hour_text), int(minute_text), tzinfo=TIMEZONE)


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in .env")

    asyncio.set_event_loop(asyncio.new_event_loop())

    application = Application.builder().token(token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("next", next_command))
    application.add_handler(CommandHandler("check", check))

    application.job_queue.run_daily(
        send_due_reminders,
        time=parse_reminder_time(REMINDER_TIME),
        name="daily-bin-reminder",
    )

    LOGGER.info("Starting bot for %s", ADDRESS)
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
