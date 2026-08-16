# -*- coding: utf-8 -*-
"""Dates and times, which is where a booking assistant quietly goes wrong.

The server runs in UTC and the user lives in Africa/Cairo. Every case here is one of the
ways that gap turns "بكرة بعد العصر" into an appointment on the wrong day or three hours
out — silently, and never during testing, because a developer testing at 2pm local never
crosses a midnight.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from app.tools.when import (
    WhenError,
    parse_day,
    parse_moment,
    spoken_clock,
    spoken_datetime,
    zone,
)

CAIRO = zone("Africa/Cairo")
TODAY = date(2026, 8, 17)


class TestParseDay:
    def test_empty_means_today(self):
        assert parse_day("", TODAY) == TODAY
        assert parse_day(None, TODAY) == TODAY

    def test_iso_dates_pass_through(self):
        assert parse_day("2026-08-19", TODAY) == date(2026, 8, 19)

    def test_relative_words_resolve_against_the_users_today(self):
        assert parse_day("tomorrow", TODAY) == date(2026, 8, 18)
        assert parse_day("today", TODAY) == TODAY
        assert parse_day("yesterday", TODAY) == date(2026, 8, 16)

    def test_tomorrow_crosses_a_month_boundary(self):
        assert parse_day("tomorrow", date(2026, 8, 31)) == date(2026, 9, 1)

    def test_a_full_iso_datetime_is_read_as_its_date(self):
        assert parse_day("2026-08-19T17:00:00", TODAY) == date(2026, 8, 19)

    def test_nonsense_is_refused_with_a_speakable_sentence(self):
        with pytest.raises(WhenError) as raised:
            parse_day("next thursday", TODAY)
        assert "YYYY-MM-DD" in str(raised.value)


class TestParseMoment:
    def test_a_naive_time_is_the_users_local_time_not_utc(self):
        # The whole point: 17:00 means five in the afternoon where the person is standing.
        moment = parse_moment("2026-08-18T17:00", CAIRO)
        assert moment.tzinfo is CAIRO
        assert moment.hour == 17
        assert moment.utcoffset().total_seconds() == 3 * 3600

    def test_a_space_separated_time_is_accepted(self):
        assert parse_moment("2026-08-18 17:00", CAIRO).hour == 17

    def test_an_explicit_offset_is_honoured_as_written(self):
        moment = parse_moment("2026-08-18T17:00:00+01:00", CAIRO)
        assert moment.utcoffset().total_seconds() == 3600

    def test_a_zulu_time_is_utc(self):
        moment = parse_moment("2026-08-18T14:00:00Z", CAIRO)
        assert moment.utcoffset().total_seconds() == 0
        # ...and lands at 5pm on the user's clock, which is what the confirmation must say.
        assert moment.astimezone(CAIRO).hour == 17

    def test_a_missing_time_is_refused(self):
        with pytest.raises(WhenError):
            parse_moment("", CAIRO)

    def test_nonsense_is_refused(self):
        with pytest.raises(WhenError):
            parse_moment("tomorrow afternoon", CAIRO)


class TestSpokenForms:
    def test_the_clock_is_the_form_a_person_says(self):
        assert spoken_clock(datetime(2026, 8, 18, 16, 58, tzinfo=CAIRO)) == "4:58 PM"

    def test_midnight_and_noon_do_not_come_out_as_zero(self):
        assert spoken_clock(datetime(2026, 8, 18, 0, 5, tzinfo=CAIRO)) == "12:05 AM"
        assert spoken_clock(datetime(2026, 8, 18, 12, 0, tzinfo=CAIRO)) == "12:00 PM"

    def test_the_confirmation_names_the_weekday_and_the_date(self):
        spoken = spoken_datetime(datetime(2026, 8, 18, 16, 58, tzinfo=CAIRO))
        assert spoken == "Tuesday 18 August 2026 at 4:58 PM"


class TestZone:
    def test_a_real_zone_resolves(self):
        assert str(zone("Asia/Riyadh")) == "Asia/Riyadh"

    def test_an_unknown_zone_falls_back_instead_of_raising(self):
        # A geocoder returning something odd must not take a whole turn down with it.
        assert str(zone("Middle/Earth")) == "UTC"
