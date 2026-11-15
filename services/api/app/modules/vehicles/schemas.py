"""Request/response models for the vehicles module (``docs/api-contracts.md`` §3.2-3.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

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


ConnectionStatus = Literal["connected", "disconnected", "pending"]


class VehicleSummary(BaseModel):
    """One Garage card (api §3.2)."""

    id: UUID
    make: str
    model: str
    year: int
    connection_status: ConnectionStatus
    supported_signals: list[str]
    # Newest provider timestamp in ``latest_state``; null until the first webhook.
    last_state_recorded_at: datetime | None
    is_stale: bool


class VehicleList(BaseModel):
    items: list[VehicleSummary]


class ChargingCurvePoint(BaseModel):
    soc: float
    power_kw: float


class VehicleSpecsOut(BaseModel):
    # Null when no catalog entry exists and the user hasn't entered one (D8).
    usable_capacity_kwh: float | None
    connectors: list[str]
    charging_curve: list[ChargingCurvePoint]


class VehicleDetail(VehicleSummary):
    """Vehicle detail (api §3.4). Adds timestamps and specs to the summary."""

    connected_at: datetime | None
    disconnected_at: datetime | None
    specs: VehicleSpecsOut | None


__all__ = [
    "ChargingCurvePoint",
    "ConnectCompleteRequest",
    "ConnectCompleteResponse",
    "ConnectFlag",
    "ConnectRequest",
    "ConnectResponse",
    "ConnectionStatus",
    "VehicleDetail",
    "VehicleList",
    "VehicleSpecsOut",
    "VehicleSummary",
]
