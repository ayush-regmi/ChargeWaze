"""Static default specs per make/model/year (phase2_plan.md D8).

Smartcar exposes no battery capacity for the 2026 bZ (smartcar-v3-notes.md §7),
so defaults come from here or from the user (F7). Every entry must cite a
``source``; we never guess numbers (PRD §7). A vehicle with no matching entry
gets ``NULL`` usable capacity and empty connectors, and the UI asks the user.

The catalog is intentionally empty until an entry with a citable source is
added.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


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


CATALOG: tuple[CatalogSpec, ...] = ()


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


__all__ = ["CATALOG", "CatalogSpec", "lookup_spec"]
