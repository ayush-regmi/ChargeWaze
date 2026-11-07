"""Tests for ``GET /api/v1/me`` and the JWT verification dependency."""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_USER_ID, make_token


def test_me_requires_bearer_token(client: TestClient) -> None:
    response = client.get("/api/v1/me")
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "UNAUTHENTICATED"


def test_me_rejects_expired_token(client: TestClient, key_material) -> None:
    token = make_token(key_material, ttl_seconds=-10)
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_me_rejects_wrong_audience(client: TestClient, key_material) -> None:
    now = int(time.time())
    payload = {
        "sub": str(TEST_USER_ID),
        "aud": "wrong-audience",
        "exp": now + 60,
        "iat": now,
        "email": "x@example.com",
    }
    token = jwt.encode(
        payload,
        key_material.private_pem,
        algorithm="ES256",
        headers={"kid": key_material.jwk["kid"]},
    )
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_me_creates_profile_on_first_call(client: TestClient, key_material) -> None:
    token = make_token(key_material, user_metadata={"full_name": "Ayush Regmi"})
    response = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["id"] == str(TEST_USER_ID)
    assert body["email"] == "driver@example.com"
    assert body["display_name"] == "Ayush Regmi"
    assert body["preferences"]["units"] == "imperial"
    assert body["preferences"]["reserve_soc"] == pytest.approx(0.15)
    assert body["preferences"]["preferred_charge_to"] == pytest.approx(0.80)
    assert body["preferences"]["max_detour_minutes"] == 20
    assert body["preferences"]["preferred_networks"] == []
    assert body["preferences"]["primary_vehicle_id"] is None


def test_me_is_idempotent_on_second_call(client: TestClient, key_material) -> None:
    token = make_token(key_material)
    first = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    second = client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["created_at"] == second.json()["created_at"]
