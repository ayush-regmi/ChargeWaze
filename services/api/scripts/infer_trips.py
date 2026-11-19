"""Catch-up trip inference (Phase 5 F3, ADR 0009).

Inference normally runs on its own after each telemetry webhook. Use this after
a deploy, a bug fix, or to rebuild a vehicle's history. It calls the same
function as ``POST /api/v1/observed-trips/recompute`` and prints counts only.
Confirmed and rejected legs are never changed.

Run from services/api/. It uses ``DATABASE_URL`` (``services/api/.env`` points
at hosted Supabase, so set it explicitly for a local database):

  uv run python scripts/infer_trips.py --vehicle-id <uuid> [--full]
  uv run python scripts/infer_trips.py --all [--full]
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.session import get_engine  # noqa: E402
from app.modules.trips.observed_service import run_for_vehicle  # noqa: E402
from app.modules.vehicles.models import Vehicle  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--vehicle-id", type=uuid.UUID, help="One vehicle (Chargewise ID).")
    target.add_argument("--all", action="store_true", help="Every vehicle.")
    parser.add_argument("--full", action="store_true", help="Rebuild the whole history.")
    args = parser.parse_args(argv)

    totals = {"vehicles": 0, "created": 0, "updated": 0, "deleted": 0, "unchanged": 0}
    with Session(get_engine()) as db:
        query = select(Vehicle.id, Vehicle.user_id).order_by(Vehicle.id)
        if args.vehicle_id is not None:
            query = query.where(Vehicle.id == args.vehicle_id)
        vehicles = db.execute(query).all()
        if not vehicles:
            print("No matching vehicle.", file=sys.stderr)
            return 1
        for vehicle_id, user_id in vehicles:
            summary = run_for_vehicle(db, vehicle_id=vehicle_id, user_id=user_id, full=args.full)
            db.commit()  # releases the vehicle's advisory lock
            totals["vehicles"] += 1
            totals["created"] += summary.legs_created
            totals["updated"] += summary.legs_updated
            totals["deleted"] += summary.legs_deleted
            totals["unchanged"] += summary.legs_unchanged

    print(
        "vehicles={vehicles} legs_created={created} legs_updated={updated} "
        "legs_deleted={deleted} legs_unchanged={unchanged}".format(**totals)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
