# -*- coding: utf-8 -*-
"""Five people call Sarjy at once. Does anybody hear anybody else's conversation?

    cd backend && venv/bin/python scripts/load_test.py                 # local, 5 sessions
    cd backend && venv/bin/python scripts/load_test.py --url wss://HOST/ws --origin https://…
    cd backend && venv/bin/python scripts/load_test.py --sessions 4 --synthetic

This exists for one failure class, and it is the worst one this project could ship. The
speech pipeline keeps real per-call state — two Deepgram connections, a race with a pending
map, an epoch counter (D-046), a turn gate (D-056). Every one of those is *supposed* to live
inside the WebSocket handler and therefore inside one call. If any of it were module-level,
the app would behave perfectly in every test we have run so far — a single session at a time —
and then, on the one afternoon the whole Sarj team opens the demo at once, somebody would hear
somebody else's sentence come back in their own voice. That is not a bug you want to discover
in front of the panel.

So each session says something **different** and the transcripts are checked against every
session's truth, not just their own: a session whose transcript matches a *neighbour's*
sentence better than its own has been contaminated. Replies and audio are hashed for the same
reason.

The audio is the benchmark's own recordings (`eval/recordings/`) when they exist, and macOS
`say` otherwise, so the script runs before the recording session as well as after.

Exit codes: 0 = all answered and isolated · 1 = a failure worth investigating · 2 = could not
connect at all.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import subprocess
import sys
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

BACKEND_DIR = Path(__file__).resolve().parent.parent
EVAL_DIR = BACKEND_DIR.parent / "eval"
RECORDINGS = EVAL_DIR / "recordings"
TRUTH = EVAL_DIR / "truth.csv"

CHUNK_MS = 250
TRAILING_SILENCE_MS = 2_000
REPLY_TIMEOUT_S = 120

# Deliberately different from each other in content *and* language, so a crossed wire shows up
# as nonsense rather than as a plausible answer. Used when there are no recordings yet.
SYNTHETIC = [
    ("ar", "Majed", "الجو النهارده عامل ايه في اسكندرية"),
    ("en", "Samantha", "Book me a table for four people tonight"),
    ("ar", "Majed", "العصر الساعة كام النهارده"),
    ("en", "Samantha", "What are my upcoming bookings"),
    ("ar", "Majed", "اسمي كريم وبحب اللون الأزرق"),
]


# --------------------------------------------------------------------------------------
# Audio
# --------------------------------------------------------------------------------------


def wav_bytes(path: Path) -> bytes:
    """The file's audio plus trailing silence, as one valid WAV (see ws_smoke.py)."""
    with wave.open(str(path), "rb") as source:
        channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
        frames = source.readframes(source.getnframes())

    silence = b"\x00" * int(rate * channels * width * TRAILING_SILENCE_MS / 1000)
    padded = Path(f"{path}.padded.wav")
    with wave.open(str(padded), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(frames + silence)
    data = padded.read_bytes()
    padded.unlink(missing_ok=True)
    return data


def synthesise(text: str, voice: str, destination: Path) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("`say` is macOS-only — record eval/recordings/ first, or use --audio.")
    subprocess.run(
        ["say", "-v", voice, "-o", str(destination), "--data-format=LEI16@16000", text],
        check=True,
        capture_output=True,
    )
    return destination


def from_recordings(count: int) -> list[tuple[str, Path]]:
    """One utterance per session, spread across the three groups so they differ maximally."""
    if not TRUTH.exists():
        return []
    with TRUTH.open(encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if (RECORDINGS / f"{row['filename']}.wav").exists()]
    if len(rows) < count:
        return []

    picked, seen = [], set()
    # Round-robin the groups first, so five sessions are not five Arabic sentences.
    for group in ("ar", "en", "mixed"):
        for row in rows:
            if row["group"] == group and row["filename"] not in seen and row["condition"] == "quiet":
                picked.append(row)
                seen.add(row["filename"])
                break
    for row in rows:
        if len(picked) >= count:
            break
        if row["filename"] not in seen:
            picked.append(row)
            seen.add(row["filename"])
    return [(row["text"], RECORDINGS / f"{row['filename']}.wav") for row in picked[:count]]


# --------------------------------------------------------------------------------------
# One session
# --------------------------------------------------------------------------------------


@dataclass
class Session:
    index: int
    said: str
    audio: bytes

    instance: str | None = None
    greeting: str | None = None
    heard: str | None = None
    reply: str | None = None
    frames: int = 0
    audio_sha: str | None = None
    errors: list[str] = field(default_factory=list)
    ready_ms: float | None = None
    answered_ms: float | None = None

    @property
    def answered(self) -> bool:
        return bool(self.heard and self.reply and self.frames)


async def run_session(url: str, origin: str | None, session: Session) -> None:
    headers = {"Origin": origin} if origin else {}
    started = time.perf_counter()
    audio_hash = hashlib.sha256()
    answering = False
    done = asyncio.Event()

    async with connect(url, additional_headers=headers, max_size=None) as socket:

        async def reader() -> None:
            nonlocal answering
            async for message in socket:
                if isinstance(message, bytes):
                    if answering:
                        session.frames += 1
                        audio_hash.update(message)
                    continue
                event = json.loads(message)
                kind = event.get("type")
                if kind == "ready":
                    session.instance = event.get("instance")
                    session.ready_ms = (time.perf_counter() - started) * 1000
                elif kind == "final":
                    session.heard = event.get("text")
                    answering = True
                elif kind == "reply_text":
                    if answering:
                        session.reply = event.get("text")
                    else:
                        session.greeting = event.get("text")
                elif kind in ("speak_end", "stop_speaking") and answering:
                    session.answered_ms = (time.perf_counter() - started) * 1000
                    done.set()
                elif kind == "error":
                    session.errors.append(f"{event.get('key')}: {event.get('message_en')}")

        pump = asyncio.create_task(reader())
        await socket.send(
            json.dumps({
                "type": "hello",
                # A distinct user per session: sharing one would make cross-talk in the
                # *database* look like isolation working, which is the opposite of the point.
                "user_id": str(uuid.uuid4()),
                "session_id": str(uuid.uuid4()),
                "persona": "egyptian",
                "audio_mime": "audio/wav",
                "timeslice_ms": CHUNK_MS,
            })
        )

        step = int(16_000 * 2 * CHUNK_MS / 1000)
        for offset in range(0, len(session.audio), step):
            await socket.send(session.audio[offset : offset + step])
            await asyncio.sleep(CHUNK_MS / 1000)

        try:
            await asyncio.wait_for(done.wait(), timeout=REPLY_TIMEOUT_S)
        except TimeoutError:
            session.errors.append("timed out waiting for the reply to finish")

        await socket.send(json.dumps({"type": "bye"}))
        pump.cancel()

    session.audio_sha = audio_hash.hexdigest()[:16] if session.frames else None


# --------------------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------------------


def overlap(a: str, b: str) -> float:
    """Share of `b`'s words that appear in `a`. Crude on purpose — it only has to tell one
    sentence from four completely different ones, and a WER here would need the eval package."""
    words = [w for w in (b or "").split() if w]
    if not words:
        return 0.0
    heard = set((a or "").split())
    return sum(1 for word in words if word in heard) / len(words)


def check_isolation(sessions: list[Session]) -> list[str]:
    """Did any session hear, answer, or play back something that belonged to another one?"""
    problems: list[str] = []

    for session in sessions:
        if not session.heard:
            continue
        mine = overlap(session.heard, session.said)
        for other in sessions:
            if other is session:
                continue
            theirs = overlap(session.heard, other.said)
            # The bar is deliberately "strictly better than any neighbour", not "above some
            # threshold": Deepgram may mangle a sentence badly and still be perfectly
            # isolated, and that must not be reported as cross-talk.
            if theirs > mine:
                problems.append(
                    f"session {session.index} heard something closer to session "
                    f"{other.index}'s sentence ({theirs:.0%} vs its own {mine:.0%}): "
                    f"{session.heard!r}"
                )

    def collisions(field_name: str, label: str) -> None:
        seen: dict[str, int] = {}
        for session in sessions:
            value = getattr(session, field_name)
            if not value:
                continue
            if value in seen:
                problems.append(
                    f"sessions {seen[value]} and {session.index} got identical {label} — "
                    f"they asked different questions, so this is one turn answering two calls"
                )
            else:
                seen[value] = session.index

    # The greeting is a template (D-059) and is identical everywhere by design, so only the
    # *reply* and the *audio of the reply* are checked.
    collisions("reply", "reply text")
    collisions("audio_sha", "reply audio")
    return problems


# --------------------------------------------------------------------------------------


def report(sessions: list[Session], elapsed: float) -> int:
    print()
    print(f"  {'#':<3} {'instance':<10} {'ready':>8} {'answered':>10} {'frames':>7}  transcript")
    for session in sessions:
        ready = f"{session.ready_ms:.0f}ms" if session.ready_ms else "—"
        answered = f"{session.answered_ms:.0f}ms" if session.answered_ms else "—"
        print(
            f"  {session.index:<3} {session.instance or '?':<10} {ready:>8} {answered:>10} "
            f"{session.frames:>7}  {(session.heard or '(nothing)')[:44]}"
        )
    print()
    for session in sessions:
        print(f"  {session.index}. said    {session.said}")
        print(f"     heard   {session.heard or '(nothing)'}")
        print(f"     replied {(session.reply or '(nothing)')[:100]}")
        for error in session.errors:
            print(f"     ERROR   {error}")

    instances = {s.instance for s in sessions if s.instance}
    answered = [s for s in sessions if s.answered]
    print()
    print(f"  {len(answered)}/{len(sessions)} sessions answered, in {elapsed:.1f}s wall clock")
    print(f"  served by {len(instances)} instance(s): {', '.join(sorted(instances)) or 'unknown'}")

    problems = check_isolation(sessions)
    quota = [error for s in sessions for error in s.errors if "quota" in error or "429" in error]
    if quota:
        print(f"  {len(quota)} rate-limit message(s) — the designed state, not a failure")

    if problems:
        print("\n  ISOLATION FAILURES")
        for problem in problems:
            print(f"    ! {problem}")
        print("\nFAIL — sessions are not isolated from each other.")
        return 1
    print("  isolation: no session heard, answered or played back another session's turn")

    if len(answered) < len(sessions):
        print("\nFAIL — not every session got a spoken answer (see the ERROR lines above).")
        return 1
    print("\nPASS — every session was answered, and every call stayed its own.")
    return 0


async def main_async(args) -> int:
    if args.synthetic:
        chosen = []
    else:
        chosen = from_recordings(args.sessions)
    if not chosen:
        if not args.synthetic:
            print("  (no eval recordings yet — falling back to synthesised speech)")
        scratch = Path("/tmp")
        chosen = [
            (text, synthesise(text, voice, scratch / f"sarjy-load-{index}.wav"))
            for index, (_, voice, text) in enumerate(SYNTHETIC[: args.sessions])
        ]

    sessions = [
        Session(index=index + 1, said=text, audio=wav_bytes(path))
        for index, (text, path) in enumerate(chosen)
    ]

    print(f"→ {args.url}")
    print(f"  {len(sessions)} simultaneous session(s), each saying something different")

    started = time.perf_counter()
    results = await asyncio.gather(
        *(run_session(args.url, args.origin, session) for session in sessions),
        return_exceptions=True,
    )
    elapsed = time.perf_counter() - started

    for session, result in zip(sessions, results, strict=True):
        if isinstance(result, BaseException):
            session.errors.append(f"{type(result).__name__}: {result}")

    return report(sessions, elapsed)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://localhost:8000/ws")
    parser.add_argument("--origin", default="http://localhost:5173")
    parser.add_argument("--sessions", type=int, default=5)
    parser.add_argument("--synthetic", action="store_true",
                        help="use macOS `say` even when eval recordings exist")
    args = parser.parse_args()

    try:
        return asyncio.run(main_async(args))
    except (WebSocketException, OSError) as exc:
        print(f"\nFAIL — could not hold the sockets: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
