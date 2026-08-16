# -*- coding: utf-8 -*-
"""`get_weather` — Open-Meteo forecast, returned small enough to be read out loud.

The brain reads whatever this returns and then *says* it, so the return value is shaped for
a sentence, not for a dashboard: one condition word, a high, a low, and the two extras a
person actually asks about (rain and wind). A wall of JSON here becomes a wall of speech.
"""

from __future__ import annotations

import logging
from datetime import date

import httpx

from .places import geocode

log = logging.getLogger("sarjy.tools.weather")

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT_S = 8.0

# Open-Meteo reports WMO weather codes. These are the English phrases the brain will
# translate into whichever language it is speaking — kept plain so it can.
WMO_CODES = {
    0: "clear sky",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light showers",
    81: "showers",
    82: "violent showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with hail",
}

# Open-Meteo's free forecast covers 16 days; anything past that is not a lookup, it is a guess.
MAX_FORECAST_DAYS = 14


def get_weather(city: str, day: date, today: date) -> dict:
    """Forecast for one city on one day. `today` is the user's local date, not the server's."""
    ahead = (day - today).days
    if ahead < 0:
        return {"error": f"I only have forecasts from today onwards, not for {day.isoformat()}."}
    if ahead > MAX_FORECAST_DAYS:
        return {"error": f"The forecast only reaches {MAX_FORECAST_DAYS} days ahead."}

    place = geocode(city)

    with httpx.Client() as client:
        response = client.get(
            FORECAST_URL,
            params={
                "latitude": place.latitude,
                "longitude": place.longitude,
                "daily": (
                    "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_probability_max,wind_speed_10m_max"
                ),
                "timezone": place.timezone,
                "start_date": day.isoformat(),
                "end_date": day.isoformat(),
            },
            timeout=TIMEOUT_S,
        )
        response.raise_for_status()
        daily = response.json().get("daily") or {}

    def first(name: str):
        values = daily.get(name) or []
        return values[0] if values else None

    code = first("weather_code")
    return {
        "city": place.name,
        "country": place.country,
        "date": day.isoformat(),
        "conditions": WMO_CODES.get(code, "mixed weather"),
        "high_celsius": _round(first("temperature_2m_max")),
        "low_celsius": _round(first("temperature_2m_min")),
        "chance_of_rain_percent": first("precipitation_probability_max"),
        "wind_kmh": _round(first("wind_speed_10m_max")),
    }


def _round(value):
    return round(value) if isinstance(value, (int, float)) else None
