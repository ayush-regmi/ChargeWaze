"""Ingesting Smartcar telemetry: webhook events (Phase 2 F5) and refreshes (ADR 0010).

``ingest_vehicle_event`` stores a ``VEHICLE_STATE``/``VEHICLE_ERROR`` webhook;
``ingest_polled_signals`` stores an on-demand ``GET /signals`` read. Both go
through ``_store_signals``, so the rules below apply to both; only dedup
differs (see ``ingest_polled_signals``).

The router verifies the signature and parses the envelope; this module owns
the business rules (``docs/architecture.md`` §5, ``phase2_plan.md`` D5/D12):

- The vehicle is found by Smartcar vehicle ID *and* Smartcar user ID. Unknown
  or not-``connected`` vehicles are acknowledged without persisting anything,
  so Smartcar doesn't retry them for days.
- Every signal becomes one ``vehicle_telemetry`` row, inserted with
  ``ON CONFLICT DO NOTHING`` on ``(source, source_event_id, signal_code)``. A
  redelivered event therefore inserts nothing and changes nothing.
- ``latest_state`` moves forward per signal: a reading replaces the cached one
  only if its provider ``recorded_at`` is at least as new (out-of-order rule).
  Only rows actually inserted by this delivery are applied.
- Errored and unmapped signals are stored (value ``NULL``, details in
  ``raw_payload``) but never touch ``latest_state``.
- ``supported_signals`` only grows: signals that report data are added, so a
  vehicle whose first read after Connect was empty catches up.

Everything happens in the caller's transaction with the vehicle row locked
(``SELECT … FOR UPDATE``), so concurrent deliveries for one vehicle serialize.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.integrations.smartcar.schemas import (
    SIGNAL_MAP,
    NormalizedSignal,
    SignalsResponse,
    VehicleStateData,
    WebhookEvent,
    normalize_signals,
    parse_timestamp,
    supported_signal_codes,
)
from app.modules.vehicles.models import Vehicle, VehicleTelemetry
from app.modules.vehicles.vehicle_state import KM_PER_MILE

logger = logging.getLogger(__name__)

SOURCE = "smartcar"


@dataclass(frozen=True)
class IngestResult:
    accepted: bool
    event_id: str | None
    # Telemetry rows written by this delivery (0 for a duplicate or a refusal).
    inserted: int = 0
    # Short machine reason when not accepted; logged, never returned to Smartcar.
    reason: str | None = None
    # Set when accepted, for the post-ingest hooks (ADR 0009).
    vehicle_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    is_simulated: bool = False
    # Signal codes actually inserted by this delivery (empty for a duplicate).
    inserted_codes: frozenset[str] = frozenset()
    # Oldest provider timestamp among the inserted rows (late deliveries).
    earliest_recorded_at: datetime | None = None


def ingest_vehicle_event(
    db: Session,
    event: WebhookEvent,
    data: VehicleStateData,
    *,
    now: datetime | None = None,
) -> IngestResult:
    """Persist one signed, parsed ``VEHICLE_STATE``/``VEHICLE_ERROR`` event.

    Commits on success. Database errors propagate so the router can answer 5xx
    and Smartcar retries.
    """

    event_id = event.event_id
    received_at = now or datetime.now(UTC)

    vehicle = db.execute(
        select(Vehicle)
        .where(
            Vehicle.smartcar_vehicle_id == data.vehicle.id,
            Vehicle.smartcar_user_id == data.user.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()

    if vehicle is None:
        db.rollback()
        return IngestResult(accepted=False, event_id=event_id, reason="unknown_vehicle")
    if vehicle.connection_status != "connected":
        db.rollback()
        return IngestResult(accepted=False, event_id=event_id, reason="vehicle_not_connected")

    delivered_at = parse_timestamp(event.meta.delivered_at)
    stored = _store_signals(
        db,
        vehicle,
        normalize_signals(data),
        event_id_for=lambda _recorded_at: event_id,
        received_at=received_at,
        fallback_recorded_at=delivered_at,
        # A refresh may already have stored this reading (Smartcar retries
        # failed deliveries for days). Webhook-vs-webhook stays as before.
        skip_if_stored="poll",
        log_ref=event_id,
    )

    db.commit()
    logger.info(
        "smartcar_webhook_ingested",
        extra={
            "event_id": event_id,
            "event_type": event.event_type,
            "signals": stored.candidates,
            "inserted": len(stored.codes),
        },
    )
    return stored.result(vehicle, event_id=event_id)


def ingest_polled_signals(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    signals: SignalsResponse,
    now: datetime | None = None,
) -> IngestResult:
    """Persist one on-demand ``GET /vehicles/{id}/signals`` read (ADR 0010).

    Same rules as a webhook delivery: same normalization, ``recorded_at`` is
    the OEM timestamp, ``latest_state`` only moves forward, ``supported_signals``
    only grows. Dedup differs because a REST read has no event ID:

    - ``source_event_id`` is ``poll:<vehicle_id>:<recorded_at>``, so refreshing
      the same OEM reading twice hits the ``(source, source_event_id,
      signal_code)`` unique index and inserts nothing. The vehicle is part of
      the ID because that index is global: two cars reporting at the same
      second must not collide.
    - A reading already stored from any Smartcar row (a webhook, typically)
      with the same ``(vehicle, signal_code, recorded_at)`` is skipped.

    REST signals have no delivery time, so a signal without its own timestamp
    (errored signals, usually) is skipped rather than given an invented one.
    Commits. Not accepted if the vehicle was disconnected meanwhile.
    """

    received_at = now or datetime.now(UTC)
    # ``populate_existing``: the caller's session may already hold this vehicle
    # from before the Smartcar call (sessions don't expire on commit). Reload
    # it under the lock so a webhook or disconnect that landed meanwhile is
    # seen, and its ``latest_state`` isn't overwritten with the stale copy.
    vehicle = db.execute(
        select(Vehicle)
        .where(Vehicle.id == vehicle_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if vehicle is None:
        db.rollback()
        return IngestResult(accepted=False, event_id=None, reason="unknown_vehicle")
    if vehicle.connection_status != "connected":
        db.rollback()
        return IngestResult(accepted=False, event_id=None, reason="vehicle_not_connected")

    stored = _store_signals(
        db,
        vehicle,
        normalize_signals(signals),
        event_id_for=lambda recorded_at: poll_event_id(vehicle.id, recorded_at),
        received_at=received_at,
        fallback_recorded_at=None,
        skip_if_stored="any",
        log_ref=None,
    )
    db.commit()
    logger.info(
        "smartcar_refresh_ingested",
        extra={"signals": stored.candidates, "inserted": len(stored.codes)},
    )
    return stored.result(vehicle, event_id=None)


POLL_EVENT_PREFIX = "poll:"


def poll_event_id(vehicle_id: uuid.UUID, recorded_at: datetime) -> str:
    """Synthetic ``source_event_id`` for a refreshed reading.

    One per vehicle and OEM timestamp. The dedup index is
    ``(source, source_event_id, signal_code)`` with no ``vehicle_id``, so the
    vehicle must be in the ID or two cars reporting at the same second would
    collide and the second car's reading would be dropped.
    """

    return f"{POLL_EVENT_PREFIX}{vehicle_id}:{_iso(recorded_at)}"


@dataclass(frozen=True)
class _Stored:
    candidates: int
    codes: list[str]
    times: list[datetime]

    def result(self, vehicle: Vehicle, *, event_id: str | None) -> IngestResult:
        return IngestResult(
            accepted=True,
            event_id=event_id,
            inserted=len(self.codes),
            vehicle_id=vehicle.id,
            user_id=vehicle.user_id,
            is_simulated=vehicle.is_simulated,
            inserted_codes=frozenset(self.codes),
            earliest_recorded_at=min(self.times, default=None),
        )


def _store_signals(
    db: Session,
    vehicle: Vehicle,
    signals: list[NormalizedSignal],
    *,
    event_id_for: Callable[[datetime], str | None],
    received_at: datetime,
    fallback_recorded_at: datetime | None,
    skip_if_stored: Literal["any", "poll"],
    log_ref: str | None,
) -> _Stored:
    """Insert ``signals`` for a locked ``vehicle`` and apply them to its cache.

    Shared by webhook deliveries and refreshes. ``skip_if_stored`` drops a
    reading whose ``(signal_code, recorded_at)`` is already stored for the
    vehicle from Smartcar: from any row (``"any"``) or only from a refresh
    (``"poll"``). The caller commits.
    """

    by_code: dict[str, tuple[NormalizedSignal, datetime]] = {}
    for signal in signals:
        recorded_at = signal.recorded_at or fallback_recorded_at
        if recorded_at is None:
            # vehicle_telemetry.recorded_at is NOT NULL and we won't invent one.
            logger.warning(
                "smartcar_telemetry_signal_skipped",
                extra={"event_id": log_ref, "reason": "no_timestamp"},
            )
            continue
        code = _telemetry_code(signal)
        if code in by_code:
            # One reading per code per event; the dedup key can't hold two.
            continue
        by_code[code] = (signal, recorded_at)

    candidates = len(by_code)
    for code in _already_stored(db, vehicle.id, by_code, only_polls=skip_if_stored == "poll"):
        del by_code[code]

    rows: list[dict[str, Any]] = [
        {
            "vehicle_id": vehicle.id,
            "recorded_at": recorded_at,
            "received_at": received_at,
            "source": SOURCE,
            "source_event_id": event_id_for(recorded_at),
            "signal_code": code,
            "value": signal.value,
            "unit": signal.unit,
            "raw_payload": _raw_payload(signal),
            "is_simulated": vehicle.is_simulated,
        }
        for code, (signal, recorded_at) in by_code.items()
    ]

    inserted_codes: list[str] = []
    inserted_times: list[datetime] = []
    if rows:
        stmt = (
            pg_insert(VehicleTelemetry)
            .values(rows)
            .on_conflict_do_nothing(
                index_elements=["source", "source_event_id", "signal_code"],
                index_where=text("source_event_id IS NOT NULL"),
            )
            .returning(VehicleTelemetry.signal_code, VehicleTelemetry.recorded_at)
        )
        for row in db.execute(stmt):
            inserted_codes.append(row.signal_code)
            inserted_times.append(row.recorded_at)

    if inserted_codes:
        inserted = [by_code[code][0] for code in inserted_codes]
        new_state = apply_to_latest_state(
            vehicle.latest_state,
            inserted,
            received_at=received_at,
            fallback_recorded_at=fallback_recorded_at,
        )
        if new_state != vehicle.latest_state:
            vehicle.latest_state = new_state
        supported = sorted(
            set(vehicle.supported_signals or []) | set(supported_signal_codes(inserted))
        )
        if supported != list(vehicle.supported_signals or []):
            vehicle.supported_signals = supported

    return _Stored(candidates=candidates, codes=inserted_codes, times=inserted_times)


def _already_stored(
    db: Session,
    vehicle_id: uuid.UUID,
    readings: Mapping[str, tuple[NormalizedSignal, datetime]],
    *,
    only_polls: bool,
) -> list[str]:
    """Codes in ``readings`` whose exact ``(code, recorded_at)`` is already stored."""

    if not readings:
        return []
    stmt = select(VehicleTelemetry.signal_code, VehicleTelemetry.recorded_at).where(
        VehicleTelemetry.vehicle_id == vehicle_id,
        VehicleTelemetry.source == SOURCE,
        VehicleTelemetry.signal_code.in_(list(readings)),
        VehicleTelemetry.recorded_at.in_({ts for _, ts in readings.values()}),
    )
    if only_polls:
        stmt = stmt.where(VehicleTelemetry.source_event_id.startswith(POLL_EVENT_PREFIX))
    existing = {(row.signal_code, row.recorded_at) for row in db.execute(stmt)}
    return [code for code, (_, ts) in readings.items() if (code, ts) in existing]


# ---------------------------------------------------------------------------
# Read-only access for trip inference (phase5_plan.md §3 F3)
# ---------------------------------------------------------------------------

ReadingCode = Literal["odometer", "soc", "charging_status"]
READING_CODES: tuple[ReadingCode, ...] = ("odometer", "soc", "charging_status")

# Same unit rules as ``vehicle_state.py``: an unrecognised unit is skipped,
# never guessed (PRD §7).
_ODOMETER_TO_KM: dict[str, float] = {
    "km": 1.0,
    "kilometers": 1.0,
    "kilometres": 1.0,
    "mi": KM_PER_MILE,
    "mile": KM_PER_MILE,
    "miles": KM_PER_MILE,
}


@dataclass(frozen=True)
class TelemetryReading:
    """One usable reading: odometer in km, SoC in percent, charging as 1.0/0.0."""

    recorded_at: datetime
    code: ReadingCode
    value: float
    is_simulated: bool


def list_readings(
    db: Session,
    *,
    vehicle_id: uuid.UUID,
    since: datetime | None = None,
    codes: Iterable[ReadingCode] = READING_CODES,
) -> list[TelemetryReading]:
    """Readings of ``codes`` recorded at or after ``since`` (all when ``None``).

    When ``since`` is given, the latest usable reading of each code *before*
    it is included too, so a window's first odometer pair is complete and the
    engine knows the SoC and charging state at its start. Errored rows
    (``value`` ``NULL``) and unknown units are dropped. Ordered by
    ``(recorded_at, code)``. Read-only; the caller owns the transaction.
    """

    wanted = tuple(codes)
    base = select(
        VehicleTelemetry.recorded_at,
        VehicleTelemetry.signal_code,
        VehicleTelemetry.value,
        VehicleTelemetry.unit,
        VehicleTelemetry.is_simulated,
    ).where(
        VehicleTelemetry.vehicle_id == vehicle_id,
        VehicleTelemetry.value.is_not(None),
    )

    readings: list[TelemetryReading] = []
    window = base.where(VehicleTelemetry.signal_code.in_(wanted))
    if since is not None:
        window = window.where(VehicleTelemetry.recorded_at >= since)
    for row in db.execute(window):
        reading = _to_reading(row)
        if reading is not None:
            readings.append(reading)

    if since is not None:
        for code in wanted:
            # A few candidates in case the newest has an unknown unit.
            prior = (
                base.where(
                    VehicleTelemetry.signal_code == code,
                    VehicleTelemetry.recorded_at < since,
                )
                .order_by(VehicleTelemetry.recorded_at.desc())
                .limit(5)
            )
            for row in db.execute(prior):
                reading = _to_reading(row)
                if reading is not None:
                    readings.append(reading)
                    break

    readings.sort(key=lambda r: (r.recorded_at, r.code, r.value))
    return readings


def _to_reading(row: Any) -> TelemetryReading | None:
    code = row.signal_code
    raw = float(row.value)
    unit = row.unit.strip().lower() if isinstance(row.unit, str) else None
    value: float | None
    if code == "odometer":
        factor = _ODOMETER_TO_KM.get(unit) if unit else None
        value = None if factor is None else round(raw * factor, 3)
    elif code == "soc":
        value = raw if unit in (None, "percent") else None
    elif code == "charging_status":
        value = 1.0 if raw else 0.0
    else:
        value = None
    if value is None:
        return None
    return TelemetryReading(
        recorded_at=row.recorded_at, code=code, value=value, is_simulated=row.is_simulated
    )


def apply_to_latest_state(
    current: Mapping[str, Any] | None,
    signals: list[NormalizedSignal],
    *,
    received_at: datetime,
    fallback_recorded_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Return the new ``latest_state`` after applying ``signals`` (pure function).

    Only mapped, successful signals count. A signal replaces the cached entry
    when its ``recorded_at`` is ≥ the cached one (D5); ties take the newer
    delivery. Returns ``current`` unchanged (as a new dict) if nothing applies,
    or ``None`` if there was no state and nothing to add.
    """

    state_signals: dict[str, Any] = {}
    if isinstance(current, Mapping) and isinstance(current.get("signals"), Mapping):
        state_signals = {
            k: dict(v) for k, v in current["signals"].items() if isinstance(v, Mapping)
        }

    changed = False
    for signal in signals:
        if signal.code is None or signal.outcome != "ok":
            continue
        recorded_at = signal.recorded_at or fallback_recorded_at
        if recorded_at is None:
            continue
        cached = state_signals.get(signal.code)
        cached_at = _parse_iso(cached.get("recorded_at")) if cached else None
        if cached_at is not None and recorded_at < cached_at:
            continue
        state_signals[signal.code] = {
            "value": _state_value(signal),
            "unit": signal.unit,
            "recorded_at": _iso(recorded_at),
        }
        changed = True

    if not changed:
        return dict(current) if isinstance(current, Mapping) else None

    newest = max(
        (ts for entry in state_signals.values() if (ts := _parse_iso(entry.get("recorded_at")))),
        default=None,
    )
    return {
        "recorded_at": _iso(newest) if newest else None,
        "received_at": _iso(received_at),
        "signals": state_signals,
    }


def _telemetry_code(signal: NormalizedSignal) -> str:
    # Unmapped signals keep Smartcar's code so nothing is lost and they can't
    # collide with Chargewise codes (which never contain a hyphen).
    return signal.code if signal.code is not None else signal.smartcar_code


def _state_value(signal: NormalizedSignal) -> Any:
    if signal.text_value is not None:
        return signal.text_value
    spec = SIGNAL_MAP.get(signal.smartcar_code)
    if spec is not None and spec.kind == "bool" and signal.value is not None:
        return bool(signal.value)
    return _json_number(signal.value)


def _json_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral_value() else float(value)


def _raw_payload(signal: NormalizedSignal) -> dict[str, Any]:
    return {
        "smartcar_code": signal.smartcar_code,
        "outcome": signal.outcome,
        "error_code": signal.error_code,
        "text_value": signal.text_value,
        "retrieved_at": _iso(signal.retrieved_at) if signal.retrieved_at else None,
        "signal": signal.raw,
    }


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = [
    "POLL_EVENT_PREFIX",
    "READING_CODES",
    "IngestResult",
    "TelemetryReading",
    "apply_to_latest_state",
    "ingest_polled_signals",
    "ingest_vehicle_event",
    "list_readings",
    "poll_event_id",
]
