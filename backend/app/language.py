"""Language detection by Arabic-script character ratio (product.md §6.5).

Deliberately not a statistical language-ID model: the only question Sarjy asks is "which
script is this person writing in", and for that a character ratio is exact, instant, and
explainable on a slide. The thresholds are the locked ones:

    ratio > 0.70  → "ar"
    ratio < 0.30  → "en"
    otherwise     → "mixed"

Only *alphabetic* characters vote. Digits, punctuation, emoji and whitespace are ignored,
so "اسمي أحمد ورقمي ٠١٠٠١٢٣٤٥٦٧" stays Arabic instead of being dragged toward mixed by a
phone number, and "meet me at 5" stays English.
"""

from __future__ import annotations

Language = str  # one of "ar" | "en" | "mixed"

ARABIC_THRESHOLD = 0.70
LATIN_THRESHOLD = 0.30

# Every Unicode block that carries Arabic-script letters. Arabic Presentation Forms are
# included because copy-pasted text and some keyboards still emit them.
_ARABIC_RANGES = (
    (0x0600, 0x06FF),  # Arabic
    (0x0750, 0x077F),  # Arabic Supplement
    (0x0870, 0x089F),  # Arabic Extended-B
    (0x08A0, 0x08FF),  # Arabic Extended-A
    (0xFB50, 0xFDFF),  # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),  # Arabic Presentation Forms-B
    (0x10EC0, 0x10EFF),  # Arabic Extended-C
    (0x1EE00, 0x1EEFF),  # Arabic Mathematical Alphabetic Symbols
)


def is_arabic_char(char: str) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in _ARABIC_RANGES)


def arabic_ratio(text: str) -> float:
    """Share of alphabetic characters that are Arabic script. 0.0 when there are none."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if is_arabic_char(c)) / len(letters)


def detect_language(text: str) -> Language:
    """Label one utterance "ar", "en" or "mixed".

    Empty or letterless input ("", "؟", "123", "🙂") falls back to "en": it is the safe
    default for a label that only drives a UI badge and the reply-language hint.
    """
    if not text or not any(c.isalpha() for c in text):
        return "en"

    ratio = arabic_ratio(text)
    if ratio > ARABIC_THRESHOLD:
        return "ar"
    if ratio < LATIN_THRESHOLD:
        return "en"
    return "mixed"


def dominant_language(text: str) -> Language:
    """Which language a *reply* to this text should be in (product.md §6.1).

    Mirror by default; for a mixed utterance, the majority script wins — so
    "عايز أعمل book لميتنج" is labelled `mixed` in the transcript but answered in Arabic.
    """
    return "ar" if arabic_ratio(text) >= 0.5 else "en"


# --------------------------------------------------------------------------------------
# §6.2 — "explicit switches win and stick"
# --------------------------------------------------------------------------------------
#
# This was a background job until Phase 5's persona checklist caught it. `preferred_language`
# was written only by the fact extractor (D-058), which is a second model call that runs
# *after* the reply — so "كلمني عربي" followed immediately by an English question was answered
# in English on both personas, and over `POST /api/chat`, which never runs the extractor at
# all, it could never work.
#
# §6.2 is a locked rule, and a locked rule should not depend on a probabilistic background
# call finishing in time. So the request is recognised here, synchronously, inside the same
# round trip that stores the user's line — deterministic, free, and unit-tested. The extractor
# keeps its own preference detection as a second net for phrasings this misses.

_DIACRITICS = "".join(chr(code) for code in [*range(0x064B, 0x0660), 0x0640, 0x0670])
_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا",  # أ إ آ → ا
    "ة": "ه",                                          # ة → ه
    "ى": "ي",                                          # ى → ي
    "ڤ": "ج", "چ": "ج",                      # ڤ چ → ج (Egyptian spellings)
    **{char: None for char in _DIACRITICS},
})

# Substrings, because Arabic glues its articles and prepositions on: matching "عربي" also
# catches العربي، بالعربي، عربية (folded to عربيه). Same for the English names.
_NAMES = {
    "ar": ("عربي", "arabic"),
    "en": ("انجليزي", "انكليزي", "english"),
}

# Something in the sentence has to make it a *request*. Without this, "الكتاب ده عربي"
# ("that book is in Arabic") would switch the assistant's language.
_REQUEST_HINTS = (
    "كلمني", "كلميني", "اتكلم", "تكلم", "احكي", "احكيلي", "رد", "ردي", "جاوب", "قول",
    "خليك", "من فضلك", "لو سمحت", "ممكن", "عايز", "عاوز", "ابغى", "ابي", "ودي", "بدي",
    "speak", "talk", "reply", "answer", "say it", "switch", "please", "use ",
)

# ...and something in it can take it back out again: a person describing which languages they
# speak is not asking us to change ours. Checked first, and it wins.
_STATEMENT_VETOES = (
    "انا بتكلم", "انا اتكلم", "بتكلم", "باتكلم", "i speak", "i talk", "do you speak",
    "بتفهم", "بتعرف", "can you speak", "تعرف تتكلم", "بحب ال",
)

# A switch request is a short thing somebody says on its own. Anything longer is a sentence
# that happens to mention a language, and guessing at those is how a demo answers in the
# wrong language for the rest of the call.
MAX_REQUEST_CHARS = 80


def _fold(text: str) -> str:
    return (text or "").translate(_FOLD).lower()


def explicit_language_request(text: str) -> Language | None:
    """"كلمني عربي" / "speak English" → "ar" / "en". Anything else → None.

    Deliberately conservative: it must name exactly one language, read as a request, not read
    as a statement about the speaker, and be short. A false negative costs one turn of
    mirroring — which is the *correct* behaviour anyway — while a false positive silently
    locks the assistant into the wrong language for the rest of the conversation.
    """
    folded = _fold(text)
    if not folded or len(folded) > MAX_REQUEST_CHARS:
        return None
    if any(veto in folded for veto in _STATEMENT_VETOES):
        return None

    named = [code for code, names in _NAMES.items() if any(name in folded for name in names)]
    # Both named at once is "I speak Arabic and English", never "switch to one of them".
    if len(named) != 1:
        return None
    if not any(hint in folded for hint in _REQUEST_HINTS):
        return None
    return named[0]
