# -*- coding: utf-8 -*-
"""Word Error Rate for a bilingual system — and the three traps that make a single number a lie.

Arabic WER is not English WER with different letters. Three things will silently decide the
result if they are not handled deliberately, so all three are handled here, in the open, and
every one of them is reported as its own scoring pass rather than being folded in quietly.

**Trap 1 — orthography.** Arabic is written with several spellings of the same word. Deepgram
returns "احجز لي" where the truth says "احجزلي"; it fully vocalises "بَكْرَةِ" where the truth
writes "بكرة"; أ / إ / آ / ا are one letter to a listener and four to a string comparison.
Scored raw, a transcript a human would call perfect can land at 60% WER. So `normalize()`
exists — and because a normalizer can also *hide* real errors, the raw number is reported
next to it every time.

**Trap 2 — script.** The Arabic channel writes borrowed English in Arabic script: "book" comes
back as "بوك", "weather" as "الوذر". That is not an error — product.md §6.4 asks the *replies*
to do exactly this, and D-047 found it is the reason code-switching survives at all. Against a
truth written in the script the speaker actually spoke ("عايز أعمل book"), it scores as one
substitution per borrowed word. So there is a third pass, `borrow_canonical()`, which maps the
Arabic-script forms this project actually observed back onto their Latin words. It is a lookup
table read from `borrow_pairs.csv`, built by reading real output — not a transliterator, and
not applied unless the table has the exact form in it.

**Trap 3 — aggregation.** The WER of a group is total errors over total reference words, never
the mean of per-utterance WERs. A three-word utterance and a fifteen-word utterance do not get
an equal vote. `score()` aggregates with jiwer's own counts.

Every rule below is repeated in `eval/results.md`, because a normalization nobody can read is
a thumb on the scale.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import jiwer

EVAL_DIR = Path(__file__).resolve().parent
BORROW_PAIRS = EVAL_DIR / "borrow_pairs.csv"

# ---------------------------------------------------------------------------------------
# Normalization — pass 2. Each rule is one line, and each line is defensible out loud.
# ---------------------------------------------------------------------------------------

# Tashkeel (fatha…sukun), the hamza/madda marks, the superscript alef, the Quranic
# annotation marks, and the tatweel stretcher. Written as code points rather than as literal
# characters, because a diacritic pasted into a character class is invisible in a diff.
# Deepgram's Arabic model vocalises some words and not others, at random, inside one sentence.
_DIACRITICS = re.compile(
    "["
    "\u0610-\u061A"  # Arabic signs above and below
    "\u064B-\u065F"  # tashkeel: fathatan … wavy hamza below
    "\u0640"          # tatweel (kashida) — a stretcher, never a letter
    "\u0670"          # superscript alef
    "\u06D6-\u06ED"  # Quranic annotation marks
    "]"
)

# Letter forms a listener does not distinguish. The hamza carriers are included with the alef
# variants for the same reason: which carrier a transcriber picks is spelling, not hearing.
_LETTERS = str.maketrans({
    "\u0623": "\u0627", "\u0625": "\u0627",   # أ إ  alef with hamza above / below
    "\u0622": "\u0627", "\u0671": "\u0627",   # آ ٱ  alef with madda / wasla
    "\u0629": "\u0647",                        # ة    taa marbuta  → ha
    "\u0649": "\u064A",                        # ى    alef maqsura → ya
    "\u0624": "\u0648", "\u0626": "\u064A",   # ؤ ئ  hamza on waw / ya
    # Arabic-Indic and extended Arabic-Indic digits. The same digit, a different script:
    # Deepgram writes 5 where a keyboard writes ٥, and nobody hears the difference.
    **{chr(0x0660 + n): str(n) for n in range(10)},
    **{chr(0x06F0 + n): str(n) for n in range(10)},
})

# Deleted rather than replaced with a space: "doctor's" is one word, and so is "don't".
_ELIDED = "'’`ـ"


def strip_diacritics(text: str) -> str:
    return _DIACRITICS.sub("", text)


def normalize(text: str) -> str:
    """The documented pass-2 normalization, applied identically to truth and hypothesis.

        1. Unicode NFKC, so presentation forms and ligatures compare as their letters.
        2. Arabic diacritics and the tatweel are removed.
        3. أ إ آ ٱ → ا · ة → ه · ى → ي · ؤ → و · ئ → ي.
        4. Arabic-Indic digits → ASCII digits (٥ and 5 are one digit in two scripts).
        5. Latin is lowercased.
        6. Apostrophes are deleted; every other punctuation mark becomes a space.
        7. Whitespace is collapsed.

    What is deliberately NOT normalized: a digit against a spelled-out number. "الساعة 5"
    where the truth says "الساعة خمسة" stays an error, because those are two different things
    for a voice assistant to say out loud (golden rule 9) — and `smart_format` is on in
    production, so if it costs us, that is a real cost of the shipped configuration. Where it
    shows up it is named in results.md rather than smoothed away here.
    """
    text = unicodedata.normalize("NFKC", text or "")
    text = strip_diacritics(text)
    text = text.translate(_LETTERS)
    text = "".join("" if char in _ELIDED else char for char in text)
    text = "".join(
        " " if unicodedata.category(char).startswith("P") else char for char in text
    )
    return " ".join(text.lower().split())


# ---------------------------------------------------------------------------------------
# Borrowed words — pass 3
# ---------------------------------------------------------------------------------------


def load_borrow_pairs(path: Path = BORROW_PAIRS) -> dict[str, str]:
    """Arabic-script transcription → the token(s) the truth uses for the same word.

    The membership rule, and it is narrow on purpose, because this pass is the one that could
    be used to cheat:

      * A row exists only when the truth writes the word in **Latin** and a channel wrote the
        **same word** in Arabic script — the §6.4 behaviour, nothing else.
      * Words the truth itself already writes in Arabic (ميتنج، الأوفيس، الدنتيست) are absent:
        both sides already agree, so there is nothing to forgive.
      * A mis-hearing is never added, however close it looks. "مارب" for "Maghrib" has lost a
        letter — that is a recognition error and it stays an error.
      * The right-hand side is whatever the truth actually contains, which is sometimes two
        tokens: the truth writes "ال weather", so "الوذر" maps to "ال weather" and not to
        "weather", or the article would score as a substitution instead.

    Read from a CSV so the whole table is reviewable as data, with the utterance or decision
    each row came from in a third column. Both sides are normalized on load, so the file can
    be written the way the words are really spelled.
    """
    if not path.exists():
        return {}
    pairs: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            arabic = normalize(row["arabic"])
            if arabic:
                pairs[arabic] = normalize(row["latin"])
    return pairs


def borrow_canonical(text: str, pairs: dict[str, str]) -> str:
    """Rewrite known Arabic-script borrowings as the Latin words they transcribe.

    Token by token, and only for tokens that are literally in the table. Nothing is guessed:
    an unknown Arabic word stays Arabic and still scores as an error, which is what keeps this
    pass from becoming a way to win.
    """
    if not pairs:
        return text
    return " ".join(pairs.get(token, token) for token in text.split())


# ---------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------


@dataclass
class Score:
    """One system on one set of utterances."""

    wer: float
    substitutions: int
    deletions: int
    insertions: int
    hits: int
    reference_words: int
    utterances: int

    @property
    def percent(self) -> str:
        return f"{self.wer * 100:.1f}%"


def score(references: list[str], hypotheses: list[str]) -> Score:
    """Aggregate WER over a set: total errors ÷ total reference words."""
    if not references:
        return Score(0.0, 0, 0, 0, 0, 0, 0)
    output = jiwer.process_words(references, hypotheses)
    return Score(
        wer=output.wer,
        substitutions=output.substitutions,
        deletions=output.deletions,
        insertions=output.insertions,
        hits=output.hits,
        reference_words=sum(len(reference.split()) for reference in references),
        utterances=len(references),
    )


def passes(
    references: list[str], hypotheses: list[str], pairs: dict[str, str]
) -> dict[str, Score]:
    """All three scoring passes for one system on one group of utterances."""
    normalized_references = [normalize(reference) for reference in references]
    normalized_hypotheses = [normalize(hypothesis) for hypothesis in hypotheses]
    return {
        "raw": score(references, hypotheses),
        "normalized": score(normalized_references, normalized_hypotheses),
        "borrow_tolerant": score(
            [borrow_canonical(reference, pairs) for reference in normalized_references],
            [borrow_canonical(hypothesis, pairs) for hypothesis in normalized_hypotheses],
        ),
    }
