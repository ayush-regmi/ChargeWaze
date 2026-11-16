"""Sign a Smartcar webhook fixture and POST it to a local API (Phase 2 F5).

For testing the webhook ingestor without a tunnel or a real car. The body is
signed with ``SMARTCAR_MANAGEMENT_TOKEN`` from ``services/api/.env`` (the same
key the API verifies with); the token is never printed.

Run from services/api/ with the API running (``uv run fastapi dev app/main.py``):

  uv run python scripts/send_test_webhook.py --vehicle-id <smartcar vehicle id> \\
      --user-id <smartcar user id> --now
  uv run python scripts/send_test_webhook.py --fixture webhook_verify_challenge.json

The fixtures name a placeholder vehicle, so pass the Smartcar IDs of a vehicle
connected in your local database (``vehicles.smartcar_vehicle_id`` /
``smartcar_user_id``) or the API answers ``accepted: false``. Each run gets a
fresh ``eventId`` unless ``--event-id`` is given, so repeated runs aren't
deduplicated; reuse an ID to see the duplicate path.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

API_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = API_ROOT.parents[1] / "fixtures" / "smartcar"
DEFAULT_URL = "http://localhost:8000/api/v1/webhooks/smartcar"

sys.path.insert(0, str(API_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.integrations.smartcar.signatures import SIGNATURE_HEADER, compute_signature  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--fixture",
        default="webhook_vehicle_state.json",
        help="File in fixtures/smartcar/ (default: %(default)s)",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help="Endpoint (default: %(default)s)")
    parser.add_argument("--vehicle-id", help="Replace data.vehicle.id")
    parser.add_argument("--user-id", help="Replace data.user.id")
    parser.add_argument("--event-id", help="Use this eventId (default: a fresh one)")
    parser.add_argument(
        "--now",
        action="store_true",
        help="Stamp every signal and the delivery with the current time",
    )
    parser.add_argument(
        "--bad-signature", action="store_true", help="Send a wrong signature (expect 401)"
    )
    args = parser.parse_args(argv)

    token = get_settings().smartcar_management_token
    if not token:
        print("SMARTCAR_MANAGEMENT_TOKEN is not set in services/api/.env.", file=sys.stderr)
        return 2

    path = FIXTURES / args.fixture
    if not path.is_file():
        print(f"No fixture {path}", file=sys.stderr)
        return 2
    payload = _prepare(json.loads(path.read_text()), args)

    body = json.dumps(payload).encode()
    signature = "0" * 64 if args.bad_signature else compute_signature(body, token)
    try:
        response = httpx.post(
            args.url,
            content=body,
            headers={"Content-Type": "application/json", SIGNATURE_HEADER: signature},
            timeout=10,
        )
    except httpx.TransportError as exc:
        print(f"Could not reach {args.url}: {exc}", file=sys.stderr)
        return 1

    print(f"{payload.get('eventType')} {payload.get('eventId')} -> HTTP {response.status_code}")
    print(response.text)
    return 0 if response.is_success else 1


def _prepare(payload: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if payload.get("eventType") != "VERIFY":
        payload["eventId"] = args.event_id or f"local-{uuid.uuid4()}"
    elif args.event_id:
        payload["eventId"] = args.event_id

    data = payload.get("data") or {}
    if args.vehicle_id and isinstance(data.get("vehicle"), dict):
        data["vehicle"]["id"] = args.vehicle_id
    if args.user_id and isinstance(data.get("user"), dict):
        data["user"]["id"] = args.user_id

    if args.now:
        now_ms = int(time.time() * 1000)
        for signal in data.get("signals") or []:
            meta = signal.setdefault("meta", {})
            meta["oemUpdatedAt"] = now_ms
            meta["retrievedAt"] = now_ms
        payload.setdefault("meta", {})["deliveredAt"] = now_ms
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
