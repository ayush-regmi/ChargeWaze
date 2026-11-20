"""The demo/live gate in ``get_current_user`` (P6-F2, architecture.md §7.2).

Live mode: ``LIVE_ALLOWED_USER_IDS`` decides who gets in, and an empty list
fails closed under ``APP_ENV=production``. Demo mode: read-only except
planning, and nothing is served while the database holds a real vehicle, so
no real row can come back.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.auth.access import DEMO_WRITE_ALLOWED
from app.modules.users.models import Profile, UserPreferences
from app.modules.vehicles.models import Vehicle, VehicleTelemetry
from tests.demo.conftest import (
    DEMO_USER_ID,
    REAL_USER_ID,
    SEED_NOW,
    load_seed_module,
    make_settings,
)

Headers = Callable[[uuid.UUID], dict[str, str]]
UseSettings = Callable[..., Settings]
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MUTATING = {"post", "put", "patch", "delete"}


def _mutating_routes(app: FastAPI) -> list[tuple[str, str]]:
    routes = []
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            if method in MUTATING and path != "/api/v1/webhooks/smartcar":
                routes.append((method.upper(), path))
    return sorted(routes)


def _concrete(path: str) -> str:
    """Fill every path parameter with a random UUID."""

    return re.sub(r"\{[^}]+\}", lambda _m: str(uuid.uuid4()), path)


def _add_real_vehicle(db: Session) -> Vehicle:
    db.add(Profile(id=REAL_USER_ID, display_name="Real Owner"))
    db.add(UserPreferences(user_id=REAL_USER_ID))
    db.flush()
    vehicle = Vehicle(
        user_id=REAL_USER_ID,
        smartcar_vehicle_id="real-smartcar-vehicle",
        smartcar_user_id="real-smartcar-user",
        make="TOYOTA",
        model="bZ XLE AWD",
        year=2026,
        connection_status="connected",
        is_simulated=False,
    )
    db.add(vehicle)
    db.flush()
    db.add(
        VehicleTelemetry(
            vehicle_id=vehicle.id,
            recorded_at=datetime(2026, 11, 19, 12, 0, tzinfo=UTC),
            source="smartcar",
            source_event_id="real-event",
            signal_code="soc",
            value=Decimal("64"),
            unit="percent",
            raw_payload={},
            is_simulated=False,
        )
    )
    db.flush()
    return vehicle


@pytest.fixture()
def seeded(db_session: Session) -> Iterator[Any]:
    seed_demo = load_seed_module()
    summary = seed_demo.seed(
        db_session,
        user_id=DEMO_USER_ID,
        settings=make_settings(app_public_demo_mode=True),
        now=SEED_NOW,
    )
    db_session.flush()
    yield summary


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_allowlist_must_be_uuids() -> None:
    with pytest.raises(ValidationError, match="LIVE_ALLOWED_USER_IDS"):
        make_settings(live_allowed_user_ids="owner@example.com")
    settings = make_settings(live_allowed_user_ids=f" {DEMO_USER_ID} ,, {REAL_USER_ID}")
    assert settings.live_allowlist == {DEMO_USER_ID, REAL_USER_ID}


@pytest.mark.parametrize("secret", ["smartcar_client_secret", "smartcar_management_token"])
def test_demo_mode_refuses_smartcar_secrets(secret: str) -> None:
    with pytest.raises(ValidationError, match="APP_PUBLIC_DEMO_MODE"):
        make_settings(app_public_demo_mode=True, **{secret: "a-real-secret"})
    assert make_settings(app_public_demo_mode=False, **{secret: "a-real-secret"})
    assert make_settings(app_public_demo_mode=True, smartcar_client_id="public-id")


# ---------------------------------------------------------------------------
# Live mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_live_empty_allowlist_is_open_outside_production(
    client: TestClient, use_settings: UseSettings, auth: Headers, app_env: str
) -> None:
    use_settings(app_env=app_env)
    assert client.get("/api/v1/me", headers=auth(REAL_USER_ID)).status_code == 200


def test_live_empty_allowlist_fails_closed_in_production(
    client: TestClient, use_settings: UseSettings, auth: Headers
) -> None:
    use_settings(app_env="production")
    response = client.get("/api/v1/me", headers=auth(REAL_USER_ID))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"
    assert response.json()["error"]["details"] == {"reason": "LIVE_ACCESS_RESTRICTED"}


@pytest.mark.parametrize("app_env", ["production", "development"])
def test_live_allowlist_admits_only_listed_users(
    client: TestClient, use_settings: UseSettings, auth: Headers, app_env: str
) -> None:
    use_settings(app_env=app_env, live_allowed_user_ids=str(REAL_USER_ID))
    assert client.get("/api/v1/me", headers=auth(REAL_USER_ID)).status_code == 200
    stranger = client.get("/api/v1/vehicles", headers=auth(DEMO_USER_ID))
    assert stranger.status_code == 403
    assert stranger.json()["error"]["details"]["reason"] == "LIVE_ACCESS_RESTRICTED"


def test_live_gate_runs_after_authentication(client: TestClient, use_settings: UseSettings) -> None:
    use_settings(app_env="production")
    assert client.get("/api/v1/me").status_code == 401
    assert client.get("/health").status_code == 200


def test_live_mode_allows_writes(
    client: TestClient, use_settings: UseSettings, auth: Headers
) -> None:
    use_settings(app_env="production", live_allowed_user_ids=str(REAL_USER_ID))
    response = client.patch(
        "/api/v1/me/preferences", json={"units": "metric"}, headers=auth(REAL_USER_ID)
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# Demo mode: read-only except planning
# ---------------------------------------------------------------------------


def test_demo_mode_ignores_the_live_allowlist(
    client: TestClient, use_settings: UseSettings, auth: Headers
) -> None:
    use_settings(app_env="production", app_public_demo_mode=True)
    assert client.get("/api/v1/me", headers=auth(DEMO_USER_ID)).status_code == 200


def test_demo_mode_blocks_every_write_except_planning(
    app_with_db: FastAPI, client: TestClient, use_settings: UseSettings, auth: Headers
) -> None:
    use_settings(app_public_demo_mode=True)
    routes = _mutating_routes(app_with_db)
    assert set(DEMO_WRITE_ALLOWED) <= set(routes)
    assert len(routes) > len(DEMO_WRITE_ALLOWED) + 10  # the table really was walked

    for method, path in routes:
        response = client.request(method, _concrete(path), json={}, headers=auth(DEMO_USER_ID))
        reason = (response.json().get("error") or {}).get("details") or {}
        if (method, path) in DEMO_WRITE_ALLOWED:
            # Reaches the endpoint, which rejects the empty body.
            assert response.status_code == 422, (method, path, response.text)
        else:
            assert response.status_code == 403, (method, path, response.text)
            assert reason == {"reason": "DEMO_READ_ONLY"}, (method, path)


def test_demo_mode_keeps_webhooks_ungated(client: TestClient, use_settings: UseSettings) -> None:
    use_settings(app_public_demo_mode=True)
    # No signing key is loaded in demo mode, so the webhook is refused by its
    # own signature check, not by the demo gate.
    response = client.post("/api/v1/webhooks/smartcar", json={})
    body = response.json()
    assert (body.get("error") or {}).get("details") != {"reason": "DEMO_READ_ONLY"}


def test_demo_user_can_plan_on_the_seeded_car(
    client: TestClient, use_settings: UseSettings, auth: Headers, seeded: Any
) -> None:
    """The one write a visitor makes reaches the planner (it fails upstream here)."""

    use_settings(app_public_demo_mode=True, rate_limit_enabled=False)
    response = client.post(
        "/api/v1/routes/plan",
        json={
            "vehicle_id": str(seeded.vehicle_id),
            "origin": {"lat": 39.4143, "lon": -77.4105, "label": "Frederick, MD"},
            "destination": {"lat": 39.2904, "lon": -76.6122, "label": "Baltimore, MD"},
            "starting_soc_override": 0.8,
        },
        headers=auth(DEMO_USER_ID),
    )
    # No Mapbox token in the test settings: the planner's own error, not the gate.
    assert response.status_code != 403, response.text
    assert response.json()["error"]["code"] != "DEMO_MODE_UNSAFE"


# ---------------------------------------------------------------------------
# Demo mode: no real row is ever returned
# ---------------------------------------------------------------------------


def _demo_reads(summary: Any) -> list[str]:
    vehicle, trip = summary.vehicle_id, summary.trip_id
    window = "from=2026-10-20&to=2026-11-20&tz=America/New_York&include_simulated=true"
    return [
        "/api/v1/me",
        "/api/v1/me/privacy",
        "/api/v1/vehicles",
        f"/api/v1/vehicles/{vehicle}",
        f"/api/v1/vehicles/{vehicle}/state",
        "/api/v1/trips",
        "/api/v1/trips?saved=false",
        f"/api/v1/trips/{trip}",
        f"/api/v1/observed-trips?vehicle_id={vehicle}&limit=200",
        f"/api/v1/analytics/consumption?vehicle_id={vehicle}&{window}",
        f"/api/v1/analytics/trips?vehicle_id={vehicle}&{window}&limit=200",
        "/api/v1/stations/favorites",
    ]


def _uuids(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set().union(*(_uuids(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_uuids(v) for v in value)) if value else set()
    if isinstance(value, str) and UUID_RE.match(value):
        return {value}
    return set()


def test_demo_mode_refuses_everything_while_a_real_vehicle_exists(
    client: TestClient,
    use_settings: UseSettings,
    auth: Headers,
    db_session: Session,
    seeded: Any,
) -> None:
    real = _add_real_vehicle(db_session)
    use_settings(app_public_demo_mode=True)

    reads = [
        *_demo_reads(seeded),
        f"/api/v1/vehicles/{real.id}",
        f"/api/v1/vehicles/{real.id}/state",
        f"/api/v1/observed-trips?vehicle_id={real.id}",
    ]
    for user in (DEMO_USER_ID, REAL_USER_ID):
        for path in reads:
            response = client.get(path, headers=auth(user))
            assert response.status_code == 503, (user, path, response.text)
            assert response.json() == {
                "error": {"code": "DEMO_MODE_UNSAFE", "message": "The public demo is unavailable."}
            }
            assert str(real.id) not in response.text
        planned = client.post("/api/v1/routes/plan", json={}, headers=auth(user))
        assert planned.status_code == 503


def test_demo_reads_return_only_simulated_rows(
    client: TestClient,
    use_settings: UseSettings,
    auth: Headers,
    db_session: Session,
    seeded: Any,
) -> None:
    use_settings(app_public_demo_mode=True)
    tables = ("vehicles", "planned_trips", "observed_trips", "prediction_results")
    returned_rows = 0
    for path in _demo_reads(seeded):
        response = client.get(path, headers=auth(DEMO_USER_ID))
        assert response.status_code == 200, (path, response.text)
        for value in _uuids(response.json()):
            for table in tables:
                flag = db_session.execute(
                    text(f"SELECT is_simulated FROM {table} WHERE id = :id"),
                    {"id": value},
                ).scalar_one_or_none()
                if flag is not None:
                    returned_rows += 1
                    assert flag is True, (path, table, value)
    # Vehicle, plan, legs and backtests all came back (and were checked).
    assert returned_rows > 60

    state = client.get(
        f"/api/v1/vehicles/{seeded.vehicle_id}/state", headers=auth(DEMO_USER_ID)
    ).json()
    assert state["source"] == "simulated"
    legs = client.get(
        f"/api/v1/observed-trips?vehicle_id={seeded.vehicle_id}&limit=200",
        headers=auth(DEMO_USER_ID),
    ).json()
    assert legs["items"] and all(leg["is_simulated"] for leg in legs["items"])
