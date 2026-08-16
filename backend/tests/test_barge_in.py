# -*- coding: utf-8 -*-
"""The barge-in bar (product.md §11, D-012).

Phase 4 opened the microphone during playback, which means Sarjy can hear itself. These
cases are the line between "somebody started talking" and "the speaker is bleeding into the
microphone" — the one piece of the design that has to be tuned rather than derived, so it is
pinned here instead of in a live session at midnight.
"""

from __future__ import annotations

from app.barge_in import DEFAULT_MIN_CONFIDENCE, is_interruption, real_words

CONFIDENT = 0.9


class TestRealWords:
    def test_punctuation_is_not_a_word(self):
        assert real_words("... ؟ ، !") == []

    def test_digits_alone_are_not_words(self):
        assert real_words("5 30") == []

    def test_arabic_and_english_both_count(self):
        assert real_words("احجزلي ميعاد") == ["احجزلي", "ميعاد"]
        assert real_words("book a table") == ["book", "a", "table"]

    def test_fillers_are_dropped_in_both_languages(self):
        # "اه اه" is a person thinking out loud, not a person interrupting.
        assert real_words("اه اه") == []
        assert real_words("um uh hmm") == []

    def test_a_filler_next_to_a_real_word_leaves_one_word(self):
        assert real_words("يعني الجو") == ["الجو"]


class TestIsInterruption:
    def test_two_real_words_interrupt(self):
        assert is_interruption("استنى شوية", CONFIDENT)
        assert is_interruption("wait, stop", CONFIDENT)

    def test_one_word_does_not(self):
        # The single most likely echo artefact: one word of Sarjy's own sentence coming back.
        assert not is_interruption("تمام", CONFIDENT)
        assert not is_interruption("okay", CONFIDENT)

    def test_a_cough_does_not(self):
        assert not is_interruption("اه", CONFIDENT)
        assert not is_interruption("...", CONFIDENT)

    def test_two_fillers_do_not(self):
        assert not is_interruption("اه اه", CONFIDENT)

    def test_a_low_confidence_hypothesis_does_not(self):
        # Echo residue transcribes as low-confidence guesses; that is the second filter.
        assert not is_interruption("wait stop that", DEFAULT_MIN_CONFIDENCE - 0.05)

    def test_the_confidence_floor_is_inclusive(self):
        assert is_interruption("wait stop that", DEFAULT_MIN_CONFIDENCE)

    def test_a_code_switched_interruption_counts(self):
        assert is_interruption("لأ استنى، cancel ده", CONFIDENT)
