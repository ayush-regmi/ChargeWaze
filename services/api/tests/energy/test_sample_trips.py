"""The energy-model numbers in the hand-checked trips (phase4_plan.md §5, D8).

Stop choice belongs to the optimizer (F3); here we check the per-leg energy,
arrival/needed SoCs and the charging pieces and totals the worked tables use.
"""

from __future__ import annotations

import pytest

from app.modules.energy import (
    EnergyVehicle,
    arrival_soc,
    charging_minutes,
    segment_energy_kwh,
    soc_used,
)

RESERVE = 0.15
CHARGE_TO = 0.80
ROAD_FACTOR = 1.3


def _leg_km(along_km: float, off_route_km: float) -> float:
    return along_km + 2 * off_route_km * ROAD_FACTOR


# ---- Trip 1: local, no charge ------------------------------------------------


def test_trip1_local_no_charge(bz: EnergyVehicle) -> None:
    assert segment_energy_kwh(bz, 80.0) == pytest.approx(16.0)
    assert soc_used(bz, 80.0) == pytest.approx(0.22535, abs=1e-5)
    arrival = arrival_soc(bz, 0.72, 80.0)
    assert arrival == pytest.approx(0.49465, abs=1e-5)
    assert arrival >= RESERVE
    assert round(arrival, 3) == 0.495


# ---- Trip 2: ~250 mi, one stop at B -----------------------------------------


def test_trip2_needs_a_stop(bz: EnergyVehicle) -> None:
    assert soc_used(bz, 400.0) == pytest.approx(1.12676, abs=1e-5)
    assert arrival_soc(bz, 0.80, 400.0) < RESERVE


def test_trip2_stop_b(bz: EnergyVehicle) -> None:
    leg = _leg_km(220.0, 1.0)
    assert leg == pytest.approx(222.6)
    assert soc_used(bz, leg) == pytest.approx(0.62704, abs=1e-5)
    arrival = arrival_soc(bz, 0.80, leg)
    assert arrival == pytest.approx(0.17296, abs=1e-5)
    assert arrival >= RESERVE

    needed = RESERVE + soc_used(bz, 400.0 - 220.0)
    assert needed == pytest.approx(0.65704, abs=1e-5)
    depart = 0.66  # smallest 1 % grid value ≥ needed (D16)

    # Station 250 kW: the vehicle curve applies.
    assert charging_minutes(bz, arrival, 0.30, 250.0) == pytest.approx(3.608, abs=5e-4)
    assert charging_minutes(bz, 0.30, 0.50, 250.0) == pytest.approx(6.337, abs=5e-4)
    assert charging_minutes(bz, 0.50, depart, 250.0) == pytest.approx(6.703, abs=5e-4)
    assert charging_minutes(bz, arrival, depart, 250.0) == pytest.approx(16.648, abs=5e-4)

    assert arrival_soc(bz, depart, 180.0) == pytest.approx(0.15296, abs=1e-5)


def test_trip2_rejected_single_stops(bz: EnergyVehicle) -> None:
    # A only: arrival 0.37380, would need 0.85423 > charge-to.
    assert arrival_soc(bz, 0.80, _leg_km(150.0, 0.5)) == pytest.approx(0.37380, abs=1e-5)
    assert RESERVE + soc_used(bz, 250.0) == pytest.approx(0.85423, abs=1e-5)
    assert RESERVE + soc_used(bz, 250.0) > CHARGE_TO
    # L only: would need 0.93873 > charge-to. §5 prints arrival 0.46018, but the leg is
    # 120 + 2 x 0.2 x 1.3 = 120.52 km → 0.46051 (doc slip; the rejection still holds).
    assert arrival_soc(bz, 0.80, _leg_km(120.0, 0.2)) == pytest.approx(0.46051, abs=1e-5)
    assert RESERVE + soc_used(bz, 280.0) == pytest.approx(0.93873, abs=1e-5)


# ---- Trip 3: Frederick -> Toronto, W -> B -> Q --------------------------------

ROUTE_KM = 748.1


def test_trip3_needs_three_stops(bz: EnergyVehicle) -> None:
    assert segment_energy_kwh(bz, ROUTE_KM) == pytest.approx(149.62)
    need = soc_used(bz, ROUTE_KM)
    assert need == pytest.approx(2.10732, abs=1e-5)
    per_stop = CHARGE_TO - RESERVE
    assert (0.90 - RESERVE) + 2 * per_stop < need < (0.90 - RESERVE) + 3 * per_stop


@pytest.mark.parametrize(
    ("start", "leg_km", "used", "arrival", "depart", "power", "pieces", "total"),
    [
        # W: 260 + 2.08 km, 150 kW, no cap.
        (0.90, 262.08, 0.73825, 0.16175, 0.61, 150.0, (3.926, 6.337, 4.353), 14.617),
        # B: 160 + 3.12 km, 250 kW, capped by the vehicle.
        (0.61, 163.12, 0.45949, 0.15051, 0.55, 250.0, (4.246, 6.337, 1.860), 12.443),
        # Q: 140 + 1.30 km, 150 kW.
        (0.55, 141.30, 0.39803, 0.15197, 0.68, 150.0, (4.204, 6.337, 7.728), 18.269),
    ],
    ids=["W", "B", "Q"],
)
def test_trip3_stops(
    bz: EnergyVehicle,
    start: float,
    leg_km: float,
    used: float,
    arrival: float,
    depart: float,
    power: float,
    pieces: tuple[float, float, float],
    total: float,
) -> None:
    assert soc_used(bz, leg_km) == pytest.approx(used, abs=1e-5)
    arrived = arrival_soc(bz, start, leg_km)
    assert arrived == pytest.approx(arrival, abs=1e-5)
    assert arrived >= RESERVE
    assert charging_minutes(bz, arrived, 0.30, power) == pytest.approx(pieces[0], abs=5e-4)
    assert charging_minutes(bz, 0.30, 0.50, power) == pytest.approx(pieces[1], abs=5e-4)
    assert charging_minutes(bz, 0.50, depart, power) == pytest.approx(pieces[2], abs=5e-4)
    assert charging_minutes(bz, arrived, depart, power) == pytest.approx(total, abs=5e-4)


def test_trip3_destination_and_totals(bz: EnergyVehicle) -> None:
    assert soc_used(bz, 188.1) == pytest.approx(0.52986, abs=1e-5)
    assert arrival_soc(bz, 0.68, 188.1) == pytest.approx(0.15014, abs=1e-5)

    charging = (
        charging_minutes(bz, arrival_soc(bz, 0.90, 262.08), 0.61, 150.0)
        + charging_minutes(bz, arrival_soc(bz, 0.61, 163.12), 0.55, 250.0)
        + charging_minutes(bz, arrival_soc(bz, 0.55, 141.30), 0.68, 150.0)
    )
    assert charging == pytest.approx(45.329, abs=2e-3)

    detour_km = 2.08 + 3.12 + 1.30
    assert segment_energy_kwh(bz, ROUTE_KM) + segment_energy_kwh(bz, detour_km) == (
        pytest.approx(150.92)
    )
