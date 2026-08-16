# -*- coding: utf-8 -*-
"""What the model is actually told, before any of it reaches Gemini.

The system prompt is the product here (D-032), and three of its sections are computed fresh
on every turn — the date block, the language override, and memory. A bug in any of them is
invisible in a log and obvious to a user: the wrong day booked, the wrong language spoken,
a name Sarjy has been told and cannot recall.
"""

from __future__ import annotations

from datetime import datetime

from app import personas
from app.brain import build_system_prompt, context_block, language_block
from app.tools.places import Place
from app.tools.when import zone

CAIRO = zone("Africa/Cairo")
ALEXANDRIA = Place(
    name="Alexandria",
    latitude=31.2,
    longitude=29.9,
    country="Egypt",
    country_code="EG",
    timezone="Africa/Cairo",
)


class TestContextBlock:
    def test_it_names_todays_date_and_the_relative_days(self):
        block = context_block(datetime(2026, 8, 17, 15, 42, tzinfo=CAIRO), ALEXANDRIA, "Alexandria")
        assert "2026-08-17" in block  # today
        assert "2026-08-18" in block  # بكرة
        assert "2026-08-19" in block  # بعد بكرة
        assert "2026-08-16" in block  # امبارح

    def test_relative_days_are_spelled_out_in_arabic_too(self):
        block = context_block(datetime(2026, 8, 17, 15, 42, tzinfo=CAIRO), ALEXANDRIA, "Alexandria")
        assert "بكرة" in block

    def test_it_names_the_clock_and_the_timezone_the_user_lives_in(self):
        block = context_block(datetime(2026, 8, 17, 15, 42, tzinfo=CAIRO), ALEXANDRIA, "Alexandria")
        assert "3:42 PM" in block
        assert "Africa/Cairo" in block
        assert "Alexandria, Egypt" in block

    def test_a_late_night_turn_still_reports_the_users_own_day(self):
        # 23:30 in Cairo is already the next day in UTC. The user's day is the one that counts.
        block = context_block(datetime(2026, 8, 17, 23, 30, tzinfo=CAIRO), ALEXANDRIA, "Alexandria")
        assert "= 2026-08-17" in block
        assert "= 2026-08-18" in block

    def test_it_falls_back_to_the_city_name_when_the_geocoder_was_unreachable(self):
        block = context_block(datetime(2026, 8, 17, 9, 0, tzinfo=CAIRO), None, "Alexandria")
        assert "Alexandria" in block


class TestLanguageBlock:
    def test_no_preference_adds_no_section(self):
        # Mirroring is the default and already lives in the persona prompt; saying it twice
        # is how a prompt starts contradicting itself.
        assert language_block(None) is None
        assert language_block("mixed") is None

    def test_an_explicit_arabic_choice_overrides_mirroring(self):
        block = language_block("ar")
        assert "Arabic" in block
        assert "overrides" in block

    def test_an_explicit_english_choice_overrides_mirroring(self):
        assert "English" in language_block("en")


class TestSystemPrompt:
    def test_it_carries_the_persona_the_tools_and_the_memory_section(self):
        prompt = build_system_prompt(personas.EGYPTIAN)
        assert "Egyptian colloquial" in prompt
        assert "get_prayer_times" in prompt
        assert "What you remember about this person" in prompt

    def test_with_no_facts_it_says_so_rather_than_leaving_a_hole(self):
        # An empty memory section invites the model to fill it in — with a made-up name.
        assert "Do not invent a name" in build_system_prompt(personas.EGYPTIAN)

    def test_facts_replace_the_placeholder(self):
        prompt = build_system_prompt(personas.EGYPTIAN, facts_block="- name: كريم")
        assert "- name: كريم" in prompt
        assert "Do not invent a name" not in prompt

    def test_the_gulf_persona_gets_the_same_tool_rules(self):
        assert "get_prayer_times" in build_system_prompt(personas.GULF)

    def test_prayer_anchored_times_are_explained_with_examples(self):
        prompt = build_system_prompt(personas.EGYPTIAN)
        for anchor in ("بعد الفجر", "بعد العصر", "بعد المغرب", "بعد العشا"):
            assert anchor in prompt

    def test_the_honesty_clause_names_the_actions_sarjy_cannot_do(self):
        # D-044: the brain claimed a booking it had no tool for. The fix is naming the gap.
        prompt = build_system_prompt(personas.EGYPTIAN)
        assert "email" in prompt.lower()
        assert "Never claim you did something you did not do." in prompt

    def test_it_is_told_there_is_no_settings_screen_to_point_at(self):
        # Found in the acceptance run: asked to change its voice, Sarjy invented a settings
        # menu — "look under speech or voice assistant options". No such screen exists.
        prompt = build_system_prompt(personas.EGYPTIAN)
        assert "no settings screen" in prompt
        assert "cannot change your own voice" in prompt

    def test_it_is_told_to_hedge_facts_that_move(self):
        prompt = build_system_prompt(personas.EGYPTIAN)
        assert "# Things that change" in prompt
        # ...but never to hedge the three things a tool answers.
        assert "Weather, prayer times and their appointments are never in this category" in (
            prompt.replace("\n", " ")
        )


class TestGreetings:
    def test_the_first_visit_line_is_bilingual_and_split_for_two_voices(self):
        assert len(personas.FIRST_VISIT_SEGMENTS) == 2
        assert [lang for _, lang in personas.FIRST_VISIT_SEGMENTS] == ["ar", "en"]
        assert "سرجي" in personas.FIRST_VISIT_TEXT
        assert "Sarjy" in personas.FIRST_VISIT_TEXT

    def test_a_returning_visitor_is_greeted_by_name_in_the_persona_dialect(self):
        assert "كريم" in personas.returning_greeting("egyptian", "كريم")
        assert "أهلاً بيك تاني" in personas.returning_greeting("egyptian", "كريم")
        assert "هلا والله" in personas.returning_greeting("gulf", "كريم")

    def test_an_english_speaker_is_greeted_in_english(self):
        # §6.2: an explicit language choice sticks, and that includes the hello.
        assert personas.returning_greeting("egyptian", "Kareem", "en").startswith("Welcome back")
