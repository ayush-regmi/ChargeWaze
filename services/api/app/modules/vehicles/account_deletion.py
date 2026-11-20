"""Smartcar side of account deletion (P6-F5).

The users module deletes the account; this module owns the vehicle rows and
the Smartcar calls, so it does the revocations (architecture §3: no
cross-module queries).

Policy: deletion never waits on Smartcar. A revocation that fails (Smartcar
down, timeout, 5xx) is returned to the caller, which stores the connection ID
on the account's tombstone; ``scripts/retention.py`` retries it. Telemetry
can't come back meanwhile, because the vehicle rows are deleted and the
webhook ingestor drops events for unknown vehicles (api §3.12).

Logging rule (arch §9): counts and outcomes only, never Smartcar IDs.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from fastapi import status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.integrations.smartcar.client import SmartcarClient
from app.modules.vehicles.models import Vehicle

logger = logging.getLogger(__name__)

RevokeOutcome = Literal["revoked", "already_revoked", "failed", "skipped_in_use"]


@dataclass(frozen=True)
class AccountRevocations:
    vehicles: int
    revoked: int
    failed_connection_ids: tuple[str, ...]


def lock_and_revoke_user_vehicles(
    db: Session, smartcar: SmartcarClient, *, user_id: uuid.UUID
) -> AccountRevocations:
    """Lock every vehicle of ``user_id`` and revoke its Smartcar connection.

    Takes ``FOR UPDATE`` on the rows, the same lock the webhook ingestor and
    the on-demand refresh take, so a delivery in flight either commits before
    the account is deleted (and its rows cascade away) or waits and then finds
    no vehicle. Disconnected rows that still hold a connection ID are a
    revocation still owed (garage disconnect), so they are revoked too.
    Simulated vehicles have no connection. Doesn't commit.
    """

    vehicles = db.scalars(
        select(Vehicle).where(Vehicle.user_id == user_id).order_by(Vehicle.id).with_for_update()
    ).all()
    revoked = 0
    failed: list[str] = []
    for vehicle in vehicles:
        connection_id = vehicle.smartcar_connection_id
        if not connection_id:
            continue
        outcome = revoke_connection(smartcar, connection_id)
        if outcome == "failed":
            failed.append(connection_id)
        else:
            revoked += 1
    logger.info(
        "account_vehicles_revoked",
        extra={"vehicles": len(vehicles), "revoked": revoked, "failed": len(failed)},
    )
    return AccountRevocations(
        vehicles=len(vehicles), revoked=revoked, failed_connection_ids=tuple(failed)
    )


def retry_revocations(
    db: Session, smartcar: SmartcarClient, connection_ids: Iterable[str]
) -> list[str]:
    """Retry owed revocations for a deleted account. Returns the ones still failing.

    A connection ID that a stored vehicle uses again is skipped and dropped:
    revoking it would disconnect a live account's car.
    """

    remaining: list[str] = []
    for connection_id in connection_ids:
        in_use = db.scalar(
            select(Vehicle.id).where(Vehicle.smartcar_connection_id == connection_id).limit(1)
        )
        if in_use is not None:
            continue
        if revoke_connection(smartcar, connection_id) == "failed":
            remaining.append(connection_id)
    return remaining


def revoke_connection(smartcar: SmartcarClient, connection_id: str) -> RevokeOutcome:
    """``DELETE /v3/connections/{id}``. 404 means Smartcar no longer has it."""

    try:
        smartcar.delete_connection(connection_id)
    except ApiError as exc:
        if (exc.details or {}).get("upstream_status") == status.HTTP_404_NOT_FOUND:
            return "already_revoked"
        logger.warning("smartcar_revoke_failed", extra={"code": exc.code})
        return "failed"
    except ValidationError:
        # Smartcar answered the token request with something unexpected.
        logger.warning("smartcar_revoke_failed", extra={"code": "INVALID_RESPONSE"})
        return "failed"
    return "revoked"


__all__ = [
    "AccountRevocations",
    "RevokeOutcome",
    "lock_and_revoke_user_vehicles",
    "retry_revocations",
    "revoke_connection",
]
