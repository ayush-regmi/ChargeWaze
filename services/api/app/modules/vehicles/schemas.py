"""Request/response models for the vehicles module (``docs/api-contracts.md`` §3.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.modules.vehicles.redirects import (
    DEFAULT_REDIRECT_AFTER,
    MAX_REDIRECT_AFTER_LENGTH,
    is_safe_redirect_path,
)


class ConnectRequest(BaseModel):
    redirect_after: str = Field(
        default=DEFAULT_REDIRECT_AFTER,
        max_length=MAX_REDIRECT_AFTER_LENGTH,
        description="Same-origin path to return to after Smartcar Connect.",
    )

    @field_validator("redirect_after")
    @classmethod
    def _same_origin_path(cls, value: str) -> str:
        if not is_safe_redirect_path(value):
            raise ValueError("redirect_after must be a same-origin relative path.")
        return value


class ConnectResponse(BaseModel):
    authorization_url: str
    state: str
    expires_at: datetime


ConnectFlag = Literal["success", "already_connected", "denied", "error"]


class ConnectCompleteRequest(BaseModel):
    """The query parameters Smartcar put on the redirect, forwarded by the web app."""

    state: str = Field(min_length=1, max_length=2048)
    user_id: str | None = Field(default=None, max_length=256)
    external_id: str | None = Field(default=None, max_length=256)
    error: str | None = Field(default=None, max_length=256)


class ConnectCompleteResponse(BaseModel):
    result: ConnectFlag
    # Validated same-origin path to send the browser to (``/garage`` when the
    # state couldn't be trusted).
    redirect_after: str


__all__ = [
    "ConnectCompleteRequest",
    "ConnectCompleteResponse",
    "ConnectFlag",
    "ConnectRequest",
    "ConnectResponse",
]
