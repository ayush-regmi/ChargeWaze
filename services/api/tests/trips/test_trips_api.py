"""``/api/v1/trips`` routes (api-contracts §3.8, §3.9, §3.9a, §3.9b)."""

from __future__ import annotations

import base64
import uuid
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.modules.trips.models import PlannedTrip
from app.modules.trips.service import NewPlannedStop, encode_cursor
from app.modules.vehicles.models import Vehicle
from tests.trips.conftest import (
    BASE_TIME,
    INFEASIBLE_PAYLOAD,
    NO_ROUTE_PAYLOAD,
    OTHER_USER_ID,
)

AddTrip = Callable[..., uuid.UUID]
Auth = Callable[..., dict[str, str]]


# ---- Auth and ownership -------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/trips"),
        ("GET", f"/api/v1/trips/{uuid.uuid4()}"),
        ("DELETE", f"/api/v1/trips/{uuid.uuid4()}"),
        ("POST", f"/api/v1/trips/{uuid.uuid4()}/save"),
    ],
)
def test_routes_require_auth(api: TestClient, method: str, path: str) -> None:
    response = api.request(method, path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize(("method", "suffix"), [("GET", ""), ("DELETE", ""), ("POST", "/save")])
def test_other_users_trip_is_not_found(
    api: TestClient,
    auth: Auth,
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_trip: AddTrip,
    method: str,
    suffix: str,
) -> None:
    theirs = add_trip(add_vehicle(user_id=OTHER_USER_ID), saved_minutes=1)
    add_vehicle()  # the caller exists too

    response = api.request(method, f"/api/v1/trips/{theirs}{suffix}", headers=auth())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    db.expire_all()
    trip = db.get(PlannedTrip, theirs)
    assert trip is not None  # not deleted
    assert trip.saved_at == BASE_TIME.replace(minute=1)  # not touched


@pytest.mark.parametrize(("method", "suffix"), [("GET", ""), ("DELETE", ""), ("POST", "/save")])
def test_unknown_trip_is_not_found(api: TestClient, auth: Auth, method: str, suffix: str) -> None:
    response = api.request(method, f"/api/v1/trips/{uuid.uuid4()}{suffix}", headers=auth())
    assert response.status_code == 404


@pytest.mark.parametrize(("method", "suffix"), [("GET", ""), ("DELETE", ""), ("POST", "/save")])
def test_non_uuid_id_is_validation_error(
    api: TestClient, auth: Auth, method: str, suffix: str
) -> None:
    response = api.request(method, f"/api/v1/trips/not-a-uuid{suffix}", headers=auth())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_list_never_shows_other_users_trips(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_trip: AddTrip
) -> None:
    add_trip(add_vehicle(user_id=OTHER_USER_ID), saved_minutes=1)
    mine = add_trip(add_vehicle(), saved_minutes=2)

    for saved in ("true", "false"):
        body = api.get(f"/api/v1/trips?saved={saved}", headers=auth()).json()
        expected = [str(mine)] if saved == "true" else []
        assert [i["id"] for i in body["items"]] == expected


def test_list_with_other_users_vehicle_is_not_found(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_trip: AddTrip
) -> None:
    theirs = add_vehicle(user_id=OTHER_USER_ID)
    add_trip(theirs, saved_minutes=1)
    add_vehicle()

    response = api.get(f"/api/v1/trips?vehicle_id={theirs.id}", headers=auth())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# ---- GET /trips --------------------------------------------------------------


def test_list_item_shape(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_trip: AddTrip
) -> None:
    vehicle = add_vehicle()
    tid = add_trip(
        vehicle,
        saved_minutes=2,
        stops=[NewPlannedStop(i, None, 0.2, 0.8, 10) for i in (1, 2, 3)],
    )

    body = api.get("/api/v1/trips", headers=auth()).json()

    assert body == {
        "items": [
            {
                "id": str(tid),
                "vehicle_id": str(vehicle.id),
                "origin_label": "Frederick, MD",
                "destination_label": "Toronto, ON",
                "status": "planned",
                "plan_status": "feasible",
                "distance_km": 748.1,
                "total_minutes": 583,
                "stop_count": 3,
                "created_at": "2026-11-06T18:00:00Z",
                "saved_at": "2026-11-06T18:02:00Z",
            }
        ],
        "next_cursor": None,
    }


def test_infeasible_items_have_null_totals(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_trip: AddTrip
) -> None:
    vehicle = add_vehicle()
    gap = add_trip(vehicle, saved_minutes=2, plan_status="infeasible", payload=INFEASIBLE_PAYLOAD)
    no_route = add_trip(
        vehicle, saved_minutes=1, plan_status="infeasible", payload=NO_ROUTE_PAYLOAD
    )

    items = api.get("/api/v1/trips", headers=auth()).json()["items"]

    by_id = {i["id"]: i for i in items}
    assert by_id[str(gap)]["plan_status"] == "infeasible"
    assert (by_id[str(gap)]["distance_km"], by_id[str(gap)]["total_minutes"]) == (748.1, None)
    assert (by_id[str(no_route)]["distance_km"], by_id[str(no_route)]["total_minutes"]) == (
        None,
        None,
    )


def test_saved_default_and_unsaved_lists_are_disjoint(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_trip: AddTrip
) -> None:
    vehicle = add_vehicle()
    saved = add_trip(vehicle, minutes=0, saved_minutes=10)
    unsaved_old = add_trip(vehicle, minutes=1)
    unsaved_new = add_trip(vehicle, minutes=2)

    default = api.get("/api/v1/trips", headers=auth()).json()["items"]
    unsaved = api.get("/api/v1/trips?saved=false", headers=auth()).json()["items"]

    assert [i["id"] for i in default] == [str(saved)]
    assert [i["id"] for i in unsaved] == [str(unsaved_new), str(unsaved_old)]
    assert all(i["saved_at"] is None for i in unsaved)


def test_saved_list_orders_by_saved_at_not_created_at(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_trip: AddTrip
) -> None:
    vehicle = add_vehicle()
    created_first_saved_last = add_trip(vehicle, minutes=0, saved_minutes=30)
    created_last_saved_first = add_trip(vehicle, minutes=10, saved_minutes=20)

    items = api.get("/api/v1/trips", headers=auth()).json()["items"]

    assert [i["id"] for i in items] == [
        str(created_first_saved_last),
        str(created_last_saved_first),
    ]


def test_filters_by_vehicle_and_status(
    api: TestClient,
    auth: Auth,
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_trip: AddTrip,
) -> None:
    car_a, car_b = add_vehicle(), add_vehicle()
    a = add_trip(car_a, saved_minutes=1)
    b = add_trip(car_b, saved_minutes=2)
    trip_b = db.get(PlannedTrip, b)
    assert trip_b is not None
    trip_b.status = "completed"
    db.commit()

    by_vehicle = api.get(f"/api/v1/trips?vehicle_id={car_a.id}", headers=auth()).json()
    by_status = api.get("/api/v1/trips?status=completed", headers=auth()).json()

    assert [i["id"] for i in by_vehicle["items"]] == [str(a)]
    assert [i["id"] for i in by_status["items"]] == [str(b)]


@pytest.mark.parametrize("saved", [True, False])
def test_cursor_pagination_is_stable_and_ends_with_null(
    api: TestClient,
    auth: Auth,
    add_vehicle: Callable[..., Vehicle],
    add_trip: AddTrip,
    saved: bool,
) -> None:
    vehicle = add_vehicle()
    # Three pairs share a timestamp, so ties are broken by id.
    ids = [
        add_trip(vehicle, minutes=m, saved_minutes=m if saved else None)
        for m in (0, 0, 1, 1, 2, 2, 3)
    ]
    expected = [
        str(i)
        for _, i in sorted(
            zip((0, 0, 1, 1, 2, 2, 3), ids, strict=True), key=lambda p: (-p[0], str(p[1]))
        )
    ]

    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        url = f"/api/v1/trips?saved={str(saved).lower()}&limit=3"
        if cursor:
            url += f"&cursor={cursor}"
        response = api.get(url, headers=auth())
        assert response.status_code == 200
        body = response.json()
        seen += [i["id"] for i in body["items"]]
        pages += 1
        cursor = body["next_cursor"]
        if cursor is None:
            break

    assert seen == expected
    assert pages == 3  # 3 + 3 + 1


def test_exact_page_boundary_has_null_cursor(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_trip: AddTrip
) -> None:
    vehicle = add_vehicle()
    for m in range(2):
        add_trip(vehicle, minutes=m, saved_minutes=m)
    body = api.get("/api/v1/trips?limit=2", headers=auth()).json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is None


@pytest.mark.parametrize(
    "cursor",
    [
        "not-base64!!",
        base64.urlsafe_b64encode(b"[1,2]").decode(),
        base64.urlsafe_b64encode(b'{"m":"saved","k":"yesterday","i":"x"}').decode(),
        # A valid cursor for the unsaved list used on the saved list.
        encode_cursor(mode="unsaved", key=BASE_TIME, trip_id=uuid.uuid4()),
    ],
)
def test_bad_cursor_is_validation_error(api: TestClient, auth: Auth, cursor: str) -> None:
    response = api.get(f"/api/v1/trips?cursor={cursor}", headers=auth())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize("query", ["limit=0", "limit=201", "status=lost", "saved=maybe"])
def test_bad_params_are_validation_errors(api: TestClient, auth: Auth, query: str) -> None:
    response = api.get(f"/api/v1/trips?{query}", headers=auth())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


# ---- GET /trips/{id} ---------------------------------------------------------


def test_get_returns_stored_plan_unchanged(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_trip: AddTrip
) -> None:
    vehicle = add_vehicle()
    tid = add_trip(vehicle, plan_status="infeasible", payload=INFEASIBLE_PAYLOAD)

    response = api.get(f"/api/v1/trips/{tid}", headers=auth())

    assert response.status_code == 200
    assert response.json() == {
        "id": str(tid),
        "vehicle_id": str(vehicle.id),
        "status": "planned",
        "plan_status": "infeasible",
        "created_at": "2026-11-06T18:00:00Z",
        "saved_at": None,
        "plan": {**INFEASIBLE_PAYLOAD, "trip_id": str(tid)},
        "observation": None,
    }


# ---- POST /trips/{id}/save ---------------------------------------------------


def test_save_is_idempotent_and_keeps_first_saved_at(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_trip: AddTrip
) -> None:
    tid = add_trip(add_vehicle(), plan_status="infeasible", payload=INFEASIBLE_PAYLOAD)

    first = api.post(f"/api/v1/trips/{tid}/save", headers=auth())
    second = api.post(f"/api/v1/trips/{tid}/save", headers=auth())

    assert first.status_code == second.status_code == 200
    assert first.json()["saved_at"] is not None
    assert second.json() == first.json()
    assert first.json()["plan_status"] == "infeasible"  # infeasible plans can be saved
    listed = api.get("/api/v1/trips", headers=auth()).json()["items"]
    assert listed == [first.json()]
    assert api.get("/api/v1/trips?saved=false", headers=auth()).json()["items"] == []


def test_save_keeps_existing_saved_at(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_trip: AddTrip
) -> None:
    tid = add_trip(add_vehicle(), saved_minutes=5)
    body = api.post(f"/api/v1/trips/{tid}/save", headers=auth()).json()
    assert body["saved_at"] == "2026-11-06T18:05:00Z"


# ---- DELETE /trips/{id} ------------------------------------------------------


def test_delete_then_get_is_not_found(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_trip: AddTrip
) -> None:
    tid = add_trip(add_vehicle(), stops=[NewPlannedStop(1, None, 0.2, 0.8, 10)])

    deleted = api.delete(f"/api/v1/trips/{tid}", headers=auth())

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert api.get(f"/api/v1/trips/{tid}", headers=auth()).status_code == 404
    assert api.delete(f"/api/v1/trips/{tid}", headers=auth()).status_code == 404
