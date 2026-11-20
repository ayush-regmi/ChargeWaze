"""Phase 5 close-out (P5-F8): the whole pipeline, webhook to analytics.

The other Phase 5 tests check one module each (inference, the hook,
matching, analytics on rows written directly). This file replays
phase5_plan §5 example 3 (a road trip with one DC stop, planned the evening
before) as signed Smartcar ``VEHICLE_STATE`` webhooks through the real
router, and checks every step after that with no other call:

webhook → telemetry → post-ingest hook (ADR 0009) → inference (legs) →
matching (plan completed, plan score) → backtests → ``GET /trips/{id}``
``observation`` → ``/analytics/consumption`` and ``/analytics/trips`` →
the CSV equals the JSON rows.

Two delivery modes:

- **live**: each reading arrives as it happens. The hook's clock is set to
  the reading's time, so the newest leg is "in progress" while the car
  drives, as it would be with a real car;
- **late**: every reading arrives a day after the trip, one webhook each
  (a backlog after an outage).

The fixture ends with a parked reading 40 minutes after the last motion,
which closes the second leg in both modes. Matching only considers the last
7 days, so the clock is pinned near the fixture's dates rather than today.

``TestClient`` runs background tasks before ``post`` returns, so the hook has
finished by then.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import groupby
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.integrations.smartcar.signatures import compute_signature
from app.modules.analytics.csv_export import format_cell
from app.modules.analytics.schemas import CSV_COLUMNS
from app.modules.trips import observed_service
from app.modules.trips.models import PlannedTrip
from app.modules.trips.observed_models import ObservedTrip, PredictionResult
from app.modules.vehicles.models import Vehicle
from tests.integrations.conftest import TEST_MANAGEMENT_TOKEN
from tests.trips.test_matching import EX3_CREATED, add_plan

FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "fixtures"
    / "telemetry"
    / "example3_road_trip_dc_stop.json"
)
WEBHOOK = "/api/v1/webhooks/smartcar"
CONSUMPTION = "/api/v1/analytics/consumption"
TRIPS = "/api/v1/analytics/trips"
CSV_URL = "/api/v1/analytics/trips.csv"
LATE_NOW = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
RANGE = {"from": "2026-11-07", "to": "2026-11-08"}

# Fixture signal → (Smartcar V3 signal code, body builder).
_SIGNALS: dict[str, tuple[str, Callable[[float], dict[str, Any]]]] = {
    "odometer_km": ("odometer-traveleddistance", lambda v: {"unit": "km", "value": v}),
    "soc_percent": ("tractionbattery-stateofcharge", lambda v: {"unit": "percent", "value": v}),
    "is_charging": ("charge-ischarging", lambda v: {"value": bool(v)}),
}

Auth = Callable[..., dict[str, str]]


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def deliveries(vehicle: Vehicle) -> list[tuple[datetime, bytes]]:
    """One signed ``VEHICLE_STATE`` body per reading time, oldest first."""

    readings = json.loads(FIXTURE.read_text())["readings"]
    out: list[tuple[datetime, bytes]] = []
    for at_text, group in groupby(readings, key=lambda r: r["recorded_at"]):
        at = _ts(at_text)
        ms = int(at.timestamp() * 1000)
        signals = []
        for r in group:
            code, body = _SIGNALS[r["signal"]]
            signals.append(
                {
                    "code": code,
                    "body": body(r["value"]),
                    "meta": {"oemUpdatedAt": ms, "retrievedAt": ms},
                }
            )
        payload = {
            "eventId": f"evt-{uuid.uuid4()}",
            "eventType": "VEHICLE_STATE",
            "data": {
                "user": {"id": vehicle.smartcar_user_id},
                "vehicle": {
                    "id": vehicle.smartcar_vehicle_id,
                    "make": "TOYOTA",
                    "model": "bZ XLE AWD",
                    "year": 2026,
                },
                "signals": signals,
                "triggers": [],
            },
            "meta": {
                "deliveryId": f"delivery-{uuid.uuid4()}",
                "deliveredAt": ms + 30_000,
                "webhookId": "test-webhook-id",
                "version": "4.0",
                "mode": "LIVE",
            },
        }
        out.append((at, json.dumps(payload).encode()))
    return out


def parked_reading(vehicle: Vehicle, at: datetime) -> bytes:
    """The last reading (odometer 30406.0, SoC 15) again at ``at``: still parked."""

    _, last = deliveries(vehicle)[-1]
    payload = json.loads(last)
    ms = int(at.timestamp() * 1000)
    payload["eventId"] = f"evt-{uuid.uuid4()}"
    payload["meta"]["deliveryId"] = f"delivery-{uuid.uuid4()}"
    for signal in payload["data"]["signals"]:
        signal["meta"] = {"oemUpdatedAt": ms, "retrievedAt": ms}
    return json.dumps(payload).encode()


class _Clock:
    """Stands in for ``datetime`` in ``observed_service``: ``now()`` is settable."""

    current: datetime | None = None

    @classmethod
    def set(cls, at: datetime | None) -> None:
        cls.current = at


class _FakeDatetime(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> Any:
        if _Clock.current is None:
            return datetime.now(tz)
        return _Clock.current if tz is None else _Clock.current.astimezone(tz)


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_Clock]]:
    monkeypatch.setattr(observed_service, "datetime", _FakeDatetime)
    _Clock.set(None)
    try:
        yield _Clock
    finally:
        _Clock.set(None)


@pytest.fixture()
def webhook_api(app_with_db: FastAPI) -> TestClient:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        smartcar_management_token=TEST_MANAGEMENT_TOKEN,
    )
    app_with_db.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app_with_db)


def post(api: TestClient, body: bytes) -> None:
    resp = api.post(
        WEBHOOK,
        content=body,
        headers={
            "Content-Type": "application/json",
            "SC-Signature": compute_signature(body, TEST_MANAGEMENT_TOKEN),
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] is True


def legs(db: Session, vehicle: Vehicle) -> list[ObservedTrip]:
    db.expire_all()
    return list(
        db.scalars(
            select(ObservedTrip)
            .where(ObservedTrip.vehicle_id == vehicle.id)
            .order_by(ObservedTrip.start_time)
        )
    )


def predictions(db: Session, vehicle: Vehicle, kind: str) -> list[PredictionResult]:
    return list(
        db.scalars(
            select(PredictionResult)
            .where(PredictionResult.vehicle_id == vehicle.id, PredictionResult.kind == kind)
            .order_by(PredictionResult.distance_km.desc())
        )
    )


@pytest.fixture(params=["live", "late"])
def driven(
    request: pytest.FixtureRequest,
    webhook_api: TestClient,
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    clock: type[_Clock],
) -> tuple[Vehicle, uuid.UUID]:
    """Example 3's plan, then its drive delivered as webhooks, and nothing else."""

    vehicle = add_vehicle()
    plan_id = add_plan(db, vehicle)
    for at, body in deliveries(vehicle):
        clock.set(at + timedelta(seconds=30) if request.param == "live" else LATE_NOW)
        post(webhook_api, body)
    clock.set(None)
    return vehicle, plan_id


def test_webhooks_alone_produce_legs_a_match_and_backtests(
    db: Session, driven: tuple[Vehicle, uuid.UUID]
) -> None:
    vehicle, plan_id = driven

    leg1, leg2 = legs(db, vehicle)
    assert [
        (leg.distance_km, leg.energy_kwh, leg.quality_flag, leg.review_status, leg.closed)
        for leg in (leg1, leg2)
    ] == [
        (Decimal("222.40"), Decimal("44.020"), "high", "auto", True),
        (Decimal("183.60"), Decimal("36.210"), "high", "auto", True),
    ]
    # The DC stop between them is never part of a leg (X2).
    assert leg1.end_time < _ts("2026-11-08T10:35:00Z") < leg2.start_time
    assert {leg1.planned_trip_id, leg2.planned_trip_id} == {plan_id}

    trip = db.get(PlannedTrip, plan_id)
    assert trip is not None and trip.status == "completed"
    (plan_row,) = predictions(db, vehicle, "plan")
    assert (plan_row.predicted_energy_kwh, plan_row.observed_energy_kwh, plan_row.error_kwh) == (
        Decimal("80.520"),
        Decimal("80.230"),
        Decimal("-0.290"),
    )
    assert [(b.observed_trip_id, b.error_kwh) for b in predictions(db, vehicle, "leg")] == [
        (leg1.id, Decimal("-0.460")),
        (leg2.id, Decimal("-0.510")),
    ]


def test_trip_page_and_analytics_show_the_result(
    api: TestClient, auth: Auth, driven: tuple[Vehicle, uuid.UUID]
) -> None:
    vehicle, plan_id = driven

    detail = api.get(f"/api/v1/trips/{plan_id}", headers=auth())
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "completed"
    observation = body["observation"]
    assert [leg["plan_leg_index"] for leg in observation["legs"]] == [1, 2]
    assert observation["planned_distance_km"] == 402.6
    assert observation["observed_distance_km"] == 406.0
    assert observation["error_kwh"] == -0.29
    assert observation["arrival_soc_error_points"] == -0.3

    params = {"vehicle_id": str(vehicle.id), **RANGE}
    consumption = api.get(CONSUMPTION, params={**params, "group_by": "day"}, headers=auth())
    assert consumption.status_code == 200, consumption.text
    totals = consumption.json()["totals"]
    assert (totals["trips"], totals["distance_km"], totals["mae_kwh"]) == (2, 406.0, 0.485)
    assert consumption.json()["plans"]["mae_kwh"] == 0.29

    rows = api.get(TRIPS, params=params, headers=auth()).json()["items"]
    assert [(r["distance_km"], r["planned_trip_id"]) for r in rows] == [
        (183.6, str(plan_id)),
        (222.4, str(plan_id)),
    ]
    resp = api.get(CSV_URL, params=params, headers=auth())
    assert resp.status_code == 200, resp.text
    reader = csv.reader(io.StringIO(resp.text))
    assert next(reader) == list(CSV_COLUMNS)
    assert list(reader) == [[format_cell(row[c]) for c in CSV_COLUMNS] for row in rows]


def test_plan_created_after_the_drive_is_not_matched(
    webhook_api: TestClient,
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    clock: type[_Clock],
) -> None:
    """A plan made after the car has already arrived never claims that drive."""

    vehicle = add_vehicle()
    for at, body in deliveries(vehicle):
        clock.set(at + timedelta(seconds=30))
        post(webhook_api, body)
    plan_id = add_plan(db, vehicle, created_at=EX3_CREATED + timedelta(days=1), departure_time=None)
    # The next delivery (the car parked, a day later) runs matching again.
    clock.set(_ts("2026-11-09T22:00:00Z"))
    post(webhook_api, parked_reading(vehicle, _ts("2026-11-09T21:59:00Z")))

    assert [leg.planned_trip_id for leg in legs(db, vehicle)] == [None, None]
    trip = db.get(PlannedTrip, plan_id)
    assert trip is not None and trip.status == "planned"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "P5-F3 follow-up: a backlog delivered after a newer reading is truncated; "
        "the first leg is closed at its second reading and never extended. "
        "`full` recompute rebuilds it."
    ),
)
def test_backlog_behind_a_newer_reading_builds_the_same_legs(
    webhook_api: TestClient,
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    clock: type[_Clock],
) -> None:
    """The car reported today (parked), then yesterday's drive arrives late."""

    vehicle = add_vehicle()
    clock.set(LATE_NOW)
    post(webhook_api, parked_reading(vehicle, LATE_NOW - timedelta(minutes=1)))
    for _, body in deliveries(vehicle):
        post(webhook_api, body)

    assert [leg.distance_km for leg in legs(db, vehicle)] == [
        Decimal("222.40"),
        Decimal("183.60"),
    ]


def test_full_recompute_repairs_a_truncated_backlog(
    webhook_api: TestClient,
    db: Session,
    add_vehicle: Callable[..., Vehicle],
    clock: type[_Clock],
) -> None:
    """The documented catch-up (`infer_trips.py --full`, recompute `full`) for the case above."""

    vehicle = add_vehicle()
    plan_id = add_plan(db, vehicle)
    clock.set(LATE_NOW)
    post(webhook_api, parked_reading(vehicle, LATE_NOW - timedelta(minutes=1)))
    for _, body in deliveries(vehicle):
        post(webhook_api, body)

    observed_service.run_for_vehicle(
        db, vehicle_id=vehicle.id, user_id=vehicle.user_id, full=True, now=LATE_NOW
    )
    db.commit()

    leg1, leg2 = legs(db, vehicle)
    assert (leg1.distance_km, leg2.distance_km) == (Decimal("222.40"), Decimal("183.60"))
    assert {leg1.planned_trip_id, leg2.planned_trip_id} == {plan_id}
