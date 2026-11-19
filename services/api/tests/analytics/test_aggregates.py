"""Aggregate definitions (phase5_plan.md §3 F5, D12)."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.modules.analytics import aggregates
from app.modules.trips.observed_queries import AnalyticsLeg, PlanScore

END = datetime(2026, 11, 8, 13, tzinfo=UTC)


def _leg(
    distance: float, energy: float | None, estimated: float | None, **overrides: object
) -> AnalyticsLeg:
    leg = AnalyticsLeg(
        id=uuid.uuid4(),
        vehicle_id=uuid.uuid4(),
        start_time=END,
        start_time_latest=END,
        end_time=END,
        distance_km=distance,
        soc_start_percent=80.0,
        soc_end_percent=20.0,
        energy_kwh=energy,
        energy_kwh_uncertainty=0.71,
        energy_source=None if energy is None else "soc_inferred",
        quality_flag="high",
        quality_reasons=(),
        review_status="auto",
        in_progress=False,
        planned_trip_id=None,
        plan_origin_label=None,
        plan_destination_label=None,
        is_simulated=False,
        estimated_energy_kwh=estimated,
        error_kwh=None if energy is None or estimated is None else round(energy - estimated, 3),
    )
    return replace(leg, **overrides)  # type: ignore[arg-type]


def test_example_3_day() -> None:
    legs = [_leg(222.40, 44.020, 44.480), _leg(183.60, 36.210, 36.720)]
    s = aggregates.summarize(legs)
    assert s == aggregates.Summary(
        trips=2,
        distance_km=406.0,
        energy_trips=2,
        observed_energy_kwh=80.23,
        estimated_energy_kwh=81.2,
        observed_kwh_per_km=0.198,
        estimated_kwh_per_km=0.2,
        mae_kwh=0.485,
        bias_kwh=-0.485,
    )


def test_energy_metrics_use_only_legs_with_both_sides() -> None:
    legs = [
        _leg(100.0, 20.0, 21.0),
        _leg(50.0, None, None),  # no energy
        _leg(30.0, 6.0, None),  # energy, but no backtest
    ]
    s = aggregates.summarize(legs)
    assert (s.trips, s.distance_km, s.energy_trips) == (3, 180.0, 1)
    assert (s.observed_energy_kwh, s.estimated_energy_kwh) == (20.0, 21.0)
    # Distance-weighted over the energy legs only.
    assert (s.observed_kwh_per_km, s.estimated_kwh_per_km) == (0.2, 0.21)
    assert (s.mae_kwh, s.bias_kwh) == (1.0, -1.0)


def test_mae_and_bias_differ_when_errors_have_mixed_signs() -> None:
    s = aggregates.summarize([_leg(10.0, 3.0, 2.0), _leg(10.0, 1.0, 2.0), _leg(10.0, 2.5, 2.0)])
    assert s.mae_kwh == pytest.approx(0.833)
    assert s.bias_kwh == pytest.approx(0.167)


def test_empty_summary_has_zero_counts_and_null_ratios() -> None:
    s = aggregates.summarize([])
    assert (s.trips, s.distance_km, s.energy_trips) == (0, 0.0, 0)
    assert s.observed_kwh_per_km is None and s.mae_kwh is None and s.bias_kwh is None
    no_energy = aggregates.summarize([_leg(12.0, None, None)])
    assert (no_energy.trips, no_energy.energy_trips, no_energy.observed_energy_kwh) == (1, 0, None)


@pytest.mark.parametrize(
    ("overrides", "include_simulated", "expected"),
    [
        ({}, False, True),
        ({"review_status": "confirmed"}, False, True),
        ({"review_status": "needs_review"}, False, False),
        ({"review_status": "rejected"}, False, False),
        ({"in_progress": True}, False, False),
        ({"is_simulated": True}, False, False),
        ({"is_simulated": True}, True, True),
    ],
)
def test_included_in_aggregates(
    overrides: dict[str, object], include_simulated: bool, expected: bool
) -> None:
    leg = _leg(10.0, 2.0, 2.0, **overrides)
    assert aggregates.included_in_aggregates(leg, include_simulated=include_simulated) is expected


def test_plan_summary() -> None:
    scores = [
        PlanScore(uuid.uuid4(), 80.52, 80.23, -0.29, -0.3),
        PlanScore(uuid.uuid4(), 20.0, None, None, 1.5),
    ]
    assert aggregates.summarize_plans(scores) == aggregates.PlanSummary(
        matched=2, with_energy=1, mae_kwh=0.29, mean_arrival_soc_error_points=0.6
    )
    assert aggregates.summarize_plans([]) == aggregates.PlanSummary(0, 0, None, None)
