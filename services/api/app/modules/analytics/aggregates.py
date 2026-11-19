"""Consumption and model-error aggregates (phase5_plan.md §3 F5, D12).

Pure functions over the legs ``observed_queries`` returns. Aggregation runs in
Python: a year of daily driving is a few thousand rows.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.modules.trips.observed_queries import AnalyticsLeg, PlanScore

AGGREGATED_REVIEW_STATUSES = frozenset({"auto", "confirmed"})


@dataclass(frozen=True)
class Summary:
    trips: int
    distance_km: float
    energy_trips: int
    observed_energy_kwh: float | None
    estimated_energy_kwh: float | None
    observed_kwh_per_km: float | None
    estimated_kwh_per_km: float | None
    mae_kwh: float | None
    bias_kwh: float | None


@dataclass(frozen=True)
class PlanSummary:
    matched: int
    with_energy: int
    mae_kwh: float | None
    mean_arrival_soc_error_points: float | None


def included_in_aggregates(leg: AnalyticsLeg, *, include_simulated: bool) -> bool:
    """D12: auto or confirmed, finished, and not simulated unless asked for."""

    return (
        leg.review_status in AGGREGATED_REVIEW_STATUSES
        and not leg.in_progress
        and (include_simulated or not leg.is_simulated)
    )


def has_energy_pair(leg: AnalyticsLeg) -> bool:
    """Observed energy *and* a model estimate, so both sides cover the same legs."""

    return (
        leg.energy_kwh is not None
        and leg.estimated_energy_kwh is not None
        and leg.error_kwh is not None
    )


def summarize(legs: Iterable[AnalyticsLeg]) -> Summary:
    """Totals over ``legs``, which the caller has already filtered with D12."""

    trips = 0
    distance = 0.0
    energy_trips = 0
    energy_distance = 0.0
    observed = 0.0
    estimated = 0.0
    abs_error = 0.0
    error = 0.0
    for leg in legs:
        trips += 1
        distance += leg.distance_km
        if not has_energy_pair(leg):
            continue
        assert leg.energy_kwh is not None and leg.estimated_energy_kwh is not None
        assert leg.error_kwh is not None
        energy_trips += 1
        energy_distance += leg.distance_km
        observed += leg.energy_kwh
        estimated += leg.estimated_energy_kwh
        abs_error += abs(leg.error_kwh)
        error += leg.error_kwh

    if energy_trips == 0:
        return Summary(
            trips=trips,
            distance_km=round(distance, 2),
            energy_trips=0,
            observed_energy_kwh=None,
            estimated_energy_kwh=None,
            observed_kwh_per_km=None,
            estimated_kwh_per_km=None,
            mae_kwh=None,
            bias_kwh=None,
        )
    return Summary(
        trips=trips,
        distance_km=round(distance, 2),
        energy_trips=energy_trips,
        observed_energy_kwh=round(observed, 3),
        estimated_energy_kwh=round(estimated, 3),
        observed_kwh_per_km=round(observed / energy_distance, 3),
        estimated_kwh_per_km=round(estimated / energy_distance, 3),
        mae_kwh=round(abs_error / energy_trips, 3),
        bias_kwh=round(error / energy_trips, 3),
    )


def summarize_plans(scores: Sequence[PlanScore]) -> PlanSummary:
    errors = [abs(s.error_kwh) for s in scores if s.error_kwh is not None]
    soc_errors = [
        s.arrival_soc_error_points for s in scores if s.arrival_soc_error_points is not None
    ]
    return PlanSummary(
        matched=len(scores),
        with_energy=len(errors),
        mae_kwh=round(sum(errors) / len(errors), 3) if errors else None,
        mean_arrival_soc_error_points=(
            round(sum(soc_errors) / len(soc_errors), 2) if soc_errors else None
        ),
    )
