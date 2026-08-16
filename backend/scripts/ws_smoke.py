# -*- coding: utf-8 -*-
"""Drive one complete voice turn over `/ws` — no microphone, no human, no browser.

Phase 3 has to answer "does the *deployed thing* work", and the honest version of that
question is not `curl /api/health`: it is a whole turn — audio up, the Deepgram race, the
brain, synthesis, audio frames back down. This streams a WAV into the socket in real time,
the way MediaRecorder does, and prints what came back.

The test audio comes from macOS `say` (voice Majed, ar_001), not from Gemini TTS: it is free,
offline and byte-identical between runs, and D-036 established that Deepgram transcribes
synthesised Egyptian correctly. The only paid calls are the ones under test — one brain call
and the reply's synthesis.

    python scripts/ws_smoke.py                       # the local container on :8000
    python scripts/ws_smoke.py --url wss://HOST/ws --origin https://sarjy.vercel.app
    python scripts/ws_smoke.py --text "ايه الجو بكرة في اسكندرية؟"

Exit codes: 0 = a spoken reply came back · 1 = the turn failed · 2 = could not even connect.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

# The canonical code-switch probe from D-016 / D-047, spelled the way Deepgram's Arabic
# channel writes it back so the assertion is about the pipeline, not about spelling.
DEFAULT_TEXT = "عايز أعمل بوك لميتنج بكرة الساعة خمسة"

SAMPLE_RATE = 16_000
CHUNK_MS = 250  # matches the frontend's MediaRecorder timeslice
# Deepgram ends a turn on DEEPGRAM_ENDPOINTING_MS of silence (500ms by default), and silence
# only exists if we send it: a stream that simply stops never endpoints.
TRAILING_SILENCE_MS = 2_000
REPLY_TIMEOUT_S = 90


# --------------------------------------------------------------------------------------
# Test audio
# --------------------------------------------------------------------------------------


def synthesise(text: str, destination: Path) -> Path:
    """Speak `text` into a 16kHz mono WAV using the OS voice. macOS only, by design."""
    if sys.platform != "darwin":
        raise RuntimeError("`say` is macOS-only — pass --audio with your own WAV instead.")
    subprocess.run(
        ["say", "-v", "Majed", "-o", str(destination), "--data-format=LEI16@16000", text],
        check=True,
        capture_output=True,
    )
    return destination


def wav_with_trailing_silence(path: Path) -> bytes:
    """The file's PCM plus a tail of silence, re-wrapped as one valid WAV.

    Appending zeros *after* a finished file would be a stream whose header disagrees with its
    length; rebuilding the header keeps the thing we send honest.
    """
    with wave.open(str(path), "rb") as source:
        channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
        frames = source.readframes(source.getnframes())

    silence = b"\x00" * int(rate * channels * width * TRAILING_SILENCE_MS / 1000)

    buffer = Path(str(path) + ".padded.wav")
    with wave.open(str(buffer), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(frames + silence)
    data = buffer.read_bytes()
    buffer.unlink(missing_ok=True)
    return data


# --------------------------------------------------------------------------------------
# One turn
# --------------------------------------------------------------------------------------


class Turn:
    """Everything the server said back, and when.

    Since Phase 4 the server speaks first (product.md §5), so everything that arrives before
    our own `final` belongs to the greeting and is tracked separately — otherwise the
    greeting's `speak_end` would be mistaken for the end of the turn under test, and the
    script would report PASS before the real reply had even started.
    """

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.interims = 0
        self.greeting: str | None = None
        self.barged_in = False
        self.final: str | None = None
        self.final_language: str | None = None
        self.reply: str | None = None
        self.audio_frames = 0
        self.audio_bytes = 0
        self.errors: list[dict] = []
        self.ready = asyncio.Event()
        self.done = asyncio.Event()
        self.at: dict[str, float] = {}

    @property
    def answering(self) -> bool:
        """Is what is arriving now a reply to *us*, rather than the opening greeting?"""
        return self.final is not None

    def mark(self, name: str) -> None:
        self.at[name] = (time.perf_counter() - self.started) * 1000


async def reader(socket, turn: Turn) -> None:
    async for message in socket:
        if isinstance(message, bytes):
            if not turn.answering:
                continue  # the greeting's audio
            if not turn.audio_frames:
                turn.mark("first_audio")
            turn.audio_frames += 1
            turn.audio_bytes += len(message)
            continue

        event = json.loads(message)
        kind = event.get("type")
        if kind == "ready":
            turn.mark("ready")
            turn.ready.set()
        elif kind == "interim":
            turn.interims += 1
        elif kind == "final":
            turn.mark("final")
            turn.final = event.get("text")
            turn.final_language = event.get("language")
        elif kind == "reply_text":
            if turn.answering:
                turn.mark("reply_text")
                turn.reply = event.get("text")
            else:
                turn.greeting = event.get("text")
        elif kind == "stop_speaking":
            # Talking over the greeting is the barge-in path, and this script is loud enough
            # to trigger it — so a smoke run exercises the interrupt as well as the turn.
            if turn.answering:
                turn.mark("speak_end")
                turn.done.set()
            else:
                turn.barged_in = True
        elif kind == "speak_end":
            if turn.answering:
                turn.mark("speak_end")
                turn.done.set()
        elif kind == "error":
            turn.errors.append(event)


async def run(url: str, origin: str | None, audio: bytes, persona: str) -> Turn:
    headers = {"Origin": origin} if origin else {}
    async with connect(url, additional_headers=headers, max_size=None) as socket:
        turn = Turn()
        pump = asyncio.create_task(reader(socket, turn))

        await socket.send(
            json.dumps(
                {
                    "type": "hello",
                    "user_id": str(uuid.uuid4()),
                    "session_id": str(uuid.uuid4()),
                    "persona": persona,
                    "audio_mime": "audio/wav",  # what this script actually sends
                    "timeslice_ms": CHUNK_MS,
                }
            )
        )
        await asyncio.wait_for(turn.ready.wait(), timeout=20)

        # Real time, not as fast as the socket will take it: endpointing is a function of
        # wall-clock silence, so a firehose would arrive as one undifferentiated blob.
        chunk = int(SAMPLE_RATE * 2 * CHUNK_MS / 1000)
        for offset in range(0, len(audio), chunk):
            await socket.send(audio[offset : offset + chunk])
            await asyncio.sleep(CHUNK_MS / 1000)
        turn.mark("audio_sent")

        try:
            await asyncio.wait_for(turn.done.wait(), timeout=REPLY_TIMEOUT_S)
        except TimeoutError:
            pass

        await socket.send(json.dumps({"type": "bye"}))
        pump.cancel()
        return turn


# --------------------------------------------------------------------------------------


def report(turn: Turn) -> int:
    def ms(name: str) -> str:
        return f"{turn.at[name]:.0f}ms" if name in turn.at else "—"

    print()
    if turn.greeting:
        print(f"  greeted    {turn.greeting}")
        print(f"  barge-in   {'yes — cut the greeting off' if turn.barged_in else 'no'}")
    print(f"  heard      [{turn.final_language or '?'}] {turn.final or '(nothing)'}")
    print(f"  replied    {turn.reply or '(nothing)'}")
    print(f"  audio      {turn.audio_frames} frames, {turn.audio_bytes / 1024:.0f} KiB")
    print(f"  interims   {turn.interims}")
    print(
        f"  timings    ready {ms('ready')} · final {ms('final')} · reply {ms('reply_text')} "
        f"· first audio {ms('first_audio')} · speak_end {ms('speak_end')}"
    )
    # Latency the user actually feels: from the end of their speech to the first sound back.
    if "first_audio" in turn.at and "audio_sent" in turn.at:
        felt = turn.at["first_audio"] - turn.at["audio_sent"] + TRAILING_SILENCE_MS
        print(f"  felt       {felt:.0f}ms from end-of-speech to first audio")
    for error in turn.errors:
        print(f"  ERROR      {error.get('message_en')} · {error.get('message_ar')}")

    if not turn.final:
        print("\nFAIL — nothing was transcribed. Check the audio container and Deepgram.")
        return 1
    if not turn.reply:
        print("\nFAIL — transcribed, but the brain produced no reply.")
        return 1
    if not turn.audio_frames:
        print("\nFAIL — replied in text, but no audio came back. Check the TTS chain.")
        return 1
    print("\nPASS — a spoken reply came back over the socket.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:8000/ws")
    parser.add_argument("--origin", default="http://localhost:5173")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--audio", type=Path, help="Use this WAV instead of synthesising one.")
    parser.add_argument("--persona", default="egyptian")
    args = parser.parse_args()

    if args.audio:
        source = args.audio
    else:
        source = Path("/tmp") / f"sarjy-smoke-{abs(hash(args.text)) % 10**8}.wav"
        synthesise(args.text, source)

    audio = wav_with_trailing_silence(source)
    print(f"→ {args.url}")
    print(f"  saying     {args.text}")
    print(f"  audio      {len(audio) / 1024:.0f} KiB · {len(audio) / (SAMPLE_RATE * 2):.1f}s")

    try:
        turn = asyncio.run(run(args.url, args.origin, audio, args.persona))
    except (WebSocketException, OSError, TimeoutError) as exc:
        print(f"\nFAIL — could not hold the socket: {exc}")
        return 2
    return report(turn)


if __name__ == "__main__":
    raise SystemExit(main())
