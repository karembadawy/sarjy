# -*- coding: utf-8 -*-
"""The speech-recognition benchmark: five systems, thirty utterances, three scoring passes.

    backend/venv/bin/python eval/run_benchmark.py            # transcribe (cached) and score
    backend/venv/bin/python eval/run_benchmark.py --refresh  # ignore the cache, call the APIs
    backend/venv/bin/python eval/run_benchmark.py --systems racer,gemini
    backend/venv/bin/python eval/run_benchmark.py --write    # also update eval/results.md

**What is under test is the thing we shipped.** The roadmap's original plan compared "Deepgram
vs Web Speech vs Gemini", but production speech recognition is not a Deepgram setting — it is
the two-channel racer of D-036/D-045, and its decision rule is the interesting claim. So the
racer is a system in this table, and it is measured by running **the production function**
(`app.speech_recognition.pick_winner`) over the two channels' results, not by a reimplementation
that could quietly disagree with the shipped one.

Methodology caveat, stated here and again in results.md: the racer runs on Deepgram's
**prerecorded** endpoint here and on the **streaming** endpoint in production. The models are
the same (nova-3) but the two paths do not have to behave identically — streaming decides
without having heard the end of the sentence, and D-055 documents a live-only Arabic failure
this benchmark cannot reproduce. The racer numbers below are therefore an upper bound on the
live racer, and the live evidence in D-047 and D-055 is not replaced by them.

Every raw transcript is cached under `eval/raw/` so that scoring can be re-run, the borrow
table extended, and the tables regenerated without spending another API call — and so that the
numbers in results.md can be traced back to the exact strings that produced them.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent / "backend"
RECORDINGS = EVAL_DIR / "recordings"
RAW = EVAL_DIR / "raw"
TRUTH = EVAL_DIR / "truth.csv"
RESULTS = EVAL_DIR / "results.md"
WEBSPEECH_RESULTS = EVAL_DIR / "webspeech_results.csv"

sys.path.insert(0, str(EVAL_DIR))
sys.path.insert(0, str(BACKEND_DIR))

import scoring  # noqa: E402

# The production racer, imported rather than copied. `_Candidate` is private to that module and
# is used here on purpose: the benchmark's whole claim is that it scored the shipped rule, so
# it feeds the shipped rule the shipped data structure.
from app.speech_recognition import MODEL, Transcriber, _Candidate, pick_winner  # noqa: E402

GROUPS = ["ar", "en", "mixed"]
GROUP_TITLES = {"ar": "Egyptian Arabic", "en": "English", "mixed": "Code-switched"}

# Where the generated tables live inside results.md. Prose outside these markers survives a
# re-run; everything between them is rewritten from the raw transcripts.
BEGIN = "<!-- BEGIN GENERATED -->"
END = "<!-- END GENERATED -->"


# ---------------------------------------------------------------------------------------
# The set
# ---------------------------------------------------------------------------------------


@dataclass
class Utterance:
    filename: str
    group: str
    condition: str
    tags: str
    text: str

    @property
    def wav(self) -> Path:
        return RECORDINGS / f"{self.filename}.wav"

    @property
    def entity_class(self) -> bool:
        """The D-047 weakness class: an English sentence carrying Arabic proper nouns."""
        return "entity" in self.tags


def load_utterances() -> list[Utterance]:
    with TRUTH.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        Utterance(
            filename=row["filename"],
            group=row["group"],
            condition=row["condition"],
            tags=row.get("tags", "") or "",
            text=row["text"],
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------------------
# Raw-transcript cache
# ---------------------------------------------------------------------------------------


def cache_path(system: str) -> Path:
    return RAW / f"{system}.json"


def load_cache(system: str) -> dict[str, dict]:
    path = cache_path(system)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_cache(system: str, cache: dict[str, dict]) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    cache_path(system).write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )


# ---------------------------------------------------------------------------------------
# Systems under test
# ---------------------------------------------------------------------------------------


def deepgram_channel(language: str):
    """One Deepgram prerecorded channel, with production's own parameters.

    `model`, `punctuate` and `smart_format` match app/speech_recognition.py exactly. The
    streaming-only settings (endpointing, utterance_end_ms, interim_results) have no
    prerecorded equivalent, which is one half of the caveat in the module docstring.
    """
    from deepgram import DeepgramClient  # imported late: only this path needs the SDK

    from app import config

    client = DeepgramClient(api_key=config.deepgram_api_key())

    def transcribe(utterance: Utterance) -> dict:
        response = client.listen.v1.media.transcribe_file(
            request=utterance.wav.read_bytes(),
            model=MODEL,
            language=language,
            punctuate=True,
            smart_format=True,
        )
        try:
            alternative = response.results.channels[0].alternatives[0]
        except (AttributeError, IndexError):
            return {"text": "", "confidence": 0.0}
        return {
            "text": (alternative.transcript or "").strip(),
            "confidence": float(alternative.confidence or 0.0),
        }

    return transcribe


# The transcription prompt is deliberately explicit about the two things a chat model does
# wrong here without being told: it translates, and it tidies. Neither is transcription.
GEMINI_PROMPT = """\
Transcribe this audio clip word for word. It is one short sentence spoken by an Egyptian
speaker, and it may be in Egyptian Arabic, in English, or in both mixed inside one sentence.

Rules:
- Write exactly what was said. Do not translate anything into the other language.
- Keep each word in the script it was spoken in: Arabic words in Arabic letters, English
  words in Latin letters.
- No punctuation beyond what is needed, no diacritics, no commentary, no quotation marks.
- Output the transcription and nothing else.
"""


def gemini_transcriber():
    """Gemini as a speech recogniser: the same brain model, audio in and text out."""
    from google.genai import types

    from app import brain, config

    model = config.gemini_model()

    def transcribe(utterance: Utterance) -> dict:
        response = brain.client().models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=utterance.wav.read_bytes(), mime_type="audio/wav"),
                types.Part(text=GEMINI_PROMPT),
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                thinking_config=types.ThinkingConfig(thinking_level="LOW"),
            ),
        )
        return {"text": (response.text or "").strip(), "confidence": None, "model": model}

    return transcribe


def transcribe_all(system: str, transcribe, utterances: list[Utterance],
                   refresh: bool) -> dict[str, dict]:
    """Run one system over the set, reusing anything already cached."""
    cache = {} if refresh else load_cache(system)
    todo = [u for u in utterances if u.filename not in cache]
    if not todo:
        print(f"  {system:<14} {len(cache)} cached, nothing to do")
        return cache

    print(f"  {system:<14} transcribing {len(todo)} utterance(s)…")
    for utterance in todo:
        if not utterance.wav.exists():
            print(f"    ! {utterance.filename}: no recording — skipped")
            continue
        started = time.perf_counter()
        try:
            result = transcribe(utterance)
        except Exception as exc:  # noqa: BLE001 — one bad clip must not lose the whole run
            print(f"    ! {utterance.filename}: {type(exc).__name__}: {exc}")
            continue
        result["ms"] = round((time.perf_counter() - started) * 1000)
        cache[utterance.filename] = result
        print(f"    {utterance.filename}  {result['ms']:>5}ms  {result['text'][:70]}")
        save_cache(system, cache)  # written as we go: a crash on clip 27 keeps clips 1–26
    return cache


# ---------------------------------------------------------------------------------------
# The racer — the shipped decision rule, run over the two channels' results
# ---------------------------------------------------------------------------------------


def race(ar: dict, en: dict) -> dict:
    """Decide one utterance the way production decides it (D-045).

    Empty candidates never enter the race, the winner is the one with the most expected
    correct words (confidence × word count), and length breaks an exact tie.
    """
    candidates = {
        name: _Candidate(text=result.get("text", ""), confidence=result.get("confidence") or 0.0,
                         deepgram_languages=[])
        for name, result in (("ar", ar), ("en", en))
        if result
    }
    won = pick_winner(candidates)
    if won is None:
        return {"text": "", "confidence": 0.0, "channel": None, "both_spoke": False}
    name, candidate = won
    return {
        "text": candidate.text,
        "confidence": candidate.confidence,
        "channel": name,
        # Did both channels return real text? This is the signal option (b) of the badge
        # decision would fire on, so the benchmark measures how often it would.
        "both_spoke": all(c.text.strip() for c in candidates.values()) and len(candidates) == 2,
        "scores": {
            key: round(c.confidence * len(c.text.split()), 2) for key, c in candidates.items()
        },
    }


def build_racer_sim(utterances: list[Utterance]) -> dict[str, dict]:
    """The racer as the roadmap imagined it: the shipped rule over prerecorded results."""
    ar, en = load_cache("deepgram_ar"), load_cache("deepgram_en")
    racer = {}
    for utterance in utterances:
        if utterance.filename not in ar and utterance.filename not in en:
            continue
        racer[utterance.filename] = race(
            ar.get(utterance.filename, {}), en.get(utterance.filename, {})
        )
    save_cache("racer_sim", racer)
    return racer


# ---------------------------------------------------------------------------------------
# The racer as actually shipped — the streaming path, driven by the production Transcriber
# ---------------------------------------------------------------------------------------
#
# The simulation above is a fair test of the *decision rule* and a poor test of the *system*:
# it feeds the rule prerecorded transcripts, and prerecorded is not the endpoint we ship on.
# Measured on synthesised Egyptian, the two are not slightly different — the prerecorded
# Arabic model returned "يا يا يا يا" for a sentence the streaming model transcribes cleanly.
#
# So the benchmark also drives the real thing: `Transcriber`, both channels, real-time audio,
# the adaptive race window (D-042), the epoch guard (D-046), the lost-interim fallback
# (D-055). Everything the deployed service does, minus the microphone. The gap between
# `racer_sim` and `racer_live` in results.md is the methodology caveat, measured rather than
# hedged about.

# Matches the browser's MediaRecorder timeslice, so Deepgram sees the same arrival pattern.
STREAM_CHUNK_MS = 250
# Endpointing is a function of wall-clock silence, and silence only exists if we send it:
# a stream that simply stops never reaches its endpoint.
STREAM_TRAILING_SILENCE_MS = 2_000
# After the audio, how long to keep waiting for the race to resolve. The slowest legitimate
# path is the abandoned-buffer watchdog (FINALIZE_GRACE_S, 3.0s) plus the unsure race window.
STREAM_PATIENCE_S = 8.0


def wav_with_trailing_silence(path: Path) -> bytes:
    """The file's audio plus a tail of silence, rebuilt as one valid WAV.

    Appending zeros after a finished file would be a stream whose header disagrees with its
    length; rebuilding the header keeps what we send honest. (Same approach as ws_smoke.py.)
    """
    import wave

    with wave.open(str(path), "rb") as source:
        channels, width, rate = source.getnchannels(), source.getsampwidth(), source.getframerate()
        frames = source.readframes(source.getnframes())

    silence = b"\x00" * int(rate * channels * width * STREAM_TRAILING_SILENCE_MS / 1000)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        out.writeframes(frames + silence)
    return buffer.getvalue()


async def _stream_one(utterance: Utterance) -> dict:
    """One clip through the production Transcriber, in real time, both channels racing."""
    heard: list = []

    async def on_interim(text: str, confidence: float) -> None:
        return None

    async def on_final(result) -> None:
        heard.append(result)

    audio = wav_with_trailing_silence(utterance.wav)
    # 16-bit mono at whatever rate the file carries; the byte count per timeslice only has to
    # be roughly right, because the pacing below is what Deepgram actually reacts to.
    step = int(16_000 * 2 * STREAM_CHUNK_MS / 1000)

    async with Transcriber(on_interim=on_interim, on_final=on_final) as stt:
        for offset in range(0, len(audio), step):
            await stt.feed(audio[offset : offset + step])
            await asyncio.sleep(STREAM_CHUNK_MS / 1000)

        deadline = time.monotonic() + STREAM_PATIENCE_S
        while not heard and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        # A clip that endpointed twice said one thing in two turns; give the stragglers a
        # moment so the halves can be joined rather than half the sentence being scored.
        if heard:
            await asyncio.sleep(1.0)

    if not heard:
        return {"text": "", "confidence": 0.0, "channel": None, "turns": 0,
                "both_spoke": False, "alternatives": {}}

    # Merge, so a sentence Deepgram split into two turns is scored as the one sentence it was.
    text = " ".join(result.text for result in heard if result.text).strip()
    first = heard[0]
    alternatives: dict[str, dict] = {}
    for result in heard:
        for name, entry in (result.alternatives or {}).items():
            if name in alternatives:
                alternatives[name] = {
                    **entry,
                    "text": f"{alternatives[name]['text']} {entry['text']}".strip(),
                }
            else:
                alternatives[name] = dict(entry)

    return {
        "text": text,
        "confidence": first.confidence,
        "channel": first.channel,
        "turns": len(heard),
        "language": first.language,
        # Did both channels reach the race with real text? The badge decision (D-033,
        # option b) would fire on exactly this, so the benchmark counts how often it happens.
        "both_spoke": len([a for a in alternatives.values() if a["text"].strip()]) >= 2,
        "alternatives": alternatives,
    }


def stream_transcriber():
    def transcribe(utterance: Utterance) -> dict:
        return asyncio.run(_stream_one(utterance))

    return transcribe


def split_stream_channels(racer_live: dict[str, dict]) -> tuple[dict, dict]:
    """Each streaming channel on its own, taken from what the shipped racer actually saw.

    A channel that produced nothing is simply absent from `alternatives` — empty candidates
    never enter the race (D-042) — which is faithful: for that utterance it heard nothing.
    """
    per_channel: dict[str, dict[str, dict]] = {"ar": {}, "en": {}}
    for filename, result in racer_live.items():
        for name, entry in (result.get("alternatives") or {}).items():
            if name in per_channel:
                per_channel[name][filename] = {"text": entry["text"],
                                               "confidence": entry.get("confidence", 0.0)}
    return per_channel["ar"], per_channel["en"]


def load_webspeech(utterances: list[Utterance], pairs: dict[str, str]) -> dict[str, dict]:
    """The manual-protocol rows, reduced to one transcript per utterance.

    The Web Speech API has no code-switching mode — it is given exactly one `lang` and hears
    everything as that language — so the harness runs each code-switched clip in **both**
    `ar-EG` and `en-US`. Here we keep whichever of them scored better against the truth.

    That is oracle selection and it is stated as such: a real user could not know in advance
    which language to set, so this number is the *most generous* reading of the baseline.
    Being generous to the baseline is the safe direction for a benchmark whose author has a
    stake in the alternative. Repeat runs of the same (utterance, lang) are corrections — the
    last one wins.
    """
    if not WEBSPEECH_RESULTS.exists():
        return {}

    truths = {u.filename: u.text for u in utterances}
    attempts: dict[str, dict[str, dict]] = {}
    with WEBSPEECH_RESULTS.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            attempts.setdefault(row["filename"], {})[row.get("lang", "")] = {
                "text": (row.get("heard") or "").strip(),
                "confidence": None,
                "lang": row.get("lang", ""),
                "note": row.get("note", ""),
            }

    best: dict[str, dict] = {}
    for filename, by_lang in attempts.items():
        truth = truths.get(filename)
        if truth is None:
            continue
        chosen = min(
            by_lang.values(),
            key=lambda attempt: scoring.passes([truth], [attempt["text"]], pairs)[
                "borrow_tolerant"
            ].wer,
        )
        chosen["langs_tried"] = sorted(by_lang)
        best[filename] = chosen
    save_cache("webspeech", best)
    return best


# ---------------------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------------------

SYSTEM_TITLES = {
    "deepgram_ar": "Deepgram `ar` alone · prerecorded",
    "deepgram_en": "Deepgram `en` alone · prerecorded",
    "racer_sim": "The racer, simulated on prerecorded",
    "stream_ar": "Deepgram `ar` alone · streaming",
    "stream_en": "Deepgram `en` alone · streaming",
    "racer_live": "**The racer as shipped · streaming**",
    "gemini": "Gemini audio",
    "webspeech": "Web Speech API ¹",
}


@dataclass
class Cell:
    scores: dict[str, scoring.Score]
    covered: int = 0  # utterances this system actually returned something for
    total: int = 0


def measure(results: dict[str, dict], utterances: list[Utterance],
            pairs: dict[str, str]) -> Cell:
    """Score one system over one set of utterances.

    A missing transcript is scored as an empty hypothesis rather than skipped: a system that
    returns nothing has failed the utterance, and dropping it would reward silence.
    """
    references = [u.text for u in utterances]
    hypotheses = [(results.get(u.filename) or {}).get("text", "") or "" for u in utterances]
    return Cell(
        scores=scoring.passes(references, hypotheses, pairs),
        covered=sum(1 for h in hypotheses if h.strip()),
        total=len(references),
    )


def table(systems: dict[str, dict], utterances: list[Utterance], pairs: dict[str, str],
          scoring_pass: str) -> str:
    """One system × group table for one scoring pass."""
    header = "| System | " + " | ".join(
        f"{GROUP_TITLES[g]} ({sum(1 for u in utterances if u.group == g)})" for g in GROUPS
    ) + " | All |"
    rule = "| --- |" + " --- |" * (len(GROUPS) + 1)
    lines = [header, rule]

    for system, results in systems.items():
        cells = []
        for group in [*GROUPS, None]:
            subset = [u for u in utterances if group is None or u.group == group]
            if not subset:
                cells.append("—")
                continue
            cell = measure(results, subset, pairs)
            if cell.covered == 0:
                cells.append(f"_no output_ (0/{cell.total})")
            else:
                score = cell.scores[scoring_pass]
                suffix = "" if cell.covered == cell.total else f" ({cell.covered}/{cell.total})"
                cells.append(f"{score.percent}{suffix}")
        lines.append(f"| {SYSTEM_TITLES.get(system, system)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def condition_table(systems: dict[str, dict], utterances: list[Utterance],
                    pairs: dict[str, str]) -> str:
    conditions = ["quiet", "noisy", "fast"]
    lines = ["| System | " + " | ".join(
        f"{c} ({sum(1 for u in utterances if u.condition == c)})" for c in conditions
    ) + " |", "| --- |" + " --- |" * len(conditions)]
    for system, results in systems.items():
        cells = []
        for condition in conditions:
            subset = [u for u in utterances if u.condition == condition]
            cell = measure(results, subset, pairs)
            cells.append(cell.scores["normalized"].percent if cell.covered else "_no output_")
        lines.append(f"| {SYSTEM_TITLES.get(system, system)} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def entity_table(systems: dict[str, dict], utterances: list[Utterance],
                 pairs: dict[str, str]) -> str:
    """The D-047 weakness class, on its own, with the racer's channel choice spelled out."""
    subset = [u for u in utterances if u.entity_class]
    if not subset:
        return "_No utterances are tagged `entity` in truth.csv._"

    lines = ["| System | normalized WER | borrow-tolerant WER |", "| --- | --- | --- |"]
    for system, results in systems.items():
        cell = measure(results, subset, pairs)
        if not cell.covered:
            lines.append(f"| {SYSTEM_TITLES.get(system, system)} | _no output_ | _no output_ |")
            continue
        lines.append(
            f"| {SYSTEM_TITLES.get(system, system)} | {cell.scores['normalized'].percent} | "
            f"{cell.scores['borrow_tolerant'].percent} |"
        )
    return "\n".join(lines)


def per_utterance_table(systems: dict[str, dict], utterances: list[Utterance]) -> str:
    """Every transcript, so a number in the tables above can be traced to a string."""
    lines = []
    for group in GROUPS:
        lines.append(f"\n#### {GROUP_TITLES[group]}\n")
        for utterance in [u for u in utterances if u.group == group]:
            tag = f" · `{utterance.tags}`" if utterance.tags else ""
            lines.append(f"**`{utterance.filename}`** ({utterance.condition}{tag})  ")
            lines.append(f"said · {utterance.text}  ")
            for system in systems:
                result = systems[system].get(utterance.filename) or {}
                text = (result.get("text") or "").strip() or "_(nothing)_"
                confidence = result.get("confidence")
                suffix = f" `{confidence:.2f}`" if isinstance(confidence, (int, float)) else ""
                if system == "racer" and result.get("channel"):
                    suffix += f" · won by `{result['channel']}`"
                lines.append(f"{system} ·{suffix} {text}  ")
            lines.append("")
    return "\n".join(lines)


def racer_facts(racer: dict[str, dict], utterances: list[Utterance]) -> str:
    """The numbers the badge decision (D-033) turns on, measured rather than assumed."""
    have = [u for u in utterances if u.filename in racer]
    if not have:
        return "_The racer has not been run._"

    from app import language  # the label of record, one rule for the whole app

    both = [u for u in have if racer[u.filename].get("both_spoke")]
    won_by = {"ar": 0, "en": 0}
    labels = {"ar": 0, "en": 0, "mixed": 0}
    mixed_group_labels = {"ar": 0, "en": 0, "mixed": 0}
    for utterance in have:
        result = racer[utterance.filename]
        if result.get("channel"):
            won_by[result["channel"]] = won_by.get(result["channel"], 0) + 1
        label = language.detect_language(result.get("text") or "")
        labels[label] += 1
        if utterance.group == "mixed":
            mixed_group_labels[label] += 1

    mixed_total = sum(mixed_group_labels.values())
    lines = [
        f"- The `ar` channel won **{won_by['ar']}/{len(have)}** utterances, `en` won "
        f"**{won_by['en']}/{len(have)}**.",
        f"- **Both** channels returned real text on **{len(both)}/{len(have)}** utterances "
        f"({', '.join(u.filename for u in both) or 'none'}).",
        f"- Our §6.5 ratio rule labels the winning transcripts: "
        f"`ar` {labels['ar']} · `en` {labels['en']} · `mixed` {labels['mixed']}.",
        f"- On the {mixed_total} utterances a human would call code-switched, the badge says "
        f"`ar` {mixed_group_labels['ar']} · `en` {mixed_group_labels['en']} · "
        f"`mixed` {mixed_group_labels['mixed']}.",
    ]
    return "\n".join(lines)


def render(systems: dict[str, dict], utterances: list[Utterance],
           pairs: dict[str, str], total: int) -> str:
    """Everything between the generated markers. `utterances` is the *recorded* set only —
    an utterance with no audio is absent from the tables rather than scored as a failure."""
    parts = [
        f"_Generated by `eval/run_benchmark.py` · {len(utterances)}/{total} utterances "
        f"recorded · Deepgram `{MODEL}` · borrow table: {len(pairs)} pairs._",
        "",
        "### Pass 1 — raw WER (no normalization at all)",
        "",
        table(systems, utterances, pairs, "raw"),
        "",
        "### Pass 2 — normalized WER (the documented normalization below)",
        "",
        table(systems, utterances, pairs, "normalized"),
        "",
        "### Pass 3 — borrow-tolerant WER (pass 2, plus the transliteration table)",
        "",
        table(systems, utterances, pairs, "borrow_tolerant"),
        "",
        "### By condition (normalized WER)",
        "",
        condition_table(systems, utterances, pairs),
        "",
        "### The D-047 weakness class: English sentences carrying Arabic proper nouns",
        "",
        entity_table(systems, utterances, pairs),
        "",
        "### What the racer did",
        "",
        racer_facts(systems.get("racer_live") or systems.get("racer_sim") or {}, utterances),
        "",
        "### Every transcript",
        "",
        per_utterance_table(systems, utterances),
    ]
    return "\n".join(parts)


def write_results(body: str) -> None:
    if not RESULTS.exists():
        RESULTS.write_text(f"# Benchmark results\n\n{BEGIN}\n{body}\n{END}\n", encoding="utf-8")
        print(f"\n  wrote {RESULTS}")
        return
    text = RESULTS.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise SystemExit(
            f"{RESULTS} has no {BEGIN} / {END} markers — refusing to overwrite hand-written prose."
        )
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    RESULTS.write_text(f"{head}{BEGIN}\n{body}\n{END}{tail}", encoding="utf-8")
    print(f"\n  updated the generated tables in {RESULTS}")


# ---------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="ignore the cache and re-transcribe")
    parser.add_argument(
        "--systems",
        default="deepgram_ar,deepgram_en,racer_sim,racer_live,stream_ar,stream_en,"
                "gemini,webspeech",
    )
    parser.add_argument("--write", action="store_true", help="update the tables in results.md")
    args = parser.parse_args()

    utterances = load_utterances()
    have = [u for u in utterances if u.wav.exists()]
    print(f"\n  {len(have)}/{len(utterances)} utterances recorded in {RECORDINGS}")
    if not have:
        print("  Record them first: backend/venv/bin/python eval/record.py")
        return 1
    if len(have) < len(utterances):
        missing = [u.filename for u in utterances if not u.wav.exists()]
        print(f"  missing: {', '.join(missing)}")

    wanted = [name.strip() for name in args.systems.split(",") if name.strip()]
    systems: dict[str, dict] = {}

    if "deepgram_ar" in wanted:
        systems["deepgram_ar"] = transcribe_all(
            "deepgram_ar", deepgram_channel("ar"), have, args.refresh)
    if "deepgram_en" in wanted:
        systems["deepgram_en"] = transcribe_all(
            "deepgram_en", deepgram_channel("en"), have, args.refresh)
    if "racer_sim" in wanted:
        systems["racer_sim"] = build_racer_sim(have)
        print(f"  {'racer_sim':<14} decided {len(systems['racer_sim'])} utterance(s) offline")
    if "racer_live" in wanted or "stream_ar" in wanted or "stream_en" in wanted:
        live = transcribe_all("racer_live", stream_transcriber(), have, args.refresh)
        stream_ar, stream_en = split_stream_channels(live)
        save_cache("stream_ar", stream_ar)
        save_cache("stream_en", stream_en)
        if "racer_live" in wanted:
            systems["racer_live"] = live
        if "stream_ar" in wanted:
            systems["stream_ar"] = stream_ar
        if "stream_en" in wanted:
            systems["stream_en"] = stream_en
    if "gemini" in wanted:
        systems["gemini"] = transcribe_all("gemini", gemini_transcriber(), have, args.refresh)
    pairs = scoring.load_borrow_pairs()
    if "webspeech" in wanted:
        systems["webspeech"] = load_webspeech(have, pairs)
        print(f"  {'webspeech':<14} {len(systems['webspeech'])} manual-protocol utterance(s)")

    # Table order is the reading order, not the order they happened to run in.
    systems = {name: systems[name] for name in SYSTEM_TITLES if name in systems}

    body = render(systems, have, pairs, total=len(utterances))
    print()
    print(table(systems, have, pairs, "normalized"))

    if args.write:
        write_results(body)
    else:
        print("\n  (add --write to update eval/results.md)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
