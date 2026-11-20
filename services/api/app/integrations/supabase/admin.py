"""Supabase Auth admin adapter (P6-F5). The only code that calls the admin API.

Account deletion removes the Supabase auth user with
``DELETE {SUPABASE_URL}/auth/v1/admin/users/{id}``, authenticated with the
service-role key. That key bypasses row level security and must never reach
the browser; it lives only in the backend's environment.

``delete_user`` never raises: account deletion records an unfinished step and
the retention job retries it. Logs carry provider, endpoint template, status
and duration only, never the user ID or the key.
"""

from __future__ import annotations

import logging
import time
import uuid
from functools import lru_cache
from typing import Literal

import httpx

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

PROVIDER = "supabase"
AuthUserDeletion = Literal["deleted", "already_deleted", "not_configured", "failed"]


class SupabaseAdminClient:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        timeout_seconds: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = supabase_url.rstrip("/")
        self._key = service_role_key
        self._http = httpx.Client(timeout=timeout_seconds, transport=transport)

    @classmethod
    def from_settings(
        cls, settings: Settings, *, transport: httpx.BaseTransport | None = None
    ) -> SupabaseAdminClient:
        return cls(
            supabase_url=settings.supabase_url,
            service_role_key=settings.supabase_service_role_key,
            transport=transport,
        )

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url and self._key)

    def close(self) -> None:
        self._http.close()

    def delete_user(self, user_id: uuid.UUID) -> AuthUserDeletion:
        """Delete the auth user (hard delete; their sessions and refresh tokens go too)."""

        if not self.is_configured:
            logger.error("supabase_admin_not_configured")
            return "not_configured"
        started = time.perf_counter()
        status_code: int | None = None
        try:
            response = self._http.delete(
                f"{self._base_url}/auth/v1/admin/users/{user_id}",
                headers={"apikey": self._key, "Authorization": f"Bearer {self._key}"},
            )
            status_code = response.status_code
        except httpx.HTTPError as exc:
            logger.warning("supabase_admin_failed", extra={"error_type": type(exc).__name__})
            return "failed"
        finally:
            logger.info(
                "supabase_request",
                extra={
                    "provider": PROVIDER,
                    "method": "DELETE",
                    "endpoint": "auth/v1/admin/users/{id}",
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
        if response.is_success:
            return "deleted"
        if response.status_code == 404:
            return "already_deleted"
        logger.warning("supabase_admin_failed", extra={"status": response.status_code})
        return "failed"


@lru_cache(maxsize=1)
def _default_client() -> SupabaseAdminClient:
    return SupabaseAdminClient.from_settings(get_settings())


def get_supabase_admin() -> SupabaseAdminClient:
    """FastAPI dependency. Tests override it with a ``MockTransport`` client."""

    return _default_client()


__all__ = ["AuthUserDeletion", "SupabaseAdminClient", "get_supabase_admin"]
