# -*- coding: utf-8 -*-
"""Score the live go/no-go session (D-047) with the benchmark's own rules.

    backend/venv/bin/python eval/score_live_gate.py [--write]

This is not the thirty-utterance study — it is five utterances, spoken once, into a real
microphone, during the Phase-2 gate. It is in the repository for one reason: **the transcripts
and their ground truth were both written down at the time** (D-047), so they can be scored
now, honestly, with exactly the same normalization and borrow table the main benchmark uses,
instead of being quoted as anecdotes.

Its limits are the reason it is a separate file and a separate section: n=5, one speaker, one
room, no noisy or fast conditions, and the transcripts come from the **streaming** path as it
stood in Phase 2 — before the lost-interim fallback (D-055), the holding-channel fix and the
interim watchdog (D-071). It is a floor on today's system, not a measurement of it.

Where a row records both channels, both are scored: g3 is the D-047 weakness class, and the
whole point of it is that the channel which *lost* the race kept the proper nouns.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVAL_DIR))

import scoring  # noqa: E402

SOURCE = EVAL_DIR / "live_gate.csv"
RESULTS = EVAL_DIR / "results.md"
BEGIN = "<!-- BEGIN LIVE GATE -->"
END = "<!-- END LIVE GATE -->"


def rows() -> list[dict]:
    with SOURCE.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def render() -> str:
    data = rows()
    pairs = scoring.load_borrow_pairs()

    lines = [
        f"_Scored by `eval/score_live_gate.py` from the transcripts recorded in D-047 — "
        f"{len(data)} utterances, one live microphone, ground truth written down at the time._",
        "",
        "| # | said | winning channel | raw | normalized | borrow-tolerant |",
        "| --- | --- | --- | --- | --- | --- |",
    ]

    winners_said, winners_heard = [], []
    for row in data:
        heard = row[f"heard_{row['winner']}"]
        winners_said.append(row["said"])
        winners_heard.append(heard)
        passes = scoring.passes([row["said"]], [heard], pairs)
        lines.append(
            f"| `{row['id']}` | {row['said']} | `{row['winner']}` {heard} | "
            f"{passes['raw'].percent} | {passes['normalized'].percent} | "
            f"{passes['borrow_tolerant'].percent} |"
        )

    overall = scoring.passes(winners_said, winners_heard, pairs)
    lines += [
        "",
        f"**Aggregate over the {len(data)} utterances** "
        f"(total errors ÷ total reference words, never the mean of the rates above): "
        f"raw **{overall['raw'].percent}** · normalized **{overall['normalized'].percent}** · "
        f"borrow-tolerant **{overall['borrow_tolerant'].percent}** "
        f"({overall['raw'].reference_words} reference words).",
        "",
    ]

    # The weakness class, both channels side by side. This is the row the whole section is for.
    entity = [row for row in data if "entity" in (row["tags"] or "")]
    if entity:
        lines += [
            "### The same utterance, both channels — the D-047 weakness class",
            "",
            "| channel | transcript | normalized WER | proper nouns |",
            "| --- | --- | --- | --- |",
        ]
        for row in entity:
            for channel in ("ar", "en"):
                heard = row[f"heard_{channel}"]
                if not heard:
                    continue
                wer = scoring.passes([row["said"]], [heard], pairs)["normalized"].percent
                kept = "kept (دينا, مارب)" if channel == "ar" else "**lost** (Dina→dinner, Maghrib→my rib)"
                won = " ← won the race" if row["winner"] == channel else ""
                lines.append(f"| `{channel}`{won} | {heard} | {wer} | {kept} |")
        lines.append("")
    return "\n".join(lines)


def write(body: str) -> None:
    text = RESULTS.read_text(encoding="utf-8")
    if BEGIN not in text or END not in text:
        raise SystemExit(f"{RESULTS} has no {BEGIN} / {END} markers.")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    RESULTS.write_text(f"{head}{BEGIN}\n{body}\n{END}{tail}", encoding="utf-8")
    print(f"  updated the live-gate section in {RESULTS}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    body = render()
    print(body)
    if args.write:
        write(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
