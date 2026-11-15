"""Reading the cached ``vehicles.latest_state`` document.

Shape (``docs/database-design.md`` §3.3, phase2_plan.md D5)::

    {
      "recorded_at": "2026-11-06T18:52:00Z",      # max of the per-signal values
      "signals": {
        "<signal_code>": {"value": 72, "unit": "percent", "recorded_at": "..."}
      }
    }

The webhook ingestor (F5) is the only writer. Readers here are defensive: the
column is JSON, so anything malformed is treated as "no data yet" rather than
failing the request.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # A naive timestamp would be ambiguous; the writer always stores UTC.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def last_recorded_at(latest_state: Mapping[str, Any] | None) -> datetime | None:
    """Newest provider timestamp in ``latest_state``, or ``None`` if there is none."""

    if not isinstance(latest_state, Mapping):
        return None
    candidates: list[datetime] = []
    top = _parse_datetime(latest_state.get("recorded_at"))
    if top is not None:
        candidates.append(top)
    signals = latest_state.get("signals")
    if isinstance(signals, Mapping):
        for entry in signals.values():
            if isinstance(entry, Mapping):
                ts = _parse_datetime(entry.get("recorded_at"))
                if ts is not None:
                    candidates.append(ts)
    return max(candidates) if candidates else None


def is_stale(
    recorded_at: datetime | None, *, stale_after: timedelta, now: datetime | None = None
) -> bool:
    """No data at all counts as stale (api §3.5: ``is_stale: true`` with no telemetry)."""

    if recorded_at is None:
        return True
    return (now or datetime.now(UTC)) - recorded_at > stale_after


__all__ = ["is_stale", "last_recorded_at"]
