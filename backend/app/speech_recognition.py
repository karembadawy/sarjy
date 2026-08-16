# -*- coding: utf-8 -*-
"""Speech recognition: a two-channel Deepgram racer (D-036).

Deepgram's `language=multi` code-switching mode does not include Arabic — measured, not
assumed (see D-036 for the evidence table). Feeding it Egyptian Arabic returns Latin or
Devanagari transliteration. What *does* work is the monolingual Arabic model: `language=ar`
transcribes code-switched Egyptian near-perfectly and renders borrowed English words in
Arabic script ("book" → "بوك"), which is exactly what product.md §6.4 asks the *replies* to
do. Its one failure: pure English comes back empty.

So we open one connection per language and race them. Every audio chunk is fanned out to
both; when an utterance completes we pick the better transcript. The channel that did not
understand the audio reliably returns an empty string, which makes the choice easy:

    non-empty beats empty · if both are non-empty, higher confidence wins

Deepgram's own language guess travels alongside the text for the writeup, but OUR
`language.py` detector is the label of record — one rule for the whole app, Phase 1 onward.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from deepgram import AsyncDeepgramClient
from deepgram.core.events import EventType

from . import config, language

log = logging.getLogger("sarjy.speech")

# nova-3 is the only Deepgram model with Arabic at all (D-003, re-verified in Phase 2).
MODEL = "nova-3"

# How long a finished utterance waits for the other channel before we decide without it.
#
# The two channels do NOT finish together: each runs its own endpointing over audio it
# understands differently, and the observed skew ran from 130ms to 510ms. A window wide
# enough for the worst case would tax every turn, so it adapts to how much the first arrival
# can be trusted — a confident transcript is very unlikely to be beaten, a doubtful one
# usually is. Measured: the English channel's 0.55-confidence "Anasrgi," arriving first must
# wait for the Arabic channel's 0.98 real sentence 510ms behind it.
CONFIDENT_ENOUGH = 0.90
RACE_WINDOW_CONFIDENT_S = 0.25
RACE_WINDOW_UNSURE_S = 0.90

# Deepgram's own default is 10ms, which cuts a speaker off mid-thought. Egyptian speakers
# pause mid-sentence often enough that 300ms splits one thought into two turns — Sarjy then
# answers half a question. 500ms still feels immediate and survives a thinking pause.
DEFAULT_ENDPOINTING_MS = 500

# Backstop for the turn end. `speech_final` is Deepgram's endpointing verdict and is what
# normally fires; `UtteranceEnd` is derived from word timings and arrives even when
# endpointing does not, so it rescues a buffer that would otherwise sit unflushed forever.
# Deepgram requires >= 1000 and interim_results for it to be sent at all.
UTTERANCE_END_MS = 1000

# Deepgram's Arabic model sometimes throws away a transcript it has already shown us:
# the interims carry the words, and then the `is_final` for the same span comes back with
# an empty string, zero confidence and no word list at all. Measured on
# "احجزلي ميعاد عند الدكتور بكرة بعد العصر" — the demo's own utterance — it happens on 4 runs
# out of 4, while "عايز أعمل بوك لميتنج بكرة الساعة خمسة" is fine on 4 out of 4. It is
# deterministic per utterance and does not happen on the English channel at all.
#
# What rescues it: an explicit `Finalize`, which makes Deepgram flush the audio it is holding
# and return the words properly. The tell that one is needed is an interim that comes back
# EMPTY after we have already been shown words — that is Deepgram losing its grip on the
# segment, a moment before it discards it.
#
# The flush is not free: it ends the segment, so a sentence the person has not finished gets
# cut in two. That is why a provoked final does not end the turn (see `from_finalize` in
# on_message) — the pieces accumulate and the turn still ends where the person stopped
# talking. Full evidence in the decision log.
#
# **Measured off by default.** The Finalize ends the segment wherever it lands, and if less
# than about a second of speech follows it, Deepgram emits no interim for the remainder and
# the same bug eats *that* segment with nothing left to rescue it from. Head to head on the
# demo utterance, three runs each against the deployed service:
#     on  → 'مِيعَادٍ عِنْدَ الدُّكْتُورُ بَكْن'      (cut mid-word, 3 of 3)
#     off → 'مِيعاد عند الدكتور بَكْرَةِ بعد'   (3 of 3, and Sarjy asks "بعد إيه؟")
# So the interim fallback below is the defence that carries its weight, and this one is kept
# only as a knob for the case where a future Deepgram fixes the underlying bug differently.
FINALIZE_ON_LOST_INTERIM = (
    config.get("FINALIZE_ON_LOST_INTERIM", "0") or "0"
).strip() not in ("0", "false", "no")

# If Deepgram never sends a natural end-of-turn after a Finalize we provoked, this is how
# long we wait before speaking for it. Longer than STALL_GRACE_S on purpose: the measured gap
# between a provoked final and the natural one that follows it ran to 1.73s.
FINALIZE_GRACE_S = 3.0

# ...but it must stay a *backstop*. The two channels segment audio differently — the channel
# that did not understand the language reaches its utterance boundary at a completely
# different moment — so flushing the instant UtteranceEnd arrives lets a junk transcript
# reach the race before the good channel has finished, and win it by default. Measured: the
# English channel flushed "Anasrgi," and beat the Arabic channel's real sentence. So an
# UtteranceEnd only flushes if nothing else has flushed this channel after this long.
STALL_GRACE_S = 1.5

# How long an interim may be the last thing a channel said before we take it at its word.
# Deepgram emits one about every second while somebody is speaking, so this is comfortably
# longer than a pause inside a sentence and much shorter than the 19-second hole a live call
# measured when nothing was watching interims at all.
INTERIM_STALL_S = 2.5

# Deepgram closes a live connection that goes 10 seconds without audio (NET-0001). Phase 4's
# barge-in keeps the microphone live through playback, so audio now flows continuously and
# the common case no longer needs the heartbeat — but the mute button still stops the stream
# dead for as long as the user likes, which is exactly the silence NET-0001 kills a socket
# for. So it stays. Docs say every 3–5s; 4s leaves room for a slow event loop.
KEEPALIVE_INTERVAL_S = 4.0

# A channel that drops mid-call is put back rather than left dead. Three attempts with a
# widening gap covers a network blip and a Deepgram hiccup; past that the honest answer is
# "hang up and tap again" rather than a reconnect loop nobody can see failing.
MAX_RECONNECTS = 3
RECONNECT_BACKOFF_S = (0.5, 2.0, 5.0)

# A stream Deepgram finished normally is reopened almost at once — there is nothing wrong,
# the last container simply ran out (see _listen).
REOPEN_DELAY_S = 0.1

# ...but only if it lasted this long first. A connection that "completes" the instant it
# opens is not completing, it is failing politely, and it must not become a silent spin.
SETTLED_S = 1.0

# `SPEECH_DEBUG=1` logs every message from both channels. Off by default because it is one
# line per 250ms per channel; on when "the call went deaf" needs an answer rather than a guess.
SPEECH_DEBUG = (config.get("SPEECH_DEBUG", "") or "").strip() not in ("", "0", "false")


class SpeechRecognitionError(RuntimeError):
    """Deepgram could not be reached, or refused the connection."""


@dataclass
class Utterance:
    """One completed thing the user said, after the race has been decided."""

    text: str
    language: str  # ours (product.md §6.5) — the label of record
    confidence: float
    channel: str  # which Deepgram language setting won
    deepgram_languages: list[str] = field(default_factory=list)  # what Deepgram thought
    # Every channel that spoke, keyed by language: {"text", "confidence", "score"}. Structured
    # rather than pre-formatted, because the loser's numbers are read by more than the log —
    # `eval/run_benchmark.py` scores each channel from exactly what the shipped racer saw.
    alternatives: dict[str, dict] = field(default_factory=dict)
    # `time.monotonic()` at the moment this person stopped speaking, reconstructed from
    # Deepgram's own word timings. None when the channel reported no word boundaries.
    speech_ended_at: float | None = None

    def __str__(self) -> str:
        return f"[{self.channel}→{self.language} {self.confidence:.2f}] {self.text}"


@dataclass
class _Candidate:
    text: str
    confidence: float
    deepgram_languages: list[str]
    # `time.monotonic()` at which this channel heard the last word end.
    speech_ended_at: float | None = None


def languages() -> list[str]:
    """The channels to race, from DEEPGRAM_LANGUAGES (default the Arabic/English pair)."""
    raw = config.get("DEEPGRAM_LANGUAGES", "ar,en") or "ar,en"
    return [part.strip() for part in raw.split(",") if part.strip()]


def endpointing_ms() -> int:
    return int(config.get("DEEPGRAM_ENDPOINTING_MS", str(DEFAULT_ENDPOINTING_MS)))


def content_score(candidate: _Candidate) -> float:
    """How much of the utterance this channel actually captured.

    Confidence alone is the wrong measure, and it failed in production: asked a question in
    Egyptian, the Arabic channel returned all eight words of it while the English channel
    returned the two-word fragment "Type, infarct" at 0.83 — and won, because 0.83 was the
    larger number. Sarjy answered with the medical definition of an infarct.

    Deepgram's confidence is roughly a per-word accuracy, so multiplying it by the number of
    words approximates *expected correct words* — how much real content the channel heard.
    A short fragment can no longer outrank a whole sentence by being sure of very little.
    """
    return candidate.confidence * len(candidate.text.split())


def _describe(alternatives: dict[str, dict]) -> str:
    """One log-friendly line for every channel that spoke, winner and loser alike.

    A wrong pick cannot be diagnosed after the fact without the loser's numbers — that cost
    us a whole session once (D-045), which is why this prints the score as well as the text.
    """
    return "  ".join(
        # Two decimals on the score, not one: the numbers that decided the D-045 failure were
        # 6.30 against 1.66, and a race lost by 0.04 rounds to a tie at one decimal.
        f"{name}: {entry['text']!r} (conf {entry['confidence']:.2f}, score {entry['score']:.2f})"
        for name, entry in alternatives.items()
    )


def pick_winner(candidates: dict[str, _Candidate]) -> tuple[str, _Candidate] | None:
    """The race rule. Pure function so the tests can prove it without a network."""
    scored = [(name, c) for name, c in candidates.items() if c.text.strip()]
    if not scored:
        return None
    # Length breaks an exact tie, so "No." never loses to an equally-confident "لأ".
    scored.sort(key=lambda item: (content_score(item[1]), len(item[1].text)), reverse=True)
    return scored[0]


class _Channel:
    """One Deepgram connection listening in one language.

    Deepgram splits an utterance across several `is_final` messages and marks the end of the
    turn with `speech_final`. We accumulate the former and emit on the latter, so the
    pipeline sees whole sentences rather than fragments.
    """

    def __init__(self, name: str, transcriber: Transcriber) -> None:
        self.name = name
        self.transcriber = transcriber
        self.connection = None
        self._parts: list[str] = []
        self._confidences: list[float] = []
        self._deepgram_languages: list[str] = []
        self._speech_ended_at: float | None = None
        # Have we been shown words that Deepgram might still discard? Re-armed by every new
        # piece of text, disarmed by a flush and by asking for a Finalize.
        self._armed = False
        # The best hypothesis Deepgram has shown for the segment currently in progress.
        # Interims within a segment are cumulative, so the longest is the whole of it — and
        # when the segment's own `is_final` comes back empty, this is what it should have
        # said (D-055). Cleared at every `is_final`, because that starts a new segment.
        self._interim_best = ""
        self._interim_confidence = 0.0
        self._interim_ended_at: float | None = None
        # Bumped on every flush, so a pending stall-check can tell "still the same stuck
        # buffer" from "already flushed and refilled by new speech".
        self._generation = 0
        # Bumped on every interim that carries text, for the watchdog below.
        self._interim_seq = 0
        # Which turn this channel's in-progress utterance belongs to. See Transcriber._epoch.
        self._epoch: int | None = None
        # How many times this channel has had to be put back after an *unexpected* drop.
        self.attempts = 0
        # Deepgram sends `Metadata` — its end-of-request summary — immediately before closing
        # a stream it considers finished. Seeing one turns the close that follows from "the
        # connection fell over" into "that stream is complete", which are worth telling apart.
        self.saw_metadata = False

    async def on_message(self, message) -> None:
        try:
            await self._on_message(message)
        except Exception:  # noqa: BLE001
            # The SDK dispatches these from its read loop; an exception that escapes here
            # takes the whole listener with it, and the call goes deaf in total silence —
            # exactly the failure mode D-050 warned about. Log it and keep listening.
            log.exception("speech[%s]: handler failed on a %s message", self.name,
                          getattr(message, "type", "?"))

    async def _on_message(self, message) -> None:
        kind = getattr(message, "type", None)
        if SPEECH_DEBUG and kind == "Results":
            try:
                alternative = message.channel.alternatives[0]
                log.info(
                    "speech[%s]: is_final=%s speech_final=%s conf=%.2f %r",
                    self.name, message.is_final, message.speech_final,
                    alternative.confidence or 0.0, alternative.transcript,
                )
            except (AttributeError, IndexError):
                log.info("speech[%s]: malformed Results", self.name)
        elif SPEECH_DEBUG:
            log.info("speech[%s]: %s", self.name, kind)

        if kind == "Metadata":
            # Deepgram has finished with this stream and the socket is about to close. See
            # _listen: this is what separates a completed stream from a dropped connection.
            self.saw_metadata = True
            return

        if kind == "UtteranceEnd":
            if self._parts:
                asyncio.create_task(self._flush_if_stalled(self._generation))
            return

        if kind != "Results":
            return
        try:
            alternative = message.channel.alternatives[0]
        except (AttributeError, IndexError):
            return

        text = (alternative.transcript or "").strip()

        # Stamp this channel's utterance with the turn it belongs to, the moment it starts
        # hearing anything. If that turn is answered before this channel finishes, whatever
        # it eventually produces is about speech the user has already had a reply to.
        if text and self._epoch is None:
            self._epoch = self.transcriber._epoch

        if not message.is_final:
            # Live partial. Only one channel produces text for any given utterance, so
            # forwarding every non-empty interim gives the UI a single clean stream without
            # the two channels fighting over the caption line. Since Phase 4 this is also
            # the barge-in trigger, so the confidence travels with it.
            if text:
                self._armed = True
                if len(text) > len(self._interim_best):
                    self._interim_best = text
                    self._interim_confidence = alternative.confidence or 0.0
                    self._interim_ended_at = self._word_end(message, alternative)
                # Words that never become a final are invisible to both other watchdogs: one
                # needs an `is_final`, the other needs an `UtteranceEnd`. Measured on a live
                # call, "عايز كتب رعب" sat in an open segment for **19 seconds** and then
                # surfaced 44s after it was said, two turns later. So an interim starts its
                # own timer.
                self._interim_seq += 1
                asyncio.create_task(
                    self._flush_if_interim_stalled(self._generation, self._interim_seq)
                )
                await self.transcriber._emit_interim(text, alternative.confidence or 0.0)
            elif self._armed:
                # Words we were shown, then an empty hypothesis for the same span: Deepgram
                # is about to drop them. Ask for them before it does.
                await self._provoke_finalize()
            return

        # An `is_final` closes a segment, whatever it contains.
        confidence = alternative.confidence or 0.0
        rescued = False
        if not text and self._interim_best:
            # The segment's own final came back empty while its interims carried words —
            # the Deepgram failure of D-055. The interims are what it heard; keep them.
            text, confidence, rescued = self._interim_best, self._interim_confidence, True
            log.warning("speech[%s]: final came back empty — keeping the interim %r",
                        self.name, text)

        if text:
            self._parts.append(text)
            self._confidences.append(confidence)
            for code in alternative.languages or []:
                if code not in self._deepgram_languages:
                    self._deepgram_languages.append(code)
            end = self._interim_ended_at if rescued else self._word_end(message, alternative)
            if end is not None and (self._speech_ended_at is None or end > self._speech_ended_at):
                self._speech_ended_at = end
            # A Finalize we asked for must NOT re-arm the trigger with its own answer.
            # Measured on the deployed service, 4 runs of 4: it did, so the next empty
            # interim provoked a second Finalize that closed the segment before Deepgram
            # had transcribed the rest of the sentence — and "بعد العصر" was never heard at
            # all. Only genuinely new speech re-arms.
            if not getattr(message, "from_finalize", False):
                self._armed = True

        self._interim_best, self._interim_confidence, self._interim_ended_at = "", 0.0, None

        provoked = bool(getattr(message, "from_finalize", False))
        if message.speech_final and not provoked:
            await self._flush()
            return

        if provoked:
            # We asked for this one. It ends a *segment*, not the person's sentence — they
            # may well still be mid-word — so keep accumulating and let the natural
            # end-of-turn decide.
            log.info("speech[%s]: recovered %r via Finalize", self.name, " ".join(self._parts))

        if self._parts:
            # Whatever we are holding, something must eventually say it. Measured live: the
            # Arabic channel held 'احجزي لي' from a booking request that never received a
            # natural `speech_final`, so it never entered the race — and the English
            # channel's 'Exhibit' won unopposed. Words we have heard must never be able to
            # sit in a buffer forever.
            asyncio.create_task(self._flush_if_abandoned(self._generation))

    @staticmethod
    def _word_end(message, alternative) -> float:
        """Wall-clock (monotonic) at which this person stopped speaking, near enough.

        Measured *backwards from the arrival of this message* rather than forwards from the
        start of the stream, and that is a correction, not a preference: `Finalize` resets
        Deepgram's stream clock, so a session-long origin plus a word offset produced
        `speech_recognition_ms` values of 21s, 48s, 89s and 124s in the first live run —
        numbers that grew with the age of the call rather than describing anything.

        This message covers audio up to `start + duration`, and the last word ended some way
        before that. Subtracting the difference from the moment the message reached us places
        the end of speech on our own clock with no cross-message state to go wrong. Stated
        approximation: it also absorbs Deepgram's own delivery lag, so the number is, if
        anything, slightly generous to us.
        """
        segment_end = (message.start or 0.0) + (message.duration or 0.0)
        words = getattr(alternative, "words", None) or []
        ends = [word.end for word in words if getattr(word, "end", None) is not None]
        # No word timings (a rescued interim, an odd payload) means no trailing silence to
        # subtract: the best guess is that speech ran to the end of what was transcribed.
        last_word = max(ends) if ends else segment_end
        return time.monotonic() - max(0.0, segment_end - last_word)

    async def _provoke_finalize(self) -> None:
        """Ask Deepgram to flush now, before it discards the words it has already shown us."""
        self._armed = False
        if not FINALIZE_ON_LOST_INTERIM or self.connection is None:
            return
        try:
            await self.connection.send_finalize()
        except Exception as exc:  # noqa: BLE001 — worst case we lose what we were losing anyway
            log.warning("speech[%s]: could not send Finalize: %s", self.name, exc)

    async def _flush_if_stalled(self, generation: int) -> None:
        """Rescue a buffer that endpointing never closed — and only such a buffer."""
        await asyncio.sleep(STALL_GRACE_S)
        if self._generation != generation or not self._parts:
            return  # speech_final got there first, which is the normal case
        log.warning("speech[%s]: endpointing never fired — flushing on UtteranceEnd", self.name)
        await self._flush()

    async def _flush_if_interim_stalled(self, generation: int, seq: int) -> None:
        """Speak for a segment that produced words and then simply stopped.

        Deepgram emits an interim about once a second while somebody is talking, so a gap
        this long means they have stopped — whatever its endpointing thinks. Measured live:
        `endpointing never fired` is the norm rather than the exception for this speaker, and
        when the segment also never produced an `is_final`, nothing at all was watching it.

        **Silence is a property of the room, not of a channel.** The first version of this
        keyed off *this* channel going quiet, and broke immediately: the English channel has
        nothing to transcribe during Egyptian speech, so its last interim ("I is") sat
        unchanged, the watchdog took it at its word mid-sentence, and the race it started
        dragged in a half-finished Arabic hypothesis — a truncated transcript, and then a
        duplicate turn when the real one arrived. So the wait is for the *session* to go
        quiet: as long as any channel is still hearing words, nobody has stopped talking.
        """
        while True:
            await asyncio.sleep(INTERIM_STALL_S)
            if self._generation != generation or self._interim_seq != seq:
                return  # flushed, or this channel heard more — the normal case
            if not self._interim_best:
                return
            quiet_for = time.monotonic() - self.transcriber._last_interim_at
            if quiet_for >= INTERIM_STALL_S:
                break
            # Another channel is still hearing speech. Wait it out rather than answering
            # half a sentence.

        log.warning("speech[%s]: no final after the interims — keeping %r",
                    self.name, self._interim_best)
        self._promote_interim()
        await self._flush()

    def _promote_interim(self) -> None:
        """Treat the segment's best interim as if it had been finalised.

        Interims inside a segment are cumulative, so the longest one *is* that segment
        (D-055). This is the same rescue an empty final gets, reached by a different route.
        """
        if not self._interim_best:
            return
        self._parts.append(self._interim_best)
        self._confidences.append(self._interim_confidence)
        end = self._interim_ended_at
        if end is not None and (self._speech_ended_at is None or end > self._speech_ended_at):
            self._speech_ended_at = end
        self._interim_best, self._interim_confidence, self._interim_ended_at = "", 0.0, None

    def take(self, epoch: int) -> _Candidate | None:
        """Hand over whatever this channel is holding, right now, and forget it.

        Used by the race when it is about to decide *without* this channel: a rival that has
        already heard the whole sentence must not lose because its own flush timer is slower
        than the winner's. Measured live — the English channel's stall-breaker fires at 1.5s
        and the Arabic channel's abandoned-buffer watchdog at 3.0s, so English won a sentence
        with "Eiscotobrobe." while Arabic sat on "عايز كتب رعب" at confidence 0.99.

        **Finished segments only — interims are deliberately not promoted here.** An interim
        is a hypothesis about a sentence that may still be in progress, and handing one to a
        race triggered by the *other* channel produced exactly that: a transcript cut off at
        "بكرتي الساعة", missing the last word, followed by a duplicate turn when the real
        final arrived. Words stuck as interims are the interim watchdog's job, and it waits
        for the whole room to go quiet first.
        """
        if not self._parts or self._epoch is None or self._epoch < epoch:
            return None
        self._generation += 1
        candidate = _Candidate(
            text=" ".join(self._parts).strip(),
            confidence=(sum(self._confidences) / len(self._confidences))
            if self._confidences
            else 0.0,
            deepgram_languages=list(self._deepgram_languages),
            speech_ended_at=self._speech_ended_at,
        )
        self.reset()
        return candidate if candidate.text else None

    async def _flush_if_abandoned(self, generation: int) -> None:
        """Speak for a natural end-of-turn that never arrived, so nothing heard is stranded."""
        await asyncio.sleep(FINALIZE_GRACE_S)
        if self._generation != generation or not self._parts:
            return  # the natural speech_final flushed it, which is the normal case
        log.warning("speech[%s]: no natural end of turn — flushing %r", self.name,
                    " ".join(self._parts))
        await self._flush()

    async def _flush(self) -> None:
        """Hand the buffered segments to the race as one complete utterance."""
        self._generation += 1
        full = " ".join(self._parts).strip()
        confidence = (
            sum(self._confidences) / len(self._confidences) if self._confidences else 0.0
        )
        detected = list(self._deepgram_languages)
        epoch = self._epoch
        speech_ended_at = self._speech_ended_at
        self.reset()

        await self.transcriber._submit(
            self.name, _Candidate(full, confidence, detected, speech_ended_at), epoch
        )

    def reset(self) -> None:
        """Forget the in-progress utterance entirely."""
        self._parts.clear()
        self._confidences.clear()
        self._deepgram_languages.clear()
        self._speech_ended_at = None
        self._armed = False
        self._interim_best, self._interim_confidence, self._interim_ended_at = "", 0.0, None
        self._epoch = None
        # NOT saw_metadata: that describes the *connection*, not the utterance. Clearing it
        # here lost a race by five milliseconds — the turn resolved, reset() wiped the flag,
        # and the close that arrived immediately afterwards was reported to the user as a
        # dropped microphone when Deepgram had simply finished a completed stream.


class Transcriber:
    """Feed audio in, get interim texts and finished utterances out.

    Usage:
        async with Transcriber(on_interim=..., on_final=...) as stt:
            await stt.feed(chunk)
    """

    def __init__(
        self,
        on_interim: Callable[[str, float], Awaitable[None]],
        on_final: Callable[[Utterance], Awaitable[None]],
        channel_languages: list[str] | None = None,
        on_trouble: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.on_interim = on_interim
        self.on_final = on_final
        self.channel_languages = channel_languages or languages()
        # Called with "retrying" when a channel drops and we are putting it back, and with
        # "lost" when it will not come back. Optional so tests and scripts can ignore it; the
        # voice loop passes one, because a microphone that has quietly stopped working is the
        # single worst thing this program can do (D-050).
        self.on_trouble = on_trouble

        self._client: AsyncDeepgramClient | None = None
        self._stack = AsyncExitStack()
        self._channels: dict[str, _Channel] = {}
        self._listeners: list[asyncio.Task] = []
        self._heartbeat: asyncio.Task | None = None
        # Set by aclose(), so a connection closing because *we* hung up is not mistaken for
        # one that fell over.
        self._closing = False

        self._pending: dict[str, _Candidate] = {}
        self._race_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        # One turn = one epoch. Bumped the moment a race is decided, so a channel that is
        # still transcribing speech from the previous turn can be told apart from one that
        # has heard something genuinely new.
        self._epoch = 0
        # When any channel last produced words — the room's silence clock, not a channel's.
        self._last_interim_at = time.monotonic()

    # ---------------------------------------------------------------------------------
    # Lifecycle
    # ---------------------------------------------------------------------------------

    async def __aenter__(self) -> Transcriber:
        await self.start()
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def start(self) -> None:
        self._client = AsyncDeepgramClient(api_key=config.deepgram_api_key())

        for name in self.channel_languages:
            channel = _Channel(name, self)
            self._channels[name] = channel
            try:
                await self._connect(channel)
            except Exception as exc:  # noqa: BLE001 — surfaced to the user as one message
                await self.aclose()
                raise SpeechRecognitionError(
                    f"Could not open the Deepgram '{name}' channel: {exc}"
                ) from exc
            self._listeners.append(asyncio.create_task(self._listen(channel)))

        self._heartbeat = asyncio.create_task(self._keep_alive())
        log.info("speech: racing channels %s at endpointing=%dms", self.channel_languages, endpointing_ms())

    async def _connect(self, channel: _Channel) -> None:
        """Open (or re-open) one channel's Deepgram connection and wire its handlers."""
        connection = await self._stack.enter_async_context(
            self._client.listen.v1.connect(
                model=MODEL,
                language=channel.name,
                interim_results="true",
                punctuate="true",
                smart_format="true",
                endpointing=endpointing_ms(),
                utterance_end_ms=UTTERANCE_END_MS,
                # No `encoding` / `sample_rate` on purpose: the browser sends containerised
                # WebM/Opus and Deepgram reads the container header. Setting them here is the
                # classic cause of silent empty transcripts.
            )
        )
        connection.on(EventType.MESSAGE, channel.on_message)
        connection.on(EventType.ERROR, self._make_error_handler(channel.name))
        channel.connection = connection
        # A fresh connection has not been told anything yet. This is the only place the flag
        # is cleared, so it lives exactly as long as the connection it describes.
        channel.saw_metadata = False

    async def _listen(self, channel: _Channel) -> None:
        """Run one channel's read loop for the life of the call, putting it back when it ends.

        `start_listening()` returns only when the connection closes. Before Phase 5 that
        simply ended the task, and the call went on looking perfectly healthy while hearing
        nothing at all — the failure shape D-050 named, where an error path that keeps a call
        alive also hides that the call is broken. So the channel is reopened either way.

        **Two very different closes reach this line, and conflating them cries wolf.**

        *A completed stream.* Deepgram closes with 1000 (OK), preceded by `Metadata`, as soon
        as it has received all the audio a container declared. A WAV header states its own
        length, so every script that streams a file — `ws_smoke.py`, `load_test.py`,
        `eval/run_benchmark.py` — gets one of these at the end of its clip, measured at 4.32s
        into a 4.62s file. The browser cannot: MediaRecorder's WebM/Opus is a live container
        with no declared length, which is why a real call keeps one connection for its whole
        life and multi-turn conversations were never affected. That close is expected, so it
        is put back quietly and the user is told nothing.

        *A drop.* Anything else — a network blip, an idle timeout we lost the race with
        (NET-0001), Deepgram restarting something. That one is announced while it is fixed: a
        reconnect the user is not told about is indistinguishable from the deafness it is
        curing.

        A "completed" close arriving immediately after connecting is counted as a drop
        anyway. Otherwise a Deepgram that closed every new connection at once would spin here
        forever, silently.
        """
        while not self._closing:
            connected_at = time.monotonic()
            try:
                await channel.connection.start_listening()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("speech[%s]: listener stopped: %s", channel.name, exc)
            if self._closing:
                return

            completed = channel.saw_metadata and (time.monotonic() - connected_at) >= SETTLED_S
            # Nothing may be sent to a connection that is being replaced; `feed` skips a
            # channel with no connection rather than logging a dropped chunk every 250ms.
            channel.connection = None
            channel.reset()  # also clears saw_metadata

            if completed:
                delay = REOPEN_DELAY_S
                log.info("speech[%s]: stream complete — reopening", channel.name)
            else:
                channel.attempts += 1
                if channel.attempts > MAX_RECONNECTS:
                    log.error("speech[%s]: gave up after %d reconnects",
                              channel.name, MAX_RECONNECTS)
                    await self._trouble("lost")
                    return
                delay = RECONNECT_BACKOFF_S[
                    min(channel.attempts - 1, len(RECONNECT_BACKOFF_S) - 1)
                ]
                log.warning("speech[%s]: dropped — reconnecting in %.1fs (attempt %d)",
                            channel.name, delay, channel.attempts)
                await self._trouble("retrying")

            await asyncio.sleep(delay)
            try:
                await self._connect(channel)
            except Exception as exc:  # noqa: BLE001 — try again on the next turn of the loop
                log.warning("speech[%s]: reconnect failed: %s", channel.name, exc)
                await asyncio.sleep(delay)
                continue
            log.info("speech[%s]: reconnected", channel.name)

    async def _trouble(self, state: str) -> None:
        if self.on_trouble is None:
            return
        try:
            await self.on_trouble(state)
        except Exception:  # noqa: BLE001 — telling the user must not kill the listener
            log.exception("speech: on_trouble(%s) failed", state)

    async def _keep_alive(self) -> None:
        """Hold both connections open through the silence while Sarjy is speaking."""
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL_S)
            for channel in self._channels.values():
                if channel.connection is None:
                    continue
                try:
                    await channel.connection.send_keep_alive()
                except Exception as exc:  # noqa: BLE001 — a dead channel is logged elsewhere
                    log.debug("speech[%s]: keep-alive failed: %s", channel.name, exc)

    def _make_error_handler(self, name: str):
        async def handler(error) -> None:
            log.error("speech[%s]: %s", name, error)

        return handler

    async def aclose(self) -> None:
        # Set first: every connection is about to close, and none of those closes is a fault
        # the reconnect loop should react to.
        self._closing = True
        if self._heartbeat is not None:
            self._heartbeat.cancel()
            self._heartbeat = None
        for channel in self._channels.values():
            if channel.connection is not None:
                try:
                    await channel.connection.send_close_stream()
                except Exception:  # noqa: BLE001 — already going away
                    pass
        for task in self._listeners:
            task.cancel()
        if self._race_task is not None:
            self._race_task.cancel()
        self._listeners.clear()
        await self._stack.aclose()

    # ---------------------------------------------------------------------------------
    # Audio in
    # ---------------------------------------------------------------------------------

    async def feed(self, chunk: bytes) -> None:
        """Fan one MediaRecorder chunk out to every channel."""
        for channel in self._channels.values():
            if channel.connection is None:
                continue
            try:
                await channel.connection.send_media(chunk)
            except Exception as exc:  # noqa: BLE001 — one dead channel must not kill the call
                log.warning("speech[%s]: dropped a chunk: %s", channel.name, exc)

    # ---------------------------------------------------------------------------------
    # The race
    # ---------------------------------------------------------------------------------

    async def _emit_interim(self, text: str, confidence: float) -> None:
        # When *any* channel last heard words. The interim watchdog waits on this rather than
        # on its own channel: the English channel is silent throughout an Egyptian sentence,
        # and that silence is not the person stopping.
        self._last_interim_at = time.monotonic()
        await self.on_interim(text, confidence)

    async def _submit(self, name: str, candidate: _Candidate, epoch: int | None = None) -> None:
        """A channel finished an utterance. Hold it briefly for its rival, then decide."""
        if epoch is not None and epoch < self._epoch:
            # This channel was still chewing on speech that has already been answered.
            # Measured: the user said one English sentence, the Arabic channel won the race
            # and the turn completed — then the English channel finalised 562ms after the
            # microphone un-paused and started a *second* turn on the same sentence, so
            # Sarjy replied twice. A late loser is not a new thing to say.
            log.info("speech[%s]: dropping a stale utterance from turn %d: %r",
                     name, epoch, candidate.text)
            return

        if not candidate.text.strip():
            # Silence is not a turn, and — the reason this check matters — an empty entry
            # would count towards "every channel has reported" and resolve the race early.
            # Measured: the Arabic channel emitted an empty final during the leading silence,
            # so the race closed 134ms before its real transcript arrived and a 0.55-
            # confidence English mis-hear won a sentence it should have lost.
            return

        async with self._lock:
            previous = self._pending.get(name)
            if previous is not None:
                # This channel segmented one breath into two utterances inside the race
                # window. Overwriting would silently drop the first half of the sentence,
                # so join them — from the user's side it was one continuous thing to say.
                candidate = _Candidate(
                    text=f"{previous.text} {candidate.text}".strip(),
                    confidence=min(previous.confidence, candidate.confidence),
                    deepgram_languages=previous.deepgram_languages or candidate.deepgram_languages,
                    # The later half is the one that ends the sentence.
                    speech_ended_at=candidate.speech_ended_at or previous.speech_ended_at,
                )
            self._pending[name] = candidate
            everyone_reported = len(self._pending) >= len(self._channels)
            if self._race_task is None:
                self._race_task = asyncio.create_task(self._resolve(immediately=everyone_reported))
            elif everyone_reported:
                # Both are in — no reason to keep waiting out the window.
                self._race_task.cancel()
                self._race_task = asyncio.create_task(self._resolve(immediately=True))

    async def _resolve(self, immediately: bool = False) -> None:
        try:
            if not immediately:
                best = max((c.confidence for c in self._pending.values()), default=0.0)
                await asyncio.sleep(
                    RACE_WINDOW_CONFIDENT_S if best >= CONFIDENT_ENOUGH else RACE_WINDOW_UNSURE_S
                )
        except asyncio.CancelledError:
            return

        async with self._lock:
            candidates = dict(self._pending)
            # Before deciding, take whatever the channels that have NOT reported are holding.
            #
            # The two channels are on different watchdogs — UtteranceEnd's stall-breaker at
            # 1.5s, the abandoned-buffer one at 3.0s — so the race could resolve while the
            # other channel was sitting on the whole sentence. Measured live, twice in one
            # call: the English channel flushed "Eiscotobrobe." (conf 0.56) and won unopposed
            # while the Arabic channel held "عايز كتب رعب" at 0.99, and the *same speech* then
            # came back 24 seconds later as a second turn. The person had said it once.
            #
            # Whoever is holding *finished segments* is in the race, whatever their own
            # timer thinks. This cannot make the race slower — nothing waits — it cannot let
            # stale speech in, because `take()` applies the same epoch check as `_submit`,
            # and it cannot pull in half a sentence, because `take()` ignores interims.
            for name, channel in self._channels.items():
                if name in candidates:
                    continue
                if (held := channel.take(self._epoch)) is not None:
                    log.info("speech[%s]: pulled into the race holding %r", name, held.text)
                    candidates[name] = held
            self._pending.clear()
            self._race_task = None

        won = pick_winner(candidates)
        if won is None:
            return  # silence, or a cough — nothing was said
        name, candidate = won

        # This utterance is now spoken for. Close the turn so any channel still working on
        # the same speech is recognised as stale, and drop whatever they have buffered.
        self._epoch += 1
        for channel in self._channels.values():
            channel.reset()

        utterance = Utterance(
            text=candidate.text,
            language=language.detect_language(candidate.text),
            confidence=candidate.confidence,
            channel=name,
            deepgram_languages=candidate.deepgram_languages,
            # Every channel's text *and* its numbers: without the loser's confidence and
            # score a wrong pick cannot be diagnosed after the fact, which cost us a
            # session once already.
            alternatives={
                key: {
                    "text": value.text,
                    "confidence": round(value.confidence, 4),
                    "score": round(content_score(value), 2),
                }
                for key, value in candidates.items()
            },
            speech_ended_at=candidate.speech_ended_at,
        )

        log.info("speech: %s  (alternatives: %s)", utterance, _describe(utterance.alternatives))
        await self.on_final(utterance)

