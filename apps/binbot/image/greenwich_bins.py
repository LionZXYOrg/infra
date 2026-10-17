from __future__ import annotations

import datetime as dt
import html
import json
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlencode

import requests


CHECKER_URL = (
    "https://www.royalgreenwich.gov.uk/info/200171/"
    "recycling_and_rubbish/100/find_your_bin_collection_day"
)
AUTOCOMPLETE_URL = (
    "https://www.royalgreenwich.gov.uk/site/custom_scripts/"
    "apps/waste-collection/source.php"
)
LOOKUP_URL = (
    "https://www.royalgreenwich.gov.uk/site/custom_scripts/"
    "apps/waste-collection/ajax-response-uprn.php"
)
BANK_HOLIDAY_URL = (
    "https://www.royalgreenwich.gov.uk/recycling-and-rubbish/"
    "bins-and-collections/bank-holiday-collection-dates"
)

WEEKDAY_INDEX = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

MONTH_INDEX = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

# The 2026 Royal Greenwich black-top calendar starts Week A on 2025-12-29
# and Week B on 2026-01-05.
BLACK_WEEK_ANCHORS = {
    "week a": dt.date(2025, 12, 29),
    "week b": dt.date(2026, 1, 5),
}


@dataclass(frozen=True)
class AddressSchedule:
    address: str
    day: str
    frequency: str

    @property
    def weekday(self) -> int:
        return WEEKDAY_INDEX[self.day.lower()]


@dataclass(frozen=True)
class Collection:
    date: dt.date
    bins: tuple[str, ...]
    original_date: dt.date | None = None

    @property
    def was_revised(self) -> bool:
        return self.original_date is not None and self.original_date != self.date


def fetch_schedule(address: str, timeout: int = 20) -> AddressSchedule:
    """Fetch the collection weekday and black-top week from Royal Greenwich."""
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "greenwich-bin-reminder/1.0",
            "Referer": CHECKER_URL,
        }
    )

    options = session.get(
        f"{AUTOCOMPLETE_URL}?{urlencode({'term': address})}",
        timeout=timeout,
    )
    options.raise_for_status()
    matches = options.json()
    if address not in matches:
        close = ", ".join(matches[:5]) if matches else "no matches"
        raise ValueError(f"Address was not found in Royal Greenwich autocomplete: {close}")

    response = session.post(
        LOOKUP_URL,
        data={"address": address},
        timeout=timeout,
    )
    response.raise_for_status()
    text = response.text.strip()

    if text in {"ADDRESS_NOT_FOUND", "ADDRESS_NOT_SET"}:
        raise ValueError(f"Royal Greenwich lookup failed: {text}")

    payload = response.json()
    full_address = " - ".join(
        part
        for part in [
            payload.get("sao", ""),
            payload.get("pao", ""),
            payload.get("streetName", ""),
            payload.get("postTown", ""),
            payload.get("Postcode", ""),
        ]
        if part
    )
    return AddressSchedule(
        address=full_address,
        day=payload["Day"],
        frequency=payload["Frequency"],
    )


def fetch_bank_holiday_revisions(year: int, timeout: int = 20) -> dict[dt.date, dt.date]:
    """Return usual-date to revised-date mappings from the council page."""
    response = requests.get(
        BANK_HOLIDAY_URL,
        headers={"User-Agent": "greenwich-bin-reminder/1.0"},
        timeout=timeout,
    )
    response.raise_for_status()
    text = html.unescape(_strip_tags(response.text))
    return _parse_revisions(text, year)


def upcoming_collections(
    schedule: AddressSchedule,
    today: dt.date | None = None,
    count: int = 6,
    revisions: dict[dt.date, dt.date] | None = None,
) -> list[Collection]:
    today = today or dt.date.today()
    revisions = revisions or {}
    collections: list[Collection] = []

    for usual_date in _candidate_collection_days(schedule.weekday, today, count * 4):
        bins = ["green top", "blue top"]
        frequency = schedule.frequency.lower()

        if frequency == "weekly" or _is_black_collection_week(usual_date, frequency):
            bins.append("black top")

        collection_date = revisions.get(usual_date, usual_date)
        if collection_date >= today:
            collections.append(
                Collection(
                    date=collection_date,
                    bins=tuple(bins),
                    original_date=usual_date if collection_date != usual_date else None,
                )
            )

        if len(collections) >= count:
            return collections

    return collections


def collections_due_on(
    schedule: AddressSchedule,
    target_date: dt.date,
    revisions: dict[dt.date, dt.date] | None = None,
) -> list[Collection]:
    start = target_date - dt.timedelta(days=7)
    return [
        collection
        for collection in upcoming_collections(schedule, start, count=14, revisions=revisions)
        if collection.date == target_date
    ]


def format_collection(collection: Collection) -> str:
    bins = ", ".join(collection.bins)
    date_text = _format_date(collection.date)
    revised = ""
    if collection.was_revised:
        revised = f" (moved from {_format_date(collection.original_date)})"
    return f"{date_text}: {bins}{revised}"


def _candidate_collection_days(
    weekday: int,
    start: dt.date,
    limit: int,
) -> Iterable[dt.date]:
    days_until = (weekday - start.weekday()) % 7
    first = start + dt.timedelta(days=days_until)
    for offset in range(limit):
        yield first + dt.timedelta(days=7 * offset)


def _format_date(value: dt.date) -> str:
    return f"{value.strftime('%A')} {value.day} {value.strftime('%B')} {value.year}"


def _is_black_collection_week(collection_day: dt.date, frequency: str) -> bool:
    anchor = BLACK_WEEK_ANCHORS.get(frequency)
    if not anchor:
        return False

    start_of_week = collection_day - dt.timedelta(days=collection_day.weekday())
    return (start_of_week - anchor).days % 14 == 0


def _strip_tags(markup: str) -> str:
    return re.sub(r"<[^>]+>", " ", markup)


def _parse_revisions(text: str, default_year: int) -> dict[dt.date, dt.date]:
    compact = re.sub(r"\s+", " ", text)
    token_pattern = re.compile(
        r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+"
        r"(\d{1,2})\s+([A-Za-z]+)(?:\s+\(bank holiday\))?",
        re.IGNORECASE,
    )

    tokens = list(token_pattern.finditer(compact))
    revisions: dict[dt.date, dt.date] = {}

    for usual, revised in zip(tokens[0::2], tokens[1::2]):
        from_day, from_month = int(usual.group(2)), usual.group(3).lower()
        to_day, to_month = int(revised.group(2)), revised.group(3).lower()
        if from_month not in MONTH_INDEX or to_month not in MONTH_INDEX:
            continue

        from_date = dt.date(default_year, MONTH_INDEX[from_month], from_day)
        to_year = default_year
        if MONTH_INDEX[to_month] < MONTH_INDEX[from_month]:
            to_year += 1
        to_date = dt.date(to_year, MONTH_INDEX[to_month], to_day)
        revisions[from_date] = to_date

    return revisions


def schedule_to_json(schedule: AddressSchedule) -> str:
    return json.dumps(
        {"address": schedule.address, "day": schedule.day, "frequency": schedule.frequency},
        ensure_ascii=False,
        indent=2,
    )
