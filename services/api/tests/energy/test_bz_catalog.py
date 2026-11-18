"""The 2026 bZ XLE AWD catalog entry (phase4_plan.md §2.3)."""

from __future__ import annotations

from decimal import Decimal

from app.modules.vehicles.spec_catalog import CATALOG, lookup_spec


def test_bz_entry_lookup_is_case_insensitive() -> None:
    entry = lookup_spec("TOYOTA", "bZ XLE AWD", 2026)
    assert entry is not None
    assert lookup_spec("toyota", "BZ  xle awd", 2026) is entry
    assert entry.usable_capacity_kwh == Decimal("71.0")
    assert entry.baseline_kwh_per_km == Decimal("0.20")
    assert entry.connectors == ("NACS", "CCS1", "J1772")
    assert entry.charging_curve_source == "derived"
    assert [(p["soc"], p["power_kw"]) for p in entry.charging_curve] == [
        (0.00, 100.0),
        (0.10, 150.0),
        (0.30, 150.0),
        (0.50, 120.0),
        (0.80, 55.0),
        (1.00, 15.0),
    ]
    assert "derived" in entry.source.casefold()


def test_other_bz_trims_and_years_have_no_entry() -> None:
    assert lookup_spec("TOYOTA", "bZ XLE FWD", 2026) is None
    assert lookup_spec("TOYOTA", "bZ Limited", 2026) is None
    assert lookup_spec("TOYOTA", "bZ XLE AWD", 2025) is None


def test_every_entry_with_a_curve_says_where_it_came_from() -> None:
    for entry in CATALOG:
        if entry.charging_curve:
            assert entry.charging_curve_source in ("measured", "derived")
