"""Smartcar webhook signature checks.

``SC-Signature`` is the hex HMAC-SHA256 of the raw request body, keyed with the
Application Management Token. The same key answers the ``VERIFY`` challenge.
See docs/smartcar-v3-notes.md §5.

Always verify the signature *before* answering ``VERIFY``: answering an
unsigned challenge would let anyone use the endpoint as an HMAC oracle.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping

SIGNATURE_HEADER = "SC-Signature"
# Hex SHA-256 digest. Anything else (including non-ASCII) is rejected before
# compare_digest, which raises TypeError on non-ASCII str input.
_SIGNATURE_RE = re.compile(r"[0-9a-fA-F]{64}")


def compute_signature(raw_body: bytes, management_token: str) -> str:
    return hmac.new(management_token.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def verify_webhook_signature(
    raw_body: bytes, headers: Mapping[str, str], management_token: str
) -> bool:
    """Return ``True`` only if ``SC-Signature`` matches ``raw_body``.

    ``headers`` may be a Starlette ``Headers`` (case-insensitive) or a plain
    dict; the lookup is case-insensitive either way. An empty management token
    never verifies, so a missing setting fails closed.
    """

    if not management_token:
        return False
    provided = _get_header(headers, SIGNATURE_HEADER)
    if not provided:
        return False
    candidate = provided.strip()
    if not _SIGNATURE_RE.fullmatch(candidate):
        return False
    expected = compute_signature(raw_body, management_token)
    return hmac.compare_digest(expected, candidate.lower())


def verify_challenge_response(challenge: str, management_token: str) -> str:
    """Value for ``{"challenge": ...}`` in the reply to a signed ``VERIFY`` event."""

    return compute_signature(challenge.encode("utf-8"), management_token)


def _get_header(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is not None:
        return value
    lowered = name.lower()
    for key, candidate in headers.items():
        if key.lower() == lowered:
            return candidate
    return None


__all__ = [
    "SIGNATURE_HEADER",
    "compute_signature",
    "verify_challenge_response",
    "verify_webhook_signature",
]
