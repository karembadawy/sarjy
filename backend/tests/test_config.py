# -*- coding: utf-8 -*-
"""Tests for the origin allowlist that guards `/ws` and CORS (D-048).

Pure logic, no network: `origin_allowed` is handed its patterns explicitly so the test says
nothing about whatever happens to be in the developer's `.env`.
"""

from __future__ import annotations

import pytest

from app.config import LOCAL_ORIGINS, origin_allowed

PATTERNS = [*LOCAL_ORIGINS, "https://sarjy.vercel.app", "*.vercel.app"]


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://sarjy.vercel.app",
        "https://sarjy.vercel.app/",  # a trailing slash is not a different origin
        "https://sarjy-git-main-karem.vercel.app",  # a Vercel preview build
    ],
)
def test_allowed(origin: str) -> None:
    assert origin_allowed(origin, PATTERNS)


@pytest.mark.parametrize(
    "origin",
    [
        "https://sarjy.example.com",
        "http://localhost:3000",
        # The wildcard matches a suffix of the *host*, so a lookalike domain must not pass.
        "https://evilvercel.app",
        "https://vercel.app.attacker.com",
    ],
)
def test_refused(origin: str) -> None:
    assert not origin_allowed(origin, PATTERNS)


def test_missing_origin_is_allowed() -> None:
    """Only browsers send Origin; curl and wscat are how a deploy gets debugged."""
    assert origin_allowed(None, PATTERNS)
    assert origin_allowed("", PATTERNS)
