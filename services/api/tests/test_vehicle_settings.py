"""Tests for Phase 2 F7: primary vehicle + spec editing.

``PATCH /api/v1/me/preferences`` (api §3.1a) and
``PATCH /api/v1/vehicles/{id}/specs`` (api §3.4a), plus the rule that
disconnecting the primary vehicle clears it (api §3.6).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.modules.users.models import UserPreferences
from app.modules.vehicles.garage import is_connected_vehicle
from app.modules.vehicles.models import VehicleSpecs
from tests import test_vehicle_garage as garage
from tests.conftest import TEST_USER_ID
from tests.test_vehicle_garage import OTHER_USER_ID, VEHICLES, _add_vehicle, _auth

# Reuse the Garage tests' fixtures (real app + DB, fake Smartcar transport).
api = garage.api
db = garage.db
settings = garage.settings
smartcar = garage.smartcar

PREFS = "/api/v1/me/preferences"

# "bZ XLE" (the test vehicle) has no catalog entry, so no baseline at all.
NO_BASELINE = {
    "baseline_kwh_per_km": None,
    "catalog_baseline_kwh_per_km": None,
    "baseline_source": None,
}


def _specs_url(vehicle_id: object) -> str:
    return f"{VEHICLES}/{vehicle_id}/specs"


def _prefs(db: Session) -> UserPreferences:
    db.expire_all()
    prefs = db.get(UserPreferences, TEST_USER_ID)
    assert prefs is not None
    return prefs


def _error_code(response: Any) -> str:
    return str(response.json()["error"]["code"])


# ---------------------------------------------------------------------------
# PATCH /api/v1/me/preferences
# ---------------------------------------------------------------------------


def test_prefs_requires_auth(api: TestClient) -> None:
    response = api.patch(PREFS, json={"units": "metric"})
    assert response.status_code == 401
    assert _error_code(response) == "UNAUTHENTICATED"


def test_prefs_partial_update_returns_full_preferences(
    api: TestClient, db: Session, key_material: Any
) -> None:
    response = api.patch(
        PREFS,
        json={"units": "metric", "max_detour_minutes": 30, "preferred_networks": [" EVgo "]},
        headers=_auth(key_material),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["units"] == "metric"
    assert body["max_detour_minutes"] == 30
    assert body["preferred_networks"] == ["EVgo"]
    # Untouched keys keep their defaults.
    assert body["reserve_soc"] == pytest.approx(0.15)
    assert body["preferred_charge_to"] == pytest.approx(0.80)
    assert body["primary_vehicle_id"] is None
    assert _prefs(db).units == "metric"


def test_prefs_empty_body_is_noop(api: TestClient, key_material: Any) -> None:
    response = api.patch(PREFS, json={}, headers=_auth(key_material))
    assert response.status_code == 200
    assert response.json()["units"] == "imperial"


def test_set_primary_vehicle_and_me_follows(
    api: TestClient, db: Session, key_material: Any
) -> None:
    _add_vehicle(db)
    second = _add_vehicle(db)

    response = api.patch(
        PREFS, json={"primary_vehicle_id": str(second.id)}, headers=_auth(key_material)
    )
    assert response.status_code == 200
    assert response.json()["primary_vehicle_id"] == str(second.id)

    # The Dashboard reads the primary from GET /me.
    me = api.get("/api/v1/me", headers=_auth(key_material)).json()
    assert me["preferences"]["primary_vehicle_id"] == str(second.id)


def test_clear_primary_vehicle_with_null(api: TestClient, db: Session, key_material: Any) -> None:
    vehicle = _add_vehicle(db)
    api.patch(PREFS, json={"primary_vehicle_id": str(vehicle.id)}, headers=_auth(key_material))

    response = api.patch(PREFS, json={"primary_vehicle_id": None}, headers=_auth(key_material))
    assert response.status_code == 200
    assert response.json()["primary_vehicle_id"] is None
    assert _prefs(db).primary_vehicle_id is None


def test_primary_other_users_vehicle_is_404(
    api: TestClient, db: Session, key_material: Any
) -> None:
    theirs = _add_vehicle(db, user_id=OTHER_USER_ID)
    response = api.patch(
        PREFS, json={"primary_vehicle_id": str(theirs.id)}, headers=_auth(key_material)
    )
    assert response.status_code == 404
    assert _error_code(response) == "NOT_FOUND"


def test_primary_disconnected_vehicle_is_404(
    api: TestClient, db: Session, key_material: Any
) -> None:
    gone = _add_vehicle(db, status="disconnected")
    response = api.patch(
        PREFS, json={"primary_vehicle_id": str(gone.id)}, headers=_auth(key_material)
    )
    assert response.status_code == 404


def test_primary_unknown_vehicle_is_404_and_nothing_changes(
    api: TestClient, db: Session, key_material: Any
) -> None:
    api.get("/api/v1/me", headers=_auth(key_material))  # seed the preferences row
    response = api.patch(
        PREFS,
        json={"units": "metric", "primary_vehicle_id": "00000000-0000-0000-0000-000000000000"},
        headers=_auth(key_material),
    )
    assert response.status_code == 404
    assert _prefs(db).units == "imperial"


@pytest.mark.parametrize(
    "body",
    [
        {"units": "furlongs"},
        {"reserve_soc": -0.1},
        {"reserve_soc": 1.5},
        {"preferred_charge_to": 2},
        {"reserve_soc": 0.7999},  # numeric(4, 3) would round it to 0.800
        {"preferred_charge_to": 0.8501},
        {"max_detour_minutes": -1},
        {"max_detour_minutes": 241},
        {"preferred_networks": [""]},
        {"preferred_networks": ["EVgo", "evgo"]},
        {"preferred_networks": ["x" * 101]},
        {"preferred_networks": [f"n{i}" for i in range(21)]},
        {"units": None},
        {"reserve_soc": None},
        {"primary_vehicle_id": "not-a-uuid"},
        {"surprise": True},
        {"baseline_kwh_per_km": 0},
        {"baseline_kwh_per_km": -0.1},
        {"baseline_kwh_per_km": 1.001},
        {"baseline_kwh_per_km": 0.1855},
        {"baseline_kwh_per_km": "thirsty"},
        {"catalog_baseline_kwh_per_km": 0.2},
        {"baseline_source": "vehicle"},
    ],
)
def test_prefs_validation_errors(
    api: TestClient, db: Session, key_material: Any, body: dict[str, Any]
) -> None:
    response = api.patch(PREFS, json=body, headers=_auth(key_material))
    assert response.status_code == 422
    assert _error_code(response) == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "body",
    [
        {"reserve_soc": 0.8},  # equal to the stored 0.80 charge-to
        {"preferred_charge_to": 0.1},  # below the stored 0.15 reserve
        {"reserve_soc": 0.5, "preferred_charge_to": 0.4},
    ],
)
def test_prefs_reserve_must_be_below_charge_to(
    api: TestClient, db: Session, key_material: Any, body: dict[str, Any]
) -> None:
    api.get("/api/v1/me", headers=_auth(key_material))  # seed the preferences row
    response = api.patch(PREFS, json=body, headers=_auth(key_material))
    assert response.status_code == 422
    assert _error_code(response) == "VALIDATION_ERROR"
    prefs = _prefs(db)
    assert float(prefs.reserve_soc) == pytest.approx(0.15)
    assert float(prefs.preferred_charge_to) == pytest.approx(0.80)


# ---------------------------------------------------------------------------
# Disconnect clears the primary
# ---------------------------------------------------------------------------


def test_disconnect_primary_clears_it(api: TestClient, db: Session, key_material: Any) -> None:
    primary = _add_vehicle(db)
    api.patch(PREFS, json={"primary_vehicle_id": str(primary.id)}, headers=_auth(key_material))

    response = api.delete(f"{VEHICLES}/{primary.id}", headers=_auth(key_material))
    assert response.status_code == 204
    assert _prefs(db).primary_vehicle_id is None


def test_disconnect_other_vehicle_keeps_primary(
    api: TestClient, db: Session, key_material: Any
) -> None:
    primary = _add_vehicle(db)
    other = _add_vehicle(db)
    api.patch(PREFS, json={"primary_vehicle_id": str(primary.id)}, headers=_auth(key_material))

    api.delete(f"{VEHICLES}/{other.id}", headers=_auth(key_material))
    assert _prefs(db).primary_vehicle_id == primary.id


# ---------------------------------------------------------------------------
# PATCH /api/v1/vehicles/{id}/specs
# ---------------------------------------------------------------------------


def test_specs_requires_auth(api: TestClient, db: Session) -> None:
    vehicle = _add_vehicle(db)
    response = api.patch(_specs_url(vehicle.id), json={"connectors": []})
    assert response.status_code == 401


def test_specs_full_update(api: TestClient, db: Session, key_material: Any) -> None:
    vehicle = _add_vehicle(db)
    body = {
        "usable_capacity_kwh": 68.25,
        "connectors": ["NACS", "CCS1", "J1772"],
        "charging_curve": [
            {"soc": 0.0, "power_kw": 100},
            {"soc": 0.5, "power_kw": 120.5},
            {"soc": 1.0, "power_kw": 7},
        ],
        "baseline_kwh_per_km": 0.185,
    }
    expected = {**body, "catalog_baseline_kwh_per_km": None, "baseline_source": "vehicle"}
    response = api.patch(_specs_url(vehicle.id), json=body, headers=_auth(key_material))
    assert response.status_code == 200
    assert response.json() == expected

    detail = api.get(f"{VEHICLES}/{vehicle.id}", headers=_auth(key_material)).json()
    assert detail["specs"] == expected


def test_specs_partial_update_keeps_other_fields(
    api: TestClient, db: Session, key_material: Any
) -> None:
    vehicle = _add_vehicle(db)
    response = api.patch(
        _specs_url(vehicle.id), json={"connectors": ["CCS1"]}, headers=_auth(key_material)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["connectors"] == ["CCS1"]
    assert body["usable_capacity_kwh"] == pytest.approx(71.4)
    assert len(body["charging_curve"]) == 2


def test_specs_clear_values(api: TestClient, db: Session, key_material: Any) -> None:
    vehicle = _add_vehicle(db)
    response = api.patch(
        _specs_url(vehicle.id),
        json={"usable_capacity_kwh": None, "connectors": [], "charging_curve": []},
        headers=_auth(key_material),
    )
    assert response.status_code == 200
    assert response.json() == {
        "usable_capacity_kwh": None,
        "connectors": [],
        "charging_curve": [],
        **NO_BASELINE,
    }


def test_specs_creates_row_when_missing(api: TestClient, db: Session, key_material: Any) -> None:
    vehicle = _add_vehicle(db, with_specs=False)
    response = api.patch(
        _specs_url(vehicle.id), json={"usable_capacity_kwh": 70}, headers=_auth(key_material)
    )
    assert response.status_code == 200
    assert response.json() == {
        "usable_capacity_kwh": 70.0,
        "connectors": [],
        "charging_curve": [],
        **NO_BASELINE,
    }
    db.expire_all()
    row = db.get(VehicleSpecs, vehicle.id)
    assert row is not None
    assert row.usable_capacity_kwh == Decimal("70.00")


def test_specs_editable_on_disconnected_vehicle(
    api: TestClient, db: Session, key_material: Any
) -> None:
    vehicle = _add_vehicle(db, status="disconnected")
    response = api.patch(
        _specs_url(vehicle.id), json={"connectors": ["J1772"]}, headers=_auth(key_material)
    )
    assert response.status_code == 200


def test_specs_other_users_vehicle_is_404_and_untouched(
    api: TestClient, db: Session, key_material: Any
) -> None:
    theirs = _add_vehicle(db, user_id=OTHER_USER_ID)
    response = api.patch(
        _specs_url(theirs.id), json={"connectors": []}, headers=_auth(key_material)
    )
    assert response.status_code == 404
    assert _error_code(response) == "NOT_FOUND"
    db.expire_all()
    row = db.get(VehicleSpecs, theirs.id)
    assert row is not None
    assert row.connectors == ["NACS"]


def test_specs_unknown_vehicle_is_404(api: TestClient, key_material: Any) -> None:
    response = api.patch(
        _specs_url("00000000-0000-0000-0000-000000000000"),
        json={"connectors": []},
        headers=_auth(key_material),
    )
    assert response.status_code == 404


def test_specs_non_uuid_is_validation_error(api: TestClient, key_material: Any) -> None:
    response = api.patch(_specs_url("abc"), json={}, headers=_auth(key_material))
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"usable_capacity_kwh": 0},
        {"usable_capacity_kwh": -5},
        {"usable_capacity_kwh": 300.01},
        {"usable_capacity_kwh": 70.123},
        {"usable_capacity_kwh": "lots"},
        {"connectors": ["Type2"]},
        {"connectors": ["NACS", "NACS"]},
        {"connectors": None},
        {"charging_curve": None},
        {"charging_curve": [{"soc": 1.1, "power_kw": 50}]},
        {"charging_curve": [{"soc": -0.1, "power_kw": 50}]},
        {"charging_curve": [{"soc": 0.5, "power_kw": 0}]},
        {"charging_curve": [{"soc": 0.5, "power_kw": 1000.5}]},
        {"charging_curve": [{"soc": 0.5, "power_kw": 50}, {"soc": 0.5, "power_kw": 40}]},
        {"charging_curve": [{"soc": 0.8, "power_kw": 50}, {"soc": 0.2, "power_kw": 40}]},
        {"charging_curve": [{"soc": i / 100, "power_kw": 50} for i in range(21)]},
        {"charging_curve": [{"soc": 0.5, "power_kw": 50, "extra": 1}]},
        {"surprise": True},
        {"baseline_kwh_per_km": 0},
        {"baseline_kwh_per_km": -0.1},
        {"baseline_kwh_per_km": 1.001},
        {"baseline_kwh_per_km": 0.1855},
        {"baseline_kwh_per_km": "thirsty"},
        {"catalog_baseline_kwh_per_km": 0.2},
        {"baseline_source": "vehicle"},
    ],
)
def test_specs_validation_errors(
    api: TestClient, db: Session, key_material: Any, body: dict[str, Any]
) -> None:
    vehicle = _add_vehicle(db)
    response = api.patch(_specs_url(vehicle.id), json=body, headers=_auth(key_material))
    assert response.status_code == 422, body
    assert _error_code(response) == "VALIDATION_ERROR"
    db.expire_all()
    row = db.get(VehicleSpecs, vehicle.id)
    assert row is not None
    assert row.connectors == ["NACS"]


def test_primary_check_locks_the_vehicle_row(db: Session) -> None:
    """The check must take the same row lock as disconnect, so the two serialize."""

    vehicle = _add_vehicle(db)
    statements: list[str] = []

    def _capture(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        assert is_connected_vehicle(db, user_id=TEST_USER_ID, vehicle_id=vehicle.id)
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    assert any("FOR UPDATE" in s for s in statements)


# ---------------------------------------------------------------------------
# baseline_kwh_per_km (phase4_plan F8)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0.001, 0.2, 1, 1.0])
def test_specs_baseline_bounds_accepted(
    api: TestClient, db: Session, key_material: Any, value: float
) -> None:
    vehicle = _add_vehicle(db)
    response = api.patch(
        _specs_url(vehicle.id), json={"baseline_kwh_per_km": value}, headers=_auth(key_material)
    )
    assert response.status_code == 200
    assert response.json()["baseline_kwh_per_km"] == pytest.approx(value)
    db.expire_all()
    row = db.get(VehicleSpecs, vehicle.id)
    assert row is not None
    assert row.baseline_kwh_per_km == Decimal(str(value)).quantize(Decimal("0.001"))


def test_specs_baseline_partial_update_keeps_other_fields(
    api: TestClient, db: Session, key_material: Any
) -> None:
    vehicle = _add_vehicle(db)
    response = api.patch(
        _specs_url(vehicle.id), json={"baseline_kwh_per_km": 0.19}, headers=_auth(key_material)
    )
    body = response.json()
    assert body["usable_capacity_kwh"] == pytest.approx(71.4)
    assert body["connectors"] == ["NACS"]
    assert len(body["charging_curve"]) == 2
    # And other fields leave the baseline alone.
    response = api.patch(
        _specs_url(vehicle.id), json={"connectors": ["CCS1"]}, headers=_auth(key_material)
    )
    assert response.json()["baseline_kwh_per_km"] == pytest.approx(0.19)


def test_specs_baseline_null_clears_back_to_catalog(
    api: TestClient, db: Session, key_material: Any
) -> None:
    vehicle = _add_vehicle(db, model="bZ XLE AWD")
    set_response = api.patch(
        _specs_url(vehicle.id), json={"baseline_kwh_per_km": 0.25}, headers=_auth(key_material)
    )
    assert set_response.json()["baseline_source"] == "vehicle"
    response = api.patch(
        _specs_url(vehicle.id), json={"baseline_kwh_per_km": None}, headers=_auth(key_material)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["baseline_kwh_per_km"] is None
    assert body["catalog_baseline_kwh_per_km"] == pytest.approx(0.2)
    assert body["baseline_source"] == "catalog"
    db.expire_all()
    row = db.get(VehicleSpecs, vehicle.id)
    assert row is not None
    assert row.baseline_kwh_per_km is None
