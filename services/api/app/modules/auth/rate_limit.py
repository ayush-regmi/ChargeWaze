"""Per-user rate limits for the expensive endpoints (P6-F4, api-contracts §6 Q2).

``POST /routes/plan`` (Mapbox Directions + AFDC per call) and
``GET /places/suggest`` (Mapbox Search Box per keystroke) each get a token
bucket per signed-in user: ``burst`` requests at once, refilled at a steady
rate. An empty bucket answers ``429 RATE_LIMITED`` with
``details.retry_after_seconds`` and a ``Retry-After`` header, the same shape as
the vehicle refresh limit (§3.5a).

Every authenticated request counts, including ones that later fail
validation. Unauthenticated requests are refused (401) before they reach a
bucket.

Buckets live in process memory, one limiter per app instance. Render's free
plan runs a single instance, so that is the whole limit; with more than one
instance each would allow its own burst, and the limiter would move to
Postgres. User IDs are only dictionary keys here; they are never logged.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, Request, status

from app.core.config import Settings, get_settings
from app.core.errors import ApiError
from app.modules.auth.dependencies import AuthenticatedUser, get_current_user

logger = logging.getLogger(__name__)

Bucket = Literal["plan", "suggest"]

_MESSAGES: dict[Bucket, str] = {
    "plan": "Too many trip plans. Try again shortly.",
    "suggest": "Too many place searches. Try again shortly.",
}


@dataclass(frozen=True)
class Limit:
    capacity: int
    refill_per_second: float


def limit_for(bucket: Bucket, settings: Settings) -> Limit:
    if bucket == "plan":
        return Limit(settings.rate_limit_plan_burst, settings.rate_limit_plan_per_hour / 3600)
    return Limit(settings.rate_limit_suggest_burst, settings.rate_limit_suggest_per_minute / 60)


class TokenBucketLimiter:
    """Thread-safe token buckets keyed by ``(bucket, user)``.

    Sync route handlers run in a thread pool, so concurrent requests from one
    user take the lock in turn and can't both spend the last token.
    """

    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, max_keys: int = 10_000
    ) -> None:
        self._clock = clock
        self._max_keys = max_keys
        self._lock = threading.Lock()
        # key -> (tokens, last refill time)
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}

    def acquire(self, key: tuple[str, str], limit: Limit) -> float:
        """Spend one token. Returns 0 when allowed, else seconds until one is available."""

        with self._lock:
            now = self._clock()
            tokens = self._tokens(key, limit, now)
            if tokens >= 1:
                self._buckets[key] = (tokens - 1, now)
                self._prune()
                return 0.0
            self._buckets[key] = (tokens, now)
            return (1 - tokens) / limit.refill_per_second

    def _tokens(self, key: tuple[str, str], limit: Limit, now: float) -> float:
        state = self._buckets.get(key)
        if state is None:
            return float(limit.capacity)
        tokens, updated = state
        return min(float(limit.capacity), tokens + (now - updated) * limit.refill_per_second)

    def _prune(self) -> None:
        """Bound memory: past ``max_keys``, drop the least recently used tenth.

        A dropped bucket restarts full, which only matters for a user idle
        long enough to be among the oldest of ``max_keys`` others.
        """

        if len(self._buckets) <= self._max_keys:
            return
        keep = self._max_keys * 9 // 10
        overflow = len(self._buckets) - keep
        oldest = sorted(self._buckets.items(), key=lambda item: item[1][1])[:overflow]
        for key, _ in oldest:
            del self._buckets[key]

    def __len__(self) -> int:
        return len(self._buckets)


_init_lock = threading.Lock()


def get_rate_limiter(request: Request) -> TokenBucketLimiter:
    """The app's limiter, created on first use (one per app instance)."""

    state = request.app.state
    limiter: TokenBucketLimiter | None = getattr(state, "rate_limiter", None)
    if limiter is None:
        with _init_lock:
            limiter = getattr(state, "rate_limiter", None)
            if limiter is None:
                limiter = TokenBucketLimiter()
                state.rate_limiter = limiter
    return limiter


def rate_limit(bucket: Bucket) -> Callable[..., None]:
    """Route dependency enforcing the per-user limit for ``bucket``."""

    def dependency(
        user: AuthenticatedUser = Depends(get_current_user),
        settings: Settings = Depends(get_settings),
        limiter: TokenBucketLimiter = Depends(get_rate_limiter),
    ) -> None:
        if not settings.rate_limit_enabled:
            return
        wait = limiter.acquire((bucket, str(user.user_id)), limit_for(bucket, settings))
        if wait <= 0:
            return
        retry_after = max(1, math.ceil(wait))
        logger.info("rate_limited", extra={"bucket": bucket, "retry_after_seconds": retry_after})
        raise ApiError(
            "RATE_LIMITED",
            _MESSAGES[bucket],
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            details={"limit": bucket, "retry_after_seconds": retry_after},
            headers={"Retry-After": str(retry_after)},
        )

    return dependency


__all__ = ["Limit", "TokenBucketLimiter", "get_rate_limiter", "limit_for", "rate_limit"]
