# -*- coding: utf-8 -*-
"""Tests for the benchmark's scoring rules.

    backend/venv/bin/python -m pytest eval

These matter more than most tests in the project: the normalizer decides every number in
`results.md`, and a normalizer that quietly forgives a real error would turn the benchmark
into a press release. So each rule is asserted in both directions — what it must fold together
and what it must keep apart.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scoring  # noqa: E402


# ---------------------------------------------------------------------------------------
# What normalization must fold together
# ---------------------------------------------------------------------------------------


def test_diacritics_are_removed():
    # Deepgram vocalises some words and not others inside the same sentence.
    assert scoring.normalize("مِيعاد عِنْدَ الدُّكْتُور") == scoring.normalize("ميعاد عند الدكتور")


def test_alef_and_hamza_variants_collapse():
    assert scoring.normalize("أهلا") == scoring.normalize("اهلا") == scoring.normalize("إهلا")
    assert scoring.normalize("آدم") == scoring.normalize("ادم")


def test_taa_marbuta_and_alef_maqsura():
    assert scoring.normalize("بكرة") == scoring.normalize("بكره")
    assert scoring.normalize("مصطفى") == scoring.normalize("مصطفي")


def test_arabic_indic_digits_are_the_same_digits():
    assert scoring.normalize("الساعة ٥") == scoring.normalize("الساعة 5")


def test_latin_case_and_punctuation():
    assert scoring.normalize("Book me a table, tonight!") == "book me a table tonight"


def test_apostrophes_close_up_rather_than_split():
    assert scoring.normalize("a doctor's appointment") == "a doctors appointment"


def test_tatweel_is_not_a_letter():
    assert scoring.normalize("تـــمام") == scoring.normalize("تمام")


# ---------------------------------------------------------------------------------------
# What normalization must NOT forgive
# ---------------------------------------------------------------------------------------


def test_a_digit_is_not_a_spelled_number():
    # Two different things for a voice to say out loud (golden rule 9), so two different
    # transcripts. `smart_format` is on in production; if it costs us, that is a real cost.
    assert scoring.normalize("الساعة 5") != scoring.normalize("الساعة خمسة")


def test_a_different_word_is_still_a_different_word():
    assert scoring.normalize("دينا") != scoring.normalize("دينر")
    assert scoring.normalize("Maghrib") != scoring.normalize("my rib")


def test_arabic_script_is_not_folded_into_latin_by_normalization_alone():
    # Only the borrow pass may do this, and only from the table.
    assert scoring.normalize("بوك") != scoring.normalize("book")


# ---------------------------------------------------------------------------------------
# The borrow-tolerant pass
# ---------------------------------------------------------------------------------------


def test_borrow_pairs_are_loaded_normalized():
    pairs = scoring.load_borrow_pairs()
    assert pairs, "borrow_pairs.csv should not be empty"
    assert all(key == scoring.normalize(key) for key in pairs)


def test_a_listed_borrowing_is_forgiven():
    pairs = {"بوك": "book"}
    said = scoring.normalize("عايز أعمل book بكرة")
    heard = scoring.normalize("عايز اعمل بوك بكره")
    assert scoring.score([said], [heard]).wer > 0
    assert scoring.borrow_canonical(heard, pairs) == scoring.borrow_canonical(said, pairs)


def test_an_unlisted_arabic_word_is_still_an_error():
    pairs = {"بوك": "book"}
    said = scoring.normalize("check the weather")
    heard = scoring.normalize("check the الوذر")
    assert scoring.borrow_canonical(heard, pairs) != scoring.borrow_canonical(said, pairs)


def test_a_two_token_expansion_matches_the_truth_it_was_written_for():
    pairs = {"الوذر": "ال weather"}
    said = scoring.normalize("ايه ال weather بكرة")
    heard = scoring.normalize("ايه الوذر بكرة")
    assert scoring.borrow_canonical(heard, pairs) == scoring.borrow_canonical(said, pairs)


# ---------------------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------------------


def test_group_wer_is_total_errors_over_total_reference_words():
    # Not the mean of per-utterance rates: a two-word utterance does not get the same vote
    # as a ten-word one. One deletion in fifteen reference words is 1/15, not (1/5 + 0)/2.
    references = ["one two three four five", "six seven eight nine ten"]
    hypotheses = ["one two three four", "six seven eight nine ten"]
    result = scoring.score(references, hypotheses)
    assert result.reference_words == 10
    assert result.deletions == 1
    assert abs(result.wer - 0.1) < 1e-9


def test_an_empty_hypothesis_is_all_deletions_not_a_crash():
    # The Arabic channel returns nothing at all for English, which is the whole reason the
    # racer exists — the benchmark has to be able to score that.
    result = scoring.score(["what's the weather like tomorrow"], [""])
    assert result.wer == 1.0
    assert result.hits == 0


def test_three_passes_are_reported_separately():
    pairs = scoring.load_borrow_pairs()
    result = scoring.passes(["عايز أعمل book"], ["عايز اعمل بوك"], pairs)
    assert set(result) == {"raw", "normalized", "borrow_tolerant"}
    assert result["raw"].wer >= result["normalized"].wer >= result["borrow_tolerant"].wer
