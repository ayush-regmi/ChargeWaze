"""Seed the public demo database with simulated data (P6-F2, database-design.md §5).

  APP_PUBLIC_DEMO_MODE=true DATABASE_URL=postgresql+psycopg://… \\
    uv run python scripts/seed_demo.py --user-id <demo auth user UUID> --target HOST/DBNAME

Gives the demo account (a Supabase auth user you create in the demo project)
one simulated 2026 Toyota bZ XLE AWD and about four weeks of history:
weekday commutes, weekend errands, home charging, and a Frederick, MD →
Toronto, ON road trip with DC fast-charging stops and a drive back.

- Every row it writes is ``is_simulated = true``; telemetry is
  ``source = 'simulated'``. Telemetry has no location (Phase 3 D12); the trip
  endpoints are public city-centre points.
- The road trip's plan is made by the real planner (``plan_trip``) from the
  recorded Mapbox Directions and AFDC fixtures in the repo's ``fixtures/``
  folder, with no network call. Observed legs, the plan match and the
  backtests come from the real Phase 5 pipeline (``run_for_vehicle``), so
  every page shows numbers the app computed itself.
- The values are generated from a fixed random seed relative to ``--now``,
  so the same ``--now`` gives the same data. Re-running replaces the demo
  account's data (vehicles cascade to telemetry, plans and legs); run it daily
  if the demo should keep looking recent.

Refuses to run (exit 2) unless all of these hold, because demo data must never
be mixed into a database with real data (architecture.md §7.2):

1. ``APP_PUBLIC_DEMO_MODE=true`` in the environment. Settings are read from
   the process environment only, never from ``services/api/.env``.
2. ``--target`` equals the database URL's ``host/database``.
3. No row in ``vehicles``, ``vehicle_telemetry``, ``planned_trips`` or
   ``observed_trips`` has ``is_simulated = false``.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import random
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import create_engine, delete, make_url, text, update
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from app.core.config import Settings  # noqa: E402
from app.integrations.mapbox.client import MapboxClient  # noqa: E402
from app.integrations.smartcar.schemas import NormalizedSignal  # noqa: E402
from app.integrations.stations import AfdcProvider  # noqa: E402
from app.modules.routing.planner_service import plan_trip  # noqa: E402
from app.modules.routing.schemas import PlanEndpoint, PlanRequest, PlanResponse  # noqa: E402
from app.modules.trips.models import PlannedTrip  # noqa: E402
from app.modules.trips.observed_service import inference_settings, run_for_vehicle  # noqa: E402
from app.modules.users.models import Profile, UserPreferences  # noqa: E402
from app.modules.users.privacy_models import AccountDeletion  # noqa: E402
from app.modules.vehicles.models import Vehicle, VehicleSpecs, VehicleTelemetry  # noqa: E402
from app.modules.vehicles.spec_catalog import lookup_spec  # noqa: E402
from app.modules.vehicles.telemetry_service import apply_to_latest_state  # noqa: E402

FIXTURES = API_ROOT.parents[1] / "fixtures"
DIRECTIONS_FIXTURE = FIXTURES / "mapbox" / "directions_frederick_toronto.json"
STATIONS_FIXTURE = FIXTURES / "stations" / "afdc" / "nearby_route_cross_border_dc_fast.json"

DEMO_MAKE, DEMO_MODEL, DEMO_YEAR = "TOYOTA", "bZ XLE AWD", 2026
DEMO_DISPLAY_NAME = "Demo Driver"
HOME_TZ = ZoneInfo("America/New_York")  # Frederick, MD
# The recorded fixtures are for exactly this pair (fixtures/mapbox/README.md).
FREDERICK = PlanEndpoint(lat=39.4143, lon=-77.4105, label="Frederick, MD")
TORONTO = PlanEndpoint(lat=43.6532, lon=-79.3832, label="Toronto, ON")

HISTORY_DAYS = 28
ROAD_TRIP_DAYS_AGO = 4  # inside matching.CANDIDATE_DAYS, so the plan is matched
RANDOM_SEED = 20261120
READING_EVERY = timedelta(minutes=5)
HOME_CHARGER_KW = 7.2  # Level 2 (J1772)
HOME_CHARGE_TO = 80.0
DC_CHARGE_TO = 80.0
RAW_PAYLOAD = {"generator": "seed_demo", "version": 1}
# Smartcar codes the simulated car "supports", for the Garage page.
SUPPORTED_SIGNALS = [
    "charge-ischarging",
    "odometer-traveleddistance",
    "tractionbattery-range",
    "tractionbattery-stateofcharge",
]
_SMARTCAR_CODE = {
    "soc": "tractionbattery-stateofcharge",
    "range": "tractionbattery-range",
    "odometer": "odometer-traveleddistance",
    "charging_status": "charge-ischarging",
}
_UNIT = {"soc": "percent", "range": "km", "odometer": "km", "charging_status": "bool"}

REAL_DATA_CHECK = text(
    "SELECT EXISTS (SELECT 1 FROM vehicles WHERE NOT is_simulated)"
    " OR EXISTS (SELECT 1 FROM vehicle_telemetry WHERE NOT is_simulated)"
    " OR EXISTS (SELECT 1 FROM planned_trips WHERE NOT is_simulated)"
    " OR EXISTS (SELECT 1 FROM observed_trips WHERE NOT is_simulated)"
)


class SeedRefused(Exception):
    """A safety check failed; nothing was written."""


@dataclass(frozen=True)
class SeedSummary:
    user_id: uuid.UUID
    vehicle_id: uuid.UUID
    trip_id: uuid.UUID
    telemetry_rows: int
    legs_created: int
    plans_matched: int


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------


def check_settings(settings: Settings) -> None:
    if not settings.app_public_demo_mode:
        raise SeedRefused(
            "APP_PUBLIC_DEMO_MODE is not true. The demo seed only runs against a demo "
            "deployment's database."
        )


def check_target(url: URL, target: str) -> None:
    actual = f"{url.host}/{url.database}"
    if target != actual:
        raise SeedRefused(f"--target {target!r} doesn't match DATABASE_URL ({actual!r}).")


def check_no_real_data(db: Session) -> None:
    if db.execute(REAL_DATA_CHECK).scalar_one():
        raise SeedRefused(
            "This database has real (is_simulated = false) vehicles, telemetry or trips. "
            "Demo data never goes into a database with real data."
        )


# ---------------------------------------------------------------------------
# Telemetry simulation
# ---------------------------------------------------------------------------


@dataclass
class _Reading:
    recorded_at: datetime
    code: str
    value: float


@dataclass
class _Car:
    """A simulated car: odometer, battery and a growing list of readings."""

    capacity_kwh: float
    baseline_kwh_per_km: float
    odometer_km: float
    soc: float
    rng: random.Random
    readings: list[_Reading] = field(default_factory=list)

    def _emit(self, at: datetime, *, odometer: bool = True) -> None:
        soc = round(self.soc)  # the bZ reports whole percent
        if odometer:
            self.readings.append(_Reading(at, "odometer", round(self.odometer_km, 1)))
        self.readings.append(_Reading(at, "soc", soc))
        rng_km = soc / 100 * self.capacity_kwh / self.baseline_kwh_per_km
        self.readings.append(_Reading(at, "range", round(rng_km)))

    def drive(self, start: datetime, km: float, minutes: float, factor: float) -> datetime:
        """Drive ``km`` in ``minutes`` using ``factor`` times the baseline consumption."""

        steps = max(1, math.ceil(minutes / (READING_EVERY.total_seconds() / 60)))
        step_km = km / steps
        step_soc = step_km * self.baseline_kwh_per_km * factor / self.capacity_kwh * 100
        self.readings.append(_Reading(start, "charging_status", 0))
        self._emit(start)
        at = start
        for i in range(steps):
            at = start + timedelta(minutes=minutes * (i + 1) / steps)
            self.odometer_km += step_km
            self.soc = max(1.0, self.soc - step_soc)
            self._emit(at)
        return at

    def charge(self, start: datetime, to_soc: float, power_kw: float) -> datetime:
        """Charge to ``to_soc`` at an average ``power_kw``; readings every 5 minutes."""

        if self.soc >= to_soc:
            return start
        minutes = (to_soc - self.soc) / 100 * self.capacity_kwh / power_kw * 60
        steps = max(1, math.ceil(minutes / 5))
        gain = (to_soc - self.soc) / steps
        self.readings.append(_Reading(start, "charging_status", 1))
        self._emit(start, odometer=False)
        at = start
        for i in range(steps):
            at = start + timedelta(minutes=minutes * (i + 1) / steps)
            self.soc += gain
            self._emit(at, odometer=False)
        self.readings.append(_Reading(at, "charging_status", 0))
        return at

    def long_drive(self, start: datetime, km: float, speed_kmh: float, factor: float) -> datetime:
        """A highway drive with DC stops whenever the battery would drop below 20 %."""

        at, left = start, km
        while left > 0.5:
            usable_km = (
                (self.soc - 20) / 100 * self.capacity_kwh / (self.baseline_kwh_per_km * factor)
            )
            leg = min(left, max(usable_km, 0.0))
            if leg > 1:
                at = self.drive(at, leg, leg / speed_kmh * 60, factor)
                left -= leg
            if left > 0.5:
                at = self.charge(at + timedelta(minutes=4), DC_CHARGE_TO, 95.0)
                at += timedelta(minutes=6)
        return at


def _local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=HOME_TZ).astimezone(UTC)


def _ordinary_day(car: _Car, day: date) -> None:
    rng = car.rng
    weekday = day.weekday()
    if weekday < 5:
        km = rng.uniform(21.0, 27.0)
        car.drive(
            _local(day, 7, rng.randint(40, 59)), km, rng.uniform(26, 34), rng.uniform(0.82, 1.0)
        )
        car.drive(
            _local(day, 17, rng.randint(15, 45)), km, rng.uniform(28, 40), rng.uniform(0.85, 1.05)
        )
        if weekday == 2:  # a short mid-week errand: a low-quality leg (small SoC change)
            car.drive(_local(day, 19, 10), rng.uniform(3.0, 5.0), 9, rng.uniform(0.9, 1.1))
    elif weekday == 5:
        car.drive(_local(day, 10, 20), rng.uniform(9.0, 14.0), 18, rng.uniform(0.85, 1.0))
        car.drive(_local(day, 12, 5), rng.uniform(9.0, 14.0), 20, rng.uniform(0.85, 1.0))
    else:
        car.drive(_local(day, 11, 0), rng.uniform(55.0, 70.0), 55, rng.uniform(0.95, 1.15))
        car.drive(_local(day, 16, 30), rng.uniform(55.0, 70.0), 60, rng.uniform(0.95, 1.15))


def _overnight_charge(car: _Car, day: date, *, to_soc: float = HOME_CHARGE_TO) -> None:
    if car.soc < 60 or to_soc > HOME_CHARGE_TO:
        car.charge(_local(day, 22, 0), to_soc, HOME_CHARGER_KW)


def _follow_plan(car: _Car, start: datetime, plan: PlanResponse) -> datetime:
    """Drive the plan's route, charging at its stops to its departure SoC."""

    assert plan.route is not None and plan.totals is not None
    route_km = plan.route.distance_km
    minutes_per_km = plan.route.drive_minutes / route_km
    factor = plan.assumptions.detour_road_factor
    at, prev_along, prev_off = start, 0.0, 0.0
    for stop in plan.stops:
        km = (stop.distance_along_route_km - prev_along) + (
            prev_off + stop.distance_from_route_km
        ) * factor
        km *= car.rng.uniform(1.0, 1.015)
        at = car.drive(at, km, km * minutes_per_km, car.rng.uniform(1.01, 1.06))
        at = car.charge(
            at + timedelta(minutes=3), stop.departure_soc * 100, stop.station.power_kw * 0.7
        )
        at += timedelta(minutes=5)
        prev_along, prev_off = stop.distance_along_route_km, stop.distance_from_route_km
    km = (route_km - prev_along) + prev_off * factor
    return car.drive(at, km, km * minutes_per_km, car.rng.uniform(1.01, 1.06))


def _store_readings(db: Session, vehicle: Vehicle, readings: list[_Reading], now: datetime) -> None:
    """Insert ``readings`` as simulated telemetry and move ``latest_state`` forward."""

    rows = [
        VehicleTelemetry(
            vehicle_id=vehicle.id,
            recorded_at=r.recorded_at,
            received_at=min(r.recorded_at + timedelta(seconds=20), now),
            source="simulated",
            source_event_id=None,
            signal_code=r.code,
            value=Decimal(str(r.value)),
            unit=_UNIT[r.code],
            raw_payload=RAW_PAYLOAD,
            is_simulated=True,
        )
        for r in readings
    ]
    db.add_all(rows)
    latest: dict[str, _Reading] = {}
    for r in readings:
        if r.code not in latest or r.recorded_at >= latest[r.code].recorded_at:
            latest[r.code] = r
    signals = [
        NormalizedSignal(
            smartcar_code=_SMARTCAR_CODE[r.code],
            code=r.code,
            outcome="ok",
            value=Decimal(str(r.value)),
            text_value=None,
            unit=_UNIT[r.code],
            recorded_at=r.recorded_at,
            retrieved_at=None,
            error_code=None,
            raw={},
        )
        for r in latest.values()
    ]
    received = max((r.recorded_at for r in latest.values()), default=now) + timedelta(seconds=20)
    vehicle.latest_state = apply_to_latest_state(
        vehicle.latest_state, signals, received_at=min(received, now)
    )
    db.flush()


# ---------------------------------------------------------------------------
# Offline planner adapters (recorded fixtures, no network)
# ---------------------------------------------------------------------------


def _fixture_mapbox() -> MapboxClient:
    body = json.loads(DIRECTIONS_FIXTURE.read_bytes())
    return MapboxClient(
        access_token="fixture",
        api_base_url="https://mapbox.fixture",
        timeout_seconds=5.0,
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=body)),
    )


def _fixture_stations(settings: Settings) -> AfdcProvider:
    body = json.loads(STATIONS_FIXTURE.read_bytes())

    def respond(request: httpx.Request) -> httpx.Response:
        params = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        params.update({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        offset, limit = int(params.get("offset", "0")), int(params.get("limit", "200"))
        page = copy.deepcopy(body)
        page["fuel_stations"] = body["fuel_stations"][offset : offset + limit]
        return httpx.Response(200, json=page)

    return AfdcProvider(
        api_key="fixture",
        api_base_url="https://afdc.fixture/api/alt-fuel-stations/v1",
        timeout_seconds=5.0,
        route_max_points=settings.stations_route_max_points,
        transport=httpx.MockTransport(respond),
    )


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def _reset_account(db: Session, user_id: uuid.UUID) -> None:
    db.execute(delete(PlannedTrip).where(PlannedTrip.user_id == user_id))
    db.execute(delete(Vehicle).where(Vehicle.user_id == user_id))
    db.execute(delete(AccountDeletion).where(AccountDeletion.user_id == user_id))
    db.execute(delete(UserPreferences).where(UserPreferences.user_id == user_id))
    profile = db.get(Profile, user_id)
    if profile is None:
        db.add(Profile(id=user_id, display_name=DEMO_DISPLAY_NAME))
    else:
        profile.display_name = DEMO_DISPLAY_NAME
    db.flush()


def seed(
    db: Session,
    *,
    user_id: uuid.UUID,
    settings: Settings,
    now: datetime | None = None,
) -> SeedSummary:
    """Replace ``user_id``'s data with the demo history. Flushes; the caller commits."""

    current = (now or datetime.now(UTC)).astimezone(UTC)
    check_settings(settings)
    check_no_real_data(db)
    catalog = lookup_spec(DEMO_MAKE, DEMO_MODEL, DEMO_YEAR)
    if (
        catalog is None
        or catalog.usable_capacity_kwh is None
        or catalog.baseline_kwh_per_km is None
    ):
        raise SeedRefused("The spec catalog has no 2026 bZ XLE AWD entry.")
    for path in (DIRECTIONS_FIXTURE, STATIONS_FIXTURE):
        if not path.is_file():
            raise SeedRefused(f"Missing fixture {path}; run from a full repository checkout.")

    _reset_account(db, user_id)
    today = current.astimezone(HOME_TZ).date()
    first_day = today - timedelta(days=HISTORY_DAYS - 1)
    vehicle = Vehicle(
        user_id=user_id,
        smartcar_vehicle_id=None,
        smartcar_user_id=None,
        make=DEMO_MAKE,
        model=DEMO_MODEL,
        year=DEMO_YEAR,
        connection_status="connected",
        supported_signals=list(SUPPORTED_SIGNALS),
        connected_at=_local(first_day, 6, 0),
        is_simulated=True,
        specs=VehicleSpecs(
            usable_capacity_kwh=catalog.usable_capacity_kwh,
            connectors=list(catalog.connectors),
            charging_curve=[dict(p) for p in catalog.charging_curve],
        ),
    )
    db.add(vehicle)
    db.flush()
    db.add(UserPreferences(user_id=user_id, primary_vehicle_id=vehicle.id))
    db.flush()

    rng = random.Random(RANDOM_SEED)
    car = _Car(
        capacity_kwh=float(catalog.usable_capacity_kwh),
        baseline_kwh_per_km=float(catalog.baseline_kwh_per_km),
        odometer_km=4812.0,
        soc=78.0,
        rng=rng,
    )
    trip_day = today - timedelta(days=ROAD_TRIP_DAYS_AGO)
    stored, written = 0, 0

    def flush() -> None:
        # Readings later than half an hour ago are dropped: the history ends
        # in the past, and nothing is ever recorded in the future.
        nonlocal stored, written
        cutoff = current - timedelta(minutes=30)
        batch = [r for r in car.readings[stored:] if r.recorded_at <= cutoff]
        _store_readings(db, vehicle, batch, current)
        stored, written = len(car.readings), written + len(batch)

    day = first_day
    while day < trip_day:
        _ordinary_day(car, day)
        night_target = 90.0 if day == trip_day - timedelta(days=1) else HOME_CHARGE_TO
        _overnight_charge(car, day, to_soc=night_target)
        day += timedelta(days=1)

    # The road trip: plan in the morning (the real planner on recorded
    # fixtures), then drive it, charging where the plan says.
    flush()
    departure = _local(trip_day, 7, 30)
    planned_at = departure - timedelta(minutes=25)
    plan = plan_trip(
        db,
        user_id=user_id,
        request=PlanRequest(
            vehicle_id=vehicle.id,
            origin=FREDERICK,
            destination=TORONTO,
            departure_time=departure,
            starting_soc_override=round(round(car.soc) / 100, 2),
        ),
        settings=settings,
        mapbox=_fixture_mapbox(),
        station_provider=_fixture_stations(settings),
        now=planned_at,
    )
    if plan.status != "feasible":
        raise SeedRefused(f"The demo road trip planned as {plan.status}; check the fixtures.")
    db.execute(
        update(PlannedTrip)
        .where(PlannedTrip.id == plan.trip_id)
        .values(saved_at=planned_at + timedelta(minutes=2))
    )
    arrived = _follow_plan(car, departure, plan)
    car.charge(arrived + timedelta(minutes=40), HOME_CHARGE_TO, HOME_CHARGER_KW)  # hotel L2

    # A day in Toronto, then the drive home (no plan: shows unmatched legs).
    toronto_day = trip_day + timedelta(days=1)
    car.drive(_local(toronto_day, 10, 0), rng.uniform(14.0, 18.0), 35, 0.95)
    car.drive(_local(toronto_day, 15, 0), rng.uniform(14.0, 18.0), 40, 0.95)
    car.charge(_local(toronto_day, 21, 0), 95.0, HOME_CHARGER_KW)
    home_day = trip_day + timedelta(days=2)
    back = car.long_drive(_local(home_day, 8, 0), plan.route.distance_km * 1.01, 92.0, 1.08)  # type: ignore[union-attr]
    car.charge(back + timedelta(minutes=30), HOME_CHARGE_TO, HOME_CHARGER_KW)

    day = home_day + timedelta(days=1)
    while day <= today:
        _ordinary_day(car, day)
        _overnight_charge(car, day)
        day += timedelta(days=1)
    flush()

    summary = run_for_vehicle(
        db,
        vehicle_id=vehicle.id,
        user_id=user_id,
        full=True,
        now=current,
        settings=inference_settings(settings),
    )
    return SeedSummary(
        user_id=user_id,
        vehicle_id=vehicle.id,
        trip_id=plan.trip_id,
        telemetry_rows=written,
        legs_created=summary.legs_created,
        plans_matched=summary.plans_matched,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_now(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--now needs a time zone, e.g. 2026-11-20T12:00:00Z")
    return parsed


def main(
    argv: list[str] | None = None, *, settings_factory: Callable[[], Settings] | None = None
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user-id", type=uuid.UUID, required=True, help="Demo auth user UUID")
    parser.add_argument(
        "--target",
        required=True,
        help="HOST/DBNAME of DATABASE_URL, typed out to confirm which database is seeded",
    )
    parser.add_argument("--now", type=_parse_now, default=None, help="ISO time to seed relative to")
    args = parser.parse_args(argv)

    # The process environment only: services/api/.env points at the hosted
    # live database and must never be picked up here.
    settings = (settings_factory or (lambda: Settings(_env_file=None)))()  # type: ignore[call-arg]
    url = make_url(settings.database_url)
    try:
        check_settings(settings)
        check_target(url, args.target)
        engine = create_engine(url)
        try:
            with Session(engine) as db, db.begin():
                summary = seed(db, user_id=args.user_id, settings=settings, now=args.now)
        finally:
            engine.dispose()
    except SeedRefused as exc:
        print(f"Refusing to seed: {exc}", file=sys.stderr)
        return 2

    print(
        f"Seeded demo user {summary.user_id}: vehicle {summary.vehicle_id}, "
        f"{summary.telemetry_rows} telemetry rows, {summary.legs_created} observed legs, "
        f"road-trip plan {summary.trip_id} ({summary.plans_matched} matched)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
