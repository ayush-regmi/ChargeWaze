"""Tests for the Smartcar Connect flow (Phase 2 F3).

``POST /api/v1/vehicles/connect`` and ``POST /api/v1/vehicles/connect/complete``
(called by the web app's ``/garage/connect/callback`` with the signed-in user's
token). Smartcar is a real ``SmartcarClient``
over ``httpx.MockTransport`` serving the redacted F0 fixtures, so parsing is
exercised end to end without any network.
"""

from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.integrations.smartcar.client import SmartcarClient, get_smartcar_client
from app.modules.users.models import Profile
from app.modules.vehicles.models import Vehicle
from app.modules.vehicles.state import issue_state
from tests.conftest import TEST_USER_ID, make_token
from tests.integrations.conftest import fixture_json

STATE_SECRET = "test-state-secret-0123456789abcdef0123456789"
COMPLETE = "/api/v1/vehicles/connect/complete"
SC_USER_ID = "sc-user-1"
SC_VEHICLE_ID = "sc-vehicle-1"
SC_CONNECTION_ID = "sc-connection-1"
OTHER_USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _connection(
    *,
    external_id: str = str(TEST_USER_ID),
    vehicle_id: str = SC_VEHICLE_ID,
    connection_id: str = SC_CONNECTION_ID,
    status: str = "ACTIVE",
) -> dict[str, Any]:
    conn = copy.deepcopy(fixture_json("connections_list.json")["data"][0])
    conn["id"] = connection_id
    conn["attributes"]["user"] = {"id": SC_USER_ID, "externalId": external_id}
    conn["attributes"]["status"] = {"name": status}
    conn["relationships"]["user"]["data"]["id"] = SC_USER_ID
    conn["relationships"]["vehicle"]["data"]["id"] = vehicle_id
    return conn


@dataclass
class FakeSmartcar:
    """Serves Smartcar V3 responses; tests mutate the fields to change behaviour."""

    connections: list[dict[str, Any]] = field(default_factory=lambda: [_connection()])
    signals: dict[str, Any] = field(default_factory=lambda: fixture_json("signals_bz.json"))
    connections_status: int = 200
    signals_status: int = 200
    requests: list[httpx.Request] = field(default_factory=list)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/oauth2/token"):
            return httpx.Response(200, json=fixture_json("token_response.json"))
        if path.endswith("/connections"):
            if self.connections_status != 200:
                return httpx.Response(self.connections_status, json={"errors": []})
            body = {
                "data": self.connections,
                "meta": {"pageNumber": 1, "pageSize": 50, "totalCount": len(self.connections)},
            }
            return httpx.Response(200, json=body)
        if path.endswith("/signals"):
            if self.signals_status != 200:
                return httpx.Response(self.signals_status, json={"errors": []})
            return httpx.Response(200, json=self.signals)
        return httpx.Response(404, json={"errors": []})


@pytest.fixture()
def smartcar() -> FakeSmartcar:
    return FakeSmartcar()


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        app_env="test",
        smartcar_client_id="test-client-id",
        smartcar_client_secret="test-client-secret",
        smartcar_application_id="test-application-id",
        smartcar_state_secret=STATE_SECRET,
        smartcar_api_base_url="https://vehicle.test/v3",
        smartcar_auth_url="https://iam.test/oauth2/token",
        smartcar_connect_url="https://connect.test/oauth/authorize",
    )


@pytest.fixture()
def api(
    app_with_db: FastAPI, settings: Settings, smartcar: FakeSmartcar, key_material: Any
) -> Iterator[TestClient]:
    sc_client = SmartcarClient.from_settings(
        settings, transport=httpx.MockTransport(smartcar.handler)
    )
    app_with_db.dependency_overrides[get_settings] = lambda: settings
    app_with_db.dependency_overrides[get_smartcar_client] = lambda: sc_client
    try:
        test_client = TestClient(app_with_db, follow_redirects=False)
        # Token for TEST_USER_ID, used by _callback (completion is authenticated).
        test_client.complete_headers = _auth(key_material)  # type: ignore[attr-defined]
        yield test_client
    finally:
        sc_client.close()


@pytest.fixture()
def db(db_connection: Connection) -> Iterator[Session]:
    session = Session(bind=db_connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()


def _auth(key_material: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(key_material)}"}


def _ensure_profile(db: Session, user_id: uuid.UUID = TEST_USER_ID) -> None:
    if db.get(Profile, user_id) is None:
        db.add(Profile(id=user_id))
        db.flush()


def _state(user_id: uuid.UUID = TEST_USER_ID, redirect_after: str = "/garage", **kw: Any) -> str:
    token, _ = issue_state(
        user_id=user_id, redirect_after=redirect_after, secret=STATE_SECRET, **kw
    )
    return token


def _callback(api: TestClient, **params: str) -> httpx.Response:
    """Complete Connect as TEST_USER_ID with the query Smartcar redirected back with."""

    params.setdefault("user_id", SC_USER_ID)
    return api.post(COMPLETE, json=params, headers=api.complete_headers)  # type: ignore[attr-defined]


def _flag(response: httpx.Response) -> tuple[str, str]:
    assert response.status_code == 200, response.text
    body = response.json()
    return body["redirect_after"], body["result"]


def _vehicles(db: Session) -> list[Vehicle]:
    db.expire_all()
    return list(db.scalars(select(Vehicle).where(Vehicle.smartcar_vehicle_id == SC_VEHICLE_ID)))


# ---------------------------------------------------------------------------
# POST /api/v1/vehicles/connect
# ---------------------------------------------------------------------------


def test_connect_requires_auth(api: TestClient) -> None:
    response = api.post("/api/v1/vehicles/connect", json={})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_connect_returns_authorization_url_with_signed_state(
    api: TestClient, key_material: Any
) -> None:
    response = api.post(
        "/api/v1/vehicles/connect", json={"redirect_after": "/garage"}, headers=_auth(key_material)
    )
    assert response.status_code == 200
    body = response.json()

    url = urlsplit(body["authorization_url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://connect.test/oauth/authorize"
    query = parse_qs(url.query)
    assert query["response_type"] == ["none"]
    assert query["application_id"] == ["test-application-id"]
    assert query["external_id"] == [str(TEST_USER_ID)]
    assert query["state"] == [body["state"]]

    claims = jwt.decode(
        body["state"], STATE_SECRET, algorithms=["HS256"], audience="smartcar-connect"
    )
    assert claims["sub"] == str(TEST_USER_ID)
    assert claims["ra"] == "/garage"
    expires_at = datetime.fromisoformat(body["expires_at"])
    assert timedelta(minutes=9) < expires_at - datetime.now(UTC) <= timedelta(minutes=10)


def test_connect_defaults_redirect_after_to_garage(api: TestClient, key_material: Any) -> None:
    response = api.post("/api/v1/vehicles/connect", headers=_auth(key_material))
    assert response.status_code == 200
    claims = jwt.decode(
        response.json()["state"], STATE_SECRET, algorithms=["HS256"], audience="smartcar-connect"
    )
    assert claims["ra"] == "/garage"


@pytest.mark.parametrize(
    "redirect_after",
    [
        "https://evil.example/garage",
        "//evil.example",
        "/\\evil.example",
        "/.//evil.example",
        "/garage/../..//evil",
        "garage",
        "/gar\tage",
        "/gar\nage",
        "",
    ],
)
def test_connect_rejects_unsafe_redirect_after(
    api: TestClient, key_material: Any, redirect_after: str
) -> None:
    response = api.post(
        "/api/v1/vehicles/connect",
        json={"redirect_after": redirect_after},
        headers=_auth(key_material),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_connect_without_state_secret_is_not_configured(
    api: TestClient, key_material: Any, settings: Settings
) -> None:
    settings.smartcar_state_secret = ""
    response = api.post("/api/v1/vehicles/connect", json={}, headers=_auth(key_material))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SMARTCAR_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------


def test_connect_endpoints_are_in_openapi(api: TestClient) -> None:
    paths = api.get("/api/v1/openapi.json").json()["paths"]
    assert "/api/v1/vehicles/connect" in paths
    assert COMPLETE in paths
    assert not any(p.startswith("/integrations") for p in paths)


def test_complete_requires_auth(api: TestClient, db: Session) -> None:
    response = api.post(COMPLETE, json={"state": _state(), "user_id": SC_USER_ID})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
    assert _vehicles(db) == []


def test_complete_rejects_state_of_another_signed_in_user(
    api: TestClient, db: Session, smartcar: FakeSmartcar
) -> None:
    """Attacker starts Connect, victim authorizes from the attacker's link.

    The victim's browser completes with the victim's session, but ``state``
    (and Smartcar's ``externalId``) name the attacker. Nothing may be stored.
    """

    attacker = OTHER_USER_ID
    _ensure_profile(db, attacker)
    smartcar.connections = [_connection(external_id=str(attacker))]
    response = _callback(api, state=_state(user_id=attacker, redirect_after="/trips"))
    assert _flag(response) == ("/garage", "error")
    assert _vehicles(db) == []
    assert not any(r.url.path.endswith("/connections") for r in smartcar.requests)


def test_complete_requires_state(api: TestClient) -> None:
    response = api.post(
        COMPLETE,
        json={"user_id": SC_USER_ID},
        headers=api.complete_headers,  # type: ignore[attr-defined]
    )
    assert response.status_code == 422


def test_callback_happy_path_creates_vehicle_and_specs(
    api: TestClient, db: Session, smartcar: FakeSmartcar
) -> None:
    _ensure_profile(db)
    response = _callback(api, state=_state(), external_id=str(TEST_USER_ID))
    assert _flag(response) == ("/garage", "success")

    [vehicle] = _vehicles(db)
    assert vehicle.user_id == TEST_USER_ID
    assert vehicle.connection_status == "connected"
    assert vehicle.connected_at is not None
    assert vehicle.smartcar_user_id == SC_USER_ID
    assert vehicle.smartcar_connection_id == SC_CONNECTION_ID
    assert (vehicle.make, vehicle.model, vehicle.year) == ("TOYOTA", "bZ XLE AWD", 2026)
    # From Smartcar's signal read, not hard-coded (PRD §4.4).
    assert vehicle.supported_signals == [
        "cable_connected",
        "charging_status",
        "charging_status_detail",
        "odometer",
        "range",
        "soc",
    ]
    assert vehicle.specs is not None
    # Defaults come from the cited catalog entry (phase4_plan.md §2.3), never guessed.
    assert vehicle.specs.usable_capacity_kwh == Decimal("71.0")
    assert vehicle.specs.connectors == ["NACS", "CCS1", "J1772"]
    # The callback reads Smartcar with the redirect's user, never another.
    assert all(r.headers.get("sc-user-id") in (None, SC_USER_ID) for r in smartcar.requests)


def test_callback_creates_profile_if_missing(api: TestClient, db: Session) -> None:
    response = _callback(api, state=_state())
    assert _flag(response) == ("/garage", "success")
    assert db.get(Profile, TEST_USER_ID) is not None


def test_callback_keeps_redirect_after_and_its_query(api: TestClient, db: Session) -> None:
    _ensure_profile(db)
    response = _callback(api, state=_state(redirect_after="/garage?tab=cars#top"))
    assert _flag(response) == ("/garage?tab=cars#top", "success")


def test_callback_empty_signals_still_connects(
    api: TestClient, db: Session, smartcar: FakeSmartcar
) -> None:
    smartcar.signals = {"data": [], "meta": {"totalCount": 0}}
    _ensure_profile(db)
    assert _flag(_callback(api, state=_state()))[1] == "success"
    [vehicle] = _vehicles(db)
    assert vehicle.connection_status == "connected"
    assert vehicle.supported_signals == []  # "Awaiting first update"; recomputed later


def test_callback_signal_read_failure_still_connects(
    api: TestClient, db: Session, smartcar: FakeSmartcar
) -> None:
    smartcar.signals_status = 503
    _ensure_profile(db)
    assert _flag(_callback(api, state=_state()))[1] == "success"
    assert _vehicles(db)[0].supported_signals == []


@pytest.mark.parametrize("error", ["access_denied", "user_manually_returned_to_application"])
def test_callback_denied(api: TestClient, db: Session, error: str) -> None:
    response = _callback(api, state=_state(), error=error)
    assert _flag(response) == ("/garage", "denied")
    assert _vehicles(db) == []


def test_callback_other_connect_error(api: TestClient, db: Session) -> None:
    response = _callback(api, state=_state(), error="vehicle_incompatible")
    assert _flag(response) == ("/garage", "error")
    assert _vehicles(db) == []


@pytest.mark.parametrize(
    "state",
    [
        "not-a-jwt",
        jwt.encode({"sub": str(TEST_USER_ID)}, "wrong-secret-" * 4, algorithm="HS256"),
    ],
)
def test_callback_bad_state(api: TestClient, db: Session, state: str) -> None:
    response = _callback(api, state=state)
    assert _flag(response) == ("/garage", "error")
    assert _vehicles(db) == []


def test_callback_tampered_state(api: TestClient, db: Session) -> None:
    header, payload, signature = _state().split(".")
    tampered = f"{header}.{payload}.{signature[:-2]}AA"
    assert _flag(_callback(api, state=tampered))[1] == "error"
    assert _vehicles(db) == []


def test_callback_expired_state(api: TestClient, db: Session) -> None:
    old = datetime.now(UTC) - timedelta(minutes=11)
    response = _callback(api, state=_state(now=old))
    assert _flag(response) == ("/garage", "error")
    assert _vehicles(db) == []


def test_callback_missing_user_id(api: TestClient, db: Session) -> None:
    response = api.post(
        COMPLETE,
        json={"state": _state()},
        headers=api.complete_headers,  # type: ignore[attr-defined]
    )
    assert _flag(response)[1] == "error"


def test_callback_external_id_mismatch_in_redirect(api: TestClient, db: Session) -> None:
    response = _callback(api, state=_state(), external_id=str(OTHER_USER_ID))
    assert _flag(response)[1] == "error"
    assert _vehicles(db) == []


def test_callback_rejects_connections_of_another_chargewise_user(
    api: TestClient, db: Session, smartcar: FakeSmartcar
) -> None:
    """A valid state plus someone else's Smartcar user ID must not attach their car."""

    smartcar.connections = [_connection(external_id=str(OTHER_USER_ID))]
    _ensure_profile(db)
    assert _flag(_callback(api, state=_state()))[1] == "error"
    assert _vehicles(db) == []


def test_callback_ignores_inactive_connections(
    api: TestClient, db: Session, smartcar: FakeSmartcar
) -> None:
    smartcar.connections = [_connection(status="REVOKED")]
    _ensure_profile(db)
    assert _flag(_callback(api, state=_state()))[1] == "error"
    assert _vehicles(db) == []


def test_callback_smartcar_unreachable(
    api: TestClient, db: Session, smartcar: FakeSmartcar
) -> None:
    smartcar.connections_status = 503
    _ensure_profile(db)
    assert _flag(_callback(api, state=_state()))[1] == "error"
    assert _vehicles(db) == []


def test_callback_duplicate_is_already_connected(api: TestClient, db: Session) -> None:
    _ensure_profile(db)
    assert _flag(_callback(api, state=_state()))[1] == "success"
    assert _flag(_callback(api, state=_state()))[1] == "already_connected"
    assert len(_vehicles(db)) == 1


def test_callback_reconnect_reactivates_same_row(
    api: TestClient, db: Session, smartcar: FakeSmartcar
) -> None:
    _ensure_profile(db)
    assert _flag(_callback(api, state=_state()))[1] == "success"
    [vehicle] = _vehicles(db)
    original_id = vehicle.id
    vehicle.connection_status = "disconnected"
    vehicle.disconnected_at = datetime.now(UTC)
    db.flush()

    smartcar.connections = [_connection(connection_id="sc-connection-2")]
    assert _flag(_callback(api, state=_state()))[1] == "success"

    [vehicle] = _vehicles(db)
    assert vehicle.id == original_id
    assert vehicle.connection_status == "connected"
    assert vehicle.disconnected_at is None
    assert vehicle.smartcar_connection_id == "sc-connection-2"


def test_callback_refuses_vehicle_connected_to_another_user(api: TestClient, db: Session) -> None:
    _ensure_profile(db, OTHER_USER_ID)
    db.add(
        Vehicle(
            user_id=OTHER_USER_ID,
            smartcar_vehicle_id=SC_VEHICLE_ID,
            make="TOYOTA",
            model="bZ XLE AWD",
            year=2026,
            connection_status="connected",
        )
    )
    db.flush()
    _ensure_profile(db)

    assert _flag(_callback(api, state=_state()))[1] == "error"
    owners = {v.user_id for v in _vehicles(db)}
    assert owners == {OTHER_USER_ID}


def test_callback_redirect_never_leaves_the_site(api: TestClient) -> None:
    """Even a state signed with an unsafe path (shouldn't happen) is refused."""

    forged = jwt.encode(
        {
            "iss": "chargewise-api",
            "aud": "smartcar-connect",
            "sub": str(TEST_USER_ID),
            "ra": "//evil.example",
            "iat": datetime.now(UTC),
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        STATE_SECRET,
        algorithm="HS256",
    )
    assert _flag(_callback(api, state=forged)) == ("/garage", "error")


def test_callback_logs_no_smartcar_ids(
    api: TestClient, db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    _ensure_profile(db)
    with caplog.at_level("DEBUG"):
        _callback(api, state=_state())
    dumped = json.dumps([r.__dict__ for r in caplog.records], default=str)
    for secret_id in (SC_USER_ID, SC_VEHICLE_ID, SC_CONNECTION_ID):
        assert secret_id not in dumped


def test_validation_error_body_is_serializable(api: TestClient, key_material: Any) -> None:
    response = api.post(
        "/api/v1/vehicles/connect", json={"redirect_after": "//evil"}, headers=_auth(key_material)
    )
    [error] = response.json()["error"]["details"]["errors"]
    assert error["loc"] == ["body", "redirect_after"]
    assert "same-origin" in error["ctx"]["error"]
