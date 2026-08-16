# -*- coding: utf-8 -*-
"""Tests for the character-ratio language detector (product.md §6.5)."""

from __future__ import annotations

import pytest

from app import language
from app.language import arabic_ratio, detect_language, dominant_language


@pytest.mark.parametrize(
    "text",
    [
        "اسمي أحمد وبحب اللون الأزرق",
        "إزيك يا سرجي، عامل إيه النهاردة؟",
        "الجو حر أوي",
    ],
)
def test_pure_arabic(text: str) -> None:
    assert detect_language(text) == "ar"


@pytest.mark.parametrize(
    "text",
    [
        "My name is Ahmed and I like the colour blue",
        "What's the weather like tomorrow?",
        "book me a meeting",
    ],
)
def test_pure_english(text: str) -> None:
    assert detect_language(text) == "en"


def test_code_switched_is_mixed() -> None:
    # 12 Arabic letters vs 10 Latin → ratio 0.55, inside the 0.30–0.70 band.
    assert detect_language("Can you book لي ميتنج بكرة؟") == "mixed"


def test_code_switched_mostly_arabic_is_labelled_arabic() -> None:
    """The canonical demo utterance from product.md §3.1.

    Arabic words are short, so "عايز أعمل book لميتنج" is 14 Arabic letters against 4 Latin
    → ratio 0.78, above the locked 0.70 threshold, and the locked rule therefore labels it
    `ar` rather than `mixed`. Asserted explicitly so the behaviour is a decision on record
    and not a surprise the first time it appears in the demo transcript.
    """
    assert round(arabic_ratio("عايز أعمل book لميتنج"), 2) == 0.78
    assert detect_language("عايز أعمل book لميتنج") == "ar"
    # …and either way the reply must come back in Arabic.
    assert dominant_language("عايز أعمل book لميتنج") == "ar"


def test_arabic_with_digits_stays_arabic() -> None:
    # Digits (both Latin and Arabic-Indic), punctuation and emoji must not vote.
    assert detect_language("رقمي 01001234567 وعنواني ٥ شارع فؤاد 🙂") == "ar"


@pytest.mark.parametrize("text", ["", "   ", "123456", "؟!.. 🙂", "٥٠٠"])
def test_letterless_input_falls_back_to_english(text: str) -> None:
    assert detect_language(text) == "en"


def test_arabic_presentation_forms_count_as_arabic() -> None:
    # Copy-pasted text sometimes arrives in the presentation-forms block (U+FE70–U+FEFF).
    assert detect_language("ﻣﺮﺣﺒﺎ ﺑﻚ") == "ar"


def test_dominant_language_picks_the_majority_script() -> None:
    assert dominant_language("Book me a ميعاد") == "en"
    assert dominant_language("احجزلي ميعاد tomorrow") == "ar"


# ---------------------------------------------------------------------------------------
# §6.2 — "explicit switches win and stick"
# ---------------------------------------------------------------------------------------


class TestExplicitLanguageRequests:
    """The rule that used to be a background job, and therefore did not work.

    `preferred_language` was written only by the fact extractor (D-058) — a second model call
    that runs *after* the reply. So "كلمني عربي" followed straight away by an English question
    was answered in English, on both personas, and over `POST /api/chat` it could never work
    at all because that path never runs the extractor. Phase 5's persona checklist caught it.

    These cases are split deliberately: the ones it must catch, and the ones it must refuse.
    The second list is the important one — a false negative costs a single turn of mirroring,
    which is the correct behaviour anyway, while a false positive locks the assistant into the
    wrong language for the rest of the conversation.
    """

    @pytest.mark.parametrize(
        ("said", "expected"),
        [
            ("كلمني عربي", "ar"),
            ("كلمني عربي من فضلك", "ar"),
            ("ممكن تكلمني بالعربي؟", "ar"),
            ("اتكلم معايا بالعربية لو سمحت", "ar"),
            ("رد عليا بالعربي", "ar"),
            ("عايز أتكلم انجليزي", "en"),
            ("كلمني إنجليزي", "en"),
            ("speak English please", "en"),
            ("can you reply in Arabic?", "ar"),
            ("switch to English", "en"),
            ("talk to me in English", "en"),
            # Gulf phrasing — the other persona's speakers ask differently.
            ("ابي تكلمني عربي", "ar"),
            ("ودي تردون علي بالانجليزي", "en"),
        ],
    )
    def test_it_hears_a_request(self, said, expected):
        assert language.explicit_language_request(said) == expected

    @pytest.mark.parametrize(
        "said",
        [
            "",
            "الجو النهارده عامل ايه",
            "What's the weather like tomorrow?",
            # Describing yourself is not an instruction to us.
            "أنا بتكلم عربي وانجليزي",
            "I speak Arabic and English",
            "بتكلم عربي كويس؟",
            "do you speak Arabic?",
            # Naming both languages is never a switch to one of them.
            "عايز أتعلم عربي وانجليزي",
            # A language mentioned in passing, in a sentence about something else entirely.
            "الكتاب اللي اشتريته امبارح من المكتبة الكبيرة كان مكتوب بالعربي وكان غالي أوي",
        ],
    )
    def test_it_refuses_everything_else(self, said):
        assert language.explicit_language_request(said) is None

    def test_diacritics_and_spelling_variants_do_not_hide_a_request(self):
        # Deepgram's Arabic channel vocalises words at random (D-055), so the matcher folds
        # diacritics and the alef/hamza forms before it looks at anything.
        assert language.explicit_language_request("كَلِّمْني عَرَبي") == "ar"
        assert language.explicit_language_request("كلمني إنجليزى") == "en"
