"""Response bodies for ``/analytics/*`` (``docs/api-contracts.md`` §3.11-3.11b).

Units stay raw: km, kWh, kWh/km and percent. The client converts them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.analytics.buckets import GroupBy


class AnalyticsRange(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from")
    to: date


class ConsumptionMetrics(BaseModel):
    trips: int
    distance_km: float
    energy_trips: int
    observed_energy_kwh: float | None
    estimated_energy_kwh: float | None
    observed_kwh_per_km: float | None
    estimated_kwh_per_km: float | None
    mae_kwh: float | None
    bias_kwh: float | None


class ConsumptionBucket(ConsumptionMetrics):
    bucket: str
    bucket_start: datetime


class PlanAccuracy(BaseModel):
    matched: int
    with_energy: int
    mae_kwh: float | None
    mean_arrival_soc_error_points: float | None


class ConsumptionResponse(BaseModel):
    vehicle_id: uuid.UUID
    range: AnalyticsRange
    group_by: GroupBy
    tz: str
    model_version: str
    includes_simulated: bool
    excluded_simulated_trips: int
    series: list[ConsumptionBucket]
    totals: ConsumptionMetrics
    plans: PlanAccuracy


class AnalyticsTrip(BaseModel):
    """One row of the analytics table. Field order is the CSV column order."""

    id: uuid.UUID
    vehicle_id: uuid.UUID
    start_time: datetime
    start_time_latest: datetime
    end_time: datetime
    distance_km: float
    soc_start_percent: float | None
    soc_end_percent: float | None
    observed_energy_kwh: float | None
    observed_energy_uncertainty_kwh: float | None
    estimated_energy_kwh: float | None
    error_kwh: float | None
    observed_kwh_per_km: float | None
    energy_source: str | None
    quality_flag: str
    quality_reasons: list[str]
    review_status: str
    in_progress: bool
    included_in_aggregates: bool
    planned_trip_id: uuid.UUID | None
    plan_origin_label: str | None
    plan_destination_label: str | None
    model_version: str
    is_simulated: bool


class AnalyticsTripList(BaseModel):
    items: list[AnalyticsTrip]
    next_cursor: str | None


CSV_COLUMNS: tuple[str, ...] = tuple(AnalyticsTrip.model_fields)
