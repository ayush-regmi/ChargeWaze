"""CSV cell rules (contract §3.11b, phase5_plan.md D6)."""

from __future__ import annotations

import pytest

from app.modules.analytics.csv_export import escape_text, format_cell
from app.modules.analytics.schemas import CSV_COLUMNS

CONTRACT_COLUMNS = [
    "id",
    "vehicle_id",
    "start_time",
    "start_time_latest",
    "end_time",
    "distance_km",
    "soc_start_percent",
    "soc_end_percent",
    "observed_energy_kwh",
    "observed_energy_uncertainty_kwh",
    "estimated_energy_kwh",
    "error_kwh",
    "observed_kwh_per_km",
    "energy_source",
    "quality_flag",
    "quality_reasons",
    "review_status",
    "in_progress",
    "included_in_aggregates",
    "planned_trip_id",
    "plan_origin_label",
    "plan_destination_label",
    "model_version",
    "is_simulated",
]


def test_columns_are_the_contract_fields_in_order() -> None:
    assert list(CSV_COLUMNS) == CONTRACT_COLUMNS


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r"])
def test_formula_prefixes_are_escaped(prefix: str) -> None:
    assert escape_text(f"{prefix}SUM(A1)") == f"'{prefix}SUM(A1)"
    assert format_cell(f"{prefix}SUM(A1)") == f"'{prefix}SUM(A1)"


def test_plain_text_is_unchanged() -> None:
    assert format_cell("Frederick, MD") == "Frederick, MD"
    assert format_cell("a=b") == "a=b"


def test_numbers_are_never_escaped() -> None:
    assert format_cell(-0.46) == "-0.46"
    assert format_cell(406.0) == "406.0"
    assert format_cell(0) == "0"


def test_null_booleans_and_lists() -> None:
    assert format_cell(None) == ""
    assert format_cell(True) == "true"
    assert format_cell(False) == "false"
    assert format_cell(["soc_delta_small", "start_time_bounded"]) == (
        "soc_delta_small;start_time_bounded"
    )
    assert format_cell([]) == ""
