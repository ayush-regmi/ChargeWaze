"""Static default specs per make/model/year (phase2_plan.md D8).

Smartcar exposes no battery capacity for the 2026 bZ (smartcar-v3-notes.md §7),
so defaults come from here or from the user (F7). Every entry must cite a
``source``; we never guess numbers (PRD §7). A vehicle with no matching entry
gets ``NULL`` usable capacity and empty connectors, and the UI asks the user.

Phase 4 (phase4_plan.md §2.3) adds the planning fields: ``baseline_kwh_per_km``
(D1, catalog-only until P4-F8) and ``charging_curve_source``, which says whether
the curve was measured or derived from published figures (D2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

ChargingCurveSource = Literal["measured", "derived"]


@dataclass(frozen=True)
class CatalogSpec:
    make: str
    # Matched as a case-insensitive prefix, because Smartcar's model string
    # includes the trim (e.g. "bZ XLE AWD").
    model_prefix: str
    year_from: int
    year_to: int
    usable_capacity_kwh: Decimal | None
    connectors: tuple[str, ...]
    source: str
    charging_curve: tuple[dict[str, float], ...] = field(default_factory=tuple)
    # Flat consumption for planning (phase4_plan.md D1). None = can't be planned.
    baseline_kwh_per_km: Decimal | None = None
    # None when there is no curve.
    charging_curve_source: ChargingCurveSource | None = None


_BZ_XLE_AWD_2026 = CatalogSpec(
    make="Toyota",
    # Smartcar's model string on the real car (smartcar-v3-notes.md §7). Other
    # bZ trims (57.7 kWh FWD, Limited) have no cited numbers yet, so no entry.
    model_prefix="bZ XLE AWD",
    year_from=2026,
    year_to=2026,
    usable_capacity_kwh=Decimal("71.0"),
    connectors=("NACS", "CCS1", "J1772"),
    baseline_kwh_per_km=Decimal("0.20"),
    charging_curve=(
        {"soc": 0.00, "power_kw": 100.0},
        {"soc": 0.10, "power_kw": 150.0},
        {"soc": 0.30, "power_kw": 150.0},
        {"soc": 0.50, "power_kw": 120.0},
        {"soc": 0.80, "power_kw": 55.0},
        {"soc": 1.00, "power_kw": 15.0},
    ),
    charging_curve_source="derived",
    source=(
        "phase4_plan.md §2.3. Usable capacity 71.0 kWh (74.7 kWh nominal) and "
        "323 Wh/mi mild-weather highway consumption (0.2007 kWh/km, rounded to "
        "0.20): EV Database, Toyota bZ4X Touring AWD 74.7 kWh MY26, "
        "https://ev-database.org/imp/car/3401/Toyota-bZ4X-Touring-AWD-747-kWh. "
        "Connectors: NACS port with Toyota-supplied CCS1 and J1772 adapters "
        "(PRD §2). Charging curve DERIVED, not measured: built to match 150 kW "
        "peak and ~110 kW average 10-80 % (EV Database) and 10-80 % in about "
        "30 min (Toyota pressroom, https://pressroom.toyota.com/?p=102972); "
        "gives 10-80 % in 27.4 min. Replace with a measured curve when one exists."
    ),
)

CATALOG: tuple[CatalogSpec, ...] = (_BZ_XLE_AWD_2026,)


def _norm(value: str) -> str:
    return " ".join(value.split()).casefold()


def lookup_spec(
    make: str,
    model: str,
    year: int,
    *,
    catalog: tuple[CatalogSpec, ...] = CATALOG,
) -> CatalogSpec | None:
    """Most specific catalog entry for the vehicle (longest model prefix), or None."""

    make_n, model_n = _norm(make), _norm(model)
    matches = [
        entry
        for entry in catalog
        if _norm(entry.make) == make_n
        and model_n.startswith(_norm(entry.model_prefix))
        and entry.year_from <= year <= entry.year_to
    ]
    if not matches:
        return None
    return max(matches, key=lambda entry: len(entry.model_prefix))


__all__ = ["CATALOG", "CatalogSpec", "ChargingCurveSource", "lookup_spec"]
