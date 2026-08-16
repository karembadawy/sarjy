# -*- coding: utf-8 -*-
"""Voice synthesis: the provider router of D-005.

    synthesize(text, language, persona) -> WAV bytes

Three providers, exactly one of them implemented in Phase 2:

    gemini      (default) free, Arabic-capable, used for all development
    elevenlabs  DEMO ONLY — the free tier is ~10 minutes a month, rationed for the Loom
                recording and the live demo. Deliberately raises here (CLAUDE.md rule 2).
    browser     emergency fallback, synthesised client-side. Raises: there is nothing for
                the server to return.

Gemini hands back raw 16-bit PCM (`audio/l16; rate=24000; channels=1`), not a playable
file — verified against the API, and the SDK's own `audio/wav` response format is rejected
by this model (D-037). So we glue a 44-byte WAV header on before it goes down the socket and
the browser can play the frame as-is.
"""

from __future__ import annotations

import io
import logging
import re
import time
import wave

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from . import config, personas, quota
from . import language as language_module

log = logging.getLogger("sarjy.voice")

# What Gemini TTS actually returns (confirmed against the API, not assumed).
SAMPLE_RATE_HZ = 24_000
SAMPLE_WIDTH_BYTES = 2  # 16-bit
CHANNELS = 1

# Free-tier TTS quota is brutal and it is *per day, per model* (D-038): ten requests a day on
# gemini-3.1-flash-tts-preview. Sleeping through a 429 is the wrong move — the retryDelay is
# a minute and the allowance is not coming back today anyway. But the quota is metered per
# model, so a second model is a second allowance. On a quota refusal we walk the chain
# instead of waiting, exactly the trick D-034 used for the brain.

# Voices verified by round-trip: synthesised Egyptian Arabic, then transcribed back through
# Deepgram. Sulafat ("Warm") scored 0.993 and Achernar ("Soft") 0.935 — both intelligible,
# both in the calm register a phone assistant wants. Override in .env after listening.
DEFAULT_VOICE_AR = "Sulafat"
DEFAULT_VOICE_EN = "Achernar"


class VoiceError(RuntimeError):
    """Speech could not be synthesised."""


class VoiceQuotaError(VoiceError):
    """The free tier is throttling. Not a fault — the allowance is momentarily spent."""


_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.gemini_api_key())
    return _client


# --------------------------------------------------------------------------------------
# WAV framing
# --------------------------------------------------------------------------------------


def pcm_to_wav(pcm: bytes) -> bytes:
    """Wrap raw little-endian 16-bit PCM in a WAV container the browser can decode."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH_BYTES)
        wav.setframerate(SAMPLE_RATE_HZ)
        wav.writeframes(pcm)
    return buffer.getvalue()


# --------------------------------------------------------------------------------------
# Sentence splitting — how a reply becomes audio frames
# --------------------------------------------------------------------------------------

# Arabic punctuation is its own set of code points: ؟ (question mark), ؛ (semicolon),
# ۔ (full stop) and the Arabic comma ، — none of which a Latin-only splitter sees.
_SENTENCE_END = re.compile(r"(?<=[.!?؟۔;؛…])\s+")

# Below this, a "sentence" is a fragment like "تمام." — sending it as its own frame costs a
# whole TTS request (precious, see D-038) and makes playback choppy, so it is merged with
# the next one. The first chunk stays short on purpose: it is what the user waits for.
MIN_CHUNK_CHARS = 60


def split_for_speech(text: str) -> list[str]:
    """Split a reply into the chunks that become audio frames, in order.

    Sentence-sized so the first frame can be spoken while the rest is still synthesising,
    but merged when they are tiny, because every chunk is one metered TTS request.
    """
    text = (text or "").strip()
    if not text:
        return []

    sentences = [part.strip() for part in _SENTENCE_END.split(text) if part.strip()]
    if not sentences:
        return []

    chunks: list[str] = []
    for sentence in sentences:
        if chunks and len(chunks[-1]) < MIN_CHUNK_CHARS:
            chunks[-1] = f"{chunks[-1]} {sentence}"
        else:
            chunks.append(sentence)
    return chunks


# --------------------------------------------------------------------------------------
# The router
# --------------------------------------------------------------------------------------


def provider() -> str:
    return (config.get("TTS_PROVIDER", "gemini") or "gemini").strip().lower()


def voice_for(language: str, persona: personas.Persona | None = None) -> str:
    """Which prebuilt Gemini voice speaks this reply.

    Persona is accepted because product.md §7 makes the Arabic voice part of the persona;
    Gemini's prebuilt voices are not dialect-specific, so Egyptian and Gulf share one Arabic
    voice here and the dialect comes from the generated text (the same reasoning as D-026).
    The ElevenLabs path in Phase 6 is where the per-persona voice IDs actually differ.
    """
    if language == "ar":
        return config.get("GEMINI_TTS_VOICE_AR", DEFAULT_VOICE_AR) or DEFAULT_VOICE_AR
    return config.get("GEMINI_TTS_VOICE_EN", DEFAULT_VOICE_EN) or DEFAULT_VOICE_EN


def synthesize(text: str, language: str, persona: personas.Persona | None = None) -> bytes:
    """Speak `text` and return a playable WAV. `language` is "ar" | "en" | "mixed"."""
    text = (text or "").strip()
    if not text:
        raise VoiceError("Nothing to speak.")

    # A mixed reply is mostly one language; §6.1's dominant-language rule already chose it,
    # but be defensive so a stray "mixed" never picks the wrong voice silently.
    if language == "mixed":
        language = language_module.dominant_language(text)

    name = provider()
    if name == "gemini":
        return _synthesize_gemini(text, language, persona)
    if name == "elevenlabs":
        raise VoiceError(
            "TTS_PROVIDER=elevenlabs is reserved for the Loom recording and the live demo "
            "(CLAUDE.md rule 2: ~10 minutes of free quota a month). Implemented in Phase 6. "
            "Set TTS_PROVIDER=gemini for development."
        )
    if name == "browser":
        raise VoiceError(
            "TTS_PROVIDER=browser synthesises in the client with the Web Speech API, so the "
            "server has no audio to return. Emergency fallback only; not wired up yet."
        )
    raise VoiceError(f"Unknown TTS_PROVIDER {name!r}. Use gemini | elevenlabs | browser.")


# A model whose *daily* allowance is spent, and the monotonic clock reading after which it is
# worth asking again. Measured on a live call: with the primary model out for another twelve
# hours, every single chunk paid a full round trip to it before falling back — four failed
# calls for one reply on one turn, and `first audio` at 8.9s where a healthy turn is ~2s.
#
# D-038 built the chain to survive exhaustion and then forgot which model was exhausted. The
# 429 says exactly how long to wait ("Please retry in 12h22m58s"), so we believe it: the model
# is skipped until then, and the chain starts at one that can actually speak.
_spent_until: dict[str, float] = {}

# Per-process only, deliberately. Cloud Run may hold several instances and none of them needs
# to agree about this — the worst case is that a fresh instance pays one wasted call and then
# learns the same thing.


def mark_spent(model: str, retry_after_s: float) -> None:
    _spent_until[model] = time.monotonic() + max(0.0, retry_after_s)
    log.warning(
        "voice: %s is out of daily quota for %.1f hours — skipping it until then",
        model, retry_after_s / 3600,
    )


def is_spent(model: str) -> bool:
    until = _spent_until.get(model)
    if until is None:
        return False
    if time.monotonic() >= until:
        del _spent_until[model]  # the allowance has reset; let it back in
        return False
    return True


def models(skip_spent: bool = True) -> list[str]:
    """The TTS models to try, in order: the configured one, then its spare allowances.

    Models known to be out of quota for the rest of the day are dropped — but never all of
    them: if every model is spent the full chain is returned anyway, so the caller fails with
    a real API error and a real message rather than with an empty list.
    """
    primary = config.gemini_tts_model()
    spares = config.get("GEMINI_TTS_MODEL_FALLBACKS", "") or ""
    chain = list(dict.fromkeys([primary, *(n.strip() for n in spares.split(",") if n.strip())]))
    if not skip_spent:
        return chain
    return [model for model in chain if not is_spent(model)] or chain


# A daily quota with no stated delay still means "not today". An hour is a safe floor: it
# cannot outlive a real reset by much, and the model is let back in the moment it expires.
ASSUMED_DAILY_RESET_S = 3600.0


def _daily_retry_after(exc: Exception) -> float | None:
    """How long a 429 says to wait, but only when it is a *per-day* quota.

    A per-minute burst limit must not put a model in the sin bin for the rest of the call —
    only `…PerDay…` does, and only the quotaId tells them apart (D-035, app/quota.py).
    """
    if not quota.is_per_day(exc):
        return None
    return quota.retry_after_s(exc) or ASSUMED_DAILY_RESET_S


def _synthesize_gemini(text: str, language: str, persona: personas.Persona | None) -> bytes:
    voice = voice_for(language, persona)

    # No `language_code` on the SpeechConfig: Gemini infers the language from the text, and
    # it renders Egyptian Arabic correctly this way (verified). Forcing a code here is the
    # knob to reach for if a reply is ever read with the wrong accent.
    generation_config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice)
            )
        ),
    )

    chain = models()
    last: Exception | None = None
    for model in chain:
        try:
            response = client().models.generate_content(
                model=model, contents=text, config=generation_config
            )
            pcm = _extract_pcm(response)
            log.info("voice: %s/%s %d chars → %d bytes", model, voice, len(text), len(pcm))
            return pcm_to_wav(pcm)
        except Exception as exc:  # noqa: BLE001
            # Any failure moves to the next model rather than ending the turn. The reasons
            # are all real and all different: 429 (day's allowance spent), 400 "Model tried
            # to generate text, but it should only be used for TTS" (the 2.5 previews do
            # this on some inputs — seen live mid-conversation), a response with no audio
            # part at all (finish_reason=OTHER), or a transient network fault. From the
            # user's side they are one thing — this model cannot speak this sentence — and
            # the next model usually can.
            last = exc
            if (retry_after := _daily_retry_after(exc)) is not None:
                mark_spent(model, retry_after)
            log.warning("voice: %s could not speak it (%s) — trying the next model", model, exc)
            continue

    if isinstance(last, genai_errors.APIError) and last.code == 429:
        raise VoiceQuotaError(
            f"Every configured Gemini TTS model is out of free quota ({', '.join(chain)}). "
            "The allowance is per model per day — add another to GEMINI_TTS_MODEL_FALLBACKS, "
            "or wait for the reset at midnight Pacific."
        ) from last
    raise VoiceError(f"Gemini TTS failed: {last}") from last


def _extract_pcm(response) -> bytes:
    """Pull the audio bytes out of a generate_content response, or say why there are none."""
    try:
        part = response.candidates[0].content.parts[0]
        data = part.inline_data.data
    except (AttributeError, IndexError, TypeError) as exc:
        reason = "?"
        if getattr(response, "candidates", None):
            reason = getattr(response.candidates[0], "finish_reason", "?")
        raise VoiceError(f"Gemini TTS returned no audio (finish_reason={reason}).") from exc
    if not data:
        raise VoiceError("Gemini TTS returned an empty audio part.")
    return data
