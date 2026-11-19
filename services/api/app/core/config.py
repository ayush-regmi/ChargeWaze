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
    # GET /stations/{id} refreshes a cached row older than this (phase3_plan.md D7).
    stations_cache_ttl_hours: float = Field(default=24.0, gt=0)
    # HMAC key for GET /stations cursors. Empty disables paging (next_cursor null).
    stations_cursor_secret: str = ""

    # ---- Energy model (Phase 4 F2) ----

    # ---- Trip planner (Phase 4 F4) ----
    # phase4_plan.md D11: minutes added per stop (parking, plugging in, ramp-up).
    planner_stop_overhead_minutes: float = Field(default=5.0, ge=0, le=60)
    # D4: detour = 2 * off-route km * road factor, driven at the detour speed.
    planner_detour_road_factor: float = Field(default=1.3, ge=1, le=3)
    planner_detour_speed_kmh: float = Field(default=50.0, gt=0, le=130)
    # D16: departure SoC grid (1 %).
    planner_soc_grid_step: float = Field(default=0.01, gt=0, le=0.1)
    # Upper bound of the along-route station corridor (phase3_plan.md D6 range).
    planner_max_corridor_km: float = Field(default=16.0, ge=0.5, le=16)

    # ---- Trips (Phase 4 F1, Phase 5 F3/F4) ----
    # Observed-trip inference (phase5_plan.md §3 F2 table, D1, D8, D14).
    trips_stop_split_minutes: float = Field(default=20.0, gt=0, le=24 * 60)
    trips_min_move_km: float = Field(default=0.1, ge=0, le=10)
    trips_min_leg_km: float = Field(default=0.5, ge=0, le=100)
    trips_high_min_soc_delta: float = Field(default=10.0, ge=0, le=100)
    trips_medium_min_soc_delta: float = Field(default=4.0, ge=0, le=100)
    trips_high_max_soc_age_minutes: float = Field(default=15.0, ge=0)
    trips_medium_max_soc_age_minutes: float = Field(default=360.0, ge=0)
    trips_soc_resolution_points: float = Field(default=1.0, gt=0, le=10)
    trips_soc_increase_tolerance_points: float = Field(default=1.0, ge=0, le=10)

    # ---- Analytics (Phase 5 F5) ----

    # ---- Demo mode (Phase 6 F2) ----

    # ---- Privacy and retention (Phase 6 F5) ----

    # ---- Observability (Phase 6 F1, F4) ----
    # Empty disables Sentry. It is also always off under APP_ENV=test / pytest.
    sentry_dsn_backend: str = ""
    # Sentry "environment" tag; empty uses APP_ENV.
    sentry_environment: str = ""
    # Share of requests traced for performance. 0 sends errors only.
    sentry_traces_sample_rate: float = Field(default=0.0, ge=0, le=1)

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
