"""``POST /api/v1/webhooks/smartcar`` (``docs/api-contracts.md`` §3.12, Phase 2 F5).

The only route without Supabase auth: Smartcar authenticates by signing the
raw body (``SC-Signature``). Order matters and follows ``phase2_plan.md`` F5:

1. Verify the signature on the raw bytes. Fail → 401, nothing persisted.
2. ``VERIFY`` → answer the challenge. Only after step 1, or the endpoint would
   be an HMAC oracle for forging signatures.
3. Parse the payload. Malformed → 400 (retrying won't fix it).
4. Ingest (``telemetry_service``). Unknown / disconnected vehicle → 200
   ``accepted: false`` so Smartcar stops retrying (D12).
5. Database failure → 5xx so Smartcar retries.

Logs carry the event ID and outcome only, never Smartcar user/vehicle IDs or
signal values.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import ApiError, ErrorEnvelope
from app.db.session import get_db
from app.integrations.smartcar.schemas import WebhookEvent
from app.integrations.smartcar.signatures import (
    verify_challenge_response,
    verify_webhook_signature,
)
from app.modules.vehicles.telemetry_service import ingest_vehicle_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

INGESTED_EVENT_TYPES = frozenset({"VEHICLE_STATE", "VEHICLE_ERROR"})


class WebhookAck(BaseModel):
    accepted: bool
    event_id: str | None = None


class WebhookChallenge(BaseModel):
    challenge: str


async def _raw_body(request: Request) -> bytes:
    # A dependency so the handler itself can stay sync (the DB session is sync).
    return await request.body()


@router.post(
    "/smartcar",
    response_model=WebhookAck | WebhookChallenge,
    responses={
        400: {"model": ErrorEnvelope, "description": "Signed but malformed payload."},
        401: {"model": ErrorEnvelope, "description": "WEBHOOK_SIGNATURE_INVALID."},
        500: {"model": ErrorEnvelope, "description": "Ingest failed; Smartcar retries."},
    },
)
def receive_smartcar_webhook(
    request: Request,
    raw_body: bytes = Depends(_raw_body),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> WebhookAck | WebhookChallenge:
    token = settings.smartcar_management_token
    if not token:
        logger.error("smartcar_webhook_rejected", extra={"reason": "management_token_not_set"})
    if not verify_webhook_signature(raw_body, request.headers, token):
        logger.warning("smartcar_webhook_rejected", extra={"reason": "signature_invalid"})
        raise ApiError(
            "WEBHOOK_SIGNATURE_INVALID",
            "Webhook signature did not verify.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    event = _parse_event(raw_body)

    if event.event_type == "VERIFY":
        challenge = event.challenge
        if challenge is None:
            raise _payload_invalid("VERIFY event has no challenge.")
        logger.info("smartcar_webhook_verify_answered", extra={"event_id": event.event_id})
        return WebhookChallenge(challenge=verify_challenge_response(challenge, token))

    if event.event_type not in INGESTED_EVENT_TYPES:
        logger.info(
            "smartcar_webhook_ignored",
            extra={"event_id": event.event_id, "event_type": event.event_type},
        )
        return WebhookAck(accepted=False, event_id=event.event_id)

    if not event.event_id:
        # Without an event ID there's no dedup key, and at-least-once delivery
        # would double-count readings.
        raise _payload_invalid("Event has no eventId.")

    try:
        data = event.vehicle_state()
    except ValidationError:
        if event.event_type == "VEHICLE_ERROR":
            # Shape not confirmed against a real delivery yet: acknowledge
            # rather than have Smartcar retry it for three days.
            logger.warning(
                "smartcar_webhook_ignored",
                extra={"event_id": event.event_id, "reason": "vehicle_error_unparsed"},
            )
            return WebhookAck(accepted=False, event_id=event.event_id)
        raise _payload_invalid("Event data is not a valid vehicle state.") from None

    try:
        result = ingest_vehicle_event(db, event, data)
    except SQLAlchemyError as exc:
        db.rollback()
        # No traceback or message: SQLAlchemy errors carry the SQL and bound
        # parameters (Smartcar IDs, raw_payload, possibly location), and the
        # driver message can echo values too (e.g. a unique-violation DETAIL).
        logger.error(
            "smartcar_webhook_ingest_failed",
            extra={
                "event_id": event.event_id,
                "error_type": type(exc).__name__,
                "sqlstate": getattr(getattr(exc, "orig", None), "sqlstate", None),
            },
        )
        raise ApiError(
            "INTERNAL_ERROR",
            "Webhook could not be stored; retry later.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        ) from None

    if not result.accepted:
        logger.info(
            "smartcar_webhook_not_accepted",
            extra={"event_id": result.event_id, "reason": result.reason},
        )
    return WebhookAck(accepted=result.accepted, event_id=result.event_id)


def _parse_event(raw_body: bytes) -> WebhookEvent:
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _payload_invalid("Body is not valid JSON.") from None
    if not isinstance(payload, dict):
        raise _payload_invalid("Body is not a JSON object.")
    try:
        return WebhookEvent.model_validate(payload)
    except ValidationError:
        raise _payload_invalid("Body is not a Smartcar webhook event.") from None


def _payload_invalid(message: str) -> ApiError:
    return ApiError("WEBHOOK_PAYLOAD_INVALID", message, status_code=status.HTTP_400_BAD_REQUEST)


__all__ = ["WebhookAck", "WebhookChallenge", "router"]
