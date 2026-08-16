# -*- coding: utf-8 -*-
"""The two-channel race rule (D-036).

These are the real measurements from the Phase-2 probe, frozen as tests: Deepgram's Arabic
and English channels both hear every utterance, and the rule below has to pick the right one
every time. If a future Deepgram change breaks the separation, this file says so out loud
instead of the demo saying it.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.speech_recognition import _Candidate, _Channel, Transcriber, pick_winner


@pytest.fixture
def finalize_on(monkeypatch):
    """Switch the provoked-Finalize workaround on for one test.

    It ships **off**: head to head against the deployed service it cut the demo utterance
    mid-word three times out of three, where the interim fallback kept more of it three
    times out of three (D-055). The mechanism is still reachable by env knob, so it is still
    worth holding to its contract.
    """
    monkeypatch.setattr("app.speech_recognition.FINALIZE_ON_LOST_INTERIM", True)


def candidate(text: str, confidence: float) -> _Candidate:
    return _Candidate(text=text, confidence=confidence, deepgram_languages=[])


class TestPickWinner:
    def test_silence_on_both_channels_is_not_a_turn(self):
        assert pick_winner({"ar": candidate("", 0.0), "en": candidate("", 0.0)}) is None

    def test_whitespace_only_is_still_silence(self):
        assert pick_winner({"ar": candidate("   ", 0.9)}) is None

    def test_non_empty_beats_empty_regardless_of_confidence(self):
        # The losing channel reports 0.0 with no text; it must never win on a tie-break.
        won, chosen = pick_winner(
            {"ar": candidate("عايز اعمل بوك لميتنج بكره", 0.994), "en": candidate("", 0.0)}
        )
        assert won == "ar"
        assert chosen.text.startswith("عايز")

    def test_higher_confidence_wins_when_both_heard_comparable_amounts(self):
        # Measured: "Remind me to call مامتي after Maghrib" — English dominant. Both channels
        # returned six words, so confidence is what separates them.
        won, _ = pick_winner(
            {
                "ar": candidate("رمايد ميديك هول مامتي افتر مغرب", 0.781),
                "en": candidate("Remind me to call after Maghreb.", 1.000),
            }
        )
        assert won == "en"

    def test_a_confident_fragment_loses_to_a_whole_sentence(self):
        """The live failure this rule exists for.

        Spoken in Egyptian, the English channel heard two words, was sure of them, and won —
        so Sarjy answered a question about cardiac infarction. Expected-correct-words puts
        the eight-word Arabic sentence ahead even though its confidence is lower.
        """
        won, _ = pick_winner(
            {
                "ar": candidate("طيب ااا ينفع تشوفي لي صرت بعصر لبكرة ساكن", 0.70),
                "en": candidate("Type, infarct", 0.83),
            }
        )
        assert won == "ar"

    def test_length_breaks_a_score_tie(self):
        # Same expected-correct-words, so the longer transcript wins.
        won, _ = pick_winner(
            {"ar": candidate("لأ", 1.0), "en": candidate("No.", 1.0)}
        )
        assert won == "en"

    def test_the_five_measured_utterances_all_resolve_correctly(self):
        measured = [
            ("عايز أعمل book لميتنج بكرة الساعة خمسة", ("عايز اعمل بوك لميتنج بكره الساعه خسه", 0.994), ("", 0.0), "ar"),
            ("ايه ال weather بكرة في اسكندرية؟", ("ايه الويذر بكرة في إسكندرية؟", 0.862), ("", 0.0), "ar"),
            ("Remind me to call مامتي after Maghrib", ("رمايد ميديك هول مامتي افتر مغرب", 0.781), ("Remind me to call after Maghreb.", 1.0), "en"),
            ("Can you book me a table for four people tonight?", ("", 0.0), ("Can you book me a table for four people tonight?", 1.0), "en"),
            ("احجزلي ميعاد عند الدكتور بكرة بعد العصر", ("احجز لي ميعاد عند الدكتور بكرة بعد العصر.", 0.990), ("", 0.0), "ar"),
        ]
        for said, ar, en, expected in measured:
            won, _ = pick_winner({"ar": candidate(*ar), "en": candidate(*en)})
            assert won == expected, f"{said!r} should be decided by the {expected} channel"

    def test_the_live_microphone_utterances_all_resolve_correctly(self):
        """Real turns from the D-016 gate session, with both channels' actual output.

        These are the cases the synthetic set could not produce: a human voice, a real room,
        and an English channel that hears confident nonsense in Egyptian speech.
        """
        measured = [
            # "ايه ال weather بكرة في اسكندرية؟"
            ("weather", ("هو الوذر بوكلا في إسكندرية يكون عملي", 0.95),
                        ("Well well weather. Book love.", 0.60), "ar"),
            # "آه يا ريت" — short, and the English channel had a plausible-sounding answer.
            ("short yes", ("آه يا ريت", 0.92), ("Oh, you're right.", 0.90), "ar"),
            # The infarct failure.
            ("egyptian question", ("طيب ااا ينفع تشوفي لي صرت بعصر لبكرة ساكن", 0.70),
                                  ("Type, infarct", 0.83), "ar"),
            # English-dominant with an Arabic name: the English channel is right to win.
            ("english reminder", ("ريميند ميتو كول دينا تمورو أفضل مارب", 0.66),
                                 ("Remind me to call Dina tomorrow after Maghrib.", 0.99), "en"),
        ]
        for label, ar, en, expected in measured:
            won, _ = pick_winner({"ar": candidate(*ar), "en": candidate(*en)})
            assert won == expected, f"{label}: expected the {expected} channel to win"

    def test_a_single_configured_channel_still_works(self):
        # DEEPGRAM_LANGUAGES can be narrowed to one channel for debugging.
        won, _ = pick_winner({"ar": candidate("أهلاً", 0.9)})
        assert won == "ar"


class TestOneUtteranceIsOneTurn:
    """A single spoken sentence must produce exactly one turn.

    The bug this guards against was found live: the user said "Remind me to call Dina after
    Maghrib" **once**. The Arabic channel finalised first and won the race; the turn ran and
    the microphone un-paused; 562ms later the English channel finalised the *same* sentence
    and started a second turn — so Sarjy answered twice. A channel that is late with speech
    already answered is stale, not new.
    """

    @staticmethod
    def _transcriber() -> tuple[Transcriber, list]:
        finals: list = []

        async def on_interim(text, confidence): ...
        async def on_final(utterance):
            finals.append(utterance)

        stt = Transcriber(on_interim=on_interim, on_final=on_final, channel_languages=["ar", "en"])
        return stt, finals

    @pytest.mark.asyncio
    async def test_a_late_losing_channel_does_not_start_a_second_turn(self):
        stt, finals = self._transcriber()

        # Both channels start hearing the same sentence, so both are stamped turn 0.
        for channel in ("ar", "en"):
            stt._channels[channel] = _stub_channel(stt, epoch=0)

        await stt._submit("ar", _Candidate("ريميند ميتو كول دينا تمورو أفضل مارب", 0.66, []), epoch=0)
        await asyncio.sleep(1.2)  # let the unsure-window race resolve on the Arabic channel
        assert len(finals) == 1

        # The English channel finally finalises the SAME sentence, after the turn is over.
        await stt._submit("en", _Candidate("Remind me to call dinner tomorrow after my rib.", 0.99, []), epoch=0)
        await asyncio.sleep(1.2)

        assert len(finals) == 1, f"the same sentence produced {len(finals)} turns"

    @pytest.mark.asyncio
    async def test_genuinely_new_speech_still_gets_its_turn(self):
        stt, finals = self._transcriber()
        for channel in ("ar", "en"):
            stt._channels[channel] = _stub_channel(stt, epoch=0)

        await stt._submit("ar", _Candidate("الجو النهارده عامل ايه", 0.95, []), epoch=0)
        await asyncio.sleep(1.2)
        assert len(finals) == 1

        # A second sentence, started after the first turn closed — must be answered.
        await stt._submit("ar", _Candidate("تمام شكرا", 0.95, []), epoch=stt._epoch)
        await asyncio.sleep(1.2)
        assert len(finals) == 2


def _stub_channel(transcriber, epoch, holding=None):
    """Minimal stand-in for the two things the race asks of a channel.

    `reset()` when a turn closes, and `take()` when the race is about to decide without it —
    a channel sitting on a finished sentence has to be pulled in rather than left to lose on
    its own timer. `holding=None` is a channel with nothing buffered, which is the usual case.
    """

    class _Stub:
        name = "stub"
        _epoch = epoch

        def reset(self):
            self._epoch = None

        def take(self, current_epoch):
            if holding is None or self._epoch is None or self._epoch < current_epoch:
                return None
            self._epoch = None
            return holding

    return _Stub()


class TestLostArabicFinals:
    """Deepgram's Arabic model discards transcripts it has already shown us.

    Measured against the live API on the demo's own utterance,
    "احجزلي ميعاد عند الدكتور بكرة بعد العصر": the interims carry the words, then the
    `is_final` for the same span arrives with an empty transcript, zero confidence and no
    words — 4 runs out of 4, deterministic per utterance, and never on the English channel.
    An explicit `Finalize` gets the words back, at the cost of ending the segment early.

    So a *provoked* final (`from_finalize`) must accumulate rather than end the turn, or the
    booking utterance arrives as "ميعاد عند الدكتور بكرة" and loses the word the whole demo
    turns on — العصر.
    """

    @staticmethod
    def _channel() -> tuple[_Channel, list]:
        submitted: list = []

        class _FakeTranscriber:
            _epoch = 0
            # The room's silence clock, kept the way the real Transcriber keeps it: the
            # interim watchdog waits on *any* channel going quiet, not just its own.
            _last_interim_at = 0.0

            async def _emit_interim(self, text, confidence):
                self._last_interim_at = time.monotonic()

            async def _submit(self, name, candidate, epoch):
                submitted.append(candidate)

        channel = _Channel("ar", _FakeTranscriber())
        channel.connection = _FakeConnection()
        return channel, submitted

    @pytest.mark.asyncio
    async def test_an_empty_interim_after_words_asks_deepgram_to_flush(self, finalize_on):
        channel, _ = self._channel()
        await channel.on_message(_results("ميعاد عند الدكتور", is_final=False))
        assert channel.connection.finalize_calls == 0
        await channel.on_message(_results("", is_final=False))
        assert channel.connection.finalize_calls == 1

    @pytest.mark.asyncio
    async def test_an_empty_interim_before_any_words_asks_for_nothing(self):
        # Leading silence is most of a call; a Finalize per quiet moment would shred it.
        channel, _ = self._channel()
        await channel.on_message(_results("", is_final=False))
        assert channel.connection.finalize_calls == 0

    @pytest.mark.asyncio
    async def test_only_one_finalize_per_quiet_spell(self, finalize_on):
        channel, _ = self._channel()
        await channel.on_message(_results("ميعاد", is_final=False))
        for _ in range(3):
            await channel.on_message(_results("", is_final=False))
        assert channel.connection.finalize_calls == 1

    @pytest.mark.asyncio
    async def test_a_provoked_final_does_not_end_the_turn(self):
        channel, submitted = self._channel()
        await channel.on_message(_results("ميعاد عند الدكتور بكرة", is_final=True,
                                          speech_final=True, from_finalize=True))
        assert submitted == [], "a Finalize we asked for is not the person stopping talking"

    @pytest.mark.asyncio
    async def test_the_pieces_are_joined_by_the_natural_end_of_turn(self):
        channel, submitted = self._channel()
        await channel.on_message(_results("ميعاد عند الدكتور بكرة", is_final=True,
                                          speech_final=True, from_finalize=True))
        await channel.on_message(_results("بعد العصر", is_final=True, speech_final=True))
        assert len(submitted) == 1
        assert submitted[0].text == "ميعاد عند الدكتور بكرة بعد العصر"

    @pytest.mark.asyncio
    async def test_a_natural_final_still_ends_the_turn_on_its_own(self):
        # The healthy path must be untouched: most utterances never provoke a Finalize.
        channel, submitted = self._channel()
        await channel.on_message(_results("الجو النهارده عامل ايه", is_final=True,
                                          speech_final=True))
        assert [c.text for c in submitted] == ["الجو النهارده عامل ايه"]


class TestHandlerFailuresDoNotDeafenTheCall:
    @pytest.mark.asyncio
    async def test_a_broken_message_is_logged_not_fatal(self):
        # The SDK dispatches from its read loop: an exception escaping the handler takes the
        # listener with it and the call goes deaf in silence (the lesson of D-050).
        channel, _ = TestLostArabicFinals._channel()
        await channel.on_message(object())  # no `type`, no `channel`, nothing


class _FakeConnection:
    def __init__(self) -> None:
        self.finalize_calls = 0

    async def send_finalize(self) -> None:
        self.finalize_calls += 1


class _Word:
    def __init__(self, end: float) -> None:
        self.end = end


class _Alternative:
    def __init__(self, transcript: str, confidence: float) -> None:
        self.transcript = transcript
        self.confidence = confidence
        self.languages = None
        self.words = [_Word(1.0)] if transcript else []


def _results(transcript, *, is_final=False, speech_final=False, from_finalize=False,
             confidence=0.9):
    """One Deepgram `Results` message, shaped the way the SDK delivers it."""

    class _Payload:
        pass

    message = _Payload()
    message.type = "Results"
    message.is_final = is_final
    message.speech_final = speech_final
    message.from_finalize = from_finalize
    message.start = 0.0
    message.duration = 1.0
    message.channel = _Payload()
    message.channel.alternatives = [_Alternative(transcript, confidence if transcript else 0.0)]
    return message


class TestEmptyFinalsFallBackToTheirInterims:
    """The second defence of D-055, and the one that needs no cooperation from Deepgram.

    Interims inside a segment are cumulative, so the longest one *is* that segment. When the
    segment's own `is_final` comes back empty, the interim is what Deepgram heard — and it is
    strictly better than the nothing we would otherwise hand the brain. Measured on the
    deployed service: a provoked Finalize split the booking utterance mid-word and the second
    segment's final came back empty too, so the reply asked what time the appointment was for.
    """

    @pytest.mark.asyncio
    async def test_an_empty_final_keeps_the_words_its_interims_showed(self):
        channel, submitted = TestLostArabicFinals._channel()
        await channel.on_message(_results("ميعاد عند", is_final=False))
        await channel.on_message(_results("ميعاد عند الدكتور بكرة بعد العصر", is_final=False))
        await channel.on_message(_results("", is_final=True, speech_final=True))
        assert [c.text for c in submitted] == ["ميعاد عند الدكتور بكرة بعد العصر"]

    @pytest.mark.asyncio
    async def test_a_final_with_text_wins_over_its_interims(self):
        # The final is authoritative when it has anything at all — interims are guesses.
        channel, submitted = TestLostArabicFinals._channel()
        await channel.on_message(_results("الجو النهارده عامل", is_final=False))
        await channel.on_message(_results("الجو النهارده عامل ايه", is_final=True,
                                          speech_final=True))
        assert [c.text for c in submitted] == ["الجو النهارده عامل ايه"]

    @pytest.mark.asyncio
    async def test_silence_is_still_silence(self):
        # No interim ever carried words, so an empty final means nothing was said.
        channel, submitted = TestLostArabicFinals._channel()
        await channel.on_message(_results("", is_final=False))
        await channel.on_message(_results("", is_final=True, speech_final=True))
        assert [c.text for c in submitted] == [""]

    @pytest.mark.asyncio
    async def test_a_rescued_segment_does_not_leak_into_the_next_one(self):
        # Each `is_final` starts a fresh segment; carrying the old interim forward would
        # repeat the first half of the sentence inside the second.
        channel, submitted = TestLostArabicFinals._channel()
        await channel.on_message(_results("ميعاد عند الدكتور", is_final=False))
        await channel.on_message(_results("", is_final=True, from_finalize=True,
                                          speech_final=True))
        await channel.on_message(_results("بعد العصر", is_final=False))
        await channel.on_message(_results("", is_final=True, speech_final=True))
        assert [c.text for c in submitted] == ["ميعاد عند الدكتور بعد العصر"]


class TestOneFinalizePerBurstOfSpeech:
    """A provoked Finalize must not provoke the next one.

    Measured on the deployed service, 4 runs of 4: the Finalize's own answer re-armed the
    trigger, so the very next empty interim asked for a second Finalize — which closed the
    segment before Deepgram had transcribed the rest of the sentence. "بعد العصر" was not
    truncated, it was never heard. Only genuinely new speech may re-arm.
    """

    @pytest.mark.asyncio
    async def test_a_provoked_final_does_not_provoke_another(self, finalize_on):
        channel, _ = TestLostArabicFinals._channel()
        await channel.on_message(_results("ميعاد عند الدكتور", is_final=False))
        await channel.on_message(_results("", is_final=False))          # → Finalize #1
        await channel.on_message(_results("ميعاد عند الدكتور بكرة", is_final=True,
                                          speech_final=True, from_finalize=True))
        await channel.on_message(_results("", is_final=False))          # must NOT fire again
        assert channel.connection.finalize_calls == 1

    @pytest.mark.asyncio
    async def test_new_speech_after_a_finalize_re_arms_it(self, finalize_on):
        channel, _ = TestLostArabicFinals._channel()
        await channel.on_message(_results("ميعاد عند الدكتور", is_final=False))
        await channel.on_message(_results("", is_final=False))          # → Finalize #1
        await channel.on_message(_results("ميعاد عند الدكتور بكرة", is_final=True,
                                          speech_final=True, from_finalize=True))
        await channel.on_message(_results("بعد العصر", is_final=False))  # the person is still talking
        await channel.on_message(_results("", is_final=False))          # → Finalize #2 is right
        assert channel.connection.finalize_calls == 2


class TestNothingHeardIsEverStranded:
    """Words in a buffer that never flushes are words the brain never sees.

    Live failure: the Arabic channel held 'احجزي لي' from a booking request whose natural
    `speech_final` never arrived, so it never entered the race — and the English channel's
    'Exhibit' won unopposed, at confidence 0.64 on one word. The booking was heard and then
    silently dropped inside our own code.
    """

    @pytest.mark.asyncio
    async def test_an_is_final_without_speech_final_arms_the_watchdog(self, monkeypatch):
        monkeypatch.setattr("app.speech_recognition.FINALIZE_GRACE_S", 0.05)
        channel, submitted = TestLostArabicFinals._channel()
        await channel.on_message(_results("احجزي لي", is_final=True, speech_final=False))
        assert submitted == []          # nothing yet — the person may still be talking
        await asyncio.sleep(0.15)
        assert [c.text for c in submitted] == ["احجزي لي"]

    @pytest.mark.asyncio
    async def test_a_natural_end_of_turn_beats_the_watchdog_to_it(self, monkeypatch):
        monkeypatch.setattr("app.speech_recognition.FINALIZE_GRACE_S", 0.05)
        channel, submitted = TestLostArabicFinals._channel()
        await channel.on_message(_results("احجزي لي", is_final=True, speech_final=False))
        await channel.on_message(_results("ميعاد بكرة", is_final=True, speech_final=True))
        await asyncio.sleep(0.15)
        assert [c.text for c in submitted] == ["احجزي لي ميعاد بكرة"], "flushed twice"


class TestSpeechEndSurvivesAFinalizeClockReset:
    """`Finalize` resets Deepgram's stream clock, so a session-long origin plus a word offset
    reported `speech_recognition_ms` of 21s, 48s, 89s and 124s in the first live run — numbers
    that grew with the age of the call. Measuring backwards from the message's arrival has no
    cross-message state to go wrong."""

    @pytest.mark.asyncio
    async def test_speech_end_is_a_recent_moment_not_a_stream_offset(self):
        channel, submitted = TestLostArabicFinals._channel()
        before = time.monotonic()
        await channel.on_message(_results("الجو عامل ايه", is_final=True, speech_final=True))
        ended = submitted[0].speech_ended_at
        assert ended is not None
        assert before - 1.0 <= ended <= time.monotonic() + 0.01


class TestTheRacePullsInAChannelThatIsHoldingWords:
    """Measured on a live call, twice in five minutes: the two channels are on different
    watchdogs — UtteranceEnd's stall-breaker at 1.5s, the abandoned-buffer one at 3.0s — so
    the race could resolve while the *other* channel sat on the whole sentence.

        11:36:38  speech[en]: endpointing never fired — flushing on UtteranceEnd
        11:36:39  speech: [en→en 0.56] Eiscotobrobe.        ← only candidate
                  ...while ar held 'عايز كتب رعب.' at conf 0.99
        11:37:00  speech: [ar→ar 1.00] عايز كتب رعب          ← the SAME speech, 24s later

    Two failures for the price of one: an English mis-hear answered an Arabic sentence, and
    then the same sentence was answered a second time. The person said it once.
    """

    @staticmethod
    def _transcriber():
        finals = []

        async def on_interim(text, confidence): ...

        async def on_final(utterance):
            finals.append(utterance)

        stt = Transcriber(on_interim=on_interim, on_final=on_final, channel_languages=["ar", "en"])
        return stt, finals

    @pytest.mark.asyncio
    async def test_a_holding_channel_joins_the_race_and_wins_it(self):
        stt, finals = self._transcriber()
        stt._channels["en"] = _stub_channel(stt, epoch=0)
        stt._channels["ar"] = _stub_channel(
            stt, epoch=0, holding=_Candidate("عايز كتب رعب", 0.99, [])
        )

        # Only the English channel reports, with the fragment that used to win unopposed.
        await stt._submit("en", _Candidate("Eiscotobrobe.", 0.56, []), epoch=0)
        await asyncio.sleep(1.2)

        assert len(finals) == 1
        assert finals[0].text == "عايز كتب رعب", "the channel holding the sentence must win"
        assert finals[0].channel == "ar"

    @pytest.mark.asyncio
    async def test_and_therefore_does_not_produce_a_second_turn(self):
        stt, finals = self._transcriber()
        stt._channels["en"] = _stub_channel(stt, epoch=0)
        stt._channels["ar"] = _stub_channel(
            stt, epoch=0, holding=_Candidate("عايز كتب رعب", 0.99, [])
        )
        await stt._submit("en", _Candidate("Eiscotobrobe.", 0.56, []), epoch=0)
        await asyncio.sleep(1.2)

        # The Arabic buffer was consumed by the race, so there is nothing left to arrive late.
        assert stt._channels["ar"].take(stt._epoch) is None
        await asyncio.sleep(0.3)
        assert len(finals) == 1

    @pytest.mark.asyncio
    async def test_a_channel_holding_nothing_changes_nothing(self):
        stt, finals = self._transcriber()
        for name in ("ar", "en"):
            stt._channels[name] = _stub_channel(stt, epoch=0)
        await stt._submit("ar", _Candidate("الجو النهارده عامل ايه", 0.95, []), epoch=0)
        await asyncio.sleep(1.2)
        assert len(finals) == 1
        assert finals[0].text == "الجو النهارده عامل ايه"


class TestWordsThatNeverBecomeAFinal:
    """The 19-second hole.

    Both existing watchdogs need an event that may never come: one needs an `is_final`, the
    other needs an `UtteranceEnd`. A segment that produced interims and then simply stopped
    was watched by nothing at all — measured live, "عايز كتب رعب" sat in an open segment for
    19 seconds and surfaced 44 seconds after it was spoken, two turns later.
    """

    @pytest.mark.asyncio
    async def test_an_interim_that_goes_quiet_is_taken_at_its_word(self, monkeypatch):
        monkeypatch.setattr("app.speech_recognition.INTERIM_STALL_S", 0.05)
        channel, submitted = TestLostArabicFinals._channel()

        await channel.on_message(_results("عايز", is_final=False))
        await channel.on_message(_results("عايز كتب رعب", is_final=False))
        await asyncio.sleep(0.3)  # nothing else ever arrives

        assert [c.text for c in submitted] == ["عايز كتب رعب"]

    @pytest.mark.asyncio
    async def test_it_does_not_fire_while_the_person_is_still_talking(self, monkeypatch):
        monkeypatch.setattr("app.speech_recognition.INTERIM_STALL_S", 0.2)
        channel, submitted = TestLostArabicFinals._channel()

        # Interims keep arriving, the way they do about once a second during speech.
        for text in ("عايز", "عايز كتب", "عايز كتب رعب"):
            await channel.on_message(_results(text, is_final=False))
            await asyncio.sleep(0.05)
        assert submitted == [], "a pause inside a sentence is not the end of it"

    @pytest.mark.asyncio
    async def test_a_normal_final_wins_and_the_watchdog_stays_quiet(self, monkeypatch):
        monkeypatch.setattr("app.speech_recognition.INTERIM_STALL_S", 0.05)
        channel, submitted = TestLostArabicFinals._channel()

        await channel.on_message(_results("الجو النهارده عامل", is_final=False))
        await channel.on_message(_results("الجو النهارده عامل ايه", is_final=True,
                                          speech_final=True))
        await asyncio.sleep(0.3)
        assert [c.text for c in submitted] == ["الجو النهارده عامل ايه"], "exactly once"


class TestSilenceIsAPropertyOfTheRoom:
    """The regression the first version of the interim watchdog caused, measured live.

    Keyed off a single channel going quiet, it fired on the *English* channel in the middle
    of an Egyptian sentence — English has nothing to transcribe there, so its last interim
    ("I is") simply sat unchanged for 2.5s. The race that started dragged in the Arabic
    channel's half-finished hypothesis, and the turn ran on:

        speech[en]: no final after the interims — keeping 'I is'
        speech[ar]: pulled into the race holding 'أعمل بوك لمويتنج بكرتي الساعة'   ← truncated
        ...then the real final arrived and started a SECOND turn.

    So the watchdog waits for the whole room, and `take()` never promotes an interim.
    """

    @pytest.mark.asyncio
    async def test_a_quiet_channel_does_not_speak_for_a_talking_room(self, monkeypatch):
        monkeypatch.setattr("app.speech_recognition.INTERIM_STALL_S", 0.1)
        channel, submitted = TestLostArabicFinals._channel()
        transcriber = channel.transcriber

        # This channel hears one doubtful thing and then nothing more...
        await channel.on_message(_results("I is", is_final=False, confidence=0.84))
        # ...while the other channel goes on hearing the sentence.
        for _ in range(6):
            await asyncio.sleep(0.05)
            transcriber._last_interim_at = time.monotonic()
        assert submitted == [], "the room was never quiet, so nothing should have flushed"

        # The room finally goes quiet — now it may speak for what it heard.
        await asyncio.sleep(0.35)
        assert [c.text for c in submitted] == ["I is"]

    @pytest.mark.asyncio
    async def test_take_never_hands_over_a_half_finished_hypothesis(self):
        channel, _ = TestLostArabicFinals._channel()
        await channel.on_message(_results("أعمل بوك لمويتنج بكرتي الساعة", is_final=False))

        # An interim is a guess about a sentence still in progress. The race may not have it.
        assert channel.take(0) is None
        assert channel._interim_best == "أعمل بوك لمويتنج بكرتي الساعة", "still held, not lost"

    @pytest.mark.asyncio
    async def test_take_does_hand_over_a_finished_segment(self):
        channel, _ = TestLostArabicFinals._channel()
        await channel.on_message(_results("عايز كتب رعب", is_final=True, speech_final=False))
        taken = channel.take(0)
        assert taken is not None and taken.text == "عايز كتب رعب"
