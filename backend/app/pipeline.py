# -*- coding: utf-8 -*-
"""The conductor for one spoken turn.

    final transcript → store → brain (+ tools) → store → split → synthesise → stream down
                                                                          ↘ remember (background)

Everything the brain and the database do is synchronous (D-027), so each blocking step runs
in a worker thread and the event loop stays free to keep pumping the user's audio into
Deepgram. Each database step opens its own short-lived session rather than passing one
across threads.

The reply text is sent the moment it exists, before any audio: the transcript on screen
updates while the first sentence is still being synthesised, which is most of the perceived
speed of the thing.

**A turn can be interrupted.** Phase 4 replaced the half-duplex mic-pause of D-040 with real
barge-in, so the socket handler may cancel this task at any point. The contract that keeps
the browser sane is one line long: *exactly one of `speak_end` or `stop_speaking` is sent per
turn.* A turn that finishes sends `speak_end` from its `finally`; a turn that is cancelled
suppresses it, and whoever cancelled it sends `stop_speaking` instead.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import func

from . import brain, config, language, memory, personas, voice
from .db import session_scope
from .models import ChatSession, Message, TurnMetric, User
from .speech_recognition import Utterance

log = logging.getLogger("sarjy.pipeline")


class Sender(Protocol):
    """What the pipeline needs from a WebSocket — kept narrow so tests can fake it."""

    async def send_json(self, data: dict) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...


# --------------------------------------------------------------------------------------
# What the user is told when something runs out (product.md §4, "designed states")
# --------------------------------------------------------------------------------------
#
# Every message is bilingual — Arabic first, it is the primary audience — and never a stack
# trace. Phase 5 adds the half that was missing: **whether waiting is honest**.
#
# Google returns the same 429 for a per-minute burst and a spent daily allowance, and the two
# deserve opposite answers. A burst limit clears in seconds, so the right thing is to say
# "hang on" and try again by itself. A daily allowance is not coming back before midnight
# Pacific, so an auto-retry there is a spinner that lies to the user's face — that one says
# "not today" and stops. `speak_again` marks the third case: nothing is wrong with the answer,
# only with the voice, so the browser reads the reply out instead of the call falling silent.


@dataclass(frozen=True)
class UserFacingError:
    """One thing that can go wrong, in both languages, with what to do about it."""

    english: str
    arabic: str
    # True only when trying again shortly is genuinely likely to work, AND the browser is the
    # one that has to do it — it counts down and then asks for the same utterance again.
    retryable: bool = False
    # Roughly how long to wait before that retry, or how long to leave a transient notice up.
    # None when there is nothing to wait for.
    retry_after_s: float | None = None
    # The server is already fixing it and the user only needs to know why the pause. Shown,
    # then cleared by itself — never a message that outlives the problem it describes.
    transient: bool = False
    # Ask the browser to speak the reply with its own voice — the last link of the D-005
    # fallback chain, and the difference between a silent call and a slightly odd one.
    speak_again: bool = False


ERRORS: dict[str, UserFacingError] = {
    "brain_busy": UserFacingError(
        "Sarjy is being rate-limited for a moment — trying again.",
        "سرجي زحمة شوية. ثانية واحدة وبجرب تاني.",
        retryable=True,
        retry_after_s=4.0,
    ),
    "brain_quota": UserFacingError(
        "Today's free Gemini allowance is used up — this one is not coming back before "
        "tomorrow.",
        "خلصت حصة النهاردة من Gemini المجانية. مش هترجع غير بكرة، مش هفضل أحاول.",
    ),
    "brain": UserFacingError(
        "Sarjy could not think of an answer just now. Say it again?",
        "سرجي مش قادر يرد دلوقتي. تقول تاني؟",
        retryable=True,
        retry_after_s=2.0,
    ),
    "voice_quota": UserFacingError(
        "Sarjy's free voice allowance is spent for today — reading the reply in your "
        "browser's own voice instead.",
        "خلصت حصة الصوت المجانية النهاردة. هقرالك الرد بصوت المتصفح بدل ما تفضل ساكت.",
        speak_again=True,
    ),
    "voice": UserFacingError(
        "Sarjy could not speak that one — reading it in your browser's own voice instead.",
        "سرجي مقدرش ينطق الرد. هقراهولك بصوت المتصفح.",
        speak_again=True,
    ),
    "speech_retrying": UserFacingError(
        "The microphone connection dropped — reconnecting.",
        "الاتصال بالميكروفون فصل. برجّعه دلوقتي.",
        retry_after_s=6.0,
        transient=True,
    ),
    "speech": UserFacingError(
        "Sarjy lost the microphone connection. Hang up and tap again.",
        "الاتصال بالميكروفون اتقطع. اقفل واضغط تاني.",
    ),
}


def injected_fault() -> str | None:
    """Which failure `FAULT_INJECT` is asking us to pretend is happening, if any.

    A designed state that has never been seen is a design, not a state. The ones above are
    the hardest paths in the app to reach on purpose: a daily Gemini quota takes a day to
    exhaust, a burst limit needs traffic we do not have, and a Deepgram drop needs a network
    to break — so before Phase 5 they were verified by reading the code, which is how a
    bilingual message with a missing translation ships.

    `FAULT_INJECT=brain_quota` makes the next turn raise exactly what a spent allowance
    raises, at exactly the point it would raise it, so the whole path downstream is the real
    one: the same exception, the same frame, the same countdown, the same browser voice.

    Empty by default, absent from `.env.cloudrun.yaml`, and read fresh on every turn so a
    demo can be armed and disarmed between sentences. This is a test instrument, not a
    feature — it can only ever make Sarjy *worse*, never let it do something it could not do.
    """
    return (config.get("FAULT_INJECT", "") or "").strip() or None


async def send_error(
    sender: Sender, key: str, *, retry_after_s: float | None = None
) -> None:
    """One error frame, carrying enough for the UI to behave rather than just apologise.

    `retry_after_s` overrides the default when the provider told us how long to wait — an
    honest countdown beats a guessed one, and Google does send `retryDelay` with a burst 429.
    """
    error = ERRORS.get(key, ERRORS["brain"])
    await sender.send_json({
        "type": "error",
        "key": key,
        "message_en": error.english,
        "message_ar": error.arabic,
        "retryable": error.retryable,
        "retry_after_s": retry_after_s if retry_after_s is not None else error.retry_after_s,
        "transient": error.transient,
        "speak_again": error.speak_again,
    })


class TurnSender:
    """One turn's write access to the socket, revocable the instant it is interrupted.

    Barge-in cannot simply cancel the turn task and wait for it: the task is usually parked
    in `asyncio.to_thread(voice.synthesize, ...)`, and an executor future that has already
    started **cannot be cancelled** — `await task` blocks until the HTTP request to Gemini
    finishes, which is exactly the two or three seconds the user is trying to interrupt.
    Measured: awaiting it held the Deepgram listener for 3.0s and the interruption never
    reached the browser.

    So the cancellation is fire-and-forget, and this is what makes that safe. Closing the
    gate is synchronous and immediate, so from that instant the dying turn cannot write
    another audio frame — nor its `speak_end` — even though it is still running somewhere
    with a doomed TTS response in its hand.
    """

    def __init__(self, sender: Sender) -> None:
        self._sender = sender
        self.open = True

    def close(self) -> None:
        self.open = False

    async def send_json(self, data: dict) -> None:
        if self.open:
            await self._sender.send_json(data)

    async def send_bytes(self, data: bytes) -> None:
        if self.open:
            await self._sender.send_bytes(data)


# One spoken segment: text plus the language whose voice should read it. Almost every reply
# is a single segment; the bilingual first-visit greeting is the reason this is a list.
Segment = tuple[str, str]

# A background task that must not outlive the socket — see spawn() in main.py.
Background = Callable[[Awaitable[None]], None]


# --------------------------------------------------------------------------------------
# Database steps — each one gets its own session, run off the event loop
# --------------------------------------------------------------------------------------


async def _in_db(function, *args):
    def run():
        with session_scope() as db:
            return function(db, *args)

    return await asyncio.to_thread(run)


def _open_session(db, user_id: uuid.UUID, session_id: uuid.UUID, persona_key: str) -> str:
    """Upsert the user and start the session row. Returns the persona actually in force."""
    user = db.get(User, user_id)
    if user is None:
        user = User(id=user_id, preferred_persona=personas.get_persona(persona_key).key)
        db.add(user)
        db.flush()
        log.info("new user %s", user_id)
    elif persona_key:
        # The hello frame carries the persona the UI is showing; keep the row in step.
        user.preferred_persona = personas.get_persona(persona_key).key

    if db.get(ChatSession, session_id) is None:
        db.add(ChatSession(id=session_id, user_id=user_id))
        log.info("new session %s for user %s", session_id, user_id)

    return user.preferred_persona


def _close_session(db, session_id: uuid.UUID) -> None:
    chat_session = db.get(ChatSession, session_id)
    if chat_session is not None and chat_session.ended_at is None:
        chat_session.ended_at = func.now()


def _store(db, session_id: uuid.UUID, role: str, content: str, lang: str) -> int:
    message = Message(session_id=session_id, role=role, content=content, language=lang)
    db.add(message)
    db.flush()
    return message.id


def _begin_turn(db, user_id, session_id, text: str, lang: str, store: bool = True) -> str:
    """Store what was said, and read back the persona this turn runs under.

    The persona is read *per turn*, not captured at connect: that is what makes the top-bar
    toggle take effect from the next thing Sarjy says, without restarting the call. It costs
    nothing, because the round trip that stores the user's line was already happening.

    `store=False` is a retry of a turn whose brain call was rate-limited: the person said it
    once, so it belongs in `messages` once. Storing it again would also show the model its
    own history saying the same sentence twice.
    """
    if store:
        _store(db, session_id, "user", text, lang)
    user = db.get(User, user_id)

    # §6.2, applied to *this* turn rather than to the next one. Recognised deterministically
    # (language.explicit_language_request) because the extractor that used to be the only
    # thing setting this runs after the reply — so "كلمني عربي" then an English question came
    # back in English, on both personas. Found by eval/persona_checklist.md.
    if user is not None:
        requested = language.explicit_language_request(text)
        if requested and user.preferred_language != requested:
            log.info("language: explicit switch → %s for user %s", requested, user_id)
            user.preferred_language = requested

    return user.preferred_persona if user else personas.DEFAULT_PERSONA_KEY


def _greeting_name(db, user_id: uuid.UUID) -> tuple[str | None, str, str | None]:
    """Everything the greeting depends on, in one round trip."""
    user = db.get(User, user_id)
    return (
        memory.get_fact(db, user_id, "name"),
        user.preferred_persona if user else personas.DEFAULT_PERSONA_KEY,
        user.preferred_language if user else None,
    )


def _think(db, user_id: uuid.UUID, session_id: uuid.UUID, text: str) -> brain.Reply:
    return brain.generate_reply(db, user_id, session_id, text)


def _record_metrics(db, message_id: int, stages: dict[str, int | None]) -> None:
    db.add(TurnMetric(message_id=message_id, **stages))


# --------------------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------------------


async def open_session(user_id: uuid.UUID, session_id: uuid.UUID, persona_key: str) -> str:
    return await _in_db(_open_session, user_id, session_id, persona_key)


async def close_session(session_id: uuid.UUID) -> None:
    await _in_db(_close_session, session_id)


async def greet(
    sender: Sender,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    *,
    on_speaking: Callable[[], None] | None = None,
) -> None:
    """Sarjy speaks first (product.md §5).

    Someone we have never met is asked their name, bilingually, because on a first visit
    there is no language to mirror yet. Someone we know is greeted by name in the active
    persona's dialect. Neither is a gate: the answer, if it comes, is an ordinary turn and
    the name is learned by the ordinary extractor — there is no name-capture code path.
    """
    name, persona_key, preferred = await _in_db(_greeting_name, user_id)
    persona = personas.get_persona(persona_key)

    if name:
        text = personas.returning_greeting(persona_key, name, preferred or "ar")
        segments: list[Segment] = [(text, language.dominant_language(text))]
    else:
        text = personas.FIRST_VISIT_TEXT
        segments = list(personas.FIRST_VISIT_SEGMENTS)

    lang = language.detect_language(text)
    await _in_db(_store, session_id, "assistant", text, lang)
    await sender.send_json({"type": "reply_text", "text": text, "language": lang})
    log.info("greeting: %s", f"returning ({name})" if name else "first visit")

    await _speak(sender, segments, persona, started=time.monotonic(), on_speaking=on_speaking)


async def run_turn(
    sender: Sender,
    user_id: uuid.UUID,
    session_id: uuid.UUID,
    utterance: Utterance,
    *,
    on_speaking: Callable[[], None] | None = None,
    background: Background | None = None,
    retry: bool = False,
) -> None:
    """One complete turn: what the user said in, spoken reply out.

    `started` is the origin every number in `turn_metrics` is measured from: the instant the
    race was decided and this utterance became the thing to answer (D-017).

    `retry=True` is a second attempt at an utterance whose first attempt hit a rate limit —
    same turn, same words, already in `messages`.
    """
    started = time.monotonic()

    persona_key = await _in_db(
        _begin_turn, user_id, session_id, utterance.text, utterance.language, not retry
    )
    persona = personas.get_persona(persona_key)

    try:
        fault = injected_fault()
        if fault == "brain_quota":
            raise brain.BrainQuotaError("FAULT_INJECT=brain_quota")
        if fault == "brain_busy":
            raise brain.BrainBusyError("FAULT_INJECT=brain_busy", retry_after_s=5.0)
        if fault == "brain":
            raise brain.BrainError("FAULT_INJECT=brain")
        reply = await _in_db(_think, user_id, session_id, utterance.text)
    except brain.BrainQuotaError as exc:
        log.warning("pipeline: %s", exc)
        await send_error(sender, "brain_quota")
        return
    except brain.BrainBusyError as exc:
        log.warning("pipeline: %s", exc)
        await send_error(sender, "brain_busy", retry_after_s=exc.retry_after_s)
        return
    except brain.BrainError as exc:
        log.error("pipeline: brain failed: %s", exc)
        await send_error(sender, "brain")
        return

    thought_ms = (time.monotonic() - started) * 1000
    reply_language = language.detect_language(reply.text)
    message_id = await _in_db(_store, session_id, "assistant", reply.text, reply_language)
    await sender.send_json(
        {"type": "reply_text", "text": reply.text, "language": reply_language}
    )

    # The voice the reply is *spoken* in follows §6.1's dominant-language rule, which is not
    # always the transcript badge: "عايز أعمل book" is badged mixed but spoken Arabic.
    spoken = language.dominant_language(reply.text)
    timing = await _speak(
        sender, [(reply.text, spoken)], persona, started=started, on_speaking=on_speaking
    )

    log.info(
        "turn: think %.0fms%s · first audio %s · %d/%d frames · total %.0fms",
        thought_ms,
        f" ({reply.rounds} tool round{'s' if reply.rounds != 1 else ''}: "
        f"{', '.join(reply.tool_calls)})"
        if reply.tool_calls
        else "",
        f"{timing['first_audio_ms']:.0f}ms" if timing["first_audio_ms"] else "none",
        timing["spoken"],
        timing["chunks"],
        timing["total_ms"],
    )

    # Everything below this line is off the critical path: the user has already heard the
    # reply. Nothing here may raise into the turn.
    if isinstance(sender, TurnSender) and not sender.open:
        # Interrupted on the last chunk, so the loop finished before the cancellation was
        # delivered. The exchange still happened and is still worth remembering; the timings
        # are not, because `total_ms` would describe a reply nobody heard the end of.
        log.info("turn: interrupted — remembering the exchange, discarding the timings")
        if background is not None:
            background(memory.remember(user_id, utterance.text, reply.text))
        return

    if background is not None:
        background(memory.remember(user_id, utterance.text, reply.text))
        background(
            _in_db(
                _record_metrics,
                message_id,
                {
                    "speech_recognition_ms": _stt_ms(utterance, started),
                    "brain_first_token_ms": round(thought_ms),
                    "voice_first_audio_ms": _round_ms(timing["first_audio_ms"]),
                    "total_ms": round(timing["total_ms"]),
                },
            )
        )


def _stt_ms(utterance: Utterance, accepted_at: float) -> int | None:
    """How long the person waited between finishing their sentence and being understood.

    Measured from Deepgram's own word timings (the end of the last word) to the instant the
    race was decided, so it contains the honest cost of the whole recognition stage:
    endpointing silence, Deepgram's own latency, network, and our race window (D-042).
    None when the winning channel reported no word boundaries at all.
    """
    if utterance.speech_ended_at is None:
        return None
    return max(0, round((accepted_at - utterance.speech_ended_at) * 1000))


def _round_ms(value: float | None) -> int | None:
    return round(value) if value is not None else None


async def _speak(
    sender: Sender,
    segments: list[Segment],
    persona: personas.Persona,
    started: float,
    on_speaking: Callable[[], None] | None = None,
) -> dict:
    """Synthesise chunk by chunk, sending each frame the instant it is ready.

    A cancelled turn (barge-in) suppresses `speak_end` on the way out: the interruption is
    announced by the canceller's `stop_speaking` instead, and sending both would let a dead
    turn's `speak_end` arrive after the *next* turn's `speak_start` and cut it short.
    """
    chunks: list[Segment] = [
        (chunk, lang) for text, lang in segments for chunk in voice.split_for_speech(text)
    ]
    timing = {"first_audio_ms": None, "spoken": 0, "chunks": len(chunks), "total_ms": 0.0}
    if not chunks:
        return timing

    await sender.send_json({"type": "speak_start"})
    if on_speaking is not None:
        # Barge-in is armed from here: from this instant there is audio in the room, and an
        # interim transcript means the person is talking over Sarjy rather than to it.
        on_speaking()

    interrupted = False
    try:
        for text, lang in chunks:
            if isinstance(sender, TurnSender) and not sender.open:
                # Interrupted between chunks. Stop before paying for the next one — that,
                # not the frame already in flight, is where barge-in saves real money.
                interrupted = True
                break
            try:
                fault = injected_fault()
                if fault == "voice_quota":
                    raise voice.VoiceQuotaError("FAULT_INJECT=voice_quota")
                if fault == "voice":
                    raise voice.VoiceError("FAULT_INJECT=voice")
                wav = await asyncio.to_thread(voice.synthesize, text, lang, persona)
            except voice.VoiceQuotaError as exc:
                log.warning("pipeline: %s", exc)
                await send_error(sender, "voice_quota")
                break
            except voice.VoiceError as exc:
                log.error("pipeline: voice failed: %s", exc)
                await send_error(sender, "voice")
                break

            if timing["first_audio_ms"] is None:
                timing["first_audio_ms"] = (time.monotonic() - started) * 1000
            await sender.send_bytes(wav)
            timing["spoken"] += 1
    except asyncio.CancelledError:
        interrupted = True
        raise
    finally:
        timing["total_ms"] = (time.monotonic() - started) * 1000
        if not interrupted:
            # speak_end must always go out on a turn that ran to its own end, including a
            # half-spoken reply: it is what tells the browser the reply is over.
            await sender.send_json({"type": "speak_end"})

    return timing
