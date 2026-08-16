# -*- coding: utf-8 -*-
"""The tool layer's two promises: the right prayer authority, and never a stack trace.

No network and no database here (CLAUDE.md: pure-logic tests only) — the live checks against
Aladhan and Open-Meteo were run against the real APIs and their numbers are recorded in the
decision log instead.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app import tools
from app.tools.places import DEFAULT_PRAYER_METHOD, Place
from app.tools.when import WhenError, zone

CAIRO = zone("Africa/Cairo")


def place(country_code: str) -> Place:
    return Place(
        name="Somewhere",
        latitude=0.0,
        longitude=0.0,
        country="",
        country_code=country_code,
        timezone="UTC",
    )


class TestPrayerMethod:
    """Follows the country of the city being asked about, not the persona (refines D-022)."""

    def test_egypt_uses_the_egyptian_general_authority(self):
        assert place("EG").prayer_method == 5

    def test_saudi_arabia_uses_umm_al_qura(self):
        assert place("SA").prayer_method == 4

    def test_anywhere_else_falls_back_to_the_default(self):
        assert place("FR").prayer_method == DEFAULT_PRAYER_METHOD
        assert place("").prayer_method == DEFAULT_PRAYER_METHOD

    def test_the_country_code_is_matched_case_insensitively(self):
        assert place("sa").prayer_method == 4


class TestDeclarations:
    def test_every_declared_tool_has_a_handler_and_vice_versa(self):
        assert set(tools.NAMES) == set(tools.HANDLERS)

    def test_the_four_tools_of_the_spec_are_the_four_tools(self):
        assert tools.NAMES == [
            "get_weather",
            "get_prayer_times",
            "create_booking",
            "list_bookings",
        ]

    def test_declarations_describe_their_parameters_in_english(self):
        # The SDK coerces the dicts we write into `types.Schema`, so this reads them back
        # the way Gemini will actually receive them rather than the way they were authored.
        for declaration in tools.DECLARATIONS:
            assert declaration.description
            for name, schema in (declaration.parameters.properties or {}).items():
                assert schema.type, f"{declaration.name}.{name} has no type"
                assert schema.description, f"{declaration.name}.{name} is undescribed"

    def test_the_city_parameter_asks_for_english(self):
        # Not a nicety: the geocoder resolves "اسكندرية" to a town in Syria.
        weather = next(d for d in tools.DECLARATIONS if d.name == "get_weather")
        assert "ENGLISH" in weather.parameters.properties["city"].description


class TestRunToolNeverRaises:
    """brain.py's loop has no exception handling around tool calls, on purpose: a tool that
    raised would end the turn in silence instead of in a sentence the user can act on."""

    @staticmethod
    def context() -> tools.ToolContext:
        return tools.ToolContext(
            db=None,
            user_id=uuid.uuid4(),
            now=datetime(2026, 8, 17, 15, 0, tzinfo=CAIRO),
            tz=CAIRO,
            default_city="Alexandria",
        )

    def test_an_unknown_tool_is_reported_not_raised(self):
        result = tools.run_tool("send_email", {}, self.context())
        assert "error" in result
        assert "send_email" in result["error"]

    def test_an_unparseable_date_comes_back_as_a_speakable_sentence(self):
        result = tools.run_tool("create_booking", {"service": "دكتور", "datetime_iso": "soon"}, self.context())
        assert "error" in result
        assert "YYYY-MM-DD" in result["error"]

    def test_a_booking_with_no_time_is_refused_before_it_touches_the_database(self):
        # db is None here, so reaching the insert would be a TypeError, not an error dict.
        result = tools.run_tool("create_booking", {"service": "دكتور"}, self.context())
        assert "error" in result

    def test_an_error_never_contains_a_traceback(self):
        for result in (
            tools.run_tool("list_bookings", {}, self.context()),  # db is None → blows up inside
            tools.run_tool("create_booking", {"datetime_iso": "nope"}, self.context()),
        ):
            assert "error" in result
            assert "Traceback" not in result["error"]
            assert "\n" not in result["error"]

    def test_the_round_cap_is_a_hard_number(self):
        assert tools.MAX_TOOL_ROUNDS == 4


class TestWhenErrorsAreCaught:
    def test_parse_moment_raises_the_type_run_tool_catches(self):
        with pytest.raises(WhenError):
            tools._create_booking(TestRunToolNeverRaises.context(), {"datetime_iso": "soon"})
