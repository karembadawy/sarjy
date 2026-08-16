# -*- coding: utf-8 -*-
"""Reading Google's 429s: is this a burst limit or a spent day, and how long is the wait.

One module because there are two callers — the brain (D-035) and the voice router (D-038) —
and the first time this logic was written twice, both copies were wrong in the same way.

The bug is worth recording, because it is invisible until you look at a real error. The SDK
raises with the payload rendered as a **Python dict repr**, not as JSON:

    {'error': {'code': 429, ..., 'details': [
        {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '44578s'}]}}

Single quotes. A pattern written against the documented JSON (`"retryDelay": "44578s"`)
matches nothing, silently, and the caller falls back to a guessed delay while believing it is
using the provider's own number. Both quote styles are accepted here, and the tests use the
string copied out of a real Cloud Run log rather than one written from the documentation.
"""

from __future__ import annotations

import re

# `'retryDelay': '44578s'` or `"retryDelay": "44578s"` — Google sends the first.
_RETRY_DELAY = re.compile(r"""['"]?retryDelay['"]?\s*[:=]\s*['"]?(\d+(?:\.\d+)?)s""")

# Google returns the same HTTP 429 for a per-minute burst and a spent daily allowance, and
# only the quotaId tells them apart, e.g. `GenerateRequestsPerDayPerProjectPerModel`.
_PER_DAY = re.compile(r"PerDay|per_day|_per_day", re.IGNORECASE)


def _detail(exc: Exception) -> str:
    """Everything the exception can tell us, as one searchable string."""
    return f"{getattr(exc, 'details', '') or ''}{exc}"


def is_per_day(exc: Exception) -> bool:
    """True when the allowance resets tomorrow rather than in the next minute."""
    return bool(_PER_DAY.search(_detail(exc)))


def retry_after_s(exc: Exception, maximum: float | None = None) -> float | None:
    """How long the provider says to wait, or None if it did not say.

    `maximum` clamps the answer for callers that have to show it to somebody: a minute of
    dead air on a phone call is an abandoned state, not a designed one.
    """
    match = _RETRY_DELAY.search(_detail(exc))
    if not match:
        return None
    delay = float(match.group(1))
    return min(delay, maximum) if maximum is not None else delay
