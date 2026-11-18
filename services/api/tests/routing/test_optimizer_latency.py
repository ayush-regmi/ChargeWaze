"""Latency of the pure search on the real Frederick → Toronto AFDC fixture
(phase4_plan.md §3 F3: < 500 ms)."""

from __future__ import annotations

import time

from app.modules.energy import EnergyVehicle
from app.modules.routing.optimizer import optimize
from app.modules.routing.plan_types import FeasiblePlan, PlannerSettings
from tests.routing.conftest import frederick_toronto_candidates, frederick_toronto_route


def test_real_fixture_candidates_are_the_full_corridor() -> None:
    candidates = frederick_toronto_candidates()
    # 122 DC stations in the fixture; one has no bZ-compatible connector with
    # known power (D12).
    assert len(candidates) == 121
    assert {c.connector for c in candidates} == {"NACS", "CCS1"}


def test_frederick_toronto_latency_under_500_ms(
    bz: EnergyVehicle, settings: PlannerSettings
) -> None:
    route = frederick_toronto_route()
    candidates = frederick_toronto_candidates()

    timings = []
    for starting_soc in (0.30, 0.50, 0.90, 1.00):
        started = time.perf_counter()
        result = optimize(route, candidates, bz, settings, starting_soc)
        timings.append((time.perf_counter() - started) * 1000)
        assert isinstance(result, FeasiblePlan)
        assert result.arrival_soc >= settings.reserve_soc - 1e-9
        assert all(s.arrival_soc >= settings.reserve_soc - 1e-9 for s in result.stops)

    assert max(timings) < 500, f"optimizer took {max(timings):.0f} ms"
