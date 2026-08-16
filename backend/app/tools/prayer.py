# -*- coding: utf-8 -*-
"""`get_prayer_times` — Aladhan timings, the API the whole deep dive is justified by (D-007).

Two details are the difference between "an API call" and "the right answer":

1. **Coordinates come from Open-Meteo, not from Aladhan's own city lookup.** Asked for
   Alexandria by name, Aladhan answers with `meta.latitude 8.8888888, longitude 7.7777777`
   — a placeholder in the Gulf of Guinea. It still returns Cairo-ish timings via the
   timezone, but a tool that reports coordinates it did not use is a tool nobody can debug.
   `/timings?latitude&longitude` with a geocode we control has neither problem (D-022).

2. **The calculation method follows the country of the city being asked about**, not the
   persona: Egypt → 5 (Egyptian General Authority of Survey), Saudi Arabia → 4 (Umm
   al-Qura), everywhere else → 5. Method 5 is the Asr an Egyptian sees on television and in
   the mosque timetable, and matching it to the minute is the entire point of the
   "بكرة بعد العصر" demo. A Cairene asking about Riyadh gets Riyadh's authority, because
   they are asking about a prayer that will be called in Riyadh.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

from .places import geocode

log = logging.getLogger("sarjy.tools.prayer")

TIMINGS_URL = "https://api.aladhan.com/v1/timings"
TIMEOUT_S = 8.0

# The five daily prayers, in order. Aladhan also returns Sunrise, Imsak, Midnight and the
# night thirds; none of them belong in a spoken answer to "امتى العصر".
PRAYERS = ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha")


def get_prayer_times(city: str, day: date) -> dict:
    """The five prayer times for one city on one day, in that city's local clock."""
    place = geocode(city)

    with httpx.Client() as client:
        response = client.get(
            f"{TIMINGS_URL}/{day:%d-%m-%Y}",
            params={
                "latitude": place.latitude,
                "longitude": place.longitude,
                "method": place.prayer_method,
            },
            timeout=TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json().get("data") or {}

    timings = data.get("timings") or {}
    if not all(name in timings for name in PRAYERS):
        return {"error": "The prayer-times service did not return a full day of timings."}

    method = (data.get("meta") or {}).get("method") or {}
    return {
        "city": place.name,
        "country": place.country,
        "date": day.isoformat(),
        "timezone": place.timezone,
        "method": method.get("name") or f"method {place.prayer_method}",
        # Aladhan appends the zone to some timings ("16:43 (EET)"); the clock is the answer.
        **{name.lower(): timings[name].split(" ")[0] for name in PRAYERS},
    }
