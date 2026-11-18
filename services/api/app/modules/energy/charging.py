"""Charging time over a piecewise-linear curve (architecture §6.4,
phase4_plan.md §3 F2).

The effective power is ``P_eff(s) = min(P_vehicle(s), P_station)``. We split the
SoC axis at the curve points and wherever the vehicle curve crosses the station
power, so ``P_eff`` is linear on every piece. On a piece from ``s1`` to ``s2``
with powers ``p1`` and ``p2`` the exact integral of ``60 · B / P(s) ds`` is::

    60 · B · (s2 - s1) / p1                          if p1 == p2
    60 · B · (s2 - s1) / (p2 - p1) · ln(p2 / p1)      otherwise

Below the curve's first point the first point's power holds. Above its last
point charging isn't allowed (``max_charge_soc``). There is deliberately no flat
fallback for a vehicle without a curve (D2): ``EnergyVehicle`` requires one.
"""

from __future__ import annotations

import itertools
import math
from bisect import bisect_right
from collections.abc import Callable

from app.modules.energy.types import EnergyVehicle

# Float noise allowed when a SoC is compared with the curve's bounds; a value
# inside the tolerance is clamped to the bound.
_SOC_TOLERANCE = 1e-9


def max_charge_soc(vehicle: EnergyVehicle) -> float:
    """SoC of the curve's last point."""

    return vehicle.charging_curve[-1].soc


def charging_minutes(
    vehicle: EnergyVehicle, from_soc: float, to_soc: float, station_power_kw: float
) -> float:
    """∫ B / min(P(s), station) ds in minutes; 0 when to_soc <= from_soc.
    ValueError if a SoC is outside [0, max_charge_soc] or station_power_kw <= 0."""

    profile = _EffectiveCurve(vehicle, station_power_kw)
    start = profile.check_soc(from_soc)
    end = profile.check_soc(to_soc)
    if end <= start:
        return 0.0
    return profile.minutes_between(start, end)


def cumulative_charge_minutes(
    vehicle: EnergyVehicle, station_power_kw: float
) -> Callable[[float], float]:
    """F(s) = charging_minutes(vehicle, 0, s, station_power_kw), precomputed once per
    distinct station power so the optimizer does F(d) - F(a) in O(log n)."""

    profile = _EffectiveCurve(vehicle, station_power_kw)

    def minutes_from_empty(soc: float) -> float:
        return profile.minutes_to(profile.check_soc(soc))

    return minutes_from_empty


class _EffectiveCurve:
    """``P_eff`` as breakpoints ``(soc, power)`` with ``P_eff`` linear between
    neighbours, plus the cumulative minutes from SoC 0 at each breakpoint."""

    def __init__(self, vehicle: EnergyVehicle, station_power_kw: float) -> None:
        if not (math.isfinite(station_power_kw) and station_power_kw > 0):
            raise ValueError("station_power_kw must be a finite number > 0.")
        self._minutes_per_soc = 60.0 * vehicle.usable_capacity_kwh
        self._max_soc = max_charge_soc(vehicle)

        cap = station_power_kw
        curve = [(p.soc, p.power_kw) for p in vehicle.charging_curve]
        if curve[0][0] > 0.0:
            curve.insert(0, (0.0, curve[0][1]))  # first point's power holds below it

        socs: list[float] = [curve[0][0]]
        powers: list[float] = [min(curve[0][1], cap)]
        for (s1, p1), (s2, p2) in itertools.pairwise(curve):
            if (p1 - cap) * (p2 - cap) < 0:  # the vehicle curve crosses the station power
                socs.append(s1 + (s2 - s1) * (cap - p1) / (p2 - p1))
                powers.append(cap)
            socs.append(s2)
            powers.append(min(p2, cap))
        self._socs = socs
        self._powers = powers

        cumulative = [0.0]
        for i in range(len(socs) - 1):
            cumulative.append(cumulative[-1] + self._piece(i, socs[i + 1]))
        self._cumulative = cumulative

    def check_soc(self, soc: float) -> float:
        if not math.isfinite(soc) or soc < -_SOC_TOLERANCE or soc > self._max_soc + _SOC_TOLERANCE:
            raise ValueError(f"SoC {soc!r} is outside [0, {self._max_soc}] (max_charge_soc).")
        return min(max(soc, 0.0), self._max_soc)

    def minutes_to(self, soc: float) -> float:
        """Minutes from SoC 0 to ``soc`` (already checked)."""

        i = min(bisect_right(self._socs, soc) - 1, len(self._socs) - 2)
        return self._cumulative[i] + self._piece(i, soc)

    def minutes_between(self, start: float, end: float) -> float:
        return self.minutes_to(end) - self.minutes_to(start)

    def _piece(self, i: int, soc: float) -> float:
        """Minutes from breakpoint ``i`` to ``soc``, with ``soc`` inside piece ``i``."""

        s1, s2 = self._socs[i], self._socs[i + 1]
        p1, p2 = self._powers[i], self._powers[i + 1]
        delta_soc = soc - s1
        if delta_soc <= 0:
            return 0.0
        p_end = p1 + (p2 - p1) * delta_soc / (s2 - s1)
        return self._minutes_per_soc * delta_soc * _inverse_mean_power(p1, p_end)


def _inverse_mean_power(p1: float, p2: float) -> float:
    """``ln(p2 / p1) / (p2 - p1)``: the mean of ``1 / P`` over a linear ramp from
    ``p1`` to ``p2``. Uses ``log1p`` so nearly flat pieces stay accurate."""

    ratio = (p2 - p1) / p1
    if abs(ratio) < 1e-12:
        return 1.0 / p1
    return math.log1p(ratio) / (p2 - p1)


__all__ = ["charging_minutes", "cumulative_charge_minutes", "max_charge_soc"]
