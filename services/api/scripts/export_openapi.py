"""Dump the FastAPI OpenAPI document to ``services/api/openapi.json``.

This file is the source of truth consumed by
``packages/api-client``'s code generator (openapi-typescript). The frontend
never hand-writes request/response types; it imports them from the generated
schema.

Run with:

    uv run python scripts/export_openapi.py
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import create_app

OUTPUT = Path(__file__).resolve().parents[1] / "openapi.json"


def main() -> None:
    app = create_app()
    schema = app.openapi()
    OUTPUT.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(Path.cwd())} ({len(schema.get('paths', {}))} paths)")


if __name__ == "__main__":
    main()
