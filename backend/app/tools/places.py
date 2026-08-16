# -*- coding: utf-8 -*-
"""City → coordinates, country and timezone, via Open-Meteo's keyless geocoder (D-022).

Two tools need the same answer (weather and prayer times) and the whole app needs a third
thing from it — which timezone "بكرة" is tomorrow *in* — so the lookup lives here once and
is memoised for the life of the process. A city's coordinates do not change; re-asking on
every turn would only add a network round trip to the latency budget (D-049 counts those).

The geocoder is queried in English first. That is not a stylistic preference, it is a
correctness one: `اسكندرية` resolves to Alexandretta in **Syria**, while `Alexandria`
resolves to Egypt. The tool declarations therefore ask the brain for an English city name,
and the Arabic query is only a fallback for names the English index does not carry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("sarjy.tools.places")

GEOCODER_URL = "https://geocoding-api.open-meteo.com/v1/search"
TIMEOUT_S = 8.0

# Prayer calculation method by the country of the city being asked about — NOT by persona.
# A Cairene asking about Riyadh wants the Riyadh mosque's Asr, not Cairo's (refines D-022).
PRAYER_METHOD_BY_COUNTRY = {
    "EG": 5,  # Egyptian General Authority of Survey
    "SA": 4,  # Umm al-Qura University, Makkah
}
DEFAULT_PRAYER_METHOD = 5


@dataclass(frozen=True)
class Place:
    name: str
    latitude: float
    longitude: float
    country: str
    country_code: str
    timezone: str

    @property
    def prayer_method(self) -> int:
        return PRAYER_METHOD_BY_COUNTRY.get(self.country_code.upper(), DEFAULT_PRAYER_METHOD)


class PlaceNotFound(LookupError):
    """The geocoder has never heard of this city."""


_CACHE: dict[str, Place] = {}


def _query(client: httpx.Client, city: str, language: str) -> Place | None:
    response = client.get(
        GEOCODER_URL,
        params={"name": city, "count": 1, "language": language, "format": "json"},
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    hits = response.json().get("results") or []
    if not hits:
        return None
    hit = hits[0]
    return Place(
        name=hit.get("name") or city,
        latitude=float(hit["latitude"]),
        longitude=float(hit["longitude"]),
        country=hit.get("country") or "",
        country_code=(hit.get("country_code") or "").upper(),
        timezone=hit.get("timezone") or "UTC",
    )


def geocode(city: str) -> Place:
    """Resolve a city name. Raises PlaceNotFound rather than guessing at coordinates."""
    key = (city or "").strip().lower()
    if not key:
        raise PlaceNotFound("no city given")
    if key in _CACHE:
        return _CACHE[key]

    with httpx.Client() as client:
        place = _query(client, city, "en") or _query(client, city, "ar")

    if place is None:
        raise PlaceNotFound(city)

    _CACHE[key] = place
    log.info(
        "places: %r → %s, %s (%.4f, %.4f) tz %s method %d",
        city,
        place.name,
        place.country,
        place.latitude,
        place.longitude,
        place.timezone,
        place.prayer_method,
    )
    return place
