"""Validation for the post-Connect ``redirect_after`` path.

Server-side counterpart of ``apps/web/lib/safe-next.ts``. The value ends up
in a ``Location`` header after Connect completes, so anything that a browser
could read as another origin (``//host``, ``/\\host``, tab/newline smuggling,
dot segments that collapse to ``//``) must be rejected. This is stricter than
the frontend helper: instead of normalizing odd paths, it refuses them.
"""

from __future__ import annotations

from urllib.parse import urlsplit

DEFAULT_REDIRECT_AFTER = "/garage"
MAX_REDIRECT_AFTER_LENGTH = 512


def is_safe_redirect_path(value: str) -> bool:
    """True if ``value`` is a same-origin, absolute-path reference."""

    if not value or len(value) > MAX_REDIRECT_AFTER_LENGTH:
        return False
    if not value.startswith("/") or value.startswith("//"):
        return False
    # Backslashes are treated as "/" by browsers; control chars and spaces can
    # be stripped or smuggled. None belong in a path we produce.
    if any(ch == "\\" or ord(ch) < 0x21 or ord(ch) == 0x7F for ch in value):
        return False

    parts = urlsplit(value)
    if parts.scheme or parts.netloc:
        return False
    path = parts.path
    if "//" in path:
        return False
    segments = path.split("/")[1:]
    # Reject dot segments outright, plain or percent-encoded.
    return all(seg.lower() not in {".", "..", "%2e", "%2e%2e", ".%2e", "%2e."} for seg in segments)


def safe_redirect_path(value: str | None, fallback: str = DEFAULT_REDIRECT_AFTER) -> str:
    """Return ``value`` if it is safe, otherwise ``fallback``."""

    if value is not None and is_safe_redirect_path(value):
        return value
    return fallback


__all__ = [
    "DEFAULT_REDIRECT_AFTER",
    "MAX_REDIRECT_AFTER_LENGTH",
    "is_safe_redirect_path",
    "safe_redirect_path",
]
