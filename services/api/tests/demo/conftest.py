"""Fixtures for the demo/live gate and the demo seed (P6-F2)."""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Connection
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from tests.conftest import make_token

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
DEMO_USER_ID = uuid.UUID("0de30000-0000-4000-8000-000000000001")
REAL_USER_ID = uuid.UUID("7ea10000-0000-4000-8000-000000000002")
SEED_NOW = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


def load_seed_module() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    import seed_demo

    return seed_demo


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"app_env": "test", "rate_limit_enabled": False}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture()
def db_session(db_connection: Connection) -> Iterator[Session]:
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


@pytest.fixture()
def use_settings(app_with_db: FastAPI) -> Callable[..., Settings]:
    """Override the app's settings for this test."""

    def apply(**overrides: Any) -> Settings:
        settings = make_settings(**overrides)
        app_with_db.dependency_overrides[get_settings] = lambda: settings
        return settings

    return apply


@pytest.fixture()
def auth(key_material: Any) -> Callable[[uuid.UUID], dict[str, str]]:
    def headers(user_id: uuid.UUID) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(key_material, sub=user_id)}"}

    return headers
