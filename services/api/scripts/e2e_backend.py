"""Prepare and seed the local database the Playwright E2E suite runs against.

Used by ``apps/web/playwright.config.ts``; you rarely run it by hand.

  uv run python scripts/e2e_backend.py prepare
      Create the E2E database if it's missing and migrate it to head.
      Runs before the API starts.

  uv run python scripts/e2e_backend.py seed --api-url http://127.0.0.1:8765
      Reset the fixed E2E user, give them one connected Toyota, and deliver a
      signed ``VEHICLE_STATE`` webhook for it through the running API, so the
      Dashboard is fed by the real ingestor rather than rows written directly.

  uv run python scripts/e2e_backend.py seed --stale-soc
      The same, but the webhook keeps the fixture's fixed (2026-11-07)
      timestamps instead of "now", so the battery reading is stale and
      ``POST /routes/plan`` asks for a starting SoC (P4-F6).

Settings come from the environment (the Playwright config sets them). The
script refuses any ``DATABASE_URL`` that isn't on this machine or whose
database name doesn't end in ``_e2e``, because ``seed`` deletes the E2E user's
vehicles. No Smartcar API is called; the
webhook is signed locally with the test-only ``SMARTCAR_MANAGEMENT_TOKEN``.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, delete, make_url, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

API_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_ROOT))
sys.path.insert(0, str(API_ROOT / "scripts"))

import send_test_webhook  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402

from alembic import command  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.modules.users.models import Profile, UserPreferences  # noqa: E402
from app.modules.users.privacy_models import AccountDeletion  # noqa: E402
from app.modules.vehicles.models import Vehicle, VehicleSpecs  # noqa: E402
from app.modules.vehicles.spec_catalog import lookup_spec  # noqa: E402

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Must match apps/web/e2e/support/e2e-auth.mjs.
E2E_USER_ID = uuid.UUID("e2e00000-0000-4000-8000-000000000001")
E2E_SMARTCAR_VEHICLE_ID = "e2e-smartcar-vehicle"
E2E_SMARTCAR_USER_ID = "e2e-smartcar-user"
E2E_MAKE, E2E_MODEL, E2E_YEAR = "TOYOTA", "bZ XLE AWD", 2026


def _database_url() -> URL:
    url = make_url(get_settings().database_url)
    if url.host not in LOCAL_HOSTS:
        raise SystemExit(f"Refusing to touch non-local database host {url.host!r}.")
    # Only ever a dedicated E2E database, never the dev or test one. Without
    # the Playwright env, DATABASE_URL falls back to the dev database.
    if not (url.database or "").endswith("_e2e"):
        raise SystemExit(
            f"Refusing to use database {url.database!r}: the E2E database name must end "
            "in '_e2e'. Run through Playwright, or set DATABASE_URL to the E2E database."
        )
    return url


def prepare() -> None:
    url = _database_url()
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
        ).scalar()
        if not exists:
            # Database names can't be bound parameters; this one comes from our
            # own local-only setting and is quoted.
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()

    cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    print(f"E2E database {url.database} is at head.")


def seed(api_url: str, *, stale_soc: bool = False) -> int:
    engine = create_engine(_database_url())
    with Session(engine) as db, db.begin():
        db.execute(delete(Vehicle).where(Vehicle.user_id == E2E_USER_ID))
        # The account-deletion spec leaves a tombstone that would refuse the
        # E2E user's next profile (P6-F5); a reset starts from a live account.
        db.execute(delete(AccountDeletion).where(AccountDeletion.user_id == E2E_USER_ID))
        if db.get(Profile, E2E_USER_ID) is None:
            db.add(Profile(id=E2E_USER_ID, display_name="E2E Driver"))
            db.flush()
        prefs = db.get(UserPreferences, E2E_USER_ID)
        if prefs is not None:
            db.delete(prefs)
            db.flush()
        db.add(UserPreferences(user_id=E2E_USER_ID))

        entry = lookup_spec(E2E_MAKE, E2E_MODEL, E2E_YEAR)
        db.add(
            Vehicle(
                user_id=E2E_USER_ID,
                smartcar_vehicle_id=E2E_SMARTCAR_VEHICLE_ID,
                smartcar_user_id=E2E_SMARTCAR_USER_ID,
                make=E2E_MAKE,
                model=E2E_MODEL,
                year=E2E_YEAR,
                connection_status="connected",
                supported_signals=[
                    "tractionbattery-stateofcharge",
                    "tractionbattery-range",
                    "charge-ischarging",
                ],
                connected_at=datetime.now(UTC),
                specs=VehicleSpecs(
                    usable_capacity_kwh=entry.usable_capacity_kwh if entry else None,
                    connectors=list(entry.connectors) if entry else [],
                    charging_curve=[dict(p) for p in entry.charging_curve] if entry else [],
                ),
            )
        )
    engine.dispose()

    args = [
        "--url",
        f"{api_url.rstrip('/')}/api/v1/webhooks/smartcar",
        "--vehicle-id",
        E2E_SMARTCAR_VEHICLE_ID,
        "--user-id",
        E2E_SMARTCAR_USER_ID,
    ]
    # Without --now the fixture's own timestamps (days old) are kept.
    return send_test_webhook.main(args if stale_soc else [*args, "--now"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare", help="Create and migrate the E2E database")
    seed_p = sub.add_parser("seed", help="Reset the E2E user and send a signed webhook")
    seed_p.add_argument("--api-url", default="http://127.0.0.1:8765")
    seed_p.add_argument(
        "--stale-soc",
        action="store_true",
        help="Keep the webhook fixture's old timestamps, so the SoC reading is stale",
    )
    args = parser.parse_args(argv)

    if args.cmd == "prepare":
        prepare()
        return 0
    return seed(args.api_url, stale_soc=args.stale_soc)


if __name__ == "__main__":
    raise SystemExit(main())
