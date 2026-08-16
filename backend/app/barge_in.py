# -*- coding: utf-8 -*-
"""When does speaking over Sarjy count as interrupting it? (product.md §11)

This is the whole of the barge-in policy, kept as pure functions so it can be tuned against
a unit test rather than against a live microphone at eleven at night.

The design accepts a risk that D-012 named out loud: the microphone now stays open while
Sarjy talks, so Sarjy can hear itself. Browser echo cancellation removes most of it, and
what leaks through is not silence — it is fragments, half-words and room noise that Deepgram
dutifully transcribes. The bar below is what separates "a person started talking" from "the
speaker is bleeding into the microphone":

    at least two real words · above a confidence floor · not a filler

Deliberately *not* on the list: matching against what Sarjy is currently saying. It sounds
clever and it fails on the demo's most likely interruption — the user repeating a word Sarjy
just said in order to correct it.
"""

from __future__ import annotations

import re

from . import config

# Two real words. One word is a cough, a "أه", or the tail of Sarjy's own sentence coming
# back through the speaker; two in a row is somebody talking.
DEFAULT_MIN_WORDS = 2

# Deepgram scores interim hypotheses lower than finals by nature — they are guesses about a
# half-heard phrase — so this floor is low on purpose. It is here to be *raised* if echo
# turns out to trigger the thing in the room, which is the tuning knob product.md asks for.
DEFAULT_MIN_CONFIDENCE = 0.30

# Sounds, not words. A transcript of two of these is a person thinking, not a person talking,
# and cutting Sarjy off mid-sentence for "اه اه" is worse than not cutting it off at all.
FILLERS = {
    "اه", "أه", "آه", "ااا", "امم", "همم", "يعني", "ايوه", "أيوه", "طب", "ها",
    "uh", "um", "umm", "uhh", "hmm", "mm", "mhm", "ah", "oh", "er", "yeah", "ok", "okay",
}

# Everything that is not a letter or a digit is a separator. Arabic punctuation (؟ ، ؛) is
# not in Python's `\W`-adjacent intuitions for anyone reading this, but it is here.
_TOKENS = re.compile(r"[^\W_]+", re.UNICODE)


def real_words(text: str) -> list[str]:
    """The words in `text` that carry meaning — punctuation, digits and fillers removed."""
    return [
        token
        for token in _TOKENS.findall((text or "").lower())
        if any(character.isalpha() for character in token) and token not in FILLERS
    ]


def min_words() -> int:
    return int(config.float_setting("BARGE_IN_MIN_WORDS", DEFAULT_MIN_WORDS))


def min_confidence() -> float:
    return config.float_setting("BARGE_IN_MIN_CONFIDENCE", DEFAULT_MIN_CONFIDENCE)


def is_interruption(text: str, confidence: float) -> bool:
    """Does this transcript clear the bar for cutting Sarjy off mid-reply?"""
    if confidence < min_confidence():
        return False
    return len(real_words(text)) >= min_words()
