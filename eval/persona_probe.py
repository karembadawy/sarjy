# -*- coding: utf-8 -*-
"""Run both personas against the checklist, and lint what a machine can actually check.

    backend/venv/bin/python eval/persona_probe.py                     # against localhost:8000
    backend/venv/bin/python eval/persona_probe.py --url https://HOST
    backend/venv/bin/python eval/persona_probe.py --persona gulf --write

Ten probes, run through `POST /api/chat` — the text path, so no TTS quota is spent and the
turn is the same brain, the same persona prompt and the same tools the voice loop uses.

**What this can and cannot decide.** Six of the seven things the checklist covers are
mechanical, and a machine should check them every time rather than a human checking them
once: reply length, markdown and emoji, Modern Standard Arabic tell-tales, the *other*
persona's vocabulary (the D-032 failure — the first Egyptian build greeted with "يا هلا"),
Latin script inside an Arabic reply (§6.4), and which language was answered in. Those are
scored here.

The seventh cannot be automated and is the one that matters most: **does it sound like a
person from that place**. That is the builder's ear for Egyptian and ten years in Saudi
Arabia for Gulf (product.md §7, D-026), and the generated table leaves a column for it.

A lint failure is a prompt bug, not a test bug: the fix goes in `app/personas.py` and the
probe is re-run.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib import error, request

EVAL_DIR = Path(__file__).resolve().parent
CHECKLIST = EVAL_DIR / "persona_checklist.md"

BEGIN = "<!-- BEGIN GENERATED -->"
END = "<!-- END GENERATED -->"


# ---------------------------------------------------------------------------------------
# The checklist
# ---------------------------------------------------------------------------------------


@dataclass
class Probe:
    key: str
    covers: str
    said: str
    # What the reply must be in: "ar", "en", or None for "do not care".
    expect_language: str | None = "ar"
    # Tools the turn must have called, if any.
    expect_tools: tuple[str, ...] = ()
    # Extra checks by name, run in addition to the ones every probe gets.
    extra: tuple[str, ...] = ()
    note: str = ""


PROBES: list[Probe] = [
    Probe(
        "greeting", "dialect purity",
        "السلام عليكم",
        note="The D-032 failure lived here: the first Egyptian build answered with the Gulf "
             "'يا هلا'. The greeting is where a persona leaks first.",
    ),
    Probe(
        "explaining", "dialect purity · MSA drift",
        "ممكن تفهمني يعني إيه الفرق بين الحجز والميعاد؟",
        note="An explanatory question is the strongest pull towards Modern Standard Arabic — "
             "this is where 'يمكنني أن أوضح لك' appears if it is going to.",
    ),
    Probe(
        "essay-bait", "brevity",
        "احكيلي عن اسكندرية",
        extra=("short",),
        note="An open invitation to write three paragraphs that would then be read aloud.",
    ),
    Probe(
        "weather", "speakability · tool use",
        "الجو عامل ايه بكرة؟",
        expect_tools=("get_weather",),
        extra=("spoken_numbers",),
        note="Numbers must be said the way a person says them, never '28°C' or '17:30'.",
    ),
    Probe(
        "cannot", "the honesty clause",
        "ابعتلي رسالة واتساب لأحمد وقوله إني هتأخر",
        extra=("declines",),
        note="Sarjy has four tools and none of them is this. It must say so plainly in one "
             "sentence and offer the nearest thing it *can* do (D-061).",
    ),
    Probe(
        "borrowed", "borrowed words in Arabic script",
        "عايز أعمل book لميتنج بكرة الساعة خمسة",
        expect_tools=("create_booking",),
        note="§6.4: the reply must write borrowed words in Arabic script (ميتنج, not meeting) "
             "so the Arabic voice pronounces them instead of stumbling.",
    ),
    Probe(
        "mirror-en", "language mirroring",
        "What are my upcoming bookings?",
        expect_language="en",
        expect_tools=("list_bookings",),
        note="English in, English out — and no Arabic word carried across.",
    ),
    Probe(
        "switch", "an explicit switch sticks (§6.2)",
        "كلمني عربي من فضلك",
        note="Sets the preference. The next probe is the one that proves it stuck.",
    ),
    Probe(
        "switch-held", "an explicit switch sticks (§6.2)",
        "What is the weather like tomorrow?",
        expect_language="ar",
        note="Asked in English *after* asking for Arabic. §6.2 says the explicit choice wins "
             "over mirroring, so this must come back in Arabic.",
    ),
    Probe(
        "prayer", "prayer-anchored booking",
        "احجزلي ميعاد عند الدكتور بكرة بعد المغرب",
        expect_tools=("get_prayer_times", "create_booking"),
        extra=("spoken_numbers",),
        note="The money shot: resolve Maghrib for the right city and day, book fifteen "
             "minutes after it, and say the resolved time out loud.",
    ),
]


# ---------------------------------------------------------------------------------------
# The lint
# ---------------------------------------------------------------------------------------

# Written out of app/personas.py: what each persona is told never to say. A reply containing
# one of these is the prompt losing, and it is a prompt bug rather than a test bug.
MSA_TELLS = [
    "سوف", "لا يزال", "بالإضافة إلى ذلك", "هل ترغب", "يمكنني أن", "شكراً جزيلاً",
    "لا أملك", "من أجلك", "كيف حالك اليوم", "أتمنى أن تكون", "لقد قمت", "في تمام الساعة",
    "درجة مئوية", "ماذا", "لماذا", "الآن", "أريد", "ليس",
]
EGYPTIAN_ONLY = ["إزيك", "ازيك", "دلوقتي", "أوي", "اوي", "عايز", "كده", "النهاردة", "إمبارح"]
GULF_ONLY = ["يا هلا", "هلا والله", "حياك الله", "أبشر", "وش", "الحين", "زين", "تبي",
             "ودي", "ليش", "وين", "عساك"]
FORBIDDEN_BY_PERSONA = {"egyptian": GULF_ONLY, "gulf": EGYPTIAN_ONLY}

MARKDOWN = re.compile(r"(\*\*|__|`|^\s*[-*+]\s|^\s*#{1,6}\s|^\s*\d+\.\s)", re.MULTILINE)
DIGIT_TIME = re.compile(r"\b\d{1,2}[:٫]\d{2}\b|\d+\s*°|°[CF]")
LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
ARABIC_LETTER = re.compile(r"[\u0600-\u06FF]")

# 1–3 short sentences is the ceiling in SPOKEN_STYLE_RULES. Measured in characters because a
# sentence count cannot see a 300-character one.
MAX_SPOKEN_CHARS = 320

DECLINE_WORDS = ["مش هقدر", "مقدرش", "معنديش", "ما أقدر", "ما عندي", "مو قادر",
                 "can't", "cannot", "unable", "don't have"]


def is_emoji(char: str) -> bool:
    return unicodedata.category(char) == "So"


def arabic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if ARABIC_LETTER.match(c)) / len(letters)


@dataclass
class Result:
    probe: Probe
    reply: str = ""
    tools: list[str] = field(default_factory=list)
    reply_language: str = ""
    failures: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def passed(self) -> bool:
        return not self.failures and not self.error


def lint(result: Result, persona: str) -> None:
    """Everything about a reply that a machine can decide. The ear decides the rest."""
    reply, probe = result.reply, result.probe
    fail = result.failures.append

    if not reply.strip():
        fail("empty reply")
        return

    # --- speakable at all (golden rule 9) -------------------------------------------
    if MARKDOWN.search(reply):
        fail("contains markdown — it would be read aloud as punctuation noise")
    if any(is_emoji(char) for char in reply):
        fail("contains an emoji")
    if "http" in reply or "@" in reply:
        fail("contains a URL or an email address")

    # --- language -------------------------------------------------------------------
    ratio = arabic_ratio(reply)
    result.reply_language = "ar" if ratio > 0.70 else "en" if ratio < 0.30 else "mixed"
    if probe.expect_language and result.reply_language != probe.expect_language:
        fail(f"answered in {result.reply_language}, expected {probe.expect_language}")

    if result.reply_language == "ar":
        # --- dialect --------------------------------------------------------------
        for tell in MSA_TELLS:
            if tell in reply:
                fail(f"Modern Standard Arabic: {tell!r}")
        for word in FORBIDDEN_BY_PERSONA[persona]:
            if word in reply:
                fail(f"the other persona's vocabulary: {word!r}")
        # --- borrowed words (§6.4) -------------------------------------------------
        for word in LATIN_WORD.findall(reply):
            fail(f"Latin script inside an Arabic reply: {word!r} — §6.4 wants Arabic letters")

    # --- per-probe extras -------------------------------------------------------------
    if "short" in probe.extra and len(reply) > MAX_SPOKEN_CHARS:
        fail(f"{len(reply)} characters — too long to be read aloud as an answer")
    if "spoken_numbers" in probe.extra and DIGIT_TIME.search(reply):
        fail("a clock time or temperature written in digits rather than said as words")
    if "declines" in probe.extra and not any(word in reply for word in DECLINE_WORDS):
        fail("did not plainly say it cannot do this — check it has not claimed an action")

    # --- tools ------------------------------------------------------------------------
    for tool in probe.expect_tools:
        if tool not in result.tools:
            fail(f"did not call {tool} (called: {', '.join(result.tools) or 'nothing'})")


# ---------------------------------------------------------------------------------------
# Driving it
# ---------------------------------------------------------------------------------------


def post(url: str, payload: dict, method: str = "POST") -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=body, method=method,
                          headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=120) as response:
        return json.loads(response.read())


def run_persona(base: str, persona: str) -> list[Result]:
    user_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
    print(f"\n  {persona}  (user {user_id[:8]})")
    post(f"{base}/api/users/{user_id}/persona", {"persona": persona}, method="PUT")

    results = []
    for probe in PROBES:
        result = Result(probe=probe)
        try:
            answer = post(f"{base}/api/chat",
                          {"user_id": user_id, "session_id": session_id, "text": probe.said})
            result.reply = answer["reply"]
            result.tools = answer.get("tool_calls", [])
        except error.HTTPError as exc:
            result.error = f"HTTP {exc.code}: {exc.read().decode('utf-8', 'replace')[:120]}"
        except Exception as exc:  # noqa: BLE001
            result.error = f"{type(exc).__name__}: {exc}"
        else:
            lint(result, persona)

        mark = "ok  " if result.passed else "FAIL"
        print(f"    {mark} {probe.key:<12} {(result.reply or result.error)[:80]}")
        for failure in result.failures:
            print(f"         ! {failure}")
        results.append(result)
    return results


def render(by_persona: dict[str, list[Result]]) -> str:
    lines = [
        "_Generated by `eval/persona_probe.py` over `POST /api/chat` — no TTS quota spent._",
        "",
        "The **lint** column is mechanical (length, markdown, emoji, MSA tell-tales, the other "
        "persona's vocabulary, Latin script in an Arabic reply, reply language, tools called). "
        "The **ear** column is the builder's, and it is the one the machine cannot do.",
        "",
    ]
    for persona, results in by_persona.items():
        passed = sum(1 for r in results if r.passed)
        lines += [
            f"## {persona} — {passed}/{len(results)} clean on the lint",
            "",
            "| # | covers | said | replied | lint | ear |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for index, result in enumerate(results, start=1):
            reply = (result.reply or result.error or "—").replace("|", "\\|").replace("\n", " ")
            verdict = "✅" if result.passed else "❌ " + "; ".join(result.failures)
            lines.append(
                f"| {index} | {result.probe.covers} | {result.probe.said} | {reply} | "
                f"{verdict.replace('|', '\\|')} |  |"
            )
        lines.append("")
        for index, result in enumerate(results, start=1):
            if result.probe.note:
                lines.append(f"{index}. **{result.probe.key}** — {result.probe.note}")
        lines.append("")
    return "\n".join(lines)


def write(body: str) -> None:
    if not CHECKLIST.exists():
        CHECKLIST.write_text(f"# Persona checklist\n\n{BEGIN}\n{body}\n{END}\n", encoding="utf-8")
    else:
        text = CHECKLIST.read_text(encoding="utf-8")
        if BEGIN not in text or END not in text:
            raise SystemExit(f"{CHECKLIST} has no generated markers — refusing to overwrite.")
        head, rest = text.split(BEGIN, 1)
        _, tail = rest.split(END, 1)
        CHECKLIST.write_text(f"{head}{BEGIN}\n{body}\n{END}{tail}", encoding="utf-8")
    print(f"\n  wrote {CHECKLIST}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--persona", default="egyptian,gulf")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    by_persona = {}
    for persona in [p.strip() for p in args.persona.split(",") if p.strip()]:
        by_persona[persona] = run_persona(args.url.rstrip("/"), persona)

    print()
    total_failed = 0
    for persona, results in by_persona.items():
        failed = [r for r in results if not r.passed]
        total_failed += len(failed)
        print(f"  {persona:<10} {len(results) - len(failed)}/{len(results)} clean on the lint")

    if args.write:
        write(render(by_persona))
    else:
        print("\n  (add --write to update eval/persona_checklist.md)")
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
