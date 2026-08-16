"""Environment configuration for Sarjy.

Single place that reads `backend/.env`, so no module ever calls `os.getenv` directly and
every "you forgot to fill in X" failure reads the same way. Values are read from the file
(not the process environment) for the same reason the smoke test does it — a stale shell
export must never mask a wrong `.env`.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_DIR / ".env"

# File first, real environment second: Cloud Run (Phase 3) injects secrets as env vars and
# ships no .env at all, so the deployed app falls through to os.environ transparently.
_FILE_VALUES = {k: (v or "").strip() for k, v in dotenv_values(ENV_PATH).items()}


class ConfigError(RuntimeError):
    """A required variable is missing. Message always carries the fix."""


def get(name: str, default: str | None = None) -> str | None:
    value = _FILE_VALUES.get(name) or os.environ.get(name)
    return value.strip() if value else default


def require(name: str, hint: str) -> str:
    value = get(name)
    if not value:
        raise ConfigError(
            f"{name} is not set.\n"
            f"  Add it to {ENV_PATH} (see backend/.env.example).\n"
            f"  {hint}"
        )
    return value


def database_url() -> str:
    """The Supabase Session-pooler URI, normalized for SQLAlchemy (see D-020)."""
    url = require(
        "DATABASE_URL",
        "Copy the Session pooler URI from Supabase → Connect (port 5432, host contains "
        "'pooler.supabase.com'). The direct connection is IPv6-only and dies on Cloud Run.",
    )
    # Supabase's UI still shows the legacy postgres:// scheme in places; SQLAlchemy wants
    # postgresql://.
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def gemini_api_key() -> str:
    return require("GEMINI_API_KEY", "Get one at aistudio.google.com/apikey (free tier).")


def gemini_model() -> str:
    return require(
        "GEMINI_MODEL",
        "Never guess the name (CLAUDE.md rule 7) — run `python scripts/smoke_test.py`, "
        "which prints the Flash models this key can actually see.",
    )


def gemini_tts_model() -> str:
    return require(
        "GEMINI_TTS_MODEL",
        "Never guess the name (CLAUDE.md rule 7) — `python scripts/smoke_test.py` prints "
        "the TTS models this key can see. Phase 2 uses gemini-3.1-flash-tts-preview.",
    )


# The fact extractor (product.md §9) runs once per turn on top of the brain call, so it goes
# on the cheapest model that can hold a JSON contract — not the conversational model.
DEFAULT_EXTRACTOR_MODEL = "gemini-3.5-flash-lite"


def gemini_extractor_model() -> str:
    return get("GEMINI_EXTRACTOR_MODEL", DEFAULT_EXTRACTOR_MODEL) or DEFAULT_EXTRACTOR_MODEL


def deepgram_api_key() -> str:
    return require("DEEPGRAM_API_KEY", "Get one at console.deepgram.com → API Keys.")


DEFAULT_CITY = get("DEFAULT_CITY", "Alexandria")
DEFAULT_PERSONA = get("DEFAULT_PERSONA", "egyptian")


def default_city() -> str:
    """Read at call time, not import time, so a test can point it somewhere else."""
    return get("DEFAULT_CITY", "Alexandria") or "Alexandria"


def float_setting(name: str, default: float) -> float:
    """A numeric knob from .env, falling back rather than crashing on a typo."""
    raw = get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default

# The dev server, always allowed: a deploy must never be the thing that breaks local work.
LOCAL_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def allowed_origins() -> list[str]:
    """Browser origins this API answers, local ones first.

    `ALLOWED_ORIGINS` is a comma-separated list of exact origins. An entry may start with
    `*.` to match a whole domain suffix — Vercel gives every branch and every preview build
    its own hostname, so `*.vercel.app` is the difference between "previews work" and
    "only production works".
    """
    raw = get("ALLOWED_ORIGINS", "") or ""
    configured = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
    return [*LOCAL_ORIGINS, *[o for o in configured if o not in LOCAL_ORIGINS]]


def origin_allowed(origin: str | None, patterns: list[str] | None = None) -> bool:
    """Does this browser `Origin` header match the allowlist?

    CORS middleware does not cover WebSockets — browsers send the handshake regardless — so
    `/ws` has to ask this question itself, or `ALLOWED_ORIGINS` would be decoration on the
    one endpoint that actually costs money.

    A *missing* Origin passes: only browsers send one, and a curl or wscat against the
    deployed socket is how a deploy gets debugged. This is spend control, not authentication.
    """
    if not origin:
        return True
    origin = origin.strip().rstrip("/")
    host = origin.split("://", 1)[-1]
    for pattern in (allowed_origins() if patterns is None else patterns):
        if pattern.startswith("*."):
            if host.endswith(pattern[1:]):
                return True
        elif origin == pattern:
            return True
    return False

