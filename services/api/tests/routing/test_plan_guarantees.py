"""PRD §9 guarantees for ``POST /api/v1/routes/plan`` that have no test of
their own elsewhere (phase4_plan.md §6, filled in by P4-F9):

- Missing station pricing: pricing never changes a plan.
- Smartcar failure: planning works from cached vehicle state and never calls
  Smartcar (D7).

The fixtures and helpers come from ``test_plan_endpoint.py`` (P4-F4), so both
files exercise the same wiring: real ``MapboxClient`` over ``MockTransport``,
a scripted ``StationProvider``, the shared Postgres test database.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.integrations.smartcar import client as smartcar_client
from app.integrations.smartcar.client import SmartcarClient, get_smartcar_client
from app.modules.routing.plan_types import CandidateStation
from app.modules.vehicles.models import Vehicle
from tests.routing import test_plan_endpoint as endpoint
from tests.routing.conftest import synthetic_directions
from tests.routing.test_plan_endpoint import (
    PLAN,
    FakeMapbox,
    ScriptedStations,
    synthetic_body,
    trip2_stations,
)

# Reuse P4-F4's fixtures as-is (assigned, not imported by name, so the test
# parameters that request them don't shadow an import).
app_settings = endpoint.app_settings
mapbox = endpoint.mapbox
afdc = endpoint.afdc
scripted = endpoint.scripted
wire = endpoint.wire
db = endpoint.db
auth = endpoint.auth
add_vehicle = endpoint.add_vehicle


def _plan_outcome(plan: dict[str, Any]) -> dict[str, Any]:
    """The parts of a plan that pricing could influence: stops and totals."""

    return {
        "status": plan["status"],
        "stops": [
            {
                "name": s["station"]["name"],
                "arrival_soc": s["arrival_soc"],
                "departure_soc": s["departure_soc"],
                "charging_minutes": s["charging_minutes"],
                "detour_minutes": s["detour_minutes"],
            }
            for s in plan["stops"]
        ],
        "arrival_soc": plan["arrival_soc"],
        "totals": plan["totals"],
    }


# ---------------------------------------------------------------------------
# Missing station pricing (PRD §9)
# ---------------------------------------------------------------------------


def test_optimizer_candidates_cannot_carry_pricing() -> None:
    """The optimizer's input has no pricing field, so it can't rank by price."""

    fields = {f.name for f in dataclasses.fields(CandidateStation)}
    assert not any("pric" in name or "cost" in name for name in fields), fields


def test_station_pricing_never_changes_the_plan(
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
) -> None:
    """§5 trip 2 with no pricing, then with pricing that would favour A over B:
    the same stop (B), SoCs, minutes and totals either way."""

    mapbox.serve(synthetic_directions(400, 270))
    api = wire(scripted)
    vehicle = add_vehicle()

    scripted.stations = trip2_stations()
    assert all(s.pricing_text is None for s in scripted.stations)
    unpriced = api.post(PLAN, json=synthetic_body(vehicle, 400), headers=auth())
    assert unpriced.status_code == 200, unpriced.text

    prices = {"L": "Free", "A": "Free", "X": "Free", "B": "$0.99/kWh + $5 session fee"}
    scripted.stations = [
        s.model_copy(update={"pricing_text": prices[s.name]}) for s in trip2_stations()
    ]
    priced = api.post(PLAN, json=synthetic_body(vehicle, 400), headers=auth())
    assert priced.status_code == 200, priced.text

    assert unpriced.json()["trip_id"] != priced.json()["trip_id"]
    outcome = _plan_outcome(priced.json())
    assert outcome == _plan_outcome(unpriced.json())
    assert outcome["status"] == "feasible"
    assert [s["name"] for s in outcome["stops"]] == ["B"]


# ---------------------------------------------------------------------------
# Smartcar failure (PRD §9, D7)
# ---------------------------------------------------------------------------


def _smartcar_is_down(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("POST /routes/plan must not call Smartcar")


@pytest.fixture()
def smartcar_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every Smartcar client entry point raises, including the dependency."""

    for name, member in vars(SmartcarClient).items():
        if callable(member) and not name.startswith("__"):
            monkeypatch.setattr(SmartcarClient, name, _smartcar_is_down)
    monkeypatch.setattr(smartcar_client, "_default_client", _smartcar_is_down)


def test_planning_uses_cached_state_when_smartcar_is_down(
    smartcar_down: None,
    wire: Callable[..., TestClient],
    auth: Callable[..., dict[str, str]],
    add_vehicle: Callable[..., Vehicle],
    mapbox: FakeMapbox,
    scripted: ScriptedStations,
) -> None:
    mapbox.serve(synthetic_directions(400, 270))
    scripted.stations = trip2_stations()
    api = wire(scripted)
    api.app.dependency_overrides[get_smartcar_client] = _smartcar_is_down  # type: ignore[attr-defined]
    vehicle = add_vehicle(soc_percent=80, soc_age=timedelta(minutes=5))

    response = api.post(
        PLAN, json=synthetic_body(vehicle, 400, starting_soc_override=None), headers=auth()
    )

    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["starting_soc"] == 0.8
    assert plan["starting_soc_source"] == "telemetry"
    assert plan["status"] == "feasible"
    assert [s["station"]["name"] for s in plan["stops"]] == ["B"]
