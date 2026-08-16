# -*- coding: utf-8 -*-
"""The brain: history + persona + memory + tools → Gemini → reply text.

Phase 4 turns the Phase-1 completion into an agent. Three things were added and each one is
a whole class of bug avoided:

**Tools.** A manual function-calling loop, not the SDK's automatic mode. Manual is four more
lines and buys the two things this project needs: a hard cap on rounds, and a log line for
every call and every result — which is what makes "did it really book that" answerable from
a server log instead of from trust. Gemini 3 requires the model's own turn to be echoed back
verbatim between the call and the result (it carries the thought signature), so the loop
appends `response.candidates[0].content` rather than rebuilding it.

**Memory.** Every fact known about this person is rendered into the system prompt (§9).

**Time.** The server runs in UTC and the person lives somewhere else, so the current date,
clock and timezone are injected on every turn, along with the literal dates that "today",
"tomorrow" and "the day after" resolve to. Without that, "بكرة بعد العصر" books the wrong
day roughly whenever the user is talking after 22:00 Cairo time — and never in testing.

Everything here is synchronous on purpose (D-027); the WebSocket calls it in a thread.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config, memory, personas, quota, tools
from .models import Message, User
from .tools.places import Place, geocode
from .tools.when import spoken_clock, zone

# How much of the conversation Gemini sees. Twelve turns is plenty for a spoken exchange
# and keeps every request small enough to stay comfortably inside the free tier.
HISTORY_LIMIT = 12

# Generous enough for a 1–3 sentence spoken reply plus the model's minimal thinking, tight
# enough that a runaway essay gets cut instead of being read aloud for a minute.
MAX_OUTPUT_TOKENS = 800

# Warm enough for dialect and wit, not so hot that it invents facts.
TEMPERATURE = 0.85

# The free tier answers 503 "experiencing high demand" every so often — observed twice in
# the first dozen Phase-1 calls. One or two quick retries turn a dead turn into a slightly
# slow one. The user-facing bilingual rate-limit message is Phase 5's job, not this.
RETRY_STATUSES = (429, 503)
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = (0.6, 1.8)

log = logging.getLogger("sarjy.brain")


class BrainError(RuntimeError):
    """Gemini could not be reached or returned nothing usable."""


class BrainQuotaError(BrainError):
    """The day's allowance is spent. Distinct from BrainError because it is not a fault:
    nothing is broken. Distinct from BrainBusyError because it will not clear by waiting —
    a retry here is a spinner that lies, so nothing above this ever retries it."""


class BrainBusyError(BrainError):
    """A per-minute burst limit, or transient congestion. Trying again shortly really works.

    Google reports this with the same HTTP 429 as a spent daily quota and only the quotaId
    tells them apart (D-035), which is why they are two exception types here: the caller must
    not have to re-derive the difference in order to decide whether to offer a retry.
    """

    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


_client: genai.Client | None = None


def client() -> genai.Client:
    """Lazily-built Gemini client, so importing this module needs no credentials."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.gemini_api_key())
    return _client


@dataclass
class Reply:
    """What one brain turn produced, plus what it had to do to produce it."""

    text: str
    tool_calls: list[str] = field(default_factory=list)

    @property
    def rounds(self) -> int:
        return len(self.tool_calls)


# --------------------------------------------------------------------------------------
# Prompt assembly
# --------------------------------------------------------------------------------------

FACTS_SECTION_HEADER = "# What you remember about this person"

FACTS_PLACEHOLDER = (
    "Nothing yet — you have not been told anything durable about this person. "
    "Do not invent a name, a city, or any preference. If they tell you something about "
    "themselves, just acknowledge it naturally in one short sentence."
)

# Written in English (models follow English instructions more reliably) with the Arabic
# examples that make them stick — the same reasoning as the persona prompts, D-032.
TOOL_RULES = """\
# What you can actually do
You have four tools, and they are the ONLY actions you can perform in the real world:
    get_weather · get_prayer_times · create_booking · list_bookings
Use them instead of guessing. Never state a temperature, a prayer time, or an appointment
you did not get from a tool.

Anything else, you CANNOT do: sending an email or a WhatsApp message, making a call,
ordering food, setting an alarm or a reminder, opening an app, searching the web, cancelling
or changing an appointment that already exists. When asked for one of those, say plainly in
one short sentence that you cannot do it, and offer the nearest thing you can:
    ✅ معنديش طريقة أبعت إيميل، بس أقدر أحجزلك ميعاد لو تحب.
    ❌ تمام، بعتّهاله.
Never claim you did something you did not do. If a tool did not run, nothing happened. This
is the rule you break least.

If a tool comes back with an error, say briefly what went wrong in the person's language and
ask for the one thing you need (a city, a time). Never read the error out loud word for word,
and never mention tools, APIs, functions or JSON — the person is on a phone call.

# You do not know what is on their screen
There is no settings screen, no menu, no voice picker and no button you can point anybody at,
and you cannot change your own voice, speed or accent. If asked to change something about
yourself, say plainly that you can't and that it is up to whoever built you. Do NOT invent a
place to go and look:
    ✅ مقدرش أغير صوتي، ده متظبط من ورا. تحب أساعدك في حاجة تانية؟
    ❌ تقدر تغيره من الإعدادات في التطبيق.
Describing a screen that does not exist is the same lie as claiming an action you never took.

# Things that change
Scores, prices, who plays for which club, what is open right now — you may have learned them
a while ago and they move. Answer if you know, but hang half a sentence on it ("على حد علمي"
/ "last I knew") rather than stating it flat. Weather, prayer times and their appointments
are never in this category: those come from a tool or you do not say them.

# Prayer-anchored times
Anchoring a time to a prayer is the normal way to say when here, not an edge case:
    بعد الفجر · بعد الضهر · بعد العصر · بعد المغرب · بعد العشا · قبل المغرب · على العصر
    after Maghrib · before Asr · right after Fajr
When a time is anchored to a prayer:
  1. Call get_prayer_times for that city and that date FIRST. Prayer times move every day —
     you cannot know them.
  2. Turn the prayer into a clock time: "بعد X" / "after X" = fifteen minutes after X.
     "قبل X" / "before X" = thirty minutes before X. "على X" / "at X" = X itself.
  3. Then do what was asked with that clock time — usually create_booking.
  4. Say the resolved time out loud in the confirmation, the way a person says it
     ("الساعة خمسة إلا ربع"), so they can catch it if it is wrong.
"""


def context_block(now: datetime, place: Place | None, city: str) -> str:
    """The current date, clock and timezone — injected fresh on every single turn."""
    where = f"{place.name}, {place.country}" if place else city
    today = now.date()
    lines = [
        "# Right now",
        f"Today is {now:%A %-d %B %Y}. The local time is {spoken_clock(now)} "
        f"in {where} (timezone {now.tzinfo}).",
        "These are the dates the person means:",
        f'    "النهاردة" / "اليوم" / "today"      = {_day(today, 0)}',
        f'    "بكرة" / "غدا" / "tomorrow"        = {_day(today, 1)}',
        f'    "بعد بكرة" / "day after tomorrow"  = {_day(today, 2)}',
        f'    "امبارح" / "yesterday"             = {_day(today, -1)}',
        "Use these when you call a tool. Their clock is the one above — never answer in UTC, "
        "and never assume the day rolls over at a different hour than theirs.",
    ]
    return "\n".join(lines)


def _day(today: date, offset: int) -> str:
    return (today + timedelta(days=offset)).isoformat()


def language_block(preferred: str | None) -> str | None:
    """§6.2: an explicit switch wins over mirroring, and sticks until it is changed."""
    if preferred not in ("ar", "en"):
        return None
    name = "Arabic" if preferred == "ar" else "English"
    return (
        "# Language — this person has chosen one\n"
        f"They explicitly asked you to speak {name}. Reply in {name} on every turn from now "
        "on, even when they say something to you in the other language, until they "
        "explicitly ask to switch again. This overrides the mirroring rule above."
    )


def build_system_prompt(
    persona: personas.Persona,
    facts_block: str | None = None,
    context: str | None = None,
    language: str | None = None,
) -> str:
    """Persona style guide · tool rules · now · language override · memory."""
    sections = [persona.style_guide, TOOL_RULES]
    if context:
        sections.append(context)
    if language:
        sections.append(language)
    sections.append(f"{FACTS_SECTION_HEADER}\n{facts_block or FACTS_PLACEHOLDER}")
    return "\n\n".join(sections) + "\n"


def _to_content(message: Message) -> types.Content:
    # Gemini calls the assistant side "model"; our schema calls it "assistant".
    role = "model" if message.role == "assistant" else "user"
    return types.Content(role=role, parts=[types.Part(text=message.content)])


def load_history(db: Session, session_id: uuid.UUID, limit: int = HISTORY_LIMIT) -> list[Message]:
    """The newest `limit` messages of this session, oldest first."""
    rows = db.scalars(
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.id.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


# --------------------------------------------------------------------------------------
# Where and when this person is
# --------------------------------------------------------------------------------------

# Used only when the geocoder cannot be reached at all. Without a sane fallback the app
# would silently switch the user to UTC, and every relative date would be wrong for the
# three hours around midnight — the least testable bug imaginable.
DEFAULT_TIMEZONE = "Africa/Cairo"


def locate(home_city: str | None) -> tuple[Place | None, ZoneInfo, str]:
    """Where this person is, for the clock and for tool defaults.

    Takes the city rather than looking it up, because the caller has already loaded every
    fact this person has and `home_city` is one of them — asking the database a second
    question we already know the answer to costs a sequential round trip to Ireland on
    every single turn (D-049 measured those at ~70ms each, and a turn makes eight).
    The geocode itself is memoised, so this is a network call once per city per process.
    """
    city = (home_city or "").strip() or config.default_city()
    try:
        place = geocode(city)
        return place, zone(place.timezone), city
    except Exception as exc:  # noqa: BLE001 — a turn must never fail over a geocoder
        log.warning(
            "brain: could not locate %r (%s) — falling back to %s", city, exc, DEFAULT_TIMEZONE
        )
        return None, zone(config.get("DEFAULT_TIMEZONE", DEFAULT_TIMEZONE)), city


# --------------------------------------------------------------------------------------
# The Gemini call
# --------------------------------------------------------------------------------------


def _call_with_retry(
    contents: list[types.Content], generation_config: types.GenerateContentConfig
):
    """One Gemini call, retried only on the free tier's transient congestion codes."""
    last: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return client().models.generate_content(
                model=config.gemini_model(), contents=contents, config=generation_config
            )
        except genai_errors.APIError as exc:
            last = exc
            # A *daily* quota does not come back in 1.8 seconds. Fail immediately with a
            # message that says what actually happened, instead of stalling then lying.
            if _is_daily_quota(exc):
                raise BrainQuotaError(
                    f"Free-tier daily quota exhausted for {config.gemini_model()}. "
                    "It resets at midnight Pacific; switching GEMINI_MODEL to another "
                    "Flash model gives a fresh allowance immediately (quota is per-model)."
                ) from exc
            retryable = exc.code in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1
            if not retryable:
                break
            delay = RETRY_BACKOFF_S[attempt]
            log.warning("Gemini %s — retrying in %.1fs (attempt %d)", exc.code, delay, attempt + 2)
            time.sleep(delay)
        except Exception as exc:  # noqa: BLE001 - network/SDK failures are not retried
            last = exc
            break

    if isinstance(last, genai_errors.APIError) and last.code in RETRY_STATUSES:
        # Not the day's allowance (that returned above): a burst limit or congestion, which
        # is the one failure where waiting is an honest thing to offer the user.
        raise BrainBusyError(
            f"Gemini is rate-limiting this key: {last}", retry_after_s=retry_after(last)
        ) from last
    raise BrainError(f"Gemini request failed: {last}") from last


def _is_daily_quota(exc: genai_errors.APIError) -> bool:
    """Tell a per-day allowance apart from a per-minute burst limit (see app/quota.py)."""
    return exc.code == 429 and quota.is_per_day(exc)


# Google attaches a RetryInfo to a burst 429. Read off the error rather than guessed, so the
# countdown the user sees is the provider's own number.
#
# Clamped, because a 60-second spinner on a phone call is not a designed state, it is an
# abandoned one. Past the clamp we try again earlier than we were told to; if that attempt
# fails too, the same honest notice comes back — which is a better answer than a minute of
# dead air that the person will not sit through anyway.
MAX_HONEST_WAIT_S = 20.0


def retry_after(exc: Exception) -> float | None:
    """How long the provider says to wait, or None if it did not say."""
    return quota.retry_after_s(exc, maximum=MAX_HONEST_WAIT_S)


def _generation_config(system_prompt: str, with_tools: bool) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=TEMPERATURE,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        # A spoken assistant answering "إزيك" must not stop to reason about it. LOW is the
        # floor this model accepts (it rejects MINIMAL with a 400) and measures at zero
        # thinking tokens for conversational turns.
        thinking_config=types.ThinkingConfig(thinking_level="LOW"),
        # Manual loop, not the SDK's automatic one: we want the round cap and the log lines.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        tools=[tools.tool_config()] if with_tools else None,
        # The last call of a capped turn is made with tools switched off, which is what
        # guarantees it comes back as speech rather than as a fifth function call.
        tool_config=None
        if with_tools
        else types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode="NONE")
        ),
    )


def _function_response(call, result: dict) -> types.Part:
    """One tool result, addressed back to the call that asked for it.

    Built by hand rather than with `types.Part.from_function_response`, whose signature in
    google-genai 2.18.1 is `(name, response, parts)` — no `id`. The current documentation
    passes `id=` to that same helper, so the docs are describing a newer SDK than the pin.
    The id matters: Gemini 3 returns one with every function call and expects it back, and it
    is what maps a result to its request when the model asks for two tools in one round.
    """
    return types.Part(
        function_response=types.FunctionResponse(
            id=getattr(call, "id", None), name=call.name, response=result
        )
    )


# --------------------------------------------------------------------------------------
# The one public function
# --------------------------------------------------------------------------------------


def generate_reply(db: Session, user_id: uuid.UUID, session_id: uuid.UUID, text: str) -> Reply:
    """Answer `text` in this session's context, with this person's memory and tools."""
    user = db.get(User, user_id)
    persona = personas.get_persona(user.preferred_persona if user else None)

    facts = memory.load_facts(db, user_id)
    place, tz, city = locate(memory.pick(facts, "home_city"))
    now = datetime.now(tz)

    system_prompt = build_system_prompt(
        persona,
        facts_block=memory.facts_block(facts),
        context=context_block(now, place, city),
        language=language_block(user.preferred_language if user else None),
    )

    history = load_history(db, session_id)
    # The API layer stores the user's turn before calling us, so it is usually already the
    # last row. Drop it and re-add it below, so this function behaves the same whether or
    # not the caller stored it first.
    if history and history[-1].role == "user" and history[-1].content == text:
        history = history[:-1]

    contents = [_to_content(m) for m in history]
    contents.append(types.Content(role="user", parts=[types.Part(text=text)]))

    context = tools.ToolContext(
        db=db,
        user_id=user_id,
        now=now,
        tz=tz,
        default_city=place.name if place else city,
    )

    called: list[str] = []
    for round_number in range(tools.MAX_TOOL_ROUNDS + 1):
        # The extra iteration is the cap itself: on it the tools are withdrawn, so the model
        # has no choice but to answer with words. A loop that just breaks would hand the
        # user a reply of `None`.
        with_tools = round_number < tools.MAX_TOOL_ROUNDS
        response = _call_with_retry(contents, _generation_config(system_prompt, with_tools))

        calls = list(response.function_calls or [])
        if not calls:
            break
        if not with_tools:
            break  # unreachable in practice: mode=NONE forbids calls

        # Verbatim, including the thought signature Gemini 3 requires back with the result.
        contents.append(response.candidates[0].content)

        results = []
        for call in calls:
            called.append(call.name)
            result = tools.run_tool(call.name, dict(call.args or {}), context)
            results.append(_function_response(call, result))
        contents.append(types.Content(role="user", parts=results))
        if round_number + 1 == tools.MAX_TOOL_ROUNDS:
            log.warning("brain: hit the %d-round tool cap (%s)", tools.MAX_TOOL_ROUNDS, called)

    reply = (response.text or "").strip()
    if not reply:
        reason = getattr(response.candidates[0], "finish_reason", "?") if response.candidates else "?"
        raise BrainError(f"Gemini returned no text (finish_reason={reason}).")
    return Reply(text=reply, tool_calls=called)
