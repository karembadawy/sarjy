# -*- coding: utf-8 -*-
"""Reading the extractor's answer, and rendering what we know back into the prompt.

The extractor itself is a model call and is checked against the real API (its measured
behaviour is in the decision log). What is tested here is everything around it — because a
background task that quietly writes rubbish into `facts` poisons every later system prompt,
and the first symptom is Sarjy confidently calling somebody by the wrong name.
"""

from __future__ import annotations

import json

from app.memory import facts_block, parse_extraction


class Fact:
    """Stand-in for the ORM row — facts_block only ever reads two attributes."""

    def __init__(self, key: str, value: str) -> None:
        self.key = key
        self.value = value


class TestParseExtraction:
    def test_a_well_formed_answer_is_read(self):
        facts, preferred = parse_extraction(
            json.dumps({"facts": [{"key": "name", "value": "كريم"}], "preferred_language": "none"})
        )
        assert facts == [{"key": "name", "value": "كريم"}]
        assert preferred is None

    def test_an_explicit_language_switch_is_carried_through(self):
        _, preferred = parse_extraction(json.dumps({"facts": [], "preferred_language": "ar"}))
        assert preferred == "ar"

    def test_a_language_that_is_not_ar_or_en_is_ignored(self):
        # "none" is the schema's way of saying nothing was asked for; so is anything odd.
        for value in ("none", "arabic", "fr", None, ""):
            _, preferred = parse_extraction(
                json.dumps({"facts": [], "preferred_language": value})
            )
            assert preferred is None

    def test_non_json_means_nothing_was_learned_rather_than_a_crash(self):
        assert parse_extraction("I'm sorry, I can't do that") == ([], None)
        assert parse_extraction("") == ([], None)
        assert parse_extraction(None) == ([], None)

    def test_a_json_array_instead_of_an_object_is_survived(self):
        assert parse_extraction('[{"key": "name"}]') == ([], None)

    def test_half_formed_facts_are_dropped_not_stored(self):
        facts, _ = parse_extraction(
            json.dumps(
                {
                    "facts": [
                        {"key": "name", "value": "كريم"},
                        {"key": "job"},  # no value
                        {"value": "blue"},  # no key
                        "favorite_color",  # not an object at all
                    ],
                    "preferred_language": "none",
                }
            )
        )
        assert facts == [{"key": "name", "value": "كريم"}]


class TestFactsBlock:
    def test_nothing_known_renders_as_nothing(self):
        assert facts_block([]) is None

    def test_facts_render_one_per_line(self):
        block = facts_block([Fact("name", "كريم"), Fact("home_city", "Alexandria")])
        assert "- name: كريم" in block
        assert "- home_city: Alexandria" in block

    def test_the_block_tells_the_model_to_answer_across_languages(self):
        # D-014's whole point: a fact told in Arabic must answer an English question.
        block = facts_block([Fact("favorite_color", "الأزرق")])
        assert "language" in block.lower()
