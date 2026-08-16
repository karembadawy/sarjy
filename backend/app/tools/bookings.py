# -*- coding: utf-8 -*-
"""`create_booking` and `list_bookings` — the two tools that write and read real rows.

D-013 fixed the scope: booking is one tool call, not a workflow engine. The value is that
the `bookings` table can be shown on screen next to Sarjy saying it made the booking —
which is also the honesty test of D-044, where the brain claimed a booking it had no way to
make. From this phase on, the claim is either true or the tool said why not.

`scheduled_at` is stored timezone-aware, always. The column is `TIMESTAMPTZ`, the session
runs in UTC, and the user thinks in Africa/Cairo; storing a naive datetime would silently
mean "UTC" and move every appointment by two or three hours.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Booking
from .when import spoken_datetime

log = logging.getLogger("sarjy.tools.bookings")

# What a spoken list can carry before it stops being an answer and becomes a recital.
LIST_LIMIT = 5


def create_booking(
    db: Session,
    user_id: uuid.UUID,
    service: str,
    when: datetime,
    tz: ZoneInfo,
    notes: str | None = None,
) -> dict:
    """Insert one confirmed booking and hand back what the confirmation must say."""
    service = (service or "").strip()
    if not service:
        return {"error": "I need to know what the appointment is for."}

    booking = Booking(
        user_id=user_id,
        service=service,
        scheduled_at=when,
        notes=(notes or "").strip() or None,
        status="confirmed",
    )
    db.add(booking)
    db.flush()

    local = when.astimezone(tz)
    log.info("booking: #%s %r for user %s at %s", booking.id, service, user_id, local.isoformat())
    return {
        "created": True,
        "booking_id": booking.id,
        "service": service,
        "when": spoken_datetime(local),
        "timezone": str(tz),
        "notes": booking.notes,
    }


def list_bookings(db: Session, user_id: uuid.UUID, now: datetime, tz: ZoneInfo) -> dict:
    """This user's upcoming confirmed bookings, soonest first."""
    rows = db.scalars(
        select(Booking)
        .where(
            Booking.user_id == user_id,
            Booking.status == "confirmed",
            Booking.scheduled_at >= now,
        )
        .order_by(Booking.scheduled_at)
        .limit(LIST_LIMIT)
    ).all()

    return {
        "count": len(rows),
        "bookings": [
            {
                "booking_id": row.id,
                "service": row.service,
                "when": spoken_datetime(row.scheduled_at.astimezone(tz)),
                "notes": row.notes,
            }
            for row in rows
        ],
    }
