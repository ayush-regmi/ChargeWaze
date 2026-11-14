"""Smartcar V3 HTTP client — the only code in Chargewise that talks to Smartcar.

V3 uses one application-level token (client credentials) for every user; the
``sc-user-id`` header picks whose vehicle a request is for. There are no
per-user tokens to store. See docs/smartcar-v3-notes.md.

Logging rule: one line per request with method, endpoint *template*, status and
duration. Never log tokens, Smartcar user/vehicle/connection IDs, pagination
links (they embed the user ID) or coordinates.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from functools import lru_cache
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from fastapi import status

from app.core.config import Settings, SmartcarMode, get_settings
from app.core.errors import ApiError
from app.integrations.smartcar import errors
from app.integrations.smartcar.schemas import (
    Connection,
    ConnectionsPage,
    SignalsResponse,
    TokenResponse,
    Vehicle,
    VehicleResponse,
)

logger = logging.getLogger(__name__)

PROVIDER = "smartcar"
# Refresh the application token this long before Smartcar says it expires.
TOKEN_REFRESH_MARGIN_SECONDS = 60
CONNECTIONS_PAGE_SIZE = 50
# Upper bound on connection pages, so a bad totalCount can't loop forever.
MAX_CONNECTION_PAGES = 20

DEFAULT_SCOPES: tuple[str, ...] = (
    "read_battery",
    "read_charge",
    "read_odometer",
    "read_vehicle_info",
)


class SmartcarClient:
    """Thin, typed wrapper over the Smartcar V3 REST API.

    Thread-safe: FastAPI runs sync routes in a threadpool, so the token cache
    is guarded by a lock. Pass ``transport`` (e.g. ``httpx.MockTransport``) in
    tests; production uses the default network transport.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        application_id: str,
        redirect_uri: str,
        mode: SmartcarMode,
        api_base_url: str,
        auth_url: str,
        connect_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._application_id = application_id
        self._redirect_uri = redirect_uri
        self._mode = mode
        self._api_base_url = api_base_url.rstrip("/")
        self._auth_url = auth_url
        self._connect_url = connect_url
        self._clock = clock
        self._http = httpx.Client(timeout=timeout_seconds, transport=transport)
        self._token_lock = threading.Lock()
        self._token: str | None = None
        self._token_expires_at = 0.0

    @classmethod
    def from_settings(
        cls, settings: Settings, *, transport: httpx.BaseTransport | None = None
    ) -> SmartcarClient:
        return cls(
            client_id=settings.smartcar_client_id,
            client_secret=settings.smartcar_client_secret,
            application_id=settings.smartcar_application_id,
            redirect_uri=settings.smartcar_redirect_uri,
            mode=settings.smartcar_mode,
            api_base_url=settings.smartcar_api_base_url,
            auth_url=settings.smartcar_auth_url,
            connect_url=settings.smartcar_connect_url,
            timeout_seconds=settings.smartcar_timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def get_application_token(self) -> str:
        """Return a cached application token, fetching a new one near expiry."""

        with self._token_lock:
            if self._token is not None and self._clock() < self._token_expires_at:
                return self._token
            self._ensure_credentials()
            response = self._send(
                "POST",
                self._auth_url,
                endpoint="oauth2/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
            )
            token = TokenResponse.model_validate(response.json())
            if token.token_type.lower() != "bearer":
                raise ApiError(
                    errors.UPSTREAM_ERROR,
                    "Smartcar returned an unexpected token type.",
                    status_code=status.HTTP_502_BAD_GATEWAY,
                )
            self._token = token.access_token
            lifetime = max(token.expires_in - TOKEN_REFRESH_MARGIN_SECONDS, 0)
            self._token_expires_at = self._clock() + lifetime
            return self._token

    def _invalidate_token(self) -> None:
        with self._token_lock:
            self._token = None
            self._token_expires_at = 0.0

    def _ensure_credentials(self) -> None:
        if not self._client_id or not self._client_secret:
            raise ApiError(
                "SMARTCAR_NOT_CONFIGURED",
                "Smartcar credentials are not configured.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    # ------------------------------------------------------------------
    # Connect
    # ------------------------------------------------------------------

    def build_connect_url(
        self,
        *,
        state: str,
        external_id: str,
        scopes: Iterable[str] = DEFAULT_SCOPES,
    ) -> str:
        """Smartcar Connect URL for ``response_type=none`` (application tokens).

        ``external_id`` is our profile ID; Smartcar echoes it on the redirect and
        stores it on the connection.
        """

        if not self._application_id:
            raise ApiError(
                "SMARTCAR_NOT_CONFIGURED",
                "Smartcar application ID is not configured.",
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        scope_list = list(scopes)
        if not scope_list:
            raise ValueError("At least one Smartcar scope is required.")
        params = {
            "application_id": self._application_id,
            "response_type": "none",
            "redirect_uri": self._redirect_uri,
            "scope": " ".join(scope_list),
            "state": state,
            "mode": self._mode,
            "external_id": external_id,
        }
        return f"{self._connect_url}?{urlencode(params, quote_via=quote)}"

    # ------------------------------------------------------------------
    # Connections and vehicles
    # ------------------------------------------------------------------

    def list_connections(self, sc_user_id: str) -> list[Connection]:
        """All connections for one Smartcar user, across pages."""

        connections: list[Connection] = []
        for page_number in range(1, MAX_CONNECTION_PAGES + 1):
            response = self._api(
                "GET",
                "/connections",
                endpoint="connections",
                params={
                    "filter[userId]": sc_user_id,
                    "page[number]": page_number,
                    "page[size]": CONNECTIONS_PAGE_SIZE,
                },
            )
            page = ConnectionsPage.model_validate(response.json())
            connections.extend(page.data)
            if not page.data or len(connections) >= page.meta.total_count:
                return connections
        logger.warning(
            "smartcar_pagination_capped",
            extra={"provider": PROVIDER, "endpoint": "connections"},
        )
        return connections

    def get_vehicle(self, sc_user_id: str, vehicle_id: str) -> Vehicle:
        response = self._api(
            "GET",
            f"/vehicles/{quote(vehicle_id, safe='')}",
            endpoint="vehicles/{id}",
            sc_user_id=sc_user_id,
        )
        return VehicleResponse.model_validate(response.json()).data

    def get_signals(self, sc_user_id: str, vehicle_id: str) -> SignalsResponse:
        """Every signal for the vehicle. May be empty for minutes after Connect."""

        response = self._api(
            "GET",
            f"/vehicles/{quote(vehicle_id, safe='')}/signals",
            endpoint="vehicles/{id}/signals",
            sc_user_id=sc_user_id,
        )
        return SignalsResponse.model_validate(response.json())

    def delete_connection(self, connection_id: str) -> None:
        """Revoke one connection. Smartcar answers ``204``."""

        self._api(
            "DELETE",
            f"/connections/{quote(connection_id, safe='')}",
            endpoint="connections/{id}",
        )

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _api(
        self,
        method: str,
        path: str,
        *,
        endpoint: str,
        sc_user_id: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        """Authenticated call to the V3 API; retries once if the token was rejected."""

        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self.get_application_token()}"}
            if sc_user_id is not None:
                headers["sc-user-id"] = sc_user_id
            response = self._send(
                method,
                f"{self._api_base_url}{path}",
                endpoint=endpoint,
                headers=headers,
                params=params,
                raise_for_status=False,
            )
            if response.status_code == status.HTTP_401_UNAUTHORIZED and attempt == 1:
                # Token revoked or rotated early: drop it and try once more.
                self._invalidate_token()
                continue
            if response.is_error:
                raise errors.from_response(response)
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    def _send(
        self,
        method: str,
        url: str,
        *,
        endpoint: str,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        data: Mapping[str, str] | None = None,
        raise_for_status: bool = True,
    ) -> httpx.Response:
        started = time.perf_counter()
        status_code: int | None = None
        try:
            response = self._http.request(method, url, headers=headers, params=params, data=data)
            status_code = response.status_code
        except httpx.TransportError as exc:
            raise errors.from_transport_error(exc) from None
        finally:
            logger.info(
                "smartcar_request",
                extra={
                    "provider": PROVIDER,
                    "method": method,
                    "endpoint": endpoint,
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
        if raise_for_status and response.is_error:
            raise errors.from_response(response)
        return response


@lru_cache(maxsize=1)
def _default_client() -> SmartcarClient:
    return SmartcarClient.from_settings(get_settings())


def get_smartcar_client() -> SmartcarClient:
    """FastAPI dependency. Tests override it with a fake or a MockTransport client."""

    return _default_client()


__all__ = ["DEFAULT_SCOPES", "SmartcarClient", "get_smartcar_client"]
