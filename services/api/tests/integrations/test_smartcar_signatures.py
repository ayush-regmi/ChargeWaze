from __future__ import annotations

import hashlib
import hmac

from starlette.datastructures import Headers

from app.integrations.smartcar.schemas import WebhookEvent
from app.integrations.smartcar.signatures import (
    compute_signature,
    verify_challenge_response,
    verify_webhook_signature,
)
from tests.integrations.conftest import TEST_MANAGEMENT_TOKEN, fixture_bytes


def _signed(name: str) -> tuple[bytes, str]:
    body = fixture_bytes(name)
    signature = fixture_bytes(f"{name}.signature").decode().strip()
    return body, signature


def test_fixture_signature_is_valid() -> None:
    body, signature = _signed("webhook_vehicle_state.json")
    assert verify_webhook_signature(body, {"SC-Signature": signature}, TEST_MANAGEMENT_TOKEN)


def test_header_lookup_is_case_insensitive() -> None:
    body, signature = _signed("webhook_vehicle_state.json")
    assert verify_webhook_signature(body, {"sc-signature": signature}, TEST_MANAGEMENT_TOKEN)
    starlette_headers = Headers({"sc-signature": signature})
    assert verify_webhook_signature(body, starlette_headers, TEST_MANAGEMENT_TOKEN)


def test_uppercase_hex_signature_is_accepted() -> None:
    body, signature = _signed("webhook_vehicle_state.json")
    headers = {"SC-Signature": signature.upper()}
    assert verify_webhook_signature(body, headers, TEST_MANAGEMENT_TOKEN)


def test_tampered_body_fails() -> None:
    body, signature = _signed("webhook_vehicle_state.json")
    tampered = body.replace(b'"value": 64', b'"value": 99')
    assert tampered != body
    assert not verify_webhook_signature(
        tampered, {"SC-Signature": signature}, TEST_MANAGEMENT_TOKEN
    )


def test_reserialized_body_fails() -> None:
    """The HMAC covers raw bytes; re-encoding the JSON must not verify."""

    body, signature = _signed("webhook_vehicle_state.json")
    compact = body.replace(b"\n", b"").replace(b"  ", b"")
    assert not verify_webhook_signature(compact, {"SC-Signature": signature}, TEST_MANAGEMENT_TOKEN)


def test_wrong_secret_fails() -> None:
    body, signature = _signed("webhook_vehicle_state.json")
    assert not verify_webhook_signature(body, {"SC-Signature": signature}, "some-other-token")


def test_missing_header_fails() -> None:
    body, _ = _signed("webhook_vehicle_state.json")
    assert not verify_webhook_signature(body, {}, TEST_MANAGEMENT_TOKEN)
    assert not verify_webhook_signature(body, {"SC-Signature": ""}, TEST_MANAGEMENT_TOKEN)


def test_empty_management_token_fails_closed() -> None:
    body = b"{}"
    signature = hmac.new(b"", body, hashlib.sha256).hexdigest()
    assert not verify_webhook_signature(body, {"SC-Signature": signature}, "")


def test_verify_challenge_response_matches_documented_hmac() -> None:
    body, signature = _signed("webhook_verify_challenge.json")
    assert verify_webhook_signature(body, {"SC-Signature": signature}, TEST_MANAGEMENT_TOKEN)

    event = WebhookEvent.model_validate_json(body)
    assert event.event_type == "VERIFY"
    assert event.challenge is not None
    expected = hmac.new(
        TEST_MANAGEMENT_TOKEN.encode(), event.challenge.encode(), hashlib.sha256
    ).hexdigest()
    assert verify_challenge_response(event.challenge, TEST_MANAGEMENT_TOKEN) == expected


def test_compute_signature_is_hex_sha256() -> None:
    assert len(compute_signature(b"abc", "k")) == 64


def test_malformed_signatures_are_rejected_without_raising() -> None:
    body, signature = _signed("webhook_vehicle_state.json")
    for bad in (
        signature[:-1] + "é",  # non-ASCII: compare_digest would raise TypeError
        "é" * 64,
        signature[:-2],  # too short
        signature + "00",  # too long
        "z" * 64,  # not hex
    ):
        assert not verify_webhook_signature(body, {"SC-Signature": bad}, TEST_MANAGEMENT_TOKEN)


def test_signature_with_surrounding_whitespace_is_accepted() -> None:
    body, signature = _signed("webhook_vehicle_state.json")
    headers = {"SC-Signature": f"  {signature} "}
    assert verify_webhook_signature(body, headers, TEST_MANAGEMENT_TOKEN)
