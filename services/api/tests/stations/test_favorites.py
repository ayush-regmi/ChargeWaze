"""Favorite stations (phase3_plan.md F8, api-contracts §3.19 to §3.19b).

Idempotent PUT/DELETE, 404 for unknown stations, per-user isolation, cascades
on account and station deletion, ordering, compatibility and the 500 cap.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.stations import favorites
from app.modules.stations.favorites_models import FavoriteStation
from app.modules.stations.models import ChargingStation
from app.modules.users.models import Profile, UserPreferences
from tests.conftest import TEST_USER_ID
from tests.stations.conftest import OTHER_USER_ID, FakeAfdc

STATIONS = "/api/v1/stations"
FAVORITES = f"{STATIONS}/favorites"
FREDERICK = {"lat": 39.4143, "lon": -77.4105}
Auth = Callable[..., dict[str, str]]


def _fav(station_id: str) -> str:
    return f"{STATIONS}/{station_id}/favorite"


@pytest.fixture()
def stations(api: TestClient, auth: Auth, afdc: FakeAfdc) -> list[dict[str, Any]]:
    """Cache the Frederick fixture's stations through a search; nearest first."""

    response = api.get(STATIONS, params={**FREDERICK, "limit": 5}, headers=auth())
    assert response.status_code == 200, response.text
    items: list[dict[str, Any]] = response.json()["items"]
    assert len(items) >= 3
    afdc.requests.clear()
    return items


def _favorite_ids(api: TestClient, auth: Auth, user_id: uuid.UUID = TEST_USER_ID) -> list[str]:
    response = api.get(FAVORITES, headers=auth(user_id))
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def _row_count(db: Session, user_id: uuid.UUID = TEST_USER_ID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(FavoriteStation)
            .where(FavoriteStation.user_id == user_id)
        )
        or 0
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", FAVORITES), ("PUT", _fav(str(uuid.uuid4()))), ("DELETE", _fav(str(uuid.uuid4())))],
)
def test_endpoints_require_auth(api: TestClient, method: str, path: str) -> None:
    response = api.request(method, path)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_empty_list(api: TestClient, auth: Auth) -> None:
    response = api.get(FAVORITES, headers=auth())
    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_put_is_idempotent_and_keeps_the_first_timestamp(
    api: TestClient, auth: Auth, afdc: FakeAfdc, db: Session, stations: list[dict[str, Any]]
) -> None:
    station = stations[0]
    first = api.put(_fav(station["id"]), headers=auth())
    assert first.status_code == 204
    assert first.content == b""
    [item] = api.get(FAVORITES, headers=auth()).json()["items"]
    again = api.put(_fav(station["id"]), headers=auth())
    assert again.status_code == 204
    [same] = api.get(FAVORITES, headers=auth()).json()["items"]

    assert _row_count(db) == 1
    assert same["favorited_at"] == item["favorited_at"]
    assert item["id"] == station["id"]
    assert item["name"] == station["name"]
    assert item["connectors"] == station["connectors"]
    assert item["is_stale"] is False
    assert item["live_availability"] is None
    # The list reads the cache only.
    assert afdc.requests == []


def test_delete_is_idempotent(
    api: TestClient, auth: Auth, db: Session, stations: list[dict[str, Any]]
) -> None:
    station_id = stations[0]["id"]
    assert api.put(_fav(station_id), headers=auth()).status_code == 204
    assert api.delete(_fav(station_id), headers=auth()).status_code == 204
    assert api.delete(_fav(station_id), headers=auth()).status_code == 204
    assert _row_count(db) == 0
    assert _favorite_ids(api, auth) == []


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_unknown_station_is_404(api: TestClient, auth: Auth, db: Session, method: str) -> None:
    response = api.request(method, _fav(str(uuid.uuid4())), headers=auth())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert _row_count(db) == 0


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_non_uuid_station_is_422(api: TestClient, auth: Auth, method: str) -> None:
    response = api.request(method, _fav("not-a-uuid"), headers=auth())
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_list_is_newest_first(
    api: TestClient, auth: Auth, db: Session, stations: list[dict[str, Any]]
) -> None:
    ids = [s["id"] for s in stations[:3]]
    for station_id in ids:
        assert api.put(_fav(station_id), headers=auth()).status_code == 204
    # Spread the timestamps: rows inserted in one test transaction share now().
    base = datetime.now(UTC)
    for i, station_id in enumerate(ids):
        db.execute(
            update(FavoriteStation)
            .where(FavoriteStation.station_id == uuid.UUID(station_id))
            .values(created_at=base + timedelta(minutes=i))
        )
    db.commit()
    assert _favorite_ids(api, auth) == list(reversed(ids))


def test_favorites_are_per_user(
    api: TestClient, auth: Auth, db: Session, stations: list[dict[str, Any]]
) -> None:
    mine, theirs = stations[0]["id"], stations[1]["id"]
    assert api.put(_fav(mine), headers=auth()).status_code == 204
    assert api.put(_fav(theirs), headers=auth(OTHER_USER_ID)).status_code == 204
    assert _favorite_ids(api, auth) == [mine]
    assert _favorite_ids(api, auth, OTHER_USER_ID) == [theirs]

    # Removing "my" favorite as the other user leaves mine alone.
    assert api.delete(_fav(mine), headers=auth(OTHER_USER_ID)).status_code == 204
    assert _favorite_ids(api, auth) == [mine]
    # Both users can favorite the same station.
    assert api.put(_fav(mine), headers=auth(OTHER_USER_ID)).status_code == 204
    assert sorted(_favorite_ids(api, auth, OTHER_USER_ID)) == sorted([mine, theirs])
    assert _row_count(db) == 1


def test_first_put_creates_the_profile(
    api: TestClient, auth: Auth, db: Session, stations: list[dict[str, Any]]
) -> None:
    new_user = uuid.UUID("33333333-3333-3333-3333-333333333333")
    assert db.get(Profile, new_user) is None
    assert api.put(_fav(stations[0]["id"]), headers=auth(new_user)).status_code == 204
    assert db.get(Profile, new_user) is not None


def test_account_deletion_cascades(
    api: TestClient, auth: Auth, db: Session, stations: list[dict[str, Any]]
) -> None:
    assert api.put(_fav(stations[0]["id"]), headers=auth()).status_code == 204
    assert api.put(_fav(stations[0]["id"]), headers=auth(OTHER_USER_ID)).status_code == 204
    db.execute(delete(Profile).where(Profile.id == TEST_USER_ID))
    db.commit()
    assert _row_count(db) == 0
    assert _row_count(db, OTHER_USER_ID) == 1
    # The station itself is global cache data and stays.
    assert db.get(ChargingStation, uuid.UUID(stations[0]["id"])) is not None


def test_station_eviction_cascades(
    api: TestClient, auth: Auth, db: Session, stations: list[dict[str, Any]]
) -> None:
    gone, kept = stations[0]["id"], stations[1]["id"]
    for station_id in (gone, kept):
        assert api.put(_fav(station_id), headers=auth()).status_code == 204
    db.execute(delete(ChargingStation).where(ChargingStation.id == uuid.UUID(gone)))
    db.commit()
    assert _favorite_ids(api, auth) == [kept]


def test_stale_rows_are_flagged_without_a_provider_call(
    api: TestClient, auth: Auth, afdc: FakeAfdc, db: Session, stations: list[dict[str, Any]]
) -> None:
    station_id = stations[0]["id"]
    assert api.put(_fav(station_id), headers=auth()).status_code == 204
    db.execute(
        update(ChargingStation)
        .where(ChargingStation.id == uuid.UUID(station_id))
        .values(fetched_at=datetime.now(UTC) - timedelta(hours=25))
    )
    db.commit()
    [item] = api.get(FAVORITES, headers=auth()).json()["items"]
    assert item["is_stale"] is True
    assert afdc.requests == []


def test_compatibility_with_a_vehicle(
    api: TestClient,
    auth: Auth,
    add_vehicle: Callable[..., Any],
    stations: list[dict[str, Any]],
) -> None:
    assert api.put(_fav(stations[0]["id"]), headers=auth()).status_code == 204
    [plain] = api.get(FAVORITES, headers=auth()).json()["items"]
    assert plain["compatible"] is None

    types = {c["type"] for c in stations[0]["connectors"]}
    matching = add_vehicle(connectors=sorted(types))
    other = add_vehicle(connectors=[t for t in ("NACS", "CCS1", "CHAdeMO") if t not in types][:1])
    [yes] = api.get(FAVORITES, params={"vehicle_id": str(matching.id)}, headers=auth()).json()[
        "items"
    ]
    [no] = api.get(FAVORITES, params={"vehicle_id": str(other.id)}, headers=auth()).json()["items"]
    assert yes["compatible"] is True
    assert no["compatible"] is False


def test_another_users_vehicle_is_404(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Any]
) -> None:
    vehicle = add_vehicle(user_id=OTHER_USER_ID, connectors=["NACS"])
    response = api.get(FAVORITES, params={"vehicle_id": str(vehicle.id)}, headers=auth())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_limit(
    api: TestClient,
    auth: Auth,
    db: Session,
    stations: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(favorites, "MAX_FAVORITES_PER_USER", 2)
    a, b, c = (s["id"] for s in stations[:3])
    assert api.put(_fav(a), headers=auth()).status_code == 204
    assert api.put(_fav(b), headers=auth()).status_code == 204
    response = api.put(_fav(c), headers=auth())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FAVORITES_LIMIT"
    assert response.json()["error"]["details"] == {"limit": 2}
    # An existing favorite is still a no-op, not a limit error.
    assert api.put(_fav(a), headers=auth()).status_code == 204
    assert _row_count(db) == 2


def test_favorites_route_is_not_parsed_as_a_station_id(api: TestClient, auth: Auth) -> None:
    # ``/stations/favorites`` must not reach ``GET /stations/{station_id}`` (422).
    assert api.get(FAVORITES, headers=auth()).status_code == 200


# ---------------------------------------------------------------------------
# Concurrency: the cap holds across two real transactions
# ---------------------------------------------------------------------------


def _committed_station(conn: Any, external_id: str) -> uuid.UUID:
    station = ChargingStation(
        provider="simulated",
        external_id=external_id,
        name=f"Race test {external_id}",
        lat=39.4,
        lon=-77.4,
        raw_payload={},
        is_simulated=True,
    )
    with Session(bind=conn) as s:
        s.add(station)
        s.commit()
        return station.id


def test_concurrent_puts_cannot_pass_the_cap(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two PUTs for different stations at cap - 1: the second waits, then gets 409.

    Uses two real connections with committed data (not the rolled-back test
    transaction), so READ COMMITTED behaves as in production.
    """

    monkeypatch.setattr(favorites, "MAX_FAVORITES_PER_USER", 1)
    user_id = uuid.uuid4()
    tag = uuid.uuid4().hex[:8]
    with db_engine.connect() as setup:
        with Session(bind=setup) as s:
            # As after the user's first ``/me``: profile and preferences exist.
            s.add(Profile(id=user_id))
            s.add(UserPreferences(user_id=user_id))
            s.commit()
        first = _committed_station(setup, f"race-a-{tag}")
        second = _committed_station(setup, f"race-b-{tag}")

    a = Session(bind=db_engine)
    b = Session(bind=db_engine)
    outcome: dict[str, Any] = {}

    def put_second() -> None:
        try:
            favorites.add_favorite(b, user_id=user_id, station_id=second)
            b.commit()
            outcome["b"] = "added"
        except ApiError as exc:
            b.rollback()
            outcome["b"] = exc.code

    try:
        # A favorites the first station and holds its transaction open.
        favorites.add_favorite(a, user_id=user_id, station_id=first)
        worker = threading.Thread(target=put_second)
        worker.start()
        # B must wait on A's lock rather than count 0 and insert.
        worker.join(timeout=0.5)
        assert worker.is_alive(), "second PUT didn't wait for the first"
        a.commit()
        worker.join(timeout=10)
        assert not worker.is_alive()
        assert outcome["b"] == "FAVORITES_LIMIT"
        with Session(bind=db_engine) as check:
            count = check.scalar(
                select(func.count())
                .select_from(FavoriteStation)
                .where(FavoriteStation.user_id == user_id)
            )
        assert count == 1
    finally:
        a.rollback()
        a.close()
        b.close()
        with db_engine.begin() as cleanup:
            cleanup.execute(delete(Profile).where(Profile.id == user_id))
            cleanup.execute(delete(ChargingStation).where(ChargingStation.id.in_([first, second])))
