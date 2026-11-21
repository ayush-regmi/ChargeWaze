"""Shared pytest fixtures.

Builds a FastAPI app bound to:

- A real Postgres test database (``TEST_DATABASE_URL``, default: the
  docker-compose container) migrated with Alembic, so tests exercise the same
  schema, constraints and RLS settings as Supabase. Each test runs inside a
  transaction that is rolled back afterwards.
- A ``JwtVerifier`` configured with a locally generated ES256 keypair whose
  public key is served through an in-memory ``PyJWKClient`` stub.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt
import pytest
from alembic.config import Config as AlembicConfig
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm
from sqlalchemy import Connection, Engine, create_engine, make_url, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from alembic import command
from app.db.session import get_db
from app.main import create_app
from app.modules.auth import dependencies as auth_deps
from app.modules.auth.dependencies import JwtVerifier

API_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+psycopg://chargewaze:chargewaze@localhost:5432/chargewaze_test"
)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

AUDIENCE = "authenticated"
TEST_USER_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


class _KeyMaterial:
    def __init__(self) -> None:
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.public_key = self.private_key.public_key()
        pem = self.private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self.private_pem = pem.decode("utf-8")
        self.jwk = ECAlgorithm.to_jwk(self.public_key, as_dict=True)
        self.jwk["kid"] = "test-key"
        self.jwk["alg"] = "ES256"
        self.jwk["use"] = "sig"


class _StubVerifier(JwtVerifier):
    """A ``JwtVerifier`` that verifies against an in-memory ES256 public key."""

    def __init__(self, keys: _KeyMaterial) -> None:
        self._public_key = keys.public_key
        self._audience = AUDIENCE

    def verify(self, token: str) -> dict[str, Any]:
        return self._decode(
            token,
            key=self._public_key,
            algorithms=["ES256"],
            audience=self._audience,
        )


@pytest.fixture()
def key_material() -> _KeyMaterial:
    return _KeyMaterial()


def _test_database_url() -> URL:
    """Resolve the test DB URL and refuse anything that isn't local.

    Tests drop and recreate the ``public`` schema, so pointing them at
    Supabase (``DATABASE_URL`` in ``services/api/.env``) would wipe real data.
    That is why this reads ``TEST_DATABASE_URL`` and never ``DATABASE_URL``.
    """

    url = make_url(os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))
    if url.host not in LOCAL_HOSTS:
        raise pytest.UsageError(
            f"TEST_DATABASE_URL must point at a local Postgres, got host {url.host!r}."
        )
    if not url.database or not url.database.endswith("_test"):
        raise pytest.UsageError(
            "TEST_DATABASE_URL must name a database ending in '_test' "
            f"(got {url.database!r}); the suite resets its schema."
        )
    return url


def _ensure_database_exists(url: URL) -> None:
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": url.database},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """Fresh, fully migrated Postgres database shared by the whole run."""

    url = _test_database_url()
    _ensure_database_exists(url)
    engine = create_engine(url)

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        alembic_cfg = AlembicConfig(str(API_ROOT / "alembic.ini"))
        alembic_cfg.attributes["connection"] = conn
        command.upgrade(alembic_cfg, "head")

    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def db_connection(db_engine: Engine) -> Iterator[Connection]:
    """A connection inside an outer transaction that is always rolled back."""

    conn = db_engine.connect()
    outer = conn.begin()
    try:
        yield conn
    finally:
        outer.rollback()
        conn.close()


@pytest.fixture()
def app_with_db(key_material: _KeyMaterial, db_connection: Connection) -> Iterator[FastAPI]:
    def _override_get_db() -> Iterator[Session]:
        # ``create_savepoint`` turns the app's ``commit()`` calls into savepoint
        # releases, so the outer rollback in ``db_connection`` still undoes them.
        session = Session(
            bind=db_connection,
            join_transaction_mode="create_savepoint",
            autoflush=False,
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.state.jwt_verifier = _StubVerifier(key_material)
    app.dependency_overrides[get_db] = _override_get_db

    try:
        yield app
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def client(app_with_db: FastAPI) -> TestClient:
    return TestClient(app_with_db)


def make_token(
    key_material: _KeyMaterial,
    *,
    sub: uuid.UUID | str = TEST_USER_ID,
    email: str = "driver@example.com",
    audience: str = AUDIENCE,
    user_metadata: dict[str, Any] | None = None,
    ttl_seconds: int = 300,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": str(sub),
        "aud": audience,
        "exp": now + ttl_seconds,
        "iat": now,
        "email": email,
        "user_metadata": user_metadata or {"full_name": "Example Driver"},
    }
    return jwt.encode(
        payload,
        key_material.private_pem,
        algorithm="ES256",
        headers={"kid": key_material.jwk["kid"]},
    )


@pytest.fixture(autouse=True)
def _reset_verifier_cache() -> Iterator[None]:
    """Prevent a cached real verifier from leaking into other tests."""

    auth_deps._default_verifier.cache_clear()
    yield
    auth_deps._default_verifier.cache_clear()
