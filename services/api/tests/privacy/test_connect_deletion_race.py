"""Connect completion vs account deletion (regression for the PR #69 review).

Both take the per-account advisory lock (``users.service.lock_account``)
before reading anything, so a vehicle is never deleted without its Smartcar
connection being revoked (or recorded as owed), whichever runs first. The
race tests use committed rows on two real connections, cleaned up at the end.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ApiError
from app.integrations.smartcar.client import SmartcarClient
from app.integrations.supabase.admin import SupabaseAdminClient
from app.modules.users.models import Profile
from app.modules.users.privacy import delete_account
from app.modules.users.privacy_models import AccountDeletion
from app.modules.users.service import lock_account
from app.modules.vehicles.models import Vehicle
from app.modules.vehicles.service import complete_connect
from tests.conftest import TEST_USER_ID
from tests.test_vehicle_connect import SC_USER_ID, _connection
from tests.test_vehicle_connect import FakeSmartcar as ConnectSmartcar


def _client(settings: Settings, fake: ConnectSmartcar) -> SmartcarClient:
    return SmartcarClient.from_settings(settings, transport=httpx.MockTransport(fake.handler))


def _revoked(fake: ConnectSmartcar) -> list[str]:
    return [r.url.path.rsplit("/", 1)[-1] for r in fake.requests if r.method == "DELETE"]


def _lock_timeout(session: Session) -> None:
    session.execute(text("SET LOCAL lock_timeout = '300ms'"))


@pytest.fixture()
def account(db_engine: Engine) -> Iterator[uuid.UUID]:
    """A fresh user ID whose committed rows are removed after the test."""

    user_id = uuid.uuid4()
    yield user_id
    with Session(db_engine) as cleanup:
        cleanup.execute(text("DELETE FROM profiles WHERE id = :u"), {"u": user_id})
        cleanup.execute(text("DELETE FROM account_deletions WHERE user_id = :u"), {"u": user_id})
        cleanup.commit()


def _vehicle_count(engine: Engine, user_id: uuid.UUID) -> int:
    with Session(engine) as s:
        return int(
            s.scalar(select(func.count()).select_from(Vehicle).where(Vehicle.user_id == user_id))
            or 0
        )


def test_connect_waits_for_a_deletion_then_revokes_what_it_authorized(
    db_engine: Engine,
    settings: Settings,
    supabase_client: SupabaseAdminClient,
    account: uuid.UUID,
) -> None:
    connection_id = f"sc-connection-{account}"
    fake = ConnectSmartcar(
        connections=[
            _connection(
                external_id=str(account),
                vehicle_id=f"sc-vehicle-{account}",
                connection_id=connection_id,
            )
        ]
    )
    smartcar = _client(settings, fake)
    try:
        with Session(db_engine) as deleter, Session(db_engine) as connect:
            # A deletion is in flight: it holds the account lock.
            lock_account(deleter, user_id=account)
            _lock_timeout(connect)
            with pytest.raises(OperationalError, match="lock timeout"):
                complete_connect(connect, smartcar, user_id=account, sc_user_id=SC_USER_ID)
            connect.rollback()
            assert _revoked(fake) == []

            delete_account(deleter, smartcar, supabase_client, user_id=account)

            # Connect retries after the deletion committed: it sees the
            # tombstone, revokes the fresh authorization and stores nothing.
            with pytest.raises(ApiError) as exc:
                complete_connect(connect, smartcar, user_id=account, sc_user_id=SC_USER_ID)
            connect.rollback()
        assert exc.value.status_code == 401
        assert _revoked(fake) == [connection_id]
        assert _vehicle_count(db_engine, account) == 0
    finally:
        smartcar.close()


def test_deletion_waits_for_connect_then_revokes_the_new_vehicle(
    db_engine: Engine,
    settings: Settings,
    supabase_client: SupabaseAdminClient,
    account: uuid.UUID,
) -> None:
    connection_id = f"sc-connection-{account}"
    fake = ConnectSmartcar(
        connections=[
            _connection(
                external_id=str(account),
                vehicle_id=f"sc-vehicle-{account}",
                connection_id=connection_id,
            )
        ]
    )
    smartcar = _client(settings, fake)
    try:
        with Session(db_engine) as connect, Session(db_engine) as deleter:
            # Connect has stored the car but not committed yet.
            result = complete_connect(connect, smartcar, user_id=account, sc_user_id=SC_USER_ID)
            assert result.outcome == "success"
            _lock_timeout(deleter)
            with pytest.raises(OperationalError, match="lock timeout"):
                delete_account(deleter, smartcar, supabase_client, user_id=account)
            deleter.rollback()
            assert _revoked(fake) == []

            connect.commit()
            assert _vehicle_count(db_engine, account) == 1

            delete_account(deleter, smartcar, supabase_client, user_id=account)

        # The vehicle Connect stored was seen, revoked and deleted.
        assert _revoked(fake) == [connection_id]
        assert _vehicle_count(db_engine, account) == 0
        with Session(db_engine) as check:
            tombstone = check.get(AccountDeletion, account)
            assert tombstone is not None
            assert tombstone.pending_smartcar_connection_ids == []
            assert check.get(Profile, account) is None
    finally:
        smartcar.close()


def test_deletion_records_a_failed_revocation_for_a_vehicle_connect_stored(
    db_engine: Engine,
    settings: Settings,
    supabase_client: SupabaseAdminClient,
    account: uuid.UUID,
) -> None:
    connection_id = f"sc-connection-{account}"
    fake = ConnectSmartcar(
        connections=[
            _connection(
                external_id=str(account),
                vehicle_id=f"sc-vehicle-{account}",
                connection_id=connection_id,
            )
        ]
    )
    smartcar = _client(settings, fake)
    try:
        with Session(db_engine) as connect:
            complete_connect(connect, smartcar, user_id=account, sc_user_id=SC_USER_ID)
            connect.commit()
        fake.delete_status = 503
        with Session(db_engine) as deleter:
            delete_account(deleter, smartcar, supabase_client, user_id=account)
        with Session(db_engine) as check:
            tombstone = check.get(AccountDeletion, account)
            assert tombstone is not None
            assert tombstone.pending_smartcar_connection_ids == [connection_id]
        assert _vehicle_count(db_engine, account) == 0
    finally:
        smartcar.close()


def test_connect_for_an_already_deleted_account_revokes_and_stores_nothing(
    db: Session, settings: Settings
) -> None:
    db.add(AccountDeletion(user_id=TEST_USER_ID))
    db.commit()
    fake = ConnectSmartcar()
    smartcar = _client(settings, fake)
    try:
        with pytest.raises(ApiError) as exc:
            complete_connect(db, smartcar, user_id=TEST_USER_ID, sc_user_id=SC_USER_ID)
    finally:
        smartcar.close()
    assert exc.value.code == "UNAUTHENTICATED"
    assert _revoked(fake) == ["sc-connection-1"]
    assert db.scalar(select(func.count()).select_from(Vehicle)) == 0
    assert db.get(Profile, TEST_USER_ID) is None


def test_connect_revocation_failure_still_refuses(db: Session, settings: Settings) -> None:
    db.add(AccountDeletion(user_id=TEST_USER_ID))
    db.commit()
    fake = ConnectSmartcar(delete_status=None)  # network failure on revoke
    smartcar = _client(settings, fake)
    try:
        with pytest.raises(ApiError):
            complete_connect(db, smartcar, user_id=TEST_USER_ID, sc_user_id=SC_USER_ID)
    finally:
        smartcar.close()
    assert db.scalar(select(func.count()).select_from(Vehicle)) == 0
