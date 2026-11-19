"""``/api/v1/observed-trips`` routes (contract §3.18-3.18d, Phase 5 F3)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.vehicles.models import Vehicle
from tests.trips.conftest import OTHER_USER_ID
from tests.trips.test_observed_service import T0, TWO_LEGS, add_readings, legs, run

BASE = "/api/v1/observed-trips"


@pytest.fixture()
def seeded(db: Session, add_vehicle: Callable[..., Vehicle]) -> Vehicle:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)
    run(db, vehicle)
    return vehicle


def test_list_newest_first_with_contract_fields(
    api: TestClient, auth: Callable[..., dict[str, str]], seeded: Vehicle
) -> None:
    res = api.get(BASE, headers=auth())
    assert res.status_code == 200
    body = res.json()
    assert body["next_cursor"] is None
    items = body["items"]
    assert [i["odometer_start_km"] for i in items] == [1010.0, 1000.0]
    first = items[1]
    assert first == {
        "id": first["id"],
        "vehicle_id": str(seeded.id),
        "start_time": "2026-11-09T08:00:00Z",
        "start_time_latest": "2026-11-09T08:00:00Z",
        "end_time": "2026-11-09T08:10:00Z",
        "start_time_is_exact": True,
        "distance_km": 10.0,
        "odometer_start_km": 1000.0,
        "odometer_end_km": 1010.0,
        "soc_start_percent": 80.0,
        "soc_start_recorded_at": "2026-11-09T08:00:00Z",
        "soc_end_percent": 75.0,
        "soc_end_recorded_at": "2026-11-09T08:10:00Z",
        "energy_kwh": 3.55,
        "energy_kwh_uncertainty": 0.71,
        "energy_source": "soc_inferred",
        "usable_capacity_kwh": 71.0,
        "capacity_source": "catalog",
        "quality_flag": "medium",
        "quality_reasons": ["soc_delta_small"],
        "review_status": "auto",
        "reviewed_at": None,
        "in_progress": False,
        "planned_trip_id": None,
        "plan_leg_index": None,
        "inference_version": "telemetry-v1",
        "is_simulated": False,
    }


def test_list_filters_and_cursor(
    api: TestClient, auth: Callable[..., dict[str, str]], seeded: Vehicle, db: Session
) -> None:
    a, b = legs(db, seeded)

    page1 = api.get(BASE, params={"limit": 1}, headers=auth()).json()
    assert [i["id"] for i in page1["items"]] == [str(b.id)]
    page2 = api.get(
        BASE, params={"limit": 1, "cursor": page1["next_cursor"]}, headers=auth()
    ).json()
    assert [i["id"] for i in page2["items"]] == [str(a.id)]
    assert page2["next_cursor"] is None

    window = {"from": "2026-11-09T08:05:00Z", "to": "2026-11-09T08:30:00Z"}
    assert [i["id"] for i in api.get(BASE, params=window, headers=auth()).json()["items"]] == [
        str(a.id)
    ]

    api.post(f"{BASE}/{a.id}/reject", headers=auth())
    default = api.get(BASE, params={"vehicle_id": str(seeded.id)}, headers=auth()).json()
    assert [i["id"] for i in default["items"]] == [str(b.id)]  # rejected hidden
    rejected = api.get(BASE, params={"review_status": "rejected"}, headers=auth()).json()
    assert [i["id"] for i in rejected["items"]] == [str(a.id)]
    both = api.get(
        BASE, params=[("review_status", "rejected"), ("review_status", "auto")], headers=auth()
    ).json()
    assert len(both["items"]) == 2


@pytest.mark.parametrize(
    "params",
    [{"cursor": "not-a-cursor"}, {"review_status": "bogus"}, {"limit": 0}, {"vehicle_id": "x"}],
)
def test_list_bad_params_are_422(
    api: TestClient, auth: Callable[..., dict[str, str]], params: dict[str, Any]
) -> None:
    res = api.get(BASE, params=params, headers=auth())
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_get_one_and_ownership(
    api: TestClient, auth: Callable[..., dict[str, str]], seeded: Vehicle, db: Session
) -> None:
    a, _ = legs(db, seeded)
    assert api.get(f"{BASE}/{a.id}", headers=auth()).json()["id"] == str(a.id)
    assert api.get(f"{BASE}/{uuid.uuid4()}", headers=auth()).status_code == 404
    assert api.get(f"{BASE}/not-a-uuid", headers=auth()).status_code == 422


def test_every_route_is_404_for_another_user(
    api: TestClient, auth: Callable[..., dict[str, str]], seeded: Vehicle, db: Session
) -> None:
    a, _ = legs(db, seeded)
    other = auth(OTHER_USER_ID)
    responses = [
        api.get(BASE, params={"vehicle_id": str(seeded.id)}, headers=other),
        api.get(f"{BASE}/{a.id}", headers=other),
        api.post(f"{BASE}/{a.id}/confirm", headers=other),
        api.post(f"{BASE}/{a.id}/reject", headers=other),
        api.post(f"{BASE}/recompute", json={"vehicle_id": str(seeded.id)}, headers=other),
    ]
    for res in responses:
        assert res.status_code == 404
        assert res.json()["error"]["code"] == "NOT_FOUND"
    # Their list is simply empty, and nothing changed.
    assert api.get(BASE, headers=other).json()["items"] == []
    assert legs(db, seeded)[0].review_status == "auto"


def test_routes_need_auth(api: TestClient) -> None:
    for method, path in (("get", BASE), ("post", f"{BASE}/recompute")):
        assert getattr(api, method)(path).status_code == 401


def test_confirm_is_idempotent_and_freezes(
    api: TestClient, auth: Callable[..., dict[str, str]], seeded: Vehicle, db: Session
) -> None:
    a, _ = legs(db, seeded)
    first = api.post(f"{BASE}/{a.id}/confirm", headers=auth())
    assert first.status_code == 200
    body = first.json()
    assert body["review_status"] == "confirmed"
    assert body["reviewed_at"] is not None
    assert body["energy_kwh"] == 3.55 and body["quality_flag"] == "medium"  # unchanged

    again = api.post(f"{BASE}/{a.id}/confirm", headers=auth()).json()
    assert again["reviewed_at"] == body["reviewed_at"]

    # A rejected leg can be confirmed and vice versa.
    rejected = api.post(f"{BASE}/{a.id}/reject", headers=auth()).json()
    assert rejected["review_status"] == "rejected"
    assert api.post(f"{BASE}/{a.id}/confirm", headers=auth()).json()["review_status"] == (
        "confirmed"
    )


def test_reject_unmatches_the_plan(
    api: TestClient,
    auth: Callable[..., dict[str, str]],
    seeded: Vehicle,
    db: Session,
    add_trip: Callable[..., uuid.UUID],
) -> None:
    plan_id = add_trip(seeded)
    a, b = legs(db, seeded)
    a.planned_trip_id = plan_id
    b.planned_trip_id = plan_id
    db.commit()

    items = api.get(BASE, headers=auth()).json()["items"]
    assert {i["id"]: i["plan_leg_index"] for i in items} == {str(a.id): 1, str(b.id): 2}

    body = api.post(f"{BASE}/{a.id}/reject", headers=auth()).json()
    assert body["planned_trip_id"] is None
    assert body["plan_leg_index"] is None
    # B is now the plan's only leg.
    assert api.get(f"{BASE}/{b.id}", headers=auth()).json()["plan_leg_index"] == 1
    assert api.post(f"{BASE}/{a.id}/reject", headers=auth()).status_code == 200


def test_recompute(
    api: TestClient,
    auth: Callable[..., dict[str, str]],
    db: Session,
    add_vehicle: Callable[..., Vehicle],
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, TWO_LEGS)

    res = api.post(f"{BASE}/recompute", json={"vehicle_id": str(vehicle.id)}, headers=auth())
    assert res.status_code == 200
    assert res.json() == {
        "vehicle_id": str(vehicle.id),
        "legs_created": 2,
        "legs_updated": 0,
        "legs_deleted": 0,
        "legs_unchanged": 0,
        "plans_matched": 0,
        "dropped_simulated_readings": 0,
    }
    again = api.post(
        f"{BASE}/recompute", json={"vehicle_id": str(vehicle.id), "full": True}, headers=auth()
    ).json()
    assert (again["legs_created"], again["legs_unchanged"]) == (0, 2)

    bad = api.post(f"{BASE}/recompute", json={"vehicle_id": "nope"}, headers=auth())
    assert bad.status_code == 422
    missing = api.post(f"{BASE}/recompute", json={"vehicle_id": str(uuid.uuid4())}, headers=auth())
    assert missing.status_code == 404


def test_in_progress_and_bounded_start(
    api: TestClient,
    auth: Callable[..., dict[str, str]],
    db: Session,
    add_vehicle: Callable[..., Vehicle],
) -> None:
    vehicle = add_vehicle()
    add_readings(db, vehicle, [(0, 1000.0, 80, False), (10, 1010.0, 75, None)])
    run(db, vehicle)
    (leg,) = legs(db, vehicle)
    assert leg.closed is False

    # Its last reading is days old, so it isn't in progress any more (D14) …
    assert api.get(f"{BASE}/{leg.id}", headers=auth()).json()["in_progress"] is False

    # … but a leg whose last reading is recent is.
    recent = datetime.now(UTC) - timedelta(minutes=5)
    leg.end_time = recent
    leg.start_time = leg.start_time_latest = recent - timedelta(minutes=10)
    db.commit()
    assert api.get(f"{BASE}/{leg.id}", headers=auth()).json()["in_progress"] is True

    leg.start_time = T0 - timedelta(hours=1)
    db.commit()
    body = api.get(f"{BASE}/{leg.id}", headers=auth()).json()
    assert body["start_time_is_exact"] is False


def test_observed_routes_dont_collide_with_planned_trips(
    api: TestClient, auth: Callable[..., dict[str, str]], seeded: Vehicle
) -> None:
    # /trips/{id} stays the planned-trips route; /observed-trips is ours.
    assert api.get("/api/v1/trips/observed", headers=auth()).status_code == 422
    assert api.get(BASE, headers=auth()).status_code == 200
