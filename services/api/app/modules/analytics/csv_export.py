"""CSV rendering of the analytics table (contract §3.11b, phase5_plan.md D6).

Each row is rendered from the same JSON-mode dump the ``/analytics/trips``
endpoint returns, so parsing the CSV gives the same values as the JSON pages.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from typing import Any

from app.modules.analytics.schemas import CSV_COLUMNS, AnalyticsTrip

# Spreadsheet apps treat cells starting with these as formulas.
INJECTION_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def escape_text(value: str) -> str:
    """Prefix ``'`` to a text cell a spreadsheet would run as a formula."""

    return "'" + value if value.startswith(INJECTION_PREFIXES) else value


def format_cell(value: Any) -> str:
    """One JSON value as a CSV cell. Only text cells are ever escaped."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list):
        return escape_text(";".join(str(v) for v in value))
    return escape_text(str(value))


def render_csv(rows: Iterable[AnalyticsTrip]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        data = row.model_dump(mode="json")
        writer.writerow([format_cell(data[column]) for column in CSV_COLUMNS])
    return buffer.getvalue()
