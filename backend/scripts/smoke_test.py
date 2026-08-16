#!/usr/bin/env python3
"""Phase 0 credential smoke test for Sarjy.

Verifies every service Sarjy depends on with a zero- or near-zero-cost call and prints
one line per service:

    PASS     the service answered and the credential works
    FAIL     something is wrong  (reason + a one-line fix hint)
    PENDING  the .env variable is still empty  (nothing was called)

Cost discipline (CLAUDE.md golden rule 2): only list/auth/read endpoints are touched.
This script NEVER generates speech, so it never consumes ElevenLabs or Gemini TTS quota.

Run:  cd backend && python scripts/smoke_test.py
Exit: 0 = everything PASS · 1 = at least one FAIL · 2 = no FAIL but something PENDING
"""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

try:
    import httpx
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover - setup guidance, not a runtime path
    sys.exit(
        "Dependencies missing. Run:\n"
        "  cd backend && python -m venv venv && source venv/bin/activate\n"
        "  pip install -r requirements.txt"
    )

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_DIR / ".env"
TIMEOUT = 20.0

# Aladhan calculation method 5 = Egyptian General Authority of Survey (correct for Egypt).
PRAYER_METHOD = 5
DEFAULT_COUNTRY = "Egypt"

PASS, FAIL, PENDING = "PASS", "FAIL", "PENDING"

_COLOR = sys.stdout.isatty()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def _paint(status: str) -> str:
    return {
        PASS: _c("PASS   ", "32;1"),
        FAIL: _c("FAIL   ", "31;1"),
        PENDING: _c("PENDING", "33;1"),
    }[status]


class Result:
    """Outcome of one service check."""

    def __init__(self, service: str, status: str, message: str = "", hint: str = ""):
        self.service = service
        self.status = status
        self.message = message
        self.hint = hint
        self.details: list[str] = []

    def detail(self, line: str) -> "Result":
        self.details.append(line)
        return self

    def show(self) -> None:
        print(f"  [{_paint(self.status)}] {self.service:<12} {self.message}")
        for line in self.details:
            print(f"                          {_c(line, '2')}")
        if self.hint:
            print(f"                          {_c('fix: ' + self.hint, '36')}")


def _http_error(exc: Exception) -> str:
    """Turn any httpx failure into one readable line."""
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text.strip().replace("\n", " ")
        try:
            # Providers nest the human-readable message differently, and sometimes twice
            # (ElevenLabs puts it under detail.message, Gemini under error.message).
            node = exc.response.json()
            for _ in range(3):
                if not isinstance(node, dict):
                    break
                nxt = node.get("message") or node.get("err_msg") or node.get("error") or node.get("detail")
                if nxt is None:
                    break
                node = nxt
            if isinstance(node, str):
                body = node
        except Exception:
            pass
        return f"HTTP {exc.response.status_code} — {body[:180]}"
    if isinstance(exc, httpx.TimeoutException):
        return f"timed out after {TIMEOUT:.0f}s"
    if isinstance(exc, httpx.RequestError):
        return f"network error — {exc.__class__.__name__}: {exc}"
    return f"{exc.__class__.__name__}: {exc}"


def _get(client: httpx.Client, url: str, **kwargs) -> dict:
    response = client.get(url, timeout=TIMEOUT, **kwargs)
    response.raise_for_status()
    return response.json()


# --------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------


def check_gemini(env: dict) -> Result:
    """List models (free) and print the Flash + TTS names available on this key."""
    key = env.get("GEMINI_API_KEY")
    if not key:
        return Result("Gemini", PENDING, "GEMINI_API_KEY is empty")

    models: list[dict] = []
    try:
        with httpx.Client(headers={"x-goog-api-key": key}) as client:
            url = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200"
            while url:
                payload = _get(client, url)
                models.extend(payload.get("models", []))
                token = payload.get("nextPageToken")
                url = (
                    "https://generativelanguage.googleapis.com/v1beta/models"
                    f"?pageSize=200&pageToken={token}"
                    if token
                    else None
                )
    except Exception as exc:  # noqa: BLE001 - every failure is reported, never raised
        return Result(
            "Gemini",
            FAIL,
            _http_error(exc),
            "check the key at aistudio.google.com/apikey; a 403 usually means the "
            "Generative Language API is disabled on that Google Cloud project",
        )

    def short(model: dict) -> str:
        return model.get("name", "").removeprefix("models/")

    names = [short(m) for m in models]
    tts = sorted(n for n in names if "tts" in n)
    flash = sorted(
        short(m)
        for m in models
        if "flash" in short(m)
        and "tts" not in short(m)
        and "generateContent" in m.get("supportedGenerationMethods", [])
    )

    result = Result("Gemini", PASS, f"key valid · {len(models)} models visible")

    # Only dump the catalogue while there is still a choice to make (golden rule 7:
    # the names must come from here, never from memory).
    if not env.get("GEMINI_MODEL") or not env.get("GEMINI_TTS_MODEL"):
        result.detail("Flash models (pick one for GEMINI_MODEL):")
        for name in flash or ["(none found)"]:
            result.detail(f"  · {name}")
        result.detail("TTS models (pick one for GEMINI_TTS_MODEL):")
        for name in tts or ["(none found)"]:
            result.detail(f"  · {name}")

    # If the user has already chosen models, confirm those exact names still exist.
    for var, chosen in (("GEMINI_MODEL", env.get("GEMINI_MODEL")), ("GEMINI_TTS_MODEL", env.get("GEMINI_TTS_MODEL"))):
        if not chosen:
            result.detail(f"{var} is still empty — fill it from the list above")
            continue
        if chosen.removeprefix("models/") in names:
            result.detail(f"{var}={chosen} ✓ available")
        else:
            result.status = FAIL
            result.message = f"{var}={chosen} is not available on this key"
            result.hint = f"replace {var} in backend/.env with one of the names listed above"
    return result


def check_deepgram(env: dict) -> Result:
    """Auth check via the free project-list endpoint, plus remaining signup credit."""
    key = env.get("DEEPGRAM_API_KEY")
    if not key:
        return Result("Deepgram", PENDING, "DEEPGRAM_API_KEY is empty")

    try:
        with httpx.Client(headers={"Authorization": f"Token {key}"}) as client:
            projects = _get(client, "https://api.deepgram.com/v1/projects").get("projects", [])
            result = Result("Deepgram", PASS, f"key valid · {len(projects)} project(s)")
            for project in projects[:3]:
                result.detail(f"project: {project.get('name', '?')}")
                # Balance is a nice-to-have; never let it fail the check.
                try:
                    balances = _get(
                        client,
                        f"https://api.deepgram.com/v1/projects/{project['project_id']}/balances",
                    ).get("balances", [])
                    for balance in balances:
                        result.detail(
                            f"  credit remaining: {balance.get('amount')} "
                            f"{balance.get('units', 'usd')}"
                        )
                except Exception:  # noqa: BLE001 - informational only
                    result.detail("  credit: not readable with this key's scopes")
            return result
    except Exception as exc:  # noqa: BLE001
        return Result(
            "Deepgram",
            FAIL,
            _http_error(exc),
            "create a fresh key at console.deepgram.com → API Keys (needs the "
            "'Member' or higher role) and paste it into DEEPGRAM_API_KEY",
        )


def check_elevenlabs(env: dict) -> Result:
    """Read subscription quota and voice list. Generates nothing — costs 0 characters."""
    key = env.get("ELEVENLABS_API_KEY")
    if not key:
        return Result("ElevenLabs", PENDING, "ELEVENLABS_API_KEY is empty")

    try:
        with httpx.Client(headers={"xi-api-key": key}) as client:
            sub = _get(client, "https://api.elevenlabs.io/v1/user/subscription")
            voices = _get(client, "https://api.elevenlabs.io/v1/voices").get("voices", [])
    except Exception as exc:  # noqa: BLE001
        return Result(
            "ElevenLabs",
            FAIL,
            _http_error(exc),
            "regenerate the key at elevenlabs.io → Profile → API key, and give it the "
            "'user: read' and 'voices: read' permissions",
        )

    used = sub.get("character_count", 0)
    limit = sub.get("character_limit", 0)
    remaining = max(limit - used, 0)
    result = Result(
        "ElevenLabs",
        PASS,
        f"key valid · tier '{sub.get('tier', '?')}' · {len(voices)} voices",
    )
    result.detail(f"free quota remaining: {remaining:,} of {limit:,} characters ({used:,} used)")
    result.detail("(this check generates no audio — 0 characters spent)")

    by_id = {v.get("voice_id"): v for v in voices}
    voice_vars = (
        "ELEVENLABS_VOICE_AR_EGYPTIAN_MALE",
        "ELEVENLABS_VOICE_AR_EGYPTIAN_FEMALE",
        "ELEVENLABS_VOICE_AR_GULF_MALE",
        "ELEVENLABS_VOICE_AR_GULF_FEMALE",
        "ELEVENLABS_VOICE_EN_MALE",
        "ELEVENLABS_VOICE_EN_FEMALE",
    )

    if any(not env.get(var) for var in voice_vars):
        # Print the account's voices so IDs can be copied from here instead of the dashboard.
        # Arabic first — those are the ones that matter for the deep dive.
        def sort_key(voice: dict) -> tuple:
            labels = voice.get("labels") or {}
            return (0 if str(labels.get("language", "")).startswith("ar") else 1, voice.get("name", ""))

        result.detail("voices in this account (copy an ID into the variables below):")
        for voice in sorted(voices, key=sort_key)[:40]:
            labels = voice.get("labels") or {}
            tags = ", ".join(
                str(labels[k])
                for k in ("language", "accent", "gender", "age", "descriptive", "use_case")
                if labels.get(k)
            )
            name = (voice.get("name") or "?").split(" - ")[0].split(" – ")[0]
            result.detail(f"  · {name:<18} {voice.get('voice_id')}  {tags}")
        if len(voices) > 40:
            result.detail(f"  · …and {len(voices) - 40} more")

    for var in voice_vars:
        voice_id = env.get(var)
        if not voice_id:
            result.detail(f"{var} is still empty — pick one of the IDs above")
        elif voice_id in by_id:
            result.detail(f"{var} ✓ '{by_id[voice_id].get('name')}'")
        else:
            result.status = FAIL
            result.message = f"{var}={voice_id} is not in this account's voice list"
            result.hint = (
                "open elevenlabs.io/app/voice-library, click 'Add to my voices' on the "
                f"voice you want, then copy its Voice ID into {var}"
            )
    return result


def check_supabase(env: dict) -> Result:
    """Connect to Postgres and SELECT 1."""
    url = env.get("DATABASE_URL")
    if not url:
        return Result("Supabase", PENDING, "DATABASE_URL is empty")
    if "[YOUR-PASSWORD]" in url or "[PASSWORD]" in url:
        return Result(
            "Supabase",
            FAIL,
            "DATABASE_URL still contains the [YOUR-PASSWORD] placeholder",
            "replace it with your database password (Project Settings → Database → "
            "Reset database password if you never saved it)",
        )

    # Classic paste error: the real password typed *inside* the template's square brackets.
    # Postgres then rejects a password that looks almost right, which reads as "wrong password".
    _creds, _, _host = url.partition("://")[2].rpartition("@")
    _password = _creds.partition(":")[2]
    if _password.startswith("[") and _password.endswith("]"):
        return Result(
            "Supabase",
            FAIL,
            "the password in DATABASE_URL is wrapped in square brackets",
            "delete the [ and ] around it — they are part of Supabase's placeholder, "
            "not of your password",
        )

    # Supabase's UI sometimes shows the legacy postgres:// scheme; SQLAlchemy wants postgresql://
    normalized = url.replace("postgres://", "postgresql://", 1) if url.startswith("postgres://") else url

    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(normalized, connect_args={"connect_timeout": 10})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            version = conn.execute(text("SHOW server_version")).scalar()
            database = conn.execute(text("SELECT current_database()")).scalar()
            tables = conn.execute(
                text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
            ).scalar()
        engine.dispose()
    except Exception as exc:  # noqa: BLE001
        reason = str(exc).strip().replace("\n", " ")[:200]
        hint = (
            "copy the Session pooler URI from Supabase → Connect (not the direct "
            "connection: it is IPv6-only and Cloud Run cannot reach it)"
        )
        lowered = reason.lower()
        if "password authentication failed" in lowered:
            hint = (
                "the password is wrong, or a special character in it needs percent-encoding "
                "(@ → %40, # → %23, : → %3A)"
            )
        elif "could not translate host name" in lowered or "name or service not known" in lowered:
            hint = "host is wrong or the Supabase project is paused — check the project dashboard"
        elif "timeout" in lowered or "timed out" in lowered:
            hint = (
                "likely the IPv6-only direct connection; use the Session pooler URI "
                "(port 5432, host contains 'pooler.supabase.com')"
            )
        return Result("Supabase", FAIL, reason, hint)

    result = Result("Supabase", PASS, f"connected · SELECT 1 ok · PostgreSQL {version}")
    result.detail(f"database '{database}' · {tables} table(s) in public schema")
    if "pooler.supabase.com" not in normalized:
        result.detail(
            "note: this is not a pooler URI — Cloud Run (Phase 3) needs the IPv4 Session pooler"
        )
    return result


def check_open_meteo(env: dict) -> Result:
    """Keyless. Geocode the default city, then read today's forecast."""
    city = env.get("DEFAULT_CITY") or "Alexandria"
    try:
        with httpx.Client() as client:
            geo = _get(
                client,
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "en", "format": "json"},
            )
            hits = geo.get("results") or []
            if not hits:
                return Result(
                    "Open-Meteo",
                    FAIL,
                    f"geocoder found no city named '{city}'",
                    "set DEFAULT_CITY in backend/.env to a name the geocoder knows, e.g. Alexandria",
                )
            place = hits[0]
            lat, lon = place["latitude"], place["longitude"]
            forecast = _get(
                client,
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "daily": "temperature_2m_max,temperature_2m_min",
                    "timezone": "auto",
                    "forecast_days": 1,
                },
            )
    except Exception as exc:  # noqa: BLE001
        return Result("Open-Meteo", FAIL, _http_error(exc), "keyless API — check your internet connection")

    daily = forecast.get("daily", {})
    unit = forecast.get("daily_units", {}).get("temperature_2m_max", "°C")
    result = Result("Open-Meteo", PASS, "reachable · no key needed")
    result.detail(
        f"{place.get('name')}, {place.get('country')} ({lat:.2f}, {lon:.2f}) "
        f"tz {forecast.get('timezone')}"
    )
    result.detail(
        f"today {daily.get('time', ['?'])[0]}: "
        f"{daily.get('temperature_2m_min', ['?'])[0]}–{daily.get('temperature_2m_max', ['?'])[0]}{unit}"
    )
    return result


def check_aladhan(env: dict) -> Result:
    """Keyless. Proof of life = today's Asr time for the default city."""
    city = env.get("DEFAULT_CITY") or "Alexandria"
    try:
        with httpx.Client() as client:
            payload = _get(
                client,
                f"https://api.aladhan.com/v1/timingsByCity/{date.today():%d-%m-%Y}",
                params={"city": city, "country": DEFAULT_COUNTRY, "method": PRAYER_METHOD},
            )
    except Exception as exc:  # noqa: BLE001
        return Result(
            "Aladhan",
            FAIL,
            _http_error(exc),
            f"keyless API — check the connection, or that '{city}' + '{DEFAULT_COUNTRY}' is a city it knows",
        )

    data = payload.get("data", {})
    timings = data.get("timings", {})
    if "Asr" not in timings:
        return Result(
            "Aladhan",
            FAIL,
            "response contained no prayer timings",
            "check the city/country pair sent to /v1/timingsByCity",
        )

    gregorian = data.get("date", {}).get("readable", "today")
    hijri = data.get("date", {}).get("hijri", {})
    hijri_text = f"{hijri.get('day', '')} {hijri.get('month', {}).get('en', '')} {hijri.get('year', '')}".strip()
    result = Result("Aladhan", PASS, f"reachable · no key needed · {city} Asr today = {timings['Asr']}")
    result.detail(f"{gregorian} · {hijri_text} AH · method: {data.get('meta', {}).get('method', {}).get('name', '?')}")
    result.detail(
        "  ".join(f"{name} {timings.get(name, '?')}" for name in ("Fajr", "Dhuhr", "Asr", "Maghrib", "Isha"))
    )
    return result


CHECKS = (
    ("Gemini", check_gemini),
    ("Deepgram", check_deepgram),
    ("ElevenLabs", check_elevenlabs),
    ("Supabase", check_supabase),
    ("Open-Meteo", check_open_meteo),
    ("Aladhan", check_aladhan),
)


def main() -> int:
    print()
    print(_c("  Sarjy · Phase 0 credential smoke test", "1"))
    print(_c(f"  reading {ENV_PATH}", "2"))
    print()

    if not ENV_PATH.exists():
        print(f"  {_c('backend/.env does not exist.', '31;1')}")
        print("  Create it with:  cp backend/.env.example backend/.env")
        print("  Then fill in the keys and run this again.\n")
        return 1

    # dotenv_values reads the file directly, so a stale shell export can't mask a bad .env.
    env = {key: (value or "").strip() for key, value in dotenv_values(ENV_PATH).items()}

    provider = env.get("TTS_PROVIDER") or "(unset)"
    print(f"  config: TTS_PROVIDER={provider} · DEFAULT_CITY={env.get('DEFAULT_CITY') or '(unset)'}")
    if provider == "elevenlabs":
        print(
            f"  {_c('warning:', '33;1')} TTS_PROVIDER=elevenlabs burns the ~10 min free quota. "
            "Use gemini for development (CLAUDE.md rule 2)."
        )
    print()

    results = [check(env) for _, check in CHECKS]
    for result in results:
        result.show()

    failed = [r for r in results if r.status == FAIL]
    pending = [r for r in results if r.status == PENDING]
    print()
    print(
        f"  {len(results) - len(failed) - len(pending)} PASS · "
        f"{len(failed)} FAIL · {len(pending)} PENDING"
    )

    if failed:
        print(f"  {_c('Phase 0 DoD not met', '31;1')} — fix: {', '.join(r.service for r in failed)}")
        print()
        return 1
    if pending:
        print(
            f"  {_c('Phase 0 DoD not met', '33;1')} — still to fill: "
            f"{', '.join(r.service for r in pending)}"
        )
        print()
        return 2
    print(f"  {_c('All services green — Phase 0 DoD met.', '32;1')}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
