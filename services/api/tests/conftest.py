"""Shared pytest fixtures.

Builds a FastAPI app bound to:

- An in-memory SQLite database with the ORM models created from
  ``Base.metadata`` (sufficient for user-model tests; the production schema
  is managed by Alembic migrations exercised in CI against Postgres).
- A ``JwtVerifier`` configured with a locally generated ES256 keypair whose
  public key is served through an in-memory ``PyJWKClient`` stub.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.main import create_app
from app.modules.auth import dependencies as auth_deps
from app.modules.auth.dependencies import JwtVerifier

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


@pytest.fixture()
def app_with_db(key_material: _KeyMaterial) -> Iterator[FastAPI]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )

    def _override_get_db() -> Iterator[Session]:
        session = TestingSession()
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
        Base.metadata.drop_all(engine)
        engine.dispose()


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
