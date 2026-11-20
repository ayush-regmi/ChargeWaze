"""Request/response models for the vehicles module (``docs/api-contracts.md`` §3.2-3.5)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


ConnectFlag = Literal["success", "already_connected", "owned_elsewhere", "denied", "error"]


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


BaselineSource = Literal["vehicle", "catalog"]


class VehicleSpecsOut(BaseModel):
    # Null when no catalog entry exists and the user hasn't entered one (D8).
    usable_capacity_kwh: float | None
    connectors: list[str]
    charging_curve: list[ChargingCurvePoint]
    # kWh/km (phase4_plan.md F8). The user's own value, or null.
    baseline_kwh_per_km: float | None
    # Read-only catalog value for this make/model/year (highway estimate), or null.
    catalog_baseline_kwh_per_km: float | None
    # Where the planner's value comes from; null means the vehicle can't be planned.
    baseline_source: BaselineSource | None


Connector = Literal["NACS", "CCS1", "J1772", "CHAdeMO"]

MAX_USABLE_CAPACITY_KWH = Decimal("300")
MAX_CHARGING_POWER_KW = 1000.0
MAX_CHARGING_CURVE_POINTS = 20
MAX_BASELINE_KWH_PER_KM = Decimal("1")


class ChargingCurvePointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    soc: float = Field(ge=0, le=1)
    power_kw: float = Field(gt=0, le=MAX_CHARGING_POWER_KW)


class VehicleSpecsUpdate(BaseModel):
    """Body for ``PATCH /api/v1/vehicles/{id}/specs`` (api §3.4a).

    Only keys present in the body change. ``usable_capacity_kwh: null`` clears
    the capacity and ``baseline_kwh_per_km: null`` clears the baseline (back
    to the catalog value); ``connectors`` and ``charging_curve`` can't be null (send
    ``[]`` to clear them).
    """

    model_config = ConfigDict(extra="forbid")

    usable_capacity_kwh: Decimal | None = Field(
        default=None, gt=0, le=MAX_USABLE_CAPACITY_KWH, decimal_places=2
    )
    connectors: list[Connector] | None = Field(default=None, max_length=4)
    charging_curve: list[ChargingCurvePointIn] | None = Field(
        default=None, max_length=MAX_CHARGING_CURVE_POINTS
    )
    baseline_kwh_per_km: Decimal | None = Field(
        default=None, gt=0, le=MAX_BASELINE_KWH_PER_KM, decimal_places=3
    )

    @field_validator("connectors", "charging_curve")
    @classmethod
    def _not_null(cls, value: object) -> object:
        # Validators don't run for defaults, so this only fires on explicit null.
        if value is None:
            raise ValueError("may be omitted but not null; send [] to clear.")
        return value

    @field_validator("connectors")
    @classmethod
    def _distinct_connectors(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("connectors must be distinct.")
        return value

    @field_validator("charging_curve")
    @classmethod
    def _increasing_soc(
        cls, value: list[ChargingCurvePointIn] | None
    ) -> list[ChargingCurvePointIn] | None:
        if value is not None and any(b.soc <= a.soc for a, b in pairwise(value)):
            raise ValueError("charging_curve soc values must be strictly increasing.")
        return value


class VehicleDetail(VehicleSummary):
    """Vehicle detail (api §3.4). Adds timestamps and specs to the summary."""

    connected_at: datetime | None
    disconnected_at: datetime | None
    specs: VehicleSpecsOut | None


class VehicleStateSignals(BaseModel):
    """Normalized signals (api §3.5). ``null`` means the vehicle hasn't reported it
    (or reported it in a unit we don't recognise); values are never guessed."""

    soc_percent: float | None = None
    range_km: float | None = None
    odometer_km: float | None = None
    is_charging: bool | None = None
    charging_power_kw: float | None = None


class VehicleState(BaseModel):
    """Latest cached state (api §3.5). Served from ``latest_state``, never live."""

    vehicle_id: UUID
    # Newest provider timestamp across signals; null before the first webhook.
    recorded_at: datetime | None
    received_at: datetime | None
    source: Literal["smartcar", "simulated"] | None
    is_stale: bool
    signals: VehicleStateSignals
    # Provider timestamp per non-null field in ``signals`` (D5).
    signal_recorded_at: dict[str, datetime]


class VehicleRefreshResponse(BaseModel):
    """Result of an on-demand Smartcar read (api §3.5a, ADR 0010)."""

    refreshed_at: datetime
    # vehicle_telemetry rows added; 0 when Smartcar had nothing newer than what
    # is already stored (``state`` may then still be stale).
    inserted: int
    # Earliest time the per-vehicle rate limit allows another refresh.
    next_refresh_at: datetime
    state: VehicleState


__all__ = [
    "BaselineSource",
    "ChargingCurvePoint",
    "ChargingCurvePointIn",
    "ConnectCompleteRequest",
    "ConnectCompleteResponse",
    "ConnectFlag",
    "ConnectRequest",
    "ConnectResponse",
    "ConnectionStatus",
    "Connector",
    "VehicleDetail",
    "VehicleList",
    "VehicleRefreshResponse",
    "VehicleSpecsOut",
    "VehicleSpecsUpdate",
    "VehicleState",
    "VehicleStateSignals",
    "VehicleSummary",
]
