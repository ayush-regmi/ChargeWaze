"""``/analytics/*`` routes (contract §3.11-3.11b, phase5_plan.md §3 F5)."""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.modules.analytics.csv_export import format_cell
from app.modules.analytics.schemas import CSV_COLUMNS
from app.modules.trips.models import PlannedTrip
from app.modules.trips.observed_models import PredictionResult
from app.modules.vehicles.models import Vehicle
from tests.analytics.conftest import LegFactory
from tests.trips.conftest import OTHER_USER_ID

Auth = Callable[..., dict[str, str]]

CONSUMPTION = "/api/v1/analytics/consumption"
TRIPS = "/api/v1/analytics/trips"
CSV_URL = "/api/v1/analytics/trips.csv"


def _at(day: int, hour: int, minute: int = 0, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def _example_3(
    db: Session,
    vehicle: Vehicle,
    add_leg: LegFactory,
    add_trip: Callable[..., uuid.UUID],
) -> uuid.UUID:
    """phase5_plan.md §5 example 3: two legs matched to one plan, scored."""

    plan_id = add_trip(vehicle, saved_minutes=1)
    add_leg(
        vehicle,
        end=_at(8, 10, 32),
        duration=timedelta(minutes=150),
        distance_km="222.40",
        energy_kwh="44.020",
        estimated_kwh="44.480",
        soc_start="80",
        soc_end="18",
        planned_trip_id=plan_id,
    )
    add_leg(
        vehicle,
        end=_at(8, 13, 0),
        duration=timedelta(minutes=127),
        distance_km="183.60",
        energy_kwh="36.210",
        estimated_kwh="36.720",
        soc_start="66",
        soc_end="15",
        planned_trip_id=plan_id,
    )
    db.add(
        PredictionResult(
            user_id=vehicle.user_id,
            vehicle_id=vehicle.id,
            kind="plan",
            planned_trip_id=plan_id,
            model_version="deterministic-v1",
            distance_km=Decimal("402.60"),
            observed_distance_km=Decimal("406.00"),
            predicted_energy_kwh=Decimal("80.520"),
            observed_energy_kwh=Decimal("80.230"),
            error_kwh=Decimal("-0.290"),
            predicted_arrival_soc=Decimal("0.153"),
            observed_arrival_soc=Decimal("0.150"),
            observed_start_soc=Decimal("0.800"),
            arrival_soc_error_points=Decimal("-0.30"),
            inputs={"plan_model_version": "deterministic-v1"},
        )
    )
    db.commit()
    return plan_id


def _get(api: TestClient, url: str, headers: dict[str, str], **params: Any) -> Any:
    return api.get(url, params={k: v for k, v in params.items() if v is not None}, headers=headers)


# --------------------------------------------------------------------------- consumption


def test_example_3_day_bucket_and_plan_accuracy(
    api: TestClient,
    auth: Auth,
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_leg: LegFactory,
    add_trip: Callable[..., uuid.UUID],
) -> None:
    vehicle = add_vehicle()
    _example_3(db, vehicle, add_leg, add_trip)

    resp = _get(
        api,
        CONSUMPTION,
        auth(),
        vehicle_id=str(vehicle.id),
        **{"from": "2026-11-07", "to": "2026-11-08"},
        group_by="day",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["range"] == {"from": "2026-11-07", "to": "2026-11-08"}
    assert (body["group_by"], body["tz"], body["model_version"]) == (
        "day",
        "UTC",
        "deterministic-v1",
    )
    assert body["includes_simulated"] is False
    assert body["excluded_simulated_trips"] == 0
    empty, day = body["series"]
    assert empty == {
        "bucket": "2026-11-07",
        "bucket_start": "2026-11-07T00:00:00Z",
        "trips": 0,
        "distance_km": 0.0,
        "energy_trips": 0,
        "observed_energy_kwh": None,
        "estimated_energy_kwh": None,
        "observed_kwh_per_km": None,
        "estimated_kwh_per_km": None,
        "mae_kwh": None,
        "bias_kwh": None,
    }
    expected = {
        "trips": 2,
        "distance_km": 406.0,
        "energy_trips": 2,
        "observed_energy_kwh": 80.23,
        "estimated_energy_kwh": 81.2,
        "observed_kwh_per_km": 0.198,
        "estimated_kwh_per_km": 0.2,
        "mae_kwh": 0.485,
        "bias_kwh": -0.485,
    }
    assert day == {"bucket": "2026-11-08", "bucket_start": "2026-11-08T00:00:00Z", **expected}
    assert body["totals"] == expected
    assert body["plans"] == {
        "matched": 1,
        "with_energy": 1,
        "mae_kwh": 0.29,
        "mean_arrival_soc_error_points": -0.3,
    }


def test_buckets_follow_the_time_zone(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_leg: LegFactory
) -> None:
    vehicle = add_vehicle()
    add_leg(vehicle, end=datetime(2026, 11, 2, 3, 30, tzinfo=UTC))
    params = {"vehicle_id": str(vehicle.id), "from": "2026-11-01", "to": "2026-11-02"}

    ny = _get(api, CONSUMPTION, auth(), **params, group_by="day", tz="America/New_York").json()
    assert [(b["bucket"], b["bucket_start"], b["trips"]) for b in ny["series"]] == [
        ("2026-11-01", "2026-11-01T04:00:00Z", 1),
        ("2026-11-02", "2026-11-02T05:00:00Z", 0),
    ]
    utc = _get(api, CONSUMPTION, auth(), **params, group_by="day").json()
    assert [(b["bucket"], b["trips"]) for b in utc["series"]] == [
        ("2026-11-01", 0),
        ("2026-11-02", 1),
    ]


def test_week_and_month_buckets(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_leg: LegFactory
) -> None:
    vehicle = add_vehicle()
    add_leg(vehicle, end=datetime(2027, 1, 1, 12, tzinfo=UTC), distance_km="20.00")
    add_leg(vehicle, end=datetime(2027, 1, 5, 12, tzinfo=UTC), distance_km="30.00")
    params = {"vehicle_id": str(vehicle.id), "from": "2026-12-30", "to": "2027-01-12"}

    weeks = _get(api, CONSUMPTION, auth(), **params).json()  # group_by defaults to week
    assert weeks["group_by"] == "week"
    assert [(b["bucket"], b["distance_km"]) for b in weeks["series"]] == [
        ("2026-W53", 20.0),
        ("2027-W01", 30.0),
        ("2027-W02", 0.0),
    ]
    months = _get(api, CONSUMPTION, auth(), **params, group_by="month").json()
    assert [(b["bucket"], b["trips"]) for b in months["series"]] == [
        ("2026-12", 0),
        ("2027-01", 2),
    ]


def test_bucket_cap(api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle]) -> None:
    vehicle = add_vehicle()
    params = {"vehicle_id": str(vehicle.id), "group_by": "day", "from": "2025-01-01"}
    assert _get(api, CONSUMPTION, auth(), **params, to="2026-01-01").status_code == 200
    resp = _get(api, CONSUMPTION, auth(), **params, to="2026-01-02")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    assert resp.json()["error"]["details"]["reason"] == "RANGE_TOO_LARGE"


def test_simulated_legs_are_excluded_by_default_and_counted(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_leg: LegFactory
) -> None:
    vehicle = add_vehicle()
    add_leg(vehicle, end=_at(8, 9), distance_km="10.00")
    add_leg(vehicle, end=_at(8, 10), distance_km="400.00", is_simulated=True)
    add_leg(vehicle, end=_at(8, 11), is_simulated=True, review_status="rejected")
    params = {"vehicle_id": str(vehicle.id), "from": "2026-11-08", "to": "2026-11-08"}

    default = _get(api, CONSUMPTION, auth(), **params).json()
    assert default["includes_simulated"] is False
    assert default["excluded_simulated_trips"] == 1  # the rejected one isn't counted
    assert (default["totals"]["trips"], default["totals"]["distance_km"]) == (1, 10.0)
    rows = _get(api, TRIPS, auth(), **params).json()["items"]
    assert [r["is_simulated"] for r in rows] == [False]

    included = _get(api, CONSUMPTION, auth(), **params, include_simulated="true").json()
    assert included["includes_simulated"] is True
    assert included["excluded_simulated_trips"] == 0
    assert (included["totals"]["trips"], included["totals"]["distance_km"]) == (2, 410.0)
    rows = _get(api, TRIPS, auth(), **params, include_simulated="true").json()["items"]
    assert [(r["is_simulated"], r["included_in_aggregates"]) for r in rows] == [
        (True, True),
        (False, True),
    ]


def test_review_status_and_in_progress_rules(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_leg: LegFactory
) -> None:
    vehicle = add_vehicle()
    now = datetime.now(UTC).replace(microsecond=0)
    today = now.date().isoformat()
    auto = add_leg(vehicle, end=now - timedelta(hours=3), distance_km="10.00")
    confirmed = add_leg(
        vehicle, end=now - timedelta(hours=2, minutes=30), review_status="confirmed"
    )
    review = add_leg(
        vehicle,
        end=now - timedelta(hours=2),
        review_status="needs_review",
        quality_flag="low",
        quality_reasons=["soc_delta_small", "start_time_bounded"],
    )
    add_leg(vehicle, end=now - timedelta(hours=1), review_status="rejected")
    # Not closed and recent: in progress. Not closed but stale: finished (D14).
    stale = add_leg(vehicle, end=now - timedelta(minutes=50), closed=False)
    live = add_leg(vehicle, end=now - timedelta(minutes=5), closed=False, duration=timedelta(0))
    params = {"vehicle_id": str(vehicle.id), "from": today, "to": today}
    if (now - timedelta(hours=3)).date() != now.date():
        pytest.skip("too close to midnight UTC for a one-day range")

    rows = _get(api, TRIPS, auth(), **params).json()["items"]
    flags = {r["id"]: (r["in_progress"], r["included_in_aggregates"]) for r in rows}
    assert flags == {
        str(live.id): (True, False),
        str(stale.id): (False, True),
        str(review.id): (False, False),
        str(confirmed.id): (False, True),
        str(auto.id): (False, True),
    }
    review_row = next(r for r in rows if r["id"] == str(review.id))
    assert review_row["quality_reasons"] == ["soc_delta_small", "start_time_bounded"]

    totals = _get(api, CONSUMPTION, auth(), **params, group_by="day").json()["totals"]
    assert totals["trips"] == 3


def test_trip_row_fields(
    api: TestClient,
    auth: Auth,
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_leg: LegFactory,
    add_trip: Callable[..., uuid.UUID],
) -> None:
    vehicle = add_vehicle()
    plan_id = _example_3(db, vehicle, add_leg, add_trip)
    no_energy = add_leg(vehicle, end=_at(8, 18), energy_kwh=None, estimated_kwh=None)

    params = {"vehicle_id": str(vehicle.id), "from": "2026-11-08", "to": "2026-11-08"}
    rows = _get(api, TRIPS, auth(), **params).json()["items"]
    assert [r["end_time"] for r in rows] == [
        "2026-11-08T18:00:00Z",
        "2026-11-08T13:00:00Z",
        "2026-11-08T10:32:00Z",
    ]
    leg2 = rows[1]
    assert {k: leg2[k] for k in leg2 if k != "id"} == {
        "vehicle_id": str(vehicle.id),
        "start_time": "2026-11-08T10:53:00Z",
        "start_time_latest": "2026-11-08T10:53:00Z",
        "end_time": "2026-11-08T13:00:00Z",
        "distance_km": 183.6,
        "soc_start_percent": 66.0,
        "soc_end_percent": 15.0,
        "observed_energy_kwh": 36.21,
        "observed_energy_uncertainty_kwh": 0.71,
        "estimated_energy_kwh": 36.72,
        "error_kwh": -0.51,
        "observed_kwh_per_km": 0.197,
        "energy_source": "soc_inferred",
        "quality_flag": "high",
        "quality_reasons": [],
        "review_status": "auto",
        "in_progress": False,
        "included_in_aggregates": True,
        "planned_trip_id": str(plan_id),
        "plan_origin_label": "Frederick, MD",
        "plan_destination_label": "Toronto, ON",
        "model_version": "deterministic-v1",
        "is_simulated": False,
    }
    empty = rows[0]
    assert empty["id"] == str(no_energy.id)
    for key in (
        "observed_energy_kwh",
        "observed_energy_uncertainty_kwh",
        "estimated_energy_kwh",
        "error_kwh",
        "observed_kwh_per_km",
        "energy_source",
        "planned_trip_id",
        "plan_origin_label",
    ):
        assert empty[key] is None, key


# --------------------------------------------------------------------------- CSV


def _all_pages(api: TestClient, headers: dict[str, str], **params: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = None
    while True:
        body = _get(api, TRIPS, headers, **params, limit=2, cursor=cursor).json()
        rows.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return rows


def test_csv_rows_equal_json_rows(
    api: TestClient,
    auth: Auth,
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_leg: LegFactory,
    add_trip: Callable[..., uuid.UUID],
) -> None:
    vehicle = add_vehicle()
    _example_3(db, vehicle, add_leg, add_trip)
    add_leg(vehicle, end=_at(9, 8), energy_kwh=None, estimated_kwh=None, quality_flag="low")
    add_leg(
        vehicle,
        end=_at(9, 8),  # same end_time: the cursor breaks the tie by id
        review_status="needs_review",
        quality_flag="low",
        quality_reasons=["soc_delta_small", "soc_reading_stale"],
    )
    add_leg(vehicle, end=_at(10, 7), energy_kwh="1.500", estimated_kwh=None)
    add_leg(vehicle, end=_at(12, 7))  # outside the range
    params = {"vehicle_id": str(vehicle.id), "from": "2026-11-08", "to": "2026-11-10"}

    json_rows = _all_pages(api, auth(), **params)
    assert len(json_rows) == 5
    assert len({r["id"] for r in json_rows}) == 5

    resp = _get(api, CSV_URL, auth(), **params)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "text/csv; charset=utf-8"
    assert resp.headers["content-disposition"] == (
        'attachment; filename="chargewaze-trips-2026-11-08-2026-11-10.csv"'
    )
    reader = csv.reader(io.StringIO(resp.text))
    header = next(reader)
    assert header == list(CSV_COLUMNS)
    csv_rows = list(reader)
    assert csv_rows == [[format_cell(row[c]) for c in CSV_COLUMNS] for row in json_rows]

    # Spot-check that parsing the cells gives the JSON values back.
    leg2 = dict(zip(header, csv_rows[3], strict=True))
    assert float(leg2["error_kwh"]) == -0.51
    assert float(leg2["distance_km"]) == 183.6
    assert leg2["included_in_aggregates"] == "true"
    reasons = [dict(zip(header, r, strict=True))["quality_reasons"] for r in csv_rows]
    assert "soc_delta_small;soc_reading_stale" in reasons
    no_energy = next(dict(zip(header, r, strict=True)) for r in csv_rows if r[13] == "")
    assert no_energy["observed_energy_kwh"] == "" and no_energy["estimated_energy_kwh"] == ""


def test_csv_escapes_formula_labels_but_not_numbers(
    api: TestClient,
    auth: Auth,
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    add_leg: LegFactory,
    add_trip: Callable[..., uuid.UUID],
) -> None:
    vehicle = add_vehicle()
    plan_id = add_trip(vehicle)
    trip = db.get(PlannedTrip, plan_id)
    assert trip is not None
    trip.origin = {"lat": 39.4, "lon": -77.4, "label": '=HYPERLINK("http://x","y")'}
    trip.destination = {"lat": 43.6, "lon": -79.3, "label": "@SUM(1+1)"}
    db.commit()
    add_leg(
        vehicle, end=_at(8, 9), energy_kwh="2.000", estimated_kwh="2.460", planned_trip_id=plan_id
    )
    params = {"vehicle_id": str(vehicle.id), "from": "2026-11-08", "to": "2026-11-08"}

    json_row = _get(api, TRIPS, auth(), **params).json()["items"][0]
    assert json_row["plan_origin_label"] == '=HYPERLINK("http://x","y")'  # raw in JSON

    reader = csv.DictReader(io.StringIO(_get(api, CSV_URL, auth(), **params).text))
    row = next(reader)
    assert row["plan_origin_label"] == '\'=HYPERLINK("http://x","y")'
    assert row["plan_destination_label"] == "'@SUM(1+1)"
    assert row["error_kwh"] == "-0.46"  # a negative number stays a number


@pytest.fixture()
def small_export_cap(app_with_db: FastAPI) -> None:
    app_with_db.dependency_overrides[get_settings] = lambda: Settings(analytics_export_max_rows=2)


@pytest.mark.usefixtures("small_export_cap")
def test_export_cap(
    api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle], add_leg: LegFactory
) -> None:
    vehicle = add_vehicle()
    add_leg(vehicle, end=_at(8, 9))
    add_leg(vehicle, end=_at(8, 10))
    add_leg(vehicle, end=_at(8, 11), review_status="rejected")  # not exported, not counted
    params = {"vehicle_id": str(vehicle.id), "from": "2026-11-08", "to": "2026-11-08"}

    ok = _get(api, CSV_URL, auth(), **params)
    assert ok.status_code == 200
    assert len(ok.text.strip().splitlines()) == 3  # header + 2 rows

    add_leg(vehicle, end=_at(8, 12))
    resp = _get(api, CSV_URL, auth(), **params)
    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/json"
    assert resp.json()["error"]["details"] == {
        "reason": "EXPORT_TOO_LARGE",
        "rows": 3,
        "max_rows": 2,
    }


# --------------------------------------------------------------------------- errors


@pytest.mark.parametrize("url", [CONSUMPTION, TRIPS, CSV_URL])
def test_another_users_vehicle_is_not_found(
    url: str,
    api: TestClient,
    auth: Auth,
    add_vehicle: Callable[..., Vehicle],
    add_leg: LegFactory,
) -> None:
    theirs = add_vehicle(user_id=OTHER_USER_ID)
    add_leg(theirs, end=_at(8, 9))
    resp = _get(api, url, auth(), vehicle_id=str(theirs.id))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
    assert _get(api, url, auth(), vehicle_id=str(uuid.uuid4())).status_code == 404
    assert _get(api, url, auth(OTHER_USER_ID), vehicle_id=str(theirs.id)).status_code == 200


@pytest.mark.parametrize("url", [CONSUMPTION, TRIPS, CSV_URL])
@pytest.mark.parametrize(
    ("params", "reason"),
    [
        ({"tz": "Mars/Olympus"}, "UNKNOWN_TIME_ZONE"),
        ({"from": "2026-11-09", "to": "2026-11-08"}, "INVALID_RANGE"),
        ({"from": "not-a-date"}, None),
    ],
)
def test_bad_params(
    url: str,
    params: dict[str, str],
    reason: str | None,
    api: TestClient,
    auth: Auth,
    add_vehicle: Callable[..., Vehicle],
) -> None:
    vehicle = add_vehicle()
    resp = _get(api, url, auth(), vehicle_id=str(vehicle.id), **params)
    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    if reason is not None:
        assert error["details"]["reason"] == reason


@pytest.mark.parametrize("url", [CONSUMPTION, TRIPS, CSV_URL])
def test_requires_auth_and_vehicle_id(
    url: str, api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    assert api.get(url, params={"vehicle_id": str(vehicle.id)}).status_code == 401
    assert _get(api, url, auth()).status_code == 422


@pytest.mark.parametrize("cursor", ["!!", "e30", "eyJrIjoxLCJpIjoyfQ", "x" * 600])
def test_bad_cursor(
    cursor: str, api: TestClient, auth: Auth, add_vehicle: Callable[..., Vehicle]
) -> None:
    vehicle = add_vehicle()
    resp = _get(api, TRIPS, auth(), vehicle_id=str(vehicle.id), cursor=cursor)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
