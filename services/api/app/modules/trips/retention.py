"""Retention deletes for planned trips (P6-F5, phase4_plan.md D6).

``POST /routes/plan`` stores every result unsaved; unsaved plans expire.
Only ``saved_at IS NULL AND status = 'planned'`` rows go: a plan that
matching marked ``completed`` keeps its observed legs and ``'plan'``
prediction row (phase5_plan.md §3 F1 note). Stops and ``'plan'`` predictions
cascade; matched legs would be unlinked (``SET NULL``), though only unmatched
plans are deleted here. Called by ``scripts/retention.py``, which commits.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, and_, delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.modules.trips.models import PlannedTrip


def _expired(older_than: datetime) -> ColumnElement[bool]:
    return and_(
        PlannedTrip.saved_at.is_(None),
        PlannedTrip.status == "planned",
        PlannedTrip.created_at < older_than,
    )


def count_expired_unsaved_plans(db: Session, *, older_than: datetime) -> int:
    return int(
        db.scalar(select(func.count()).select_from(PlannedTrip).where(_expired(older_than))) or 0
    )


def purge_expired_unsaved_plans(db: Session, *, older_than: datetime) -> int:
    """Delete unsaved, unmatched plans created before ``older_than``. Returns the count."""

    result = db.execute(delete(PlannedTrip).where(_expired(older_than)))
    return result.rowcount if isinstance(result, CursorResult) else 0


__all__ = ["count_expired_unsaved_plans", "purge_expired_unsaved_plans"]
