# -*- coding: utf-8 -*-
"""The four tools of product.md §8, and the one place the brain reaches them.

    get_weather · get_prayer_times · create_booking · list_bookings

Declarations are written in English with typed parameters, because that is what the model
reads. The implementations return small dicts meant to be *spoken*, and every failure —
a city the geocoder does not know, a date that will not parse, an API that is down —
becomes one short English sentence under an `error` key. The brain can speak around a
sentence; it cannot speak around a stack trace, and product.md forbids showing one anyway.

    run_tool(...) never raises. That is the contract, and the loop in brain.py depends on it.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from google.genai import types
from sqlalchemy.orm import Session

from . import bookings as bookings_tool
from . import prayer as prayer_tool
from . import weather as weather_tool
from .places import PlaceNotFound
from .when import WhenError, parse_day, parse_moment

log = logging.getLogger("sarjy.tools")

# One turn may not spend forever calling tools. Four rounds is enough for the deepest real
# chain the product has — prayer times, then a booking, then a read-back — with slack, and
# it bounds both the latency and the Gemini spend of a turn that goes wrong.
MAX_TOOL_ROUNDS = 4


@dataclass
class ToolContext:
    """Everything a tool needs that is about *this user, right now*."""

    db: Session
    user_id: uuid.UUID
    now: datetime  # timezone-aware, already in the user's zone
    tz: ZoneInfo
    default_city: str

    @property
    def today(self):
        return self.now.date()


# --------------------------------------------------------------------------------------
# Declarations — what the model sees
# --------------------------------------------------------------------------------------

_CITY_PARAM = {
    "type": "string",
    "description": (
        "City name in ENGLISH, e.g. 'Alexandria', 'Cairo', 'Riyadh'. Always translate an "
        "Arabic city name to English before calling: the geocoder resolves 'اسكندرية' to a "
        "town in Syria. Omit to use the city Sarjy already knows for this person."
    ),
}

_DAY_PARAM = {
    "type": "string",
    "description": (
        "The day, as YYYY-MM-DD. 'today' and 'tomorrow' are also accepted. Today's date in "
        "the user's timezone is given to you in the system prompt — use it."
    ),
}

DECLARATIONS = [
    types.FunctionDeclaration(
        name="get_weather",
        description=(
            "Look up the weather forecast for a city on a given day. Use this whenever the "
            "user asks about weather, temperature, rain or what to wear. Never guess the "
            "weather."
        ),
        parameters={
            "type": "object",
            "properties": {"city": _CITY_PARAM, "day": _DAY_PARAM},
            "required": [],
        },
    ),
    types.FunctionDeclaration(
        name="get_prayer_times",
        description=(
            "Look up the five Islamic prayer times (Fajr, Dhuhr, Asr, Maghrib, Isha) for a "
            "city on a given day, in that city's local clock. Use this when the user asks "
            "for a prayer time, AND whenever they anchor an appointment to a prayer — "
            "'بعد العصر', 'قبل المغرب', 'after Maghrib' — so you can resolve it to a real "
            "clock time before booking. Never guess a prayer time."
        ),
        parameters={
            "type": "object",
            "properties": {"city": _CITY_PARAM, "date": _DAY_PARAM},
            "required": [],
        },
    ),
    types.FunctionDeclaration(
        name="create_booking",
        description=(
            "Create a real appointment for this user and save it. Only call this once you "
            "know both what the appointment is for and its exact clock time. If the user "
            "anchored the time to a prayer, call get_prayer_times first and resolve it. "
            "After it succeeds, confirm out loud and say the resolved day and time."
        ),
        parameters={
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "description": (
                        "What the appointment is for, in the user's own words — 'دكتور', "
                        "'meeting with Ahmed', 'haircut'."
                    ),
                },
                "datetime_iso": {
                    "type": "string",
                    "description": (
                        "The appointment time as YYYY-MM-DDTHH:MM in the user's LOCAL "
                        "timezone (do not convert to UTC, do not add an offset)."
                    ),
                },
                "notes": {
                    "type": "string",
                    "description": "Anything extra the user mentioned. Optional.",
                },
            },
            "required": ["service", "datetime_iso"],
        },
    ),
    types.FunctionDeclaration(
        name="list_bookings",
        description=(
            "List this user's upcoming appointments, soonest first. Use it when they ask "
            "what they have booked, or to check before making a new booking."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
    ),
]


def tool_config() -> types.Tool:
    return types.Tool(function_declarations=DECLARATIONS)


NAMES = [declaration.name for declaration in DECLARATIONS]


# --------------------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------------------


def _get_weather(context: ToolContext, args: dict) -> dict:
    day = parse_day(args.get("day"), context.today)
    return weather_tool.get_weather(args.get("city") or context.default_city, day, context.today)


def _get_prayer_times(context: ToolContext, args: dict) -> dict:
    day = parse_day(args.get("date") or args.get("day"), context.today)
    return prayer_tool.get_prayer_times(args.get("city") or context.default_city, day)


def _create_booking(context: ToolContext, args: dict) -> dict:
    when = parse_moment(args.get("datetime_iso") or args.get("datetime") or "", context.tz)
    return bookings_tool.create_booking(
        context.db,
        context.user_id,
        service=args.get("service") or "",
        when=when,
        tz=context.tz,
        notes=args.get("notes"),
    )


def _list_bookings(context: ToolContext, args: dict) -> dict:
    return bookings_tool.list_bookings(context.db, context.user_id, context.now, context.tz)


HANDLERS = {
    "get_weather": _get_weather,
    "get_prayer_times": _get_prayer_times,
    "create_booking": _create_booking,
    "list_bookings": _list_bookings,
}


def run_tool(name: str, args: dict, context: ToolContext) -> dict:
    """Execute one tool call. Always returns a dict; never raises, never leaks a traceback."""
    handler = HANDLERS.get(name)
    if handler is None:
        log.warning("tool: unknown tool %r", name)
        return {"error": f"There is no tool called {name}."}

    args = dict(args or {})
    log.info("tool → %s(%s)", name, args)
    try:
        result = handler(context, args)
    except PlaceNotFound as exc:
        result = {"error": f"I could not find a city called '{exc}'. Ask the user which city."}
    except WhenError as exc:
        result = {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — one bad tool must not end the turn
        log.exception("tool: %s failed", name)
        result = {"error": f"The {name.replace('_', ' ')} service is not answering right now."}
    log.info("tool ← %s: %s", name, result)
    return result
