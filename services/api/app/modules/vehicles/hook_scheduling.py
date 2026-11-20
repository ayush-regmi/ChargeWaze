"""Scheduling the post-ingest hooks after a request (ADR 0009, ADR 0010).

Both telemetry entry points use this: the Smartcar webhook and the on-demand
refresh. When a request stored new readings, every registered hook is queued
with FastAPI ``BackgroundTasks``, so it runs after the response is sent.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager

from fastapi import BackgroundTasks, Request

from app.db.session import get_db
from app.modules.vehicles.ingest_hooks import (
    SessionOpener,
    TelemetryIngested,
    registered_ingest_hooks,
    run_ingest_hooks,
)
from app.modules.vehicles.telemetry_service import IngestResult


def schedule_ingest_hooks(
    request: Request, background_tasks: BackgroundTasks, result: IngestResult
) -> bool:
    """Queue the registered hooks when ``result`` stored new readings.

    Returns whether anything was queued.
    """

    hooks = registered_ingest_hooks()
    if (
        not hooks
        or not result.accepted
        or not result.inserted_codes
        or result.vehicle_id is None
        or result.user_id is None
        or result.earliest_recorded_at is None
    ):
        return False
    event = TelemetryIngested(
        vehicle_id=result.vehicle_id,
        user_id=result.user_id,
        signal_codes=result.inserted_codes,
        earliest_recorded_at=result.earliest_recorded_at,
        is_simulated=result.is_simulated,
    )
    background_tasks.add_task(run_ingest_hooks, event, session_opener(request), hooks)
    return True


def session_opener(request: Request) -> SessionOpener:
    """Open sessions the way this request got its own (``get_db`` or its override).

    The request's session is closed when hooks run, so each hook opens its
    own. Using the app's provider keeps hooks on the same database as the
    request (in tests, the rolled-back test connection).
    """

    provider = request.app.dependency_overrides.get(get_db, get_db)
    if not inspect.isgeneratorfunction(provider):
        provider = get_db
    return contextmanager(provider)


__all__ = ["schedule_ingest_hooks", "session_opener"]
