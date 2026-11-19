"""Analytics: consumption trends, model error and the trips table (Phase 5 F5).

Contract: ``docs/api-contracts.md`` §3.11-3.11b. Design: phase5_plan.md D6,
D9, D11-D13 and §3 F5. Reads go through ``trips.observed_queries`` only; the
vehicle ownership check goes through ``vehicles.garage`` (404 when the vehicle
isn't the caller's).
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.modules.analytics import aggregates, buckets
from app.modules.analytics.buckets import DateRange, GroupBy
from app.modules.analytics.csv_export import render_csv
from app.modules.analytics.schemas import (
    AnalyticsRange,
    AnalyticsTrip,
    AnalyticsTripList,
    ConsumptionBucket,
    ConsumptionMetrics,
    ConsumptionResponse,
    PlanAccuracy,
)
from app.modules.trips import observed_queries
from app.modules.trips.observed_queries import (
    DETERMINISTIC_MODEL_VERSION,
    AnalyticsLeg,
    LegRange,
)
from app.modules.vehicles.garage import get_vehicle_detail

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@dataclass(frozen=True)
class Filters:
    """The query every analytics route shares, resolved and validated."""

    vehicle_id: uuid.UUID
    tz: str
    zone: ZoneInfo
    range: DateRange
    include_simulated: bool

    def leg_range(self, user_id: uuid.UUID) -> LegRange:
        return LegRange(
            user_id=user_id,
            vehicle_id=self.vehicle_id,
            start=self.range.start,
            end=self.range.end,
            include_simulated=self.include_simulated,
        )


@dataclass(frozen=True)
class CsvExport:
    filename: str
    body: str


def resolve_filters(
    db: Session,
    *,
    user_id: uuid.UUID,
    vehicle_id: uuid.UUID,
    first: date | None,
    last: date | None,
    tz: str,
    include_simulated: bool,
    settings: Settings,
    now: datetime,
) -> Filters:
    """Validate the shared params and check the vehicle is the caller's (else 404)."""

    zone = buckets.resolve_zone(tz)
    rng = buckets.resolve_range(first, last, zone=zone, now=now)
    get_vehicle_detail(
        db,
        user_id=user_id,
        vehicle_id=vehicle_id,
        stale_after=timedelta(minutes=settings.telemetry_stale_after_minutes),
        now=now,
    )
    return Filters(
        vehicle_id=vehicle_id,
        tz=tz,
        zone=zone,
        range=rng,
        include_simulated=include_simulated,
    )


def consumption(
    db: Session,
    *,
    user_id: uuid.UUID,
    filters: Filters,
    group_by: GroupBy,
    settings: Settings,
    now: datetime,
) -> ConsumptionResponse:
    """§3.11: every bucket in the range, totals, plan accuracy and the simulated count."""

    series_buckets = buckets.build_buckets(
        filters.range, group_by, zone=filters.zone, max_buckets=settings.analytics_max_buckets
    )
    rng = filters.leg_range(user_id)
    legs = [
        leg
        for leg in observed_queries.list_legs(db, rng, now=now)
        if aggregates.included_in_aggregates(leg, include_simulated=filters.include_simulated)
    ]
    by_bucket: dict[date, list[AnalyticsLeg]] = defaultdict(list)
    for leg in legs:
        by_bucket[buckets.bucket_of(leg.end_time, group_by, zone=filters.zone)].append(leg)

    series = [
        ConsumptionBucket(
            bucket=b.label,
            bucket_start=b.start,
            **_metrics(aggregates.summarize(by_bucket.get(b.first_day, []))).model_dump(),
        )
        for b in series_buckets
    ]
    excluded = 0 if filters.include_simulated else observed_queries.count_simulated_legs(db, rng)
    plans = aggregates.summarize_plans(observed_queries.list_plan_scores(db, rng))
    return ConsumptionResponse(
        vehicle_id=filters.vehicle_id,
        range=AnalyticsRange.model_validate(
            {"from": filters.range.first, "to": filters.range.last}
        ),
        group_by=group_by,
        tz=filters.tz,
        model_version=DETERMINISTIC_MODEL_VERSION,
        includes_simulated=filters.include_simulated,
        excluded_simulated_trips=excluded,
        series=series,
        totals=_metrics(aggregates.summarize(legs)),
        plans=PlanAccuracy(
            matched=plans.matched,
            with_energy=plans.with_energy,
            mae_kwh=plans.mae_kwh,
            mean_arrival_soc_error_points=plans.mean_arrival_soc_error_points,
        ),
    )


def trips_page(
    db: Session,
    *,
    user_id: uuid.UUID,
    filters: Filters,
    limit: int,
    cursor: str | None,
    now: datetime,
) -> AnalyticsTripList:
    """§3.11a: one page of non-rejected legs, newest first."""

    after = decode_cursor(cursor) if cursor is not None else None
    legs = observed_queries.page_legs(
        db, filters.leg_range(user_id), now=now, limit=limit + 1, after=after
    )
    has_more = len(legs) > limit
    legs = legs[:limit]
    items = [to_trip(leg, include_simulated=filters.include_simulated) for leg in legs]
    next_cursor = encode_cursor(legs[-1].end_time, legs[-1].id) if has_more else None
    return AnalyticsTripList(items=items, next_cursor=next_cursor)


def trips_csv(
    db: Session,
    *,
    user_id: uuid.UUID,
    filters: Filters,
    settings: Settings,
    now: datetime,
) -> CsvExport:
    """§3.11b: every §3.11a row as CSV, or a 422 above the export cap (never truncated)."""

    rng = filters.leg_range(user_id)
    count = observed_queries.count_legs(db, rng)
    cap = settings.analytics_export_max_rows
    if count > cap:
        raise ApiError(
            "VALIDATION_ERROR",
            f"The export has {count} rows; the limit is {cap}. Narrow the date range.",
            status_code=422,
            details={"reason": "EXPORT_TOO_LARGE", "rows": count, "max_rows": cap},
        )
    rows = [
        to_trip(leg, include_simulated=filters.include_simulated)
        for leg in observed_queries.list_legs(db, rng, now=now)
    ]
    filename = (
        f"chargewise-trips-{filters.range.first.isoformat()}-{filters.range.last.isoformat()}.csv"
    )
    return CsvExport(filename=filename, body=render_csv(rows))


def to_trip(leg: AnalyticsLeg, *, include_simulated: bool) -> AnalyticsTrip:
    per_km = None if leg.energy_kwh is None else round(leg.energy_kwh / leg.distance_km, 3)
    return AnalyticsTrip(
        id=leg.id,
        vehicle_id=leg.vehicle_id,
        start_time=leg.start_time,
        start_time_latest=leg.start_time_latest,
        end_time=leg.end_time,
        distance_km=leg.distance_km,
        soc_start_percent=leg.soc_start_percent,
        soc_end_percent=leg.soc_end_percent,
        observed_energy_kwh=leg.energy_kwh,
        observed_energy_uncertainty_kwh=leg.energy_kwh_uncertainty,
        estimated_energy_kwh=leg.estimated_energy_kwh,
        error_kwh=leg.error_kwh,
        observed_kwh_per_km=per_km,
        energy_source=leg.energy_source,
        quality_flag=leg.quality_flag,
        quality_reasons=list(leg.quality_reasons),
        review_status=leg.review_status,
        in_progress=leg.in_progress,
        included_in_aggregates=aggregates.included_in_aggregates(
            leg, include_simulated=include_simulated
        ),
        planned_trip_id=leg.planned_trip_id,
        plan_origin_label=leg.plan_origin_label,
        plan_destination_label=leg.plan_destination_label,
        model_version=DETERMINISTIC_MODEL_VERSION,
        is_simulated=leg.is_simulated,
    )


# --------------------------------------------------------------------------- cursor


def encode_cursor(end_time: datetime, leg_id: uuid.UUID) -> str:
    """Opaque keyset cursor. Not signed: queries always filter on the caller."""

    body = json.dumps({"k": end_time.isoformat(), "i": str(leg_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise _invalid_cursor() from None
    if not isinstance(data, dict):
        raise _invalid_cursor()
    raw_key, raw_id = data.get("k"), data.get("i")
    if not (isinstance(raw_key, str) and isinstance(raw_id, str)):
        raise _invalid_cursor()
    try:
        key = datetime.fromisoformat(raw_key)
        leg_id = uuid.UUID(raw_id)
    except ValueError:
        raise _invalid_cursor() from None
    if key.tzinfo is None:
        raise _invalid_cursor()
    return key, leg_id


def _invalid_cursor() -> ApiError:
    return ApiError(
        "VALIDATION_ERROR",
        "Invalid cursor; list the trips again without it.",
        status_code=422,
        details={"reason": "INVALID_CURSOR"},
    )


def _metrics(summary: aggregates.Summary) -> ConsumptionMetrics:
    return ConsumptionMetrics(
        trips=summary.trips,
        distance_km=summary.distance_km,
        energy_trips=summary.energy_trips,
        observed_energy_kwh=summary.observed_energy_kwh,
        estimated_energy_kwh=summary.estimated_energy_kwh,
        observed_kwh_per_km=summary.observed_kwh_per_km,
        estimated_kwh_per_km=summary.estimated_kwh_per_km,
        mae_kwh=summary.mae_kwh,
        bias_kwh=summary.bias_kwh,
    )
