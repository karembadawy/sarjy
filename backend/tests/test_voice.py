# -*- coding: utf-8 -*-
"""Voice-synthesis logic that must not need a network to be trusted.

Both things tested here bit us during Phase 2: Latin-only sentence splitting silently
treated a whole Arabic reply as one sentence (so nothing streamed until the end), and raw
PCM sent without a header plays as a click in the browser.
"""

from __future__ import annotations

import io
import wave

import pytest

from app import voice


class TestSplitForSpeech:
    def test_empty_input_produces_no_frames(self):
        assert voice.split_for_speech("") == []
        assert voice.split_for_speech("   ") == []
        assert voice.split_for_speech(None) == []

    def test_single_sentence_stays_whole(self):
        text = "It will be twenty-eight degrees and sunny in Alexandria tomorrow."
        assert voice.split_for_speech(text) == [text]

    def test_arabic_question_mark_ends_a_sentence(self):
        # ؟ is U+061F, not '?'. A Latin-only splitter misses it entirely.
        # The lead sentence is deliberately past MIN_CHUNK_CHARS so the merge rule (tested
        # separately) cannot mask the split being tested here.
        lead = "الجو بكرة هيبقى حلو أوي والدنيا شمس على طول في اسكندرية وحوالي تمنتاشر درجة."
        assert len(lead) > voice.MIN_CHUNK_CHARS
        chunks = voice.split_for_speech(f"{lead} تحب أقولك درجة الحرارة؟")
        assert chunks == [lead, "تحب أقولك درجة الحرارة؟"]

    @pytest.mark.parametrize("mark", ["؟", "؛", "۔", ".", "!", "…"])
    def test_every_supported_terminator_splits(self, mark):
        first = "دي جملة طويلة كفاية عشان متتلمّش مع اللي بعدها في نفس الشنك" + mark
        chunks = voice.split_for_speech(f"{first} تمام كده يا فندم وشكرا ليك على وقتك.")
        assert len(chunks) == 2
        assert chunks[0] == first

    def test_short_fragments_merge_to_save_tts_requests(self):
        # "تمام." on its own would cost a whole metered TTS request (D-038) and sound choppy.
        chunks = voice.split_for_speech("تمام. حجزتلك الميعاد بكرة الساعة أربعة ونص بعد العصر.")
        assert chunks == ["تمام. حجزتلك الميعاد بكرة الساعة أربعة ونص بعد العصر."]

    def test_order_is_preserved_and_nothing_is_lost(self):
        reply = (
            "أهلاً بيك يا أحمد وحشتني والله وكنت مستنيك من بدري. "
            "الجو بكرة هيبقى حلو أوي وحوالي تمنتاشر درجة في اسكندرية. "
            "تحب أحجزلك؟"
        )
        chunks = voice.split_for_speech(reply)
        assert len(chunks) >= 2
        joined = " ".join(chunks).replace(" ", "")
        assert joined == reply.replace(" ", "")


class TestPcmToWav:
    def test_wraps_pcm_in_a_decodable_wav(self):
        pcm = b"\x00\x01" * 2400  # 0.1s of 16-bit mono at 24kHz
        data = voice.pcm_to_wav(pcm)

        assert data[:4] == b"RIFF"
        assert data[8:12] == b"WAVE"

        with wave.open(io.BytesIO(data), "rb") as wav:
            assert wav.getnchannels() == voice.CHANNELS
            assert wav.getsampwidth() == voice.SAMPLE_WIDTH_BYTES
            assert wav.getframerate() == voice.SAMPLE_RATE_HZ
            assert wav.readframes(wav.getnframes()) == pcm

    def test_header_costs_44_bytes(self):
        assert len(voice.pcm_to_wav(b"\x00" * 1000)) == 1044


class TestRouter:
    def test_paid_and_client_side_providers_refuse_clearly(self, monkeypatch):
        # CLAUDE.md rule 2: ElevenLabs must never be reachable by accident.
        monkeypatch.setattr(voice.config, "get", lambda name, default=None: "elevenlabs")
        with pytest.raises(voice.VoiceError, match="Loom recording"):
            voice.synthesize("مرحبا", "ar")

        monkeypatch.setattr(voice.config, "get", lambda name, default=None: "browser")
        with pytest.raises(voice.VoiceError, match="Web Speech API"):
            voice.synthesize("hello", "en")

    def test_unknown_provider_names_itself(self, monkeypatch):
        monkeypatch.setattr(voice.config, "get", lambda name, default=None: "festival")
        with pytest.raises(voice.VoiceError, match="festival"):
            voice.synthesize("hello", "en")

    def test_empty_text_is_refused_before_any_api_call(self):
        with pytest.raises(voice.VoiceError, match="Nothing to speak"):
            voice.synthesize("   ", "ar")

    def test_voice_choice_follows_language(self, monkeypatch):
        monkeypatch.setattr(voice.config, "get", lambda name, default=None: default)
        assert voice.voice_for("ar") == voice.DEFAULT_VOICE_AR
        assert voice.voice_for("en") == voice.DEFAULT_VOICE_EN


class TestASpentModelIsRememberedForTheDay:
    """Measured on a live call: with the primary TTS model out of its 100/day allowance,
    *every* chunk paid a full round trip to it before falling back — four failed calls for
    one reply on one turn, and `first audio` at 8.9s where a healthy turn is ~2s.

    D-038 built the fallback chain to survive exhaustion and then forgot which model was
    exhausted. The 429 states exactly how long to wait; we believe it.
    """

    def setup_method(self):
        voice._spent_until.clear()

    teardown_method = setup_method

    def test_a_per_day_429_takes_the_model_out_of_the_chain(self):
        chain = voice.models()
        voice.mark_spent(chain[0], 44578)
        assert voice.is_spent(chain[0])
        assert chain[0] not in voice.models()
        assert voice.models(), "the rest of the chain is still available"

    def test_the_allowance_comes_back_when_it_says_it_will(self, monkeypatch):
        chain = voice.models()
        voice.mark_spent(chain[0], 0.0)  # already elapsed
        assert not voice.is_spent(chain[0])
        assert chain[0] in voice.models()

    def test_every_model_spent_still_returns_a_chain_to_fail_against(self):
        # An empty list would raise a confusing "no models" error instead of the real 429,
        # and the user would be told nothing useful.
        for model in voice.models():
            voice.mark_spent(model, 3600)
        assert voice.models() == voice.models(skip_spent=False)

    def test_a_per_minute_burst_limit_does_not_sideline_a_model(self):
        # Only a per-day quota is a reason to stop asking. A burst limit clears in seconds,
        # and dropping the primary voice for an hour over one would be a real regression.
        burst = Exception(
            "429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerMinutePerProjectPerModel "
            '"retryDelay": "13s"'
        )
        assert voice._daily_retry_after(burst) is None

    def test_a_per_day_quota_is_read_off_the_error(self):
        daily = Exception(
            "429 RESOURCE_EXHAUSTED quotaId: GenerateRequestsPerDayPerProjectPerModel "
            "quotaValue: 100 ... 'retryDelay': '44578s'"
        )
        assert voice._daily_retry_after(daily) == 44578.0

    def test_a_day_quota_with_no_stated_delay_still_sidelines_the_model(self):
        daily = Exception("429 quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier")
        assert voice._daily_retry_after(daily) == 3600.0
