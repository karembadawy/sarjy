# -*- coding: utf-8 -*-
"""Turning the brain's date and time strings into real, timezone-aware moments.

Time is the hidden hard part of this phase. The server runs in UTC, the user lives in
Africa/Cairo, and "بكرة" means a calendar day in *their* zone — so every date here is
resolved against the user's local today, never against `datetime.utcnow()`. The system
prompt is told the local date explicitly (see brain.py), and these parsers are the second
half of that contract: whatever the model produces, it lands in the right zone or is
rejected with a sentence the model can speak.

Nothing here talks to a network or a database, so it is all unit-tested.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class WhenError(ValueError):
    """The model produced a date or time we cannot honestly interpret."""


def zone(name: str) -> ZoneInfo:
    """A timezone by IANA name, falling back to UTC rather than raising into a tool call."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


# The words a model reaches for when it has been told today's date but still answers in
# English shorthand. Everything else must be an ISO date.
_RELATIVE_DAYS = {
    "today": 0,
    "tonight": 0,
    "tomorrow": 1,
    "day after tomorrow": 2,
    "yesterday": -1,
}


def parse_day(value: str | None, today: date) -> date:
    """"2026-08-18" | "tomorrow" | "" → a calendar date in the user's zone."""
    text = (value or "").strip().lower()
    if not text:
        return today
    if text in _RELATIVE_DAYS:
        return today + timedelta(days=_RELATIVE_DAYS[text])
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise WhenError(
            f"I could not read {value!r} as a date. Use YYYY-MM-DD, or 'today'/'tomorrow'."
        ) from exc


def parse_moment(value: str, tz: ZoneInfo) -> datetime:
    """An ISO datetime from the model → an aware datetime.

    A string that carries its own offset is honoured as written. One that does not — which
    is what a model normally produces, "2026-08-18T17:00" — is read as local time in the
    user's zone. Reading it as UTC instead is the classic way to book an appointment three
    hours out, so the default is stated here rather than inherited from the platform.
    """
    text = (value or "").strip()
    if not text:
        raise WhenError("I need a date and time for the booking.")
    # "2026-08-18 17:00" is as common from a model as the T-separated form.
    text = text.replace(" ", "T", 1) if " " in text and "T" not in text else text
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise WhenError(
            f"I could not read {value!r} as a date and time. Use YYYY-MM-DDTHH:MM."
        ) from exc
    return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment


def spoken_clock(moment: datetime) -> str:
    """"5:15 PM" — a form the brain can read out loud without inventing a 24-hour clock."""
    return moment.strftime("%-I:%M %p")


def spoken_datetime(moment: datetime) -> str:
    """"Tuesday 18 August 2026 at 5:15 PM" — everything the confirmation needs to name."""
    return f"{moment:%A %-d %B %Y} at {spoken_clock(moment)}"
