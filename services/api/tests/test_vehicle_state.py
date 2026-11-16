"""Tests for ``GET /api/v1/vehicles/{id}/state`` (Phase 2 F6, api §3.5).

The endpoint must answer from ``vehicles.latest_state`` alone (E2): the
Smartcar client is wired to a recording transport and must see zero requests.
"""

# The Garage fixtures (api, db, smartcar, settings) are imported, and pytest
# injects them by parameter name, which ruff reads as redefinitions.
# ruff: noqa: F811

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.vehicles.vehicle_state import build_vehicle_state

# Fixtures and helpers shared with the Garage tests.
from tests.test_vehicle_garage import (  # noqa: F401
    OTHER_USER_ID,
    FakeSmartcar,
    _add_vehicle,
    _auth,
    _iso,
    api,
    db,
    settings,
    smartcar,
)

VEHICLES = "/api/v1/vehicles"
STALE_AFTER = timedelta(minutes=15)
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
VEHICLE_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")


def _entry(value: Any, unit: str | None, recorded_at: datetime) -> dict[str, Any]:
    return {"value": value, "unit": unit, "recorded_at": _iso(recorded_at)}


def _bz_state(recorded_at: datetime, received_at: datetime | None = None) -> dict[str, Any]:
    """Shape written by the F5 ingestor for the bZ (km, percent, no wattage)."""

    return {
        "recorded_at": _iso(recorded_at),
        "received_at": _iso(received_at or recorded_at + timedelta(seconds=8)),
        "signals": {
            "soc": _entry(72, "percent", recorded_at),
            "range": _entry(296, "km", recorded_at - timedelta(minutes=1)),
            "odometer": _entry(20082.5, "km", recorded_at - timedelta(minutes=2)),
            "charging_status": _entry(False, "bool", recorded_at),
        },
    }


def _build(latest_state: Any, *, is_simulated: bool = False) -> dict[str, Any]:
    state = build_vehicle_state(
        VEHICLE_ID, latest_state, is_simulated=is_simulated, stale_after=STALE_AFTER, now=NOW
    )
    return state.model_dump(mode="json")


# ---------------------------------------------------------------------------
# build_vehicle_state (pure)
# ---------------------------------------------------------------------------


def test_fresh_state_maps_signals_and_is_not_stale() -> None:
    recorded = NOW - timedelta(minutes=5)
    body = _build(_bz_state(recorded))

    assert body["is_stale"] is False
    assert body["source"] == "smartcar"
    assert body["recorded_at"] == _iso(recorded)
    assert body["received_at"] == _iso(recorded + timedelta(seconds=8))
    assert body["signals"] == {
        "soc_percent": 72,
        "range_km": 296,
        "odometer_km": 20082.5,
        "is_charging": False,
        # The bZ has no charge-wattage signal (F0): missing, not zero.
        "charging_power_kw": None,
    }
    assert body["signal_recorded_at"] == {
        "soc_percent": _iso(recorded),
        "range_km": _iso(recorded - timedelta(minutes=1)),
        "odometer_km": _iso(recorded - timedelta(minutes=2)),
        "is_charging": _iso(recorded),
    }


def test_old_state_is_stale() -> None:
    body = _build(_bz_state(NOW - STALE_AFTER - timedelta(seconds=1)))
    assert body["is_stale"] is True
    assert body["signals"]["soc_percent"] == 72


def test_state_exactly_at_threshold_is_not_stale() -> None:
    assert _build(_bz_state(NOW - STALE_AFTER))["is_stale"] is False


@pytest.mark.parametrize("latest_state", [None, {}, {"signals": {}}, "garbage", {"signals": []}])
def test_no_telemetry_is_empty_and_stale(latest_state: Any) -> None:
    body = _build(latest_state)
    assert body == {
        "vehicle_id": str(VEHICLE_ID),
        "recorded_at": None,
        "received_at": None,
        "source": None,
        "is_stale": True,
        "signals": {
            "soc_percent": None,
            "range_km": None,
            "odometer_km": None,
            "is_charging": None,
            "charging_power_kw": None,
        },
        "signal_recorded_at": {},
    }


def test_miles_are_converted_to_km() -> None:
    ts = NOW - timedelta(minutes=1)
    body = _build(
        {
            "recorded_at": _iso(ts),
            "signals": {
                "range": _entry(100, "miles", ts),
                "odometer": _entry(1000, "mi", ts),
            },
        }
    )
    assert body["signals"]["range_km"] == pytest.approx(160.934)
    assert body["signals"]["odometer_km"] == pytest.approx(1609.344)


def test_charging_power_watts_converted_to_kw() -> None:
    ts = NOW - timedelta(minutes=1)
    body = _build(
        {
            "recorded_at": _iso(ts),
            "signals": {
                "charging_status": _entry(True, "bool", ts),
                "charging_power": _entry(7200, "W", ts),
            },
        }
    )
    assert body["signals"]["is_charging"] is True
    assert body["signals"]["charging_power_kw"] == pytest.approx(7.2)


@pytest.mark.parametrize(
    ("code", "entry", "field"),
    [
        ("range", {"value": 296, "unit": "furlongs"}, "range_km"),
        ("range", {"value": 296, "unit": None}, "range_km"),
        ("odometer", {"value": "20082", "unit": "km"}, "odometer_km"),
        ("charging_power", {"value": 7, "unit": "hp"}, "charging_power_kw"),
        ("soc", {"value": 72, "unit": "fraction"}, "soc_percent"),
        ("soc", {"value": True, "unit": "percent"}, "soc_percent"),
        ("charging_status", {"value": 1, "unit": "bool"}, "is_charging"),
    ],
)
def test_unrecognised_units_or_values_are_null_not_guessed(
    code: str, entry: dict[str, Any], field: str
) -> None:
    ts = NOW - timedelta(minutes=1)
    body = _build({"recorded_at": _iso(ts), "signals": {code: {**entry, "recorded_at": _iso(ts)}}})
    assert body["signals"][field] is None
    assert field not in body["signal_recorded_at"]


def test_unmapped_signals_are_ignored() -> None:
    ts = NOW - timedelta(minutes=1)
    body = _build(
        {
            "recorded_at": _iso(ts),
            "signals": {
                "charge_limit": _entry(80, "percent", ts),
                "soc": _entry(50, "percent", ts),
            },
        }
    )
    assert body["signals"]["soc_percent"] == 50
    assert set(body["signal_recorded_at"]) == {"soc_percent"}


def test_simulated_vehicle_reports_simulated_source() -> None:
    body = _build(_bz_state(NOW - timedelta(minutes=1)), is_simulated=True)
    assert body["source"] == "simulated"


# ---------------------------------------------------------------------------
# GET /api/v1/vehicles/{id}/state
# ---------------------------------------------------------------------------


def test_state_requires_auth(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    response = api.get(f"{VEHICLES}/{vehicle.id}/state")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_state_served_from_cache_without_calling_smartcar(
    api: TestClient, db: Session, key_material: Any, smartcar: FakeSmartcar
) -> None:
    recorded = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=2)
    vehicle = _add_vehicle(db, latest_state=_bz_state(recorded))

    first = api.get(f"{VEHICLES}/{vehicle.id}/state", headers=_auth(key_material))
    # A reload returns the same values from the same cache (E2).
    second = api.get(f"{VEHICLES}/{vehicle.id}/state", headers=_auth(key_material))

    assert first.status_code == 200
    assert first.json() == second.json()
    body = first.json()
    assert body["vehicle_id"] == str(vehicle.id)
    assert body["is_stale"] is False
    assert body["recorded_at"] == _iso(recorded)
    assert body["signals"]["soc_percent"] == 72
    assert body["signals"]["range_km"] == 296
    assert smartcar.requests == []


def test_state_old_data_is_stale(api: TestClient, db: Session, key_material: Any) -> None:
    recorded = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    vehicle = _add_vehicle(db, latest_state=_bz_state(recorded))
    body = api.get(f"{VEHICLES}/{vehicle.id}/state", headers=_auth(key_material)).json()
    assert body["is_stale"] is True
    assert body["signals"]["soc_percent"] == 72


def test_state_no_telemetry_yet(api: TestClient, db: Session, key_material: Any) -> None:
    vehicle = _add_vehicle(db, latest_state=None)
    response = api.get(f"{VEHICLES}/{vehicle.id}/state", headers=_auth(key_material))
    assert response.status_code == 200
    body = response.json()
    assert body["is_stale"] is True
    assert body["recorded_at"] is None
    assert body["source"] is None
    assert all(v is None for v in body["signals"].values())
    assert body["signal_recorded_at"] == {}


def test_state_other_users_vehicle_is_404(api: TestClient, db: Session, key_material: Any) -> None:
    theirs = _add_vehicle(db, user_id=OTHER_USER_ID, latest_state=_bz_state(datetime.now(UTC)))
    response = api.get(f"{VEHICLES}/{theirs.id}/state", headers=_auth(key_material))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_state_unknown_vehicle_is_404(api: TestClient, key_material: Any) -> None:
    response = api.get(f"{VEHICLES}/{uuid.uuid4()}/state", headers=_auth(key_material))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_state_bad_id_is_validation_error(api: TestClient, key_material: Any) -> None:
    response = api.get(f"{VEHICLES}/not-a-uuid/state", headers=_auth(key_material))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_state_disconnected_vehicle_still_readable_by_owner(
    api: TestClient, db: Session, key_material: Any
) -> None:
    recorded = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    vehicle = _add_vehicle(db, status="disconnected", latest_state=_bz_state(recorded))
    response = api.get(f"{VEHICLES}/{vehicle.id}/state", headers=_auth(key_material))
    assert response.status_code == 200
    assert response.json()["signals"]["soc_percent"] == 72
