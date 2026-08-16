# -*- coding: utf-8 -*-
"""Memory: durable facts about the person, extracted in the background (product.md §9).

Three things happen here.

**Reading.** Every fact this user has is rendered into the system prompt on every turn, so
Sarjy knows their name and their city without being reminded. Keys are canonical English
snake_case whatever language the fact was told in (D-014) — that single normalisation is
what makes "ما هو لوني المفضل" and "what's my favourite colour" the same question.

**Writing.** After a turn is spoken, the exchange goes to a *cheaper* model with a strict
JSON contract and comes back as `[{key, value}]`. It runs as a background task so it can
never delay the reply — but the task is owned by the WebSocket handler and awaited before
the socket closes, because Cloud Run only allocates CPU while a request is in flight and a
detached task would simply be throttled into next week (D-051).

**Language preference.** The same extraction answers one more question: did the user just
ask to be spoken to in a particular language? That is the only thing allowed to set
`users.preferred_language`, which then overrides mirroring until they change it (§6.2).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from google import genai
from google.genai import types
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, language as language_module
from .db import session_scope
from .models import Fact, User

log = logging.getLogger("sarjy.memory")

# A fact is a short thing about a person. Anything longer is a conversation, and storing it
# would poison every later system prompt with narrative the model then tries to act on.
MAX_KEY_CHARS = 64
MAX_VALUE_CHARS = 200

# The extractor is one extra model call per turn, so it runs on the cheapest model that can
# hold a JSON contract — not on the conversational model (GEMINI_EXTRACTOR_MODEL).
EXTRACTOR_TEMPERATURE = 0.0
EXTRACTOR_MAX_TOKENS = 512

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["key", "value"],
            },
        },
        "preferred_language": {"type": "string", "enum": ["ar", "en", "none"]},
    },
    "required": ["facts", "preferred_language"],
}

EXTRACTOR_PROMPT = """\
You extract DURABLE PERSONAL FACTS from one exchange between a person and a voice assistant.
You are a silent background process. You never talk to anyone. You only return JSON.

# What counts as a durable fact
Something that will still be true about this person next week, and that would be useful to
know at the start of a future conversation.
    name · home_city · job · employer · favorite_color · favorite_food · favorite_team ·
    doctor_name · spouse_name · children · birthday · allergy · dietary_restriction ·
    languages_spoken · car · neighborhood · gym · hobby

# What must NEVER be stored
- The current question, request or task ("wants the weather", "asked about prayer times").
- Anything about today only: mood, plans, the weather, what time it is, being tired or busy.
- Appointments and bookings — they live in their own table already.
- Facts about the assistant, or anything the assistant said.
- Anything the person did not actually state about themselves.
If the exchange contains no durable fact, return an empty list. That is the normal case, and
an empty list is a correct answer — do not invent something to fill it.

# Key rules
- Keys are ALWAYS canonical English snake_case, whatever language the person spoke.
  "اسمي أحمد" → key "name". "بحب اللون الأزرق" → key "favorite_color".
- One fact per key. Prefer an existing common key over inventing a new one.
- Values stay in the language the person used, and stay SHORT — a word or two.
  "اسمي أحمد" → {"key": "name", "value": "أحمد"}.

# preferred_language
Set it to "ar" or "en" ONLY if the person EXPLICITLY asked to be spoken to in that language
("كلمني عربي", "اتكلم انجليزي", "speak English", "reply in Arabic"). Merely speaking a
language is NOT a request — it is handled elsewhere. Otherwise return "none".

Return JSON with exactly this shape and nothing else:
{"facts": [{"key": "...", "value": "..."}], "preferred_language": "ar" | "en" | "none"}
"""


_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.gemini_api_key())
    return _client


# --------------------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------------------


def load_facts(db: Session, user_id: uuid.UUID) -> list[Fact]:
    return list(
        db.scalars(select(Fact).where(Fact.user_id == user_id).order_by(Fact.key)).all()
    )


def facts_block(facts: list[Fact]) -> str | None:
    """The facts, rendered for the system prompt. None when there is nothing to say."""
    if not facts:
        return None
    lines = [f"- {fact.key}: {fact.value}" for fact in facts]
    return (
        "\n".join(lines)
        + "\n\nUse these naturally when they are relevant — do not recite them, and never "
        "list them back unless asked. If a fact answers the question, answer with it in "
        "whichever language the person is speaking now, even if they told you in the other "
        "one."
    )


def pick(facts: list[Fact], key: str) -> str | None:
    """One fact out of a list already loaded — no second query for something we have."""
    return next((fact.value for fact in facts if fact.key == key), None)


def get_fact(db: Session, user_id: uuid.UUID, key: str) -> str | None:
    """One fact, when that really is all the caller needs (the greeting wants only `name`)."""
    fact = db.scalar(select(Fact).where(Fact.user_id == user_id, Fact.key == key))
    return fact.value if fact else None


# --------------------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------------------


def upsert_fact(db: Session, user_id: uuid.UUID, key: str, value: str, source_language: str) -> bool:
    """Insert or update one fact. Returns True when the row actually changed."""
    key = (key or "").strip().lower().replace(" ", "_")[:MAX_KEY_CHARS]
    value = (value or "").strip()[:MAX_VALUE_CHARS]
    if not key or not value:
        return False

    fact = db.scalar(select(Fact).where(Fact.user_id == user_id, Fact.key == key))
    if fact is None:
        db.add(Fact(user_id=user_id, key=key, value=value, source_language=source_language))
        log.info("memory: learned %s = %r (%s)", key, value, source_language)
        return True
    if fact.value == value:
        return False
    log.info("memory: updated %s = %r (was %r)", key, value, fact.value)
    fact.value = value
    fact.source_language = source_language
    return True


def delete_fact(db: Session, user_id: uuid.UUID, key: str) -> bool:
    fact = db.scalar(select(Fact).where(Fact.user_id == user_id, Fact.key == key))
    if fact is None:
        return False
    db.delete(fact)
    log.info("memory: forgot %s for user %s", key, user_id)
    return True


def parse_extraction(raw: str) -> tuple[list[dict], str | None]:
    """Read the extractor's JSON defensively. A malformed answer means "nothing learned"."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("memory: extractor returned non-JSON: %r", (raw or "")[:200])
        return [], None
    if not isinstance(payload, dict):
        return [], None

    facts = []
    for item in payload.get("facts") or []:
        if isinstance(item, dict) and item.get("key") and item.get("value"):
            facts.append({"key": str(item["key"]), "value": str(item["value"])})

    preferred = payload.get("preferred_language")
    if preferred not in ("ar", "en"):
        preferred = None
    return facts, preferred


def _extract(user_text: str, assistant_text: str) -> tuple[list[dict], str | None]:
    """One extractor call. Blocking — always called from a worker thread."""
    exchange = f"PERSON: {user_text}\nASSISTANT: {assistant_text}"
    response = client().models.generate_content(
        model=config.gemini_extractor_model(),
        contents=exchange,
        config=types.GenerateContentConfig(
            system_instruction=EXTRACTOR_PROMPT,
            temperature=EXTRACTOR_TEMPERATURE,
            max_output_tokens=EXTRACTOR_MAX_TOKENS,
            response_mime_type="application/json",
            response_json_schema=RESPONSE_SCHEMA,
            # No tools here, and saying so keeps the SDK from warning about its automatic
            # function-calling path on every single turn.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )
    return parse_extraction(response.text or "")


def extract_and_store(user_id: uuid.UUID, user_text: str, assistant_text: str) -> None:
    """Blocking end-to-end: extract, then write. Runs in a thread, never on the loop."""
    facts, preferred = _extract(user_text, assistant_text)
    if not facts and not preferred:
        return

    # The language a fact was *told* in is a property of the person's utterance, not of the
    # fact's value — "اسمي Kareem" is an Arabic sentence carrying a Latin name (D-014).
    source_language = language_module.detect_language(user_text)

    with session_scope() as db:
        for fact in facts:
            upsert_fact(db, user_id, fact["key"], fact["value"], source_language)
        if preferred:
            user = db.get(User, user_id)
            if user is not None and user.preferred_language != preferred:
                log.info("memory: preferred_language → %s for user %s", preferred, user_id)
                user.preferred_language = preferred


async def remember(user_id: uuid.UUID, user_text: str, assistant_text: str) -> None:
    """Fire-and-forget wrapper. Swallows its own failures: memory is never worth a dead call."""
    try:
        await asyncio.to_thread(extract_and_store, user_id, user_text, assistant_text)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("memory: extraction failed (%s) — the turn itself was fine", exc)
