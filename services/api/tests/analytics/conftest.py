"""Fixtures for the analytics tests (Phase 5 F5).

Legs and backtests are written straight through the P5-F1 models, never
through the ingestion service (P5-F3), so these tests only depend on the
schema. The database is the shared Postgres test database, rolled back per
test.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.modules.trips.observed_models import ObservedTrip, PredictionResult
from app.modules.vehicles.models import Vehicle

# Reuse the trips fixtures (session, client, auth, vehicles, plans).
from tests.trips.conftest import add_trip, add_vehicle, api, auth, db  # noqa: F401

LegFactory = Callable[..., ObservedTrip]


@pytest.fixture()
def add_leg(db: Session) -> LegFactory:  # noqa: F811
    """Store one leg and, when ``estimated_kwh`` is given, its D9 backtest row."""

    odometer = itertools.count(10000, 1000)

    def _add(
        vehicle: Vehicle,
        *,
        end: datetime,
        distance_km: str = "10.00",
        energy_kwh: str | None = "2.000",
        estimated_kwh: str | None = "2.100",
        duration: timedelta = timedelta(minutes=30),
        review_status: str = "auto",
        quality_flag: str = "high",
        quality_reasons: list[str] | None = None,
        closed: bool = True,
        is_simulated: bool = False,
        planned_trip_id: uuid.UUID | None = None,
        soc_start: str | None = "80",
        soc_end: str | None = "70",
    ) -> ObservedTrip:
        start_km = Decimal(next(odometer))
        distance = Decimal(distance_km)
        start = end - duration
        leg = ObservedTrip(
            user_id=vehicle.user_id,
            vehicle_id=vehicle.id,
            start_time=start,
            start_time_latest=start,
            end_time=end,
            odometer_start_km=start_km,
            odometer_end_km=start_km + distance,
            distance_km=distance,
            soc_start_percent=None if soc_start is None else Decimal(soc_start),
            soc_start_recorded_at=None if soc_start is None else start,
            soc_end_percent=None if soc_end is None else Decimal(soc_end),
            soc_end_recorded_at=None if soc_end is None else end,
            usable_capacity_kwh=None if energy_kwh is None else Decimal("71.00"),
            capacity_source=None if energy_kwh is None else "catalog",
            energy_kwh=None if energy_kwh is None else Decimal(energy_kwh),
            energy_kwh_uncertainty=None if energy_kwh is None else Decimal("0.710"),
            energy_source=None if energy_kwh is None else "soc_inferred",
            quality_flag=quality_flag,
            quality_reasons=quality_reasons or [],
            review_status=review_status,
            closed=closed,
            planned_trip_id=planned_trip_id,
            inference_version="telemetry-v1",
            is_simulated=is_simulated,
        )
        db.add(leg)
        db.flush()
        if estimated_kwh is not None:
            predicted = Decimal(estimated_kwh)
            observed = None if energy_kwh is None else Decimal(energy_kwh)
            db.add(
                PredictionResult(
                    user_id=vehicle.user_id,
                    vehicle_id=vehicle.id,
                    kind="leg",
                    observed_trip_id=leg.id,
                    model_version="deterministic-v1",
                    distance_km=distance,
                    observed_distance_km=distance,
                    predicted_energy_kwh=predicted,
                    observed_energy_kwh=observed,
                    error_kwh=None if observed is None else observed - predicted,
                    inputs={"baseline_kwh_per_km": 0.2, "baseline_source": "catalog"},
                    is_simulated=is_simulated,
                )
            )
        db.commit()
        return leg

    return _add
