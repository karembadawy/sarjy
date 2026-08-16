# -*- coding: utf-8 -*-
"""Phase 5: the failure paths, and the promise that none of them is silent.

Three things are asserted here, and each one exists because the alternative is a demo that
looks fine and is not:

1. **A rate limit is told apart from a spent allowance.** Google reports both as HTTP 429 and
   only the quotaId distinguishes them (D-035). Retrying a daily quota is a spinner that
   lies; not retrying a burst limit throws away a turn that would have worked four seconds
   later. So they are two exception types and two designed states.
2. **Every user-facing message is bilingual, and no message promises something untrue.** A
   message that is not retryable must not carry a countdown; a message that is retryable must.
3. **A Deepgram channel that drops is put back, and the user is told while it happens.** A
   reconnect nobody is told about is indistinguishable from the deafness it is fixing (D-050).
"""

from __future__ import annotations

import asyncio

import pytest

from app import brain, pipeline, speech_recognition


# ---------------------------------------------------------------------------------------
# 1. Telling a burst limit from a spent day
# ---------------------------------------------------------------------------------------


class _APIError(Exception):
    """Shaped like google.genai.errors.APIError for the two attributes we read."""

    def __init__(self, code: int, details: str) -> None:
        super().__init__(details)
        self.code = code
        self.details = details


class TestRetryAfter:
    def test_reads_the_delay_the_provider_actually_sent(self):
        # The string below is copied out of a real Cloud Run log, single quotes and all: the
        # SDK renders the payload as a **Python dict repr**, not as the JSON the docs show.
        # A pattern written from the documentation matches nothing, silently, and the caller
        # falls back to a guessed delay while believing it is using Google's own number.
        # Both copies of this regex had that bug until a test used the real string.
        error = _APIError(
            429,
            "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'details': [{'@type': "
            "'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '12s'}]}}",
        )
        assert brain.retry_after(error) == 12.0

    def test_the_documented_json_spelling_works_too(self):
        assert brain.retry_after(_APIError(429, '{"retryDelay": "12s"}')) == 12.0

    def test_a_minute_long_delay_is_clamped_to_something_a_caller_will_wait_for(self):
        # A phone call has no 60-second designed state. Past the clamp we try again earlier
        # than we were told to, and if it fails again the same honest notice comes back —
        # which is a better answer than a progress bar nobody watches to the end.
        error = _APIError(429, "{'retryDelay': '58s'}")
        assert brain.retry_after(error) == brain.MAX_HONEST_WAIT_S

    def test_no_delay_means_no_promise(self):
        assert brain.retry_after(_APIError(429, "quota exceeded")) is None

    def test_a_daily_quota_is_not_a_burst_limit(self):
        daily = _APIError(
            429, "quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue: 20"
        )
        burst = _APIError(429, "quotaId: GenerateRequestsPerMinutePerProjectPerModel-FreeTier")
        assert brain._is_daily_quota(daily) is True
        assert brain._is_daily_quota(burst) is False


class TestQuotaErrorsAreDistinctTypes:
    def test_busy_carries_how_long_to_wait(self):
        error = brain.BrainBusyError("rate limited", retry_after_s=4.0)
        assert error.retry_after_s == 4.0
        assert isinstance(error, brain.BrainError)

    def test_a_spent_day_is_not_a_busy_moment(self):
        # Both are BrainErrors, so a caller that does not care still catches both — but
        # neither is a subclass of the other, so a caller that *does* care cannot confuse them.
        assert not issubclass(brain.BrainQuotaError, brain.BrainBusyError)
        assert not issubclass(brain.BrainBusyError, brain.BrainQuotaError)


# ---------------------------------------------------------------------------------------
# 2. The designed states themselves
# ---------------------------------------------------------------------------------------


class _Recorder:
    """A Sender that keeps what was written to it."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append({"type": "bytes", "len": len(data)})


class TestEveryMessageIsBilingualAndHonest:
    def test_every_error_has_both_languages(self):
        for key, error in pipeline.ERRORS.items():
            assert error.arabic.strip(), key
            assert error.english.strip(), key

    def test_no_message_is_a_stack_trace(self):
        for key, error in pipeline.ERRORS.items():
            for text in (error.arabic, error.english):
                assert "Traceback" not in text and "Exception" not in text, key

    def test_only_a_retryable_state_promises_a_retry(self):
        for key, error in pipeline.ERRORS.items():
            if error.retryable or error.transient:
                assert error.retry_after_s, f"{key} promises a wait with no length"
            else:
                # The spent-allowance states. A countdown here would be a lie with an
                # animation on it — there is nothing to count down to before midnight.
                assert error.retry_after_s is None, f"{key} counts down to nothing"

    def test_a_spent_daily_allowance_is_never_retryable(self):
        assert pipeline.ERRORS["brain_quota"].retryable is False
        assert pipeline.ERRORS["brain_quota"].transient is False

    def test_a_burst_limit_is(self):
        assert pipeline.ERRORS["brain_busy"].retryable is True

    def test_a_voice_failure_falls_through_to_the_browser_rather_than_to_silence(self):
        # D-005's last link. The reply exists as text either way; the question is only
        # whether the call keeps talking.
        assert pipeline.ERRORS["voice_quota"].speak_again is True
        assert pipeline.ERRORS["voice"].speak_again is True

    @pytest.mark.asyncio
    async def test_the_frame_carries_everything_the_browser_needs_to_behave(self):
        sender = _Recorder()
        await pipeline.send_error(sender, "brain_busy")
        (frame,) = sender.sent
        assert frame["type"] == "error"
        assert frame["key"] == "brain_busy"
        assert frame["retryable"] is True
        assert frame["retry_after_s"] > 0
        assert frame["message_ar"] and frame["message_en"]

    @pytest.mark.asyncio
    async def test_the_providers_own_delay_overrides_our_default(self):
        sender = _Recorder()
        await pipeline.send_error(sender, "brain_busy", retry_after_s=11.0)
        assert sender.sent[0]["retry_after_s"] == 11.0

    @pytest.mark.asyncio
    async def test_an_unknown_key_still_produces_a_bilingual_frame(self):
        # Nothing may reach the user as a blank box, including a key we forgot to define.
        sender = _Recorder()
        await pipeline.send_error(sender, "no-such-thing")
        assert sender.sent[0]["message_ar"] and sender.sent[0]["message_en"]


# ---------------------------------------------------------------------------------------
# 3. A dropped Deepgram channel comes back, loudly
# ---------------------------------------------------------------------------------------


class _DyingConnection:
    """A Deepgram connection whose read loop returns, the way a closed socket's does."""

    def __init__(self, lifetime: float = 0.0) -> None:
        self.listens = 0
        self.lifetime = lifetime

    async def start_listening(self) -> None:
        self.listens += 1
        await asyncio.sleep(self.lifetime)  # then "close"

    def on(self, *_args) -> None: ...


class TestAChannelThatDropsIsPutBack:
    @staticmethod
    def _transcriber(trouble: list[str]) -> speech_recognition.Transcriber:
        async def on_interim(text, confidence): ...

        async def on_final(utterance): ...

        async def on_trouble(state):
            trouble.append(state)

        return speech_recognition.Transcriber(
            on_interim=on_interim, on_final=on_final, on_trouble=on_trouble
        )

    @pytest.mark.asyncio
    async def test_it_reconnects_and_says_so(self, monkeypatch):
        monkeypatch.setattr("app.speech_recognition.RECONNECT_BACKOFF_S", (0.0,))
        trouble: list[str] = []
        stt = self._transcriber(trouble)
        channel = speech_recognition._Channel("ar", stt)
        channel.connection = _DyingConnection()
        stt._channels["ar"] = channel

        connects = 0

        async def fake_connect(target) -> None:
            nonlocal connects
            connects += 1
            target.connection = _DyingConnection()

        monkeypatch.setattr(stt, "_connect", fake_connect)

        # The loop ends by itself once it has used up its attempts, which is the point: it
        # does not spin forever, and it does not stop after one.
        await asyncio.wait_for(stt._listen(channel), timeout=5)

        assert connects == speech_recognition.MAX_RECONNECTS
        assert trouble.count("retrying") == speech_recognition.MAX_RECONNECTS
        assert trouble[-1] == "lost", "the user must be told when it is not coming back"

    @pytest.mark.asyncio
    async def test_hanging_up_is_not_a_fault(self, monkeypatch):
        # Every connection closes when the call ends. None of those closes is a drop, and
        # reconnecting one would reopen a Deepgram socket nobody is listening to.
        trouble: list[str] = []
        stt = self._transcriber(trouble)
        channel = speech_recognition._Channel("ar", stt)
        channel.connection = _DyingConnection()
        stt._channels["ar"] = channel
        stt._closing = True

        await asyncio.wait_for(stt._listen(channel), timeout=5)
        assert trouble == []

    @pytest.mark.asyncio
    async def test_a_finished_stream_is_reopened_without_alarming_anybody(self, monkeypatch):
        """Deepgram closes with 1000 (OK) once a container's declared audio has all arrived.

        A WAV header states its own length, so every script that streams a file gets one of
        these at the end of the clip — measured at 4.32s into a 4.62s file. The browser's
        WebM/Opus declares no length and never does. Announcing it as a dropped microphone
        would cry wolf on every single benchmark run.
        """
        monkeypatch.setattr("app.speech_recognition.SETTLED_S", 0.0)
        monkeypatch.setattr("app.speech_recognition.REOPEN_DELAY_S", 0.0)
        trouble: list[str] = []
        stt = self._transcriber(trouble)
        channel = speech_recognition._Channel("ar", stt)
        channel.connection = _DyingConnection()
        channel.saw_metadata = True
        stt._channels["ar"] = channel

        reopened = 0

        async def fake_connect(target) -> None:
            nonlocal reopened
            reopened += 1
            if reopened >= 3:
                stt._closing = True  # the call hangs up; stop the loop
            target.connection = _DyingConnection()
            target.saw_metadata = True

        monkeypatch.setattr(stt, "_connect", fake_connect)
        await asyncio.wait_for(stt._listen(channel), timeout=5)

        assert reopened == 3, "a completed stream must be reopened, not left dead"
        assert trouble == [], "a completed stream is not a fault and says nothing to the user"
        assert channel.attempts == 0, "and it does not use up the reconnect budget"

    @pytest.mark.asyncio
    async def test_an_instant_completion_is_treated_as_a_drop(self, monkeypatch):
        # A connection that "completes" the moment it opens is failing politely. Trusting the
        # Metadata there would spin this loop forever with nothing in the log.
        monkeypatch.setattr("app.speech_recognition.RECONNECT_BACKOFF_S", (0.0,))
        trouble: list[str] = []
        stt = self._transcriber(trouble)
        channel = speech_recognition._Channel("ar", stt)
        channel.connection = _DyingConnection()
        channel.saw_metadata = True
        stt._channels["ar"] = channel

        async def fake_connect(target) -> None:
            target.connection = _DyingConnection()
            target.saw_metadata = True

        monkeypatch.setattr(stt, "_connect", fake_connect)
        await asyncio.wait_for(stt._listen(channel), timeout=5)
        assert trouble[-1] == "lost"

    @pytest.mark.asyncio
    async def test_a_channel_being_replaced_is_not_fed_audio(self, monkeypatch):
        # `feed` skips a channel with no connection. Without that, a reconnect would log one
        # dropped chunk every 250ms and the real reason would be buried.
        stt = self._transcriber([])
        channel = speech_recognition._Channel("ar", stt)
        channel.connection = None
        stt._channels["ar"] = channel
        await stt.feed(b"\x00\x01")  # must not raise


class TestTheLosersNumbersSurviveTheRace:
    """D-045 was diagnosed from the losing channel's score. That data is now structured, so
    the benchmark reads exactly what the racer saw instead of parsing a log line."""

    def test_describe_names_every_channel(self):
        line = speech_recognition._describe({
            "ar": {"text": "الجو النهارده", "confidence": 0.98, "score": 1.96},
            "en": {"text": "Type, infarct", "confidence": 0.83, "score": 1.66},
        })
        assert "الجو النهارده" in line and "Type, infarct" in line
        assert "0.98" in line and "1.66" in line
