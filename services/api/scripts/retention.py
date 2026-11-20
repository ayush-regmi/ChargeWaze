"""Nightly retention job (P6-F5, docs/database-design.md §6).

Deletes, in short transactions:

- unsaved, unmatched planned trips older than PRIVACY_UNSAVED_TRIP_DAYS (30);
- vehicle_telemetry whose provider ``recorded_at`` is older than
  PRIVACY_TELEMETRY_RETENTION_DAYS (730), in batches per vehicle;
- vehicle_refreshes older than PRIVACY_REFRESH_LOG_DAYS (30).

Then works through the account-deletion backlog: retries Smartcar
revocations (until PRIVACY_REVOCATION_GIVE_UP_DAYS) and Supabase auth-user
deletions that failed during ``DELETE /me``, and removes tombstones with
nothing owed whose auth user was deleted more than PRIVACY_TOMBSTONE_DAYS (7)
ago (counted from the auth-user deletion, so a token issued just before it has
expired by then).

Prints one line of counts; no IDs. Exit code 0, or 1 if deletion work is
still owed after the run (so a scheduler can alert on it).

Run from services/api/ once a day. It uses ``DATABASE_URL``
(``services/api/.env`` points at hosted Supabase, so set it explicitly for a
local database):

  uv run python scripts/retention.py --dry-run
  uv run python scripts/retention.py
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))

from sqlalchemy.orm import Session  # noqa: E402

from app.core.config import Settings, get_settings  # noqa: E402
from app.db.session import get_engine  # noqa: E402
from app.integrations.smartcar.client import SmartcarClient  # noqa: E402
from app.integrations.supabase.admin import SupabaseAdminClient  # noqa: E402
from app.modules.trips import retention as trips_retention  # noqa: E402
from app.modules.users import privacy  # noqa: E402
from app.modules.vehicles import retention as vehicles_retention  # noqa: E402

TELEMETRY_BATCH_SIZE = 5000


def run(
    db: Session,
    *,
    settings: Settings,
    now: datetime,
    dry_run: bool,
    smartcar_factory: Callable[[], SmartcarClient],
    supabase_factory: Callable[[], SupabaseAdminClient],
    telemetry_batch_size: int = TELEMETRY_BATCH_SIZE,
) -> dict[str, int]:
    """One retention pass. Commits after each step unless ``dry_run``."""

    plans_cutoff = now - timedelta(days=settings.privacy_unsaved_trip_days)
    telemetry_cutoff = now - timedelta(days=settings.privacy_telemetry_retention_days)
    refresh_cutoff = now - timedelta(days=settings.privacy_refresh_log_days)
    counts: dict[str, int] = {}

    if dry_run:
        counts["unsaved_plans"] = trips_retention.count_expired_unsaved_plans(
            db, older_than=plans_cutoff
        )
        counts["telemetry"] = vehicles_retention.count_old_telemetry(
            db, older_than=telemetry_cutoff
        )
        counts["refreshes"] = vehicles_retention.count_old_refreshes(db, older_than=refresh_cutoff)
        counts["deletions_owed"] = privacy.count_deletion_backlog(db)
        db.rollback()
        return counts

    counts["unsaved_plans"] = trips_retention.purge_expired_unsaved_plans(
        db, older_than=plans_cutoff
    )
    db.commit()

    telemetry = 0
    for vehicle_id in vehicles_retention.vehicle_ids(db):
        while True:
            deleted = vehicles_retention.purge_old_telemetry_batch(
                db,
                vehicle_id=vehicle_id,
                older_than=telemetry_cutoff,
                batch_size=telemetry_batch_size,
            )
            db.commit()
            telemetry += deleted
            if deleted < telemetry_batch_size:
                break
    counts["telemetry"] = telemetry

    counts["refreshes"] = vehicles_retention.purge_old_refreshes(db, older_than=refresh_cutoff)
    db.commit()

    smartcar = smartcar_factory()
    supabase = supabase_factory()
    try:
        backlog = privacy.process_deletion_backlog(
            db,
            smartcar,
            supabase,
            now=now,
            tombstone_days=settings.privacy_tombstone_days,
            give_up_days=settings.privacy_revocation_give_up_days,
        )
        db.commit()
    finally:
        smartcar.close()
        supabase.close()
    counts["smartcar_revoked"] = backlog.smartcar_revoked
    counts["smartcar_pending"] = backlog.smartcar_pending
    counts["smartcar_abandoned"] = backlog.smartcar_abandoned
    counts["auth_users_deleted"] = backlog.auth_users_deleted
    counts["auth_users_pending"] = backlog.auth_users_pending
    counts["tombstones_removed"] = backlog.tombstones_removed
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="Count only; change nothing.")
    args = parser.parse_args(argv)

    settings = get_settings()
    with Session(get_engine()) as db:
        counts = run(
            db,
            settings=settings,
            now=datetime.now(UTC),
            dry_run=args.dry_run,
            smartcar_factory=lambda: SmartcarClient.from_settings(settings),
            supabase_factory=lambda: SupabaseAdminClient.from_settings(settings),
        )
    prefix = "dry_run " if args.dry_run else ""
    print(prefix + " ".join(f"{key}={value}" for key, value in counts.items()))
    owed = counts.get("smartcar_pending", 0) + counts.get("auth_users_pending", 0)
    return 1 if owed else 0


if __name__ == "__main__":
    raise SystemExit(main())
