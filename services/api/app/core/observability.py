"""Error reporting (Sentry) with privacy scrubbing.

Sentry is off unless ``SENTRY_DSN_BACKEND`` is set, and always off in tests
(``APP_ENV=test`` or running under pytest), so local runs and CI never send
anything.

Every event, transaction and breadcrumb passes through :func:`scrub_event`
before it leaves the process (architecture.md §9, engineering-guidelines.md
"Security"): user IDs, vehicle/connection IDs, UUIDs and coordinates are
removed, as are request bodies, query strings, cookies and auth headers.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from typing import Any

import sentry_sdk
from sentry_sdk.transport import Transport
from sentry_sdk.types import Breadcrumb, BreadcrumbHint, Event, Hint

from app.core.config import Settings

FILTERED = "[Filtered]"

# Keys whose values are always dropped, wherever they appear in an event
# (extra, contexts, tags, breadcrumb data, stack-frame locals, request data).
_SENSITIVE_KEYS = frozenset(
    {
        # identities
        "sub",
        "email",
        "user",
        "ip_address",
        # coordinates and geometry
        "lat",
        "lon",
        "lng",
        "latitude",
        "longitude",
        "coords",
        "coordinates",
        "polyline",
        "geometry",
        "bbox",
        # credentials
        "authorization",
        "cookie",
        "cookies",
        "set-cookie",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
    }
)
# ``user_id``, ``vehicle_id``, ``smartcar_vehicle_id``, ``connectionId``, ...
_ID_KEY = re.compile(r"(^|[_-])(user|vehicle|connection|owner)[_-]?id$", re.IGNORECASE)
# ``origin_lat``, ``start_lon``, ``dest_latitude``, ...
_COORD_KEY = re.compile(r"(^|[_-])(lat|lon|lng|latitude|longitude)($|[_-])", re.IGNORECASE)

_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)
# "37.7749,-122.4194", "(37.77, -122.41)", "-122.41 37.77"
_COORD_PAIR = re.compile(r"-?\d{1,3}\.\d{2,}\s*[,; ]\s*-?\d{1,3}\.\d{2,}")
# "?lat=37.7&lon=-122.4" inside URLs or log messages
_COORD_PARAM = re.compile(
    r"\b(lat|lon|lng|latitude|longitude|near|origin|destination)=[^&\s\"']*", re.IGNORECASE
)
# "'lat': 37.7", "origin_lon=-122.4" inside reprs of dicts, models and kwargs
_COORD_ASSIGN = re.compile(
    r"(?<![A-Za-z])((?:lat|lon|lng)\w*['\"]?\s*[:=]\s*)-?\d+(?:\.\d+)?", re.IGNORECASE
)
# "originLat": 37.7 (camelCase keys inside serialized JSON)
_CAMEL_COORD_ASSIGN = re.compile(r"(?<=[a-z])((?:Lat|Lon|Lng)\w*['\"]?\s*[:=]\s*)-?\d+(?:\.\d+)?")


_COORD_NUMBER = re.compile(r"-?\d{1,3}\.\d{2,}")


def _looks_like_coordinate(item: Any) -> bool:
    if isinstance(item, float):
        return -180.0 <= item <= 180.0 and not item.is_integer()
    return isinstance(item, str) and bool(_COORD_NUMBER.fullmatch(item.strip()))


def _is_coordinate_pair(value: list[Any] | tuple[Any, ...]) -> bool:
    """A ``(lat, lon)`` / ``[lon, lat]`` shaped pair, e.g. a serialized local variable."""

    return len(value) == 2 and all(_looks_like_coordinate(item) for item in value)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _SENSITIVE_KEYS or bool(_ID_KEY.search(key) or _COORD_KEY.search(key))


def scrub_string(value: str) -> str:
    """Replace UUIDs and coordinates embedded in free text."""

    value = _UUID.sub("[uuid]", value)
    value = _COORD_PARAM.sub(lambda m: f"{m.group(1)}={FILTERED}", value)
    value = _COORD_ASSIGN.sub(lambda m: f"{m.group(1)}{FILTERED}", value)
    value = _CAMEL_COORD_ASSIGN.sub(lambda m: f"{m.group(1)}{FILTERED}", value)
    return _COORD_PAIR.sub("[coords]", value)


# Keys holding a URL (request, http/navigation breadcrumbs): the query string is
# dropped whole, since it can carry coordinates, search text or tokens.
_URL_KEYS = frozenset({"url", "to", "from"})


def _scrub_item(key: Any, item: Any) -> Any:
    if not isinstance(key, str):
        return _scrub(item)
    if _is_sensitive_key(key):
        return FILTERED
    if key.lower() in _URL_KEYS and isinstance(item, str):
        item = re.split(r"[?#]", item, maxsplit=1)[0]
    return _scrub(item)


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return scrub_string(value)
    if isinstance(value, Mapping):
        return {key: _scrub_item(key, item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        if _is_coordinate_pair(value):
            return "[coords]"
        return [_scrub(item) for item in value]
    return value


def scrub_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy of a Sentry event (or breadcrumb) with personal data removed."""

    cleaned = dict(event)
    cleaned.pop("user", None)
    request = cleaned.get("request")
    if isinstance(request, Mapping):
        request = {
            k: v for k, v in request.items() if k not in {"cookies", "data", "query_string", "env"}
        }
        cleaned["request"] = request
    scrubbed: dict[str, Any] = _scrub(cleaned)
    return scrubbed


def _before_send(event: Event, _hint: Hint) -> Event | None:
    return scrub_event(event)  # type: ignore[return-value]


def _before_breadcrumb(crumb: Breadcrumb, _hint: BreadcrumbHint) -> Breadcrumb | None:
    return scrub_event(crumb)


def start_sentry(
    *,
    dsn: str,
    environment: str,
    traces_sample_rate: float = 0.0,
    transport: type[Transport] | Transport | None = None,
) -> None:
    """Initialise the SDK with the scrubbing hooks. Use :func:`init_sentry` in app code."""

    options: dict[str, Any] = {}
    if transport is not None:
        options["transport"] = transport
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        # send_default_pii / data_collection are left unset: the SDK then
        # collects no user identity, cookies or IPs and keeps its own
        # EventScrubber on as a second layer under ours.
        max_request_body_size="never",
        traces_sample_rate=traces_sample_rate,
        before_send=_before_send,
        before_send_transaction=_before_send,
        before_breadcrumb=_before_breadcrumb,
        **options,
    )


def sentry_enabled(settings: Settings) -> bool:
    """True only with a DSN, outside tests."""

    return (
        bool(settings.sentry_dsn_backend) and not settings.is_test and "pytest" not in sys.modules
    )


def init_sentry(settings: Settings) -> bool:
    """Start Sentry when configured. Returns whether it was started."""

    if not sentry_enabled(settings):
        return False
    start_sentry(
        dsn=settings.sentry_dsn_backend,
        environment=settings.sentry_environment or settings.app_env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
    )
    return True


__all__ = [
    "FILTERED",
    "init_sentry",
    "scrub_event",
    "scrub_string",
    "sentry_enabled",
    "start_sentry",
]
