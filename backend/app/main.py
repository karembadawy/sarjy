# -*- coding: utf-8 -*-
"""FastAPI app — the voice loop on `/ws`, plus the small REST surface the UI reads.

`/ws` is the product: one connection is one call. `POST /api/chat` stays as the text-path
debug tool — the thing that is trivial to curl when audio misbehaves, which isolates brain
and database bugs from audio bugs exactly the way Phase 1 did. The `/api/users/...`
endpoints exist so the "what I remember about you" drawer and the bookings panel can show
the same rows the brain is reading (product.md §4).

HTTP endpoints are plain `def`, not `async def`: FastAPI runs those in a threadpool, which
is right for blocking SQLAlchemy and a blocking Gemini call (D-027). The WebSocket handler
is async and pushes the same blocking work into threads itself (see pipeline.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import (
    barge_in,
    brain,
    config,
    language,
    memory,
    personas,
    pipeline,
    speech_recognition,
    tools,
)
from .db import SessionLocal
from .models import Booking, ChatSession, Message, User

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s")
log = logging.getLogger("sarjy")

app = FastAPI(title="Sarjy", version="0.5.0")

# One id per *process*, minted at import. Cloud Run autoscales behind a single URL, so "were
# those five calls served by one instance or by five" is otherwise a log-archaeology question
# — and it is exactly the question `scripts/load_test.py` has to answer about isolation.
# `K_REVISION` is set by Cloud Run itself and is empty locally.
INSTANCE_ID = uuid.uuid4().hex[:8]
REVISION = config.get("K_REVISION", "local")

# Local dev plus whatever ALLOWED_ORIGINS names — in production, the Vercel domain (D-048).
ALLOWED_ORIGINS = config.allowed_origins()

# Vercel gives every preview build its own hostname, so the allowlist has to accept a suffix
# pattern (`*.vercel.app`) as well as exact origins. CORSMiddleware expresses that as a regex.
_EXACT_ORIGINS = [origin for origin in ALLOWED_ORIGINS if not origin.startswith("*.")]
_ORIGIN_REGEX = (
    "|".join(
        rf"https://[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*{re.escape(origin[1:])}"
        for origin in ALLOWED_ORIGINS
        if origin.startswith("*.")
    )
    or None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_EXACT_ORIGINS,
    allow_origin_regex=_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _error_detail(key: str) -> str:
    """The same bilingual sentence the voice loop shows, on one line for an HTTP body."""
    error = pipeline.ERRORS[key]
    return f"{error.arabic} · {error.english}"


# --------------------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------------------


class ChatRequest(BaseModel):
    user_id: uuid.UUID
    session_id: uuid.UUID
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value


class ChatResponse(BaseModel):
    reply: str
    user_language: str
    reply_language: str
    tool_calls: list[str] = []


class PersonaRequest(BaseModel):
    persona: str

    @field_validator("persona")
    @classmethod
    def known(cls, value: str) -> str:
        key = (value or "").strip().lower()
        if key not in personas.PERSONAS:
            raise ValueError(f"persona must be one of {sorted(personas.PERSONAS)}")
        return key


class FactOut(BaseModel):
    key: str
    value: str
    source_language: str | None
    updated_at: datetime


class BookingOut(BaseModel):
    id: int
    service: str
    scheduled_at: datetime
    notes: str | None
    status: str


# --------------------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "phase": 5,
        "instance": INSTANCE_ID,
        "revision": REVISION,
        "model": config.get("GEMINI_MODEL"),
        "extractor_model": config.gemini_extractor_model(),
        "tts_model": config.get("GEMINI_TTS_MODEL"),
        "tts_provider": config.get("TTS_PROVIDER", "gemini"),
        "speech_channels": speech_recognition.languages(),
        "personas": sorted(personas.PERSONAS),
        "tools": tools.NAMES,
        "barge_in": {
            "min_words": barge_in.min_words(),
            "min_confidence": barge_in.min_confidence(),
        },
        # Deploy-day debugging: "why does the browser say it cannot connect" is answered by
        # comparing the Vercel domain against this list, without reading any logs.
        "allowed_origins": ALLOWED_ORIGINS,
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    user = _get_or_create_user(db, request.user_id)
    _ensure_session(db, request.session_id, user.id)

    user_language = language.detect_language(request.text)
    _store(db, request.session_id, "user", request.text, user_language)

    # §6.2, the same deterministic rule the voice loop applies in pipeline._begin_turn. The
    # debug endpoint has to honour it too, or it stops being a faithful way to reproduce what
    # the socket did — which is the only reason this endpoint still exists.
    requested = language.explicit_language_request(request.text)
    if requested and user.preferred_language != requested:
        log.info("language: explicit switch → %s for user %s", requested, user.id)
        user.preferred_language = requested

    db.commit()  # the transcript survives even if Gemini falls over on the next line

    try:
        reply = brain.generate_reply(db, user.id, request.session_id, request.text)
    except brain.BrainQuotaError as exc:
        # Not a fault — the allowance is simply spent. Say so, in both languages, rather than
        # showing the same opaque line as real breakage. The debug endpoint answers with the
        # same words the voice loop uses, so a message seen in the browser can be reproduced
        # with a curl.
        log.warning("quota: %s", exc)
        raise HTTPException(
            status_code=429, detail=_error_detail("brain_quota"), headers={"Cache-Control": "no-store"}
        ) from exc
    except brain.BrainBusyError as exc:
        # A burst limit, not the day's allowance: Retry-After is a real number here, so it is
        # sent as one instead of being buried in prose.
        log.warning("busy: %s", exc)
        headers = {}
        if exc.retry_after_s:
            headers["Retry-After"] = str(int(exc.retry_after_s))
        raise HTTPException(
            status_code=429, detail=_error_detail("brain_busy"), headers=headers
        ) from exc
    except brain.BrainError as exc:
        log.error("brain failed: %s", exc)
        raise HTTPException(status_code=502, detail="Sarjy could not answer right now.") from exc

    reply_language = language.detect_language(reply.text)
    _store(db, request.session_id, "assistant", reply.text, reply_language)
    db.commit()

    log.info(
        "turn: user[%s] %r → assistant[%s] %r%s",
        user_language,
        request.text,
        reply_language,
        reply.text,
        f"  (tools: {', '.join(reply.tool_calls)})" if reply.tool_calls else "",
    )
    return ChatResponse(
        reply=reply.text,
        user_language=user_language,
        reply_language=reply_language,
        tool_calls=reply.tool_calls,
    )


# --------------------------------------------------------------------------------------
# What the UI reads back — memory drawer, bookings panel, persona toggle (product.md §4)
# --------------------------------------------------------------------------------------


@app.get("/api/users/{user_id}/facts", response_model=list[FactOut])
def read_facts(user_id: uuid.UUID, db: Session = Depends(get_db)) -> list[FactOut]:
    """Everything Sarjy remembers about this person — the transparency half of §9."""
    return [FactOut.model_validate(fact, from_attributes=True) for fact in memory.load_facts(db, user_id)]


@app.delete("/api/users/{user_id}/facts/{key}", status_code=204)
def forget_fact(user_id: uuid.UUID, key: str, db: Session = Depends(get_db)) -> None:
    if not memory.delete_fact(db, user_id, key):
        raise HTTPException(status_code=404, detail=f"No fact called {key!r} for this user.")
    db.commit()


@app.get("/api/users/{user_id}/bookings", response_model=list[BookingOut])
def read_bookings(user_id: uuid.UUID, db: Session = Depends(get_db)) -> list[BookingOut]:
    """Upcoming bookings, soonest first — the proof that `create_booking` wrote real rows."""
    rows = db.scalars(
        select(Booking)
        .where(
            Booking.user_id == user_id,
            Booking.status == "confirmed",
            Booking.scheduled_at >= datetime.now(timezone.utc),
        )
        .order_by(Booking.scheduled_at)
        .limit(20)
    ).all()
    return [BookingOut.model_validate(row, from_attributes=True) for row in rows]


@app.put("/api/users/{user_id}/persona")
def set_persona(
    user_id: uuid.UUID, request: PersonaRequest, db: Session = Depends(get_db)
) -> dict:
    """Switch persona mid-call. The next turn reads it from the row and swaps prompt+voice."""
    user = _get_or_create_user(db, user_id)
    user.preferred_persona = request.persona
    db.commit()
    log.info("persona: user %s → %s", user_id, request.persona)
    return {"persona": request.persona}


# --------------------------------------------------------------------------------------
# The voice loop
# --------------------------------------------------------------------------------------


class _Sender:
    """Serialises everything written to one socket.

    The interim-transcript callback and the turn that is streaming audio frames are separate
    tasks; two coroutines writing to the same WebSocket at once interleaves their frames.
    One lock removes the whole class of problem.
    """

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()

    async def send_json(self, data: dict) -> None:
        async with self._lock:
            await self._websocket.send_json(data)

    async def send_bytes(self, data: bytes) -> None:
        async with self._lock:
            await self._websocket.send_bytes(data)


# How long after a turn is accepted before it may be interrupted. The two Deepgram channels
# do not finish together — the loser goes on emitting interims about speech that has already
# been answered for up to ~600ms (D-042, D-046) — and those interims look exactly like
# somebody talking. This window is what tells them apart, and it costs nothing in practice
# because synthesis has not started yet either.
BARGE_IN_SETTLE_S = 1.0

# A background task must finish inside the socket's lifetime: Cloud Run allocates CPU only
# while a request is in flight, and a WebSocket is one long request, so a task that outlives
# it is throttled rather than run (D-051). This is how long we hold the socket open for the
# stragglers before giving up on them.
BACKGROUND_DRAIN_S = 5.0


class _Call:
    """The state one call needs in order to be interruptible."""

    def __init__(self, sender: _Sender) -> None:
        self.sender = sender
        self.turn: asyncio.Task | None = None
        self.gate: pipeline.TurnSender | None = None
        self.started_at = 0.0  # monotonic, when the current turn was accepted
        self.speaking = False  # has this turn reached synthesis?
        self.background: set[asyncio.Task] = set()
        # The last thing the user said, kept so a turn the brain could not answer can be
        # answered on a second attempt without the person having to say it all over again.
        self.last = None

    @property
    def busy(self) -> bool:
        return self.turn is not None and not self.turn.done()

    def settled(self) -> bool:
        return (time.monotonic() - self.started_at) >= BARGE_IN_SETTLE_S

    def begin(self, start) -> None:
        """Start a turn. `start` receives the turn's revocable sender and returns a coroutine."""
        self.gate = pipeline.TurnSender(self.sender)
        self.turn = asyncio.create_task(start(self.gate))
        self.started_at = time.monotonic()
        self.speaking = False

    def now_speaking(self) -> None:
        """Called by the pipeline the moment synthesis starts — this arms barge-in."""
        self.speaking = True

    def spawn(self, coroutine: Awaitable[None]) -> None:
        """Run something after the turn, owned by the socket so it cannot be orphaned."""
        task = asyncio.ensure_future(coroutine)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def interrupt(self, why: str) -> None:
        """Mute the in-flight turn, then tell the browser to stop playing.

        Deliberately does NOT wait for the cancelled turn to finish dying. It is normally
        parked in `asyncio.to_thread(voice.synthesize, ...)`, and an executor future that has
        already started cannot be cancelled — awaiting it held this coroutine, and with it
        the Deepgram listener that calls it, for a measured 3.0 seconds. The user is
        interrupting *because* they do not want to wait three seconds.

        Revoking the turn's sender is synchronous and is what makes not waiting safe: the
        dying turn cannot write another frame, and cannot send the `speak_end` that would
        otherwise land in the middle of the next reply. See pipeline.TurnSender.
        """
        gate, task = self.gate, self.turn
        self.gate, self.turn, self.speaking = None, None, False
        if gate is not None:
            gate.close()
        if task is not None and not task.done():
            task.cancel()
        log.info("barge-in: cut Sarjy off — %s", why)
        await self.sender.send_json({"type": "stop_speaking"})

    async def drain(self) -> None:
        if not self.background:
            return
        log.info("ws: waiting for %d background task(s)", len(self.background))
        await asyncio.wait(set(self.background), timeout=BACKGROUND_DRAIN_S)


@app.websocket("/ws")
async def voice(websocket: WebSocket) -> None:
    """One connection = one call session (see the protocol in docs/decisions.md, D-039)."""
    # CORS middleware does not see WebSocket handshakes, so the allowlist is checked here or
    # nowhere. Closing before `accept()` refuses the handshake outright (D-048).
    origin = websocket.headers.get("origin")
    if not config.origin_allowed(origin):
        log.warning("ws: refused origin %r (allowed: %s)", origin, ALLOWED_ORIGINS)
        await websocket.close(code=1008)
        return

    await websocket.accept()
    sender = _Sender(websocket)

    # ---- hello ---------------------------------------------------------------------
    try:
        hello = await websocket.receive_json()
    except (WebSocketDisconnect, ValueError):
        return

    if hello.get("type") != "hello":
        await pipeline.send_error(sender, "speech")
        await websocket.close(code=1002)
        return

    try:
        user_id = uuid.UUID(str(hello["user_id"]))
        session_id = uuid.UUID(str(hello["session_id"]))
    except (KeyError, ValueError):
        await pipeline.send_error(sender, "speech")
        await websocket.close(code=1002)
        return

    persona_key = personas.get_persona(hello.get("persona")).key
    persona_key = await pipeline.open_session(user_id, session_id, persona_key)

    # ---- the turn machinery --------------------------------------------------------
    call = _Call(sender)

    async def on_interim(text: str, confidence: float) -> None:
        await sender.send_json({"type": "interim", "text": text})
        # An interim only interrupts once there is audio in the room to interrupt. Before
        # that there is nothing to cut, and the leftovers of the previous utterance are
        # still arriving from the losing channel.
        if call.busy and call.speaking and call.settled():
            if barge_in.is_interruption(text, confidence):
                await call.interrupt(f"interim {text!r} (conf {confidence:.2f})")

    def start_turn(utterance: speech_recognition.Utterance, retry: bool = False) -> None:
        call.last = utterance
        call.begin(
            lambda gate: pipeline.run_turn(
                gate,
                user_id,
                session_id,
                utterance,
                on_speaking=call.now_speaking,
                background=call.spawn,
                retry=retry,
            )
        )

    async def on_final(utterance: speech_recognition.Utterance) -> None:
        if call.busy:
            # A whole endpointed sentence arriving mid-turn is a stronger signal than an
            # interim — Deepgram already decided the person stopped talking — so it does not
            # wait for synthesis to have started. It still has to clear the same bar, or
            # Sarjy's own voice coming back through the speaker would answer itself.
            if call.settled() and barge_in.is_interruption(utterance.text, utterance.confidence):
                await call.interrupt(f"final {utterance.text!r}")
            else:
                log.info("ws: a turn is already running — dropping %r", utterance.text)
                return

        await sender.send_json(
            {"type": "final", "text": utterance.text, "language": utterance.language}
        )
        start_turn(utterance)

    async def on_trouble(state: str) -> None:
        """A Deepgram channel dropped. Say so — a mic that stopped working must never be
        indistinguishable from a room that went quiet (D-050)."""
        await pipeline.send_error(
            sender, "speech_retrying" if state == "retrying" else "speech"
        )

    try:
        stt = speech_recognition.Transcriber(
            on_interim=on_interim, on_final=on_final, on_trouble=on_trouble
        )
        await stt.start()
    except speech_recognition.SpeechRecognitionError as exc:
        log.error("ws: %s", exc)
        await pipeline.send_error(sender, "speech")
        await websocket.close(code=1011)
        return

    # The instance id rides along on `ready` so a client can tell whether it shares a process
    # with anybody — the load test counts distinct values, and a browser never looks at it.
    await sender.send_json({"type": "ready", "instance": INSTANCE_ID})
    # The negotiated MediaRecorder container is logged because it is the first thing to
    # check when a phone transcribes as silence: Deepgram's live API cannot decode AAC, and
    # AAC in an MP4 container is what Safari before 18.4 is limited to (D-050).
    log.info(
        "ws: session %s open (user %s, persona %s, audio %s @ %sms)",
        session_id,
        user_id,
        persona_key,
        hello.get("audio_mime") or "unknown",
        hello.get("timeslice_ms") or "?",
    )

    # Sarjy speaks first (product.md §5). It runs as an ordinary turn, so the user can talk
    # straight over it — which is exactly what someone in a hurry does to a greeting.
    call.begin(
        lambda gate: pipeline.greet(gate, user_id, session_id, on_speaking=call.now_speaking)
    )

    # ---- pump ----------------------------------------------------------------------
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (chunk := message.get("bytes")) is not None:
                await stt.feed(chunk)
            elif (text := message.get("text")) is not None:
                try:
                    kind = json.loads(text).get("type")
                except ValueError:
                    log.warning("ws: ignoring non-JSON text frame")
                    continue
                if kind == "bye":
                    break
                if kind == "retry":
                    # The browser waited out the rate limit and is asking for the same
                    # utterance again. Retrying here rather than asking the person to repeat
                    # themselves is the whole point: they already said it once, and it is
                    # still in `messages` — a second attempt costs them nothing.
                    if call.busy or call.last is None:
                        log.info("ws: ignoring a retry with nothing to retry")
                        continue
                    log.info("ws: retrying %r at the browser's request", call.last.text)
                    start_turn(call.last, retry=True)
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001 — the call ends, but tidily
        log.error("ws: %s", exc)
    finally:
        if call.busy:
            call.turn.cancel()
        await stt.aclose()
        # Before the session row is closed, and before this coroutine returns: on Cloud Run
        # this handler *is* the request, and CPU stops being allocated the moment it ends.
        await call.drain()
        await pipeline.close_session(session_id)
        log.info("ws: session %s closed", session_id)


# --------------------------------------------------------------------------------------
# Row helpers
# --------------------------------------------------------------------------------------


def _get_or_create_user(db: Session, user_id: uuid.UUID) -> User:
    """Identity is a UUID the browser minted; first sight of it creates the row (D-011)."""
    user = db.get(User, user_id)
    if user is None:
        user = User(id=user_id, preferred_persona=personas.DEFAULT_PERSONA_KEY)
        db.add(user)
        db.flush()
        log.info("new user %s", user_id)
    return user


def _ensure_session(db: Session, session_id: uuid.UUID, user_id: uuid.UUID) -> ChatSession:
    """One session row per page load; created on its first message."""
    chat_session = db.get(ChatSession, session_id)
    if chat_session is None:
        chat_session = ChatSession(id=session_id, user_id=user_id)
        db.add(chat_session)
        db.flush()
        log.info("new session %s for user %s", session_id, user_id)
    return chat_session


def _store(db: Session, session_id: uuid.UUID, role: str, content: str, lang: str) -> Message:
    message = Message(session_id=session_id, role=role, content=content, language=lang)
    db.add(message)
    db.flush()
    return message
