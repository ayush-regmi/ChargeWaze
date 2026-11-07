"""Application settings loaded from environment variables.

Follows the rules in docs/engineering-guidelines.md: secrets stay backend-only,
and nothing in ``NEXT_PUBLIC_*`` is referenced here.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "preview", "production", "test"]


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

    # ---- Observability ----
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
