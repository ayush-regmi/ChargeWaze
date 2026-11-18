"""Application settings loaded from environment variables.

Follows the rules in docs/engineering-guidelines.md: secrets stay backend-only,
and nothing in ``NEXT_PUBLIC_*`` is referenced here.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "preview", "production", "test"]
# Smartcar V3 Connect only accepts these. ``test`` (a V2 value) completes Connect
# but authorizes no vehicle, so it is rejected at startup.
SmartcarMode = Literal["live", "simulated"]


class Settings(BaseSettings):
    """Backend configuration.

    Env values are read from the process environment and, locally, from a
    ``.env`` file next to this project (``services/api/.env``). The ``.env``
    file is gitignored; only ``services/api/.env.example`` is committed.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- App ----
    app_env: AppEnv = "development"
    app_public_demo_mode: bool = False
    allowed_origins: str = "http://localhost:3000"

    # ---- Database ----
    database_url: str = "postgresql+psycopg://chargewise:chargewise@localhost:5432/chargewise"

    # ---- Supabase ----
    supabase_url: str = ""
    # URL of the project's JWKS document, e.g.
    # ``https://<project-ref>.supabase.co/auth/v1/.well-known/jwks.json``.
    supabase_jwks_url: str = ""
    # Expected JWT ``aud`` claim. Supabase uses ``authenticated`` for signed-in users.
    supabase_jwt_audience: str = "authenticated"
    # Optional fallback for legacy HS256 projects. Prefer JWKS.
    supabase_jwt_secret: str = ""
    supabase_service_role_key: str = ""

    # ---- Smartcar V3 (see docs/smartcar-v3-notes.md) ----
    smartcar_client_id: str = ""
    smartcar_client_secret: str = ""
    # Connect ``application_id``; a different value from the client ID.
    smartcar_application_id: str = ""
    # A *frontend* route: Connect must finish where the user's session is, so the
    # backend can check the signed-in user is the one who started it.
    smartcar_redirect_uri: str = "http://localhost:3000/garage/connect/callback"
    smartcar_webhook_id: str = ""
    # Application Management Token: the HMAC key for ``SC-Signature`` and VERIFY.
    smartcar_management_token: str = ""
    smartcar_mode: SmartcarMode = "live"
    # HS256 key for the signed Connect ``state`` (phase2_plan.md D2).
    smartcar_state_secret: str = ""
    smartcar_api_base_url: str = "https://vehicle.api.smartcar.com/v3"
    smartcar_auth_url: str = "https://iam.smartcar.com/oauth2/token"
    smartcar_connect_url: str = "https://connect.smartcar.com/oauth/authorize"
    smartcar_timeout_seconds: float = Field(default=10.0, gt=0)

    # ---- Mapbox (server side; see docs/maps-stations-notes.md §2) ----
    # Secret token for Directions and Search Box. No URL restriction, never sent
    # to the browser; the map's public token lives in the web app's env.
    mapbox_secret_token: str = ""
    mapbox_api_base_url: str = "https://api.mapbox.com"
    mapbox_timeout_seconds: float = Field(default=10.0, gt=0)

    # ---- Charging stations: AFDC (see docs/maps-stations-notes.md §1) ----
    # NREL's API moved from developer.nrel.gov to developer.nlr.gov (F0).
    afdc_api_key: str = ""
    afdc_api_base_url: str = "https://developer.nlr.gov/api/alt-fuel-stations/v1"
    stations_timeout_seconds: float = Field(default=15.0, gt=0)
    # Douglas-Peucker cap for routes sent to AFDC nearby-route (phase3_plan.md D6).
    stations_route_max_points: int = Field(default=1000, ge=2)

    # ---- Telemetry ----
    # Readings older than this are flagged stale (phase2_plan.md D11).
    telemetry_stale_after_minutes: int = Field(default=15, gt=0)

    # The blocks below are reserved for features built in parallel
    # (docs/plan.md §2.3). Each feature adds settings only inside its own block,
    # so parallel branches never edit the same lines.

    # ---- Stations API + cache (Phase 3 F5) ----

    # ---- Energy model (Phase 4 F2) ----

    # ---- Trip planner (Phase 4 F4) ----

    # ---- Trips (Phase 4 F1, Phase 5 F3/F4) ----

    # ---- Analytics (Phase 5 F5) ----

    # ---- Demo mode (Phase 6 F2) ----

    # ---- Privacy and retention (Phase 6 F5) ----

    # ---- Observability (Phase 6 F1, F4) ----
    sentry_dsn_backend: str = ""

    @property
    def cors_origins(self) -> list[str]:
        """Comma-separated ``ALLOWED_ORIGINS`` parsed into a list."""
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor used by routers via ``Depends(get_settings)``."""

    return Settings()


__all__ = ["Settings", "get_settings"]
