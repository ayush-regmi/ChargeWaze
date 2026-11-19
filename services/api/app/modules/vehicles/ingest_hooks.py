"""Post-ingest hooks for telemetry webhooks (ADR 0009, phase5_plan.md §3 F3).

``vehicles`` owns the webhook path and must not import later modules
(architecture §3: modules depend down). Modules that react to new telemetry,
such as trip inference, register a hook here instead. The webhook router runs
the registered hooks with FastAPI ``BackgroundTasks``, after the response has
been sent, so a slow or failing hook never changes what Smartcar sees.

A hook gets the event and a way to open a database session: the request's
session is closed by the time hooks run. The router resolves the opener the
same way requests get their session (``get_db``, or its test override), so a
hook never reaches a database the request didn't use.

This module imports nothing from other modules.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelemetryIngested:
    vehicle_id: uuid.UUID
    user_id: uuid.UUID
    # Codes actually inserted by this delivery.
    signal_codes: frozenset[str]
    # Oldest provider timestamp among those rows (late deliveries).
    earliest_recorded_at: datetime
    is_simulated: bool


SessionOpener = Callable[[], AbstractContextManager[Session]]
IngestHook = Callable[[TelemetryIngested, SessionOpener], None]

_hooks: list[IngestHook] = []


def register_ingest_hook(hook: IngestHook) -> None:
    """Add ``hook``. Registering the same function again does nothing."""

    if hook not in _hooks:
        _hooks.append(hook)


def unregister_ingest_hook(hook: IngestHook) -> None:
    """Remove ``hook`` if registered (tests)."""

    if hook in _hooks:
        _hooks.remove(hook)


def registered_ingest_hooks() -> tuple[IngestHook, ...]:
    return tuple(_hooks)


def run_ingest_hooks(
    event: TelemetryIngested,
    open_session: SessionOpener,
    hooks: tuple[IngestHook, ...] | None = None,
) -> None:
    """Run every hook in order. One hook's exception never stops the others.

    Failures are logged with the hook's name and the error type only: no
    message or traceback, which could carry SQL parameters, Smartcar IDs or
    telemetry values.
    """

    for hook in registered_ingest_hooks() if hooks is None else hooks:
        try:
            hook(event, open_session)
        except Exception as exc:  # a hook must never fail the delivery
            logger.error(
                "ingest_hook_failed",
                extra={
                    "hook": getattr(hook, "__qualname__", repr(hook)),
                    "error_type": type(exc).__name__,
                },
            )


__all__ = [
    "IngestHook",
    "SessionOpener",
    "TelemetryIngested",
    "register_ingest_hook",
    "registered_ingest_hooks",
    "run_ingest_hooks",
    "unregister_ingest_hook",
]
