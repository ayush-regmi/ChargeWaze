"""Ingesting Smartcar ``VEHICLE_STATE`` / ``VEHICLE_ERROR`` webhook events (Phase 2 F5).

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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.integrations.smartcar.schemas import (
    SIGNAL_MAP,
    NormalizedSignal,
    VehicleStateData,
    WebhookEvent,
    normalize_signals,
    parse_timestamp,
    supported_signal_codes,
)
from app.modules.vehicles.models import Vehicle, VehicleTelemetry

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
    ).scalar_one_or_none()

    if vehicle is None:
        db.rollback()
        return IngestResult(accepted=False, event_id=event_id, reason="unknown_vehicle")
    if vehicle.connection_status != "connected":
        db.rollback()
        return IngestResult(accepted=False, event_id=event_id, reason="vehicle_not_connected")

    delivered_at = parse_timestamp(event.meta.delivered_at)
    signals = normalize_signals(data)

    rows: list[dict[str, Any]] = []
    by_code: dict[str, NormalizedSignal] = {}
    for signal in signals:
        recorded_at = signal.recorded_at or delivered_at
        if recorded_at is None:
            # vehicle_telemetry.recorded_at is NOT NULL and we won't invent one.
            logger.warning(
                "smartcar_webhook_signal_skipped",
                extra={"event_id": event_id, "reason": "no_timestamp"},
            )
            continue
        code = _telemetry_code(signal)
        if code in by_code:
            # One reading per code per event; the dedup key can't hold two.
            continue
        by_code[code] = signal
        rows.append(
            {
                "vehicle_id": vehicle.id,
                "recorded_at": recorded_at,
                "received_at": received_at,
                "source": SOURCE,
                "source_event_id": event_id,
                "signal_code": code,
                "value": signal.value,
                "unit": signal.unit,
                "raw_payload": _raw_payload(signal),
                "is_simulated": vehicle.is_simulated,
            }
        )

    inserted_codes: list[str] = []
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
        inserted_codes = [row.signal_code for row in db.execute(stmt)]

    if inserted_codes:
        inserted = [by_code[code] for code in inserted_codes]
        new_state = apply_to_latest_state(
            vehicle.latest_state,
            inserted,
            received_at=received_at,
            fallback_recorded_at=delivered_at,
        )
        if new_state != vehicle.latest_state:
            vehicle.latest_state = new_state
        supported = sorted(
            set(vehicle.supported_signals or []) | set(supported_signal_codes(inserted))
        )
        if supported != list(vehicle.supported_signals or []):
            vehicle.supported_signals = supported

    db.commit()
    logger.info(
        "smartcar_webhook_ingested",
        extra={
            "event_id": event_id,
            "event_type": event.event_type,
            "signals": len(rows),
            "inserted": len(inserted_codes),
        },
    )
    return IngestResult(accepted=True, event_id=event_id, inserted=len(inserted_codes))


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


__all__ = ["IngestResult", "apply_to_latest_state", "ingest_vehicle_event"]
