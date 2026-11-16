"""Pydantic response schemas for the users module."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PREFERRED_NETWORKS = 20
MAX_NETWORK_NAME_LENGTH = 100
MAX_DETOUR_MINUTES = 240
# user_preferences.reserve_soc / preferred_charge_to are numeric(4, 3).
SOC_DECIMAL_PLACES = 3


class UserPreferencesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    units: Literal["imperial", "metric"] = "imperial"
    reserve_soc: float = Field(ge=0, le=1)
    preferred_charge_to: float = Field(ge=0, le=1)
    max_detour_minutes: int = Field(ge=0)
    preferred_networks: list[str] = Field(default_factory=list)
    primary_vehicle_id: uuid.UUID | None = None


class UserPreferencesUpdate(BaseModel):
    """Body for ``PATCH /api/v1/me/preferences`` (api §3.1a).

    Every key is optional; only keys present in the body change. ``null`` is
    accepted only for ``primary_vehicle_id`` (it clears it). The cross-field
    rule ``reserve_soc < preferred_charge_to`` is checked after merging with
    the stored values, in the service.
    """

    model_config = ConfigDict(extra="forbid")

    units: Literal["imperial", "metric"] | None = None
    reserve_soc: float | None = Field(default=None, ge=0, le=1)
    preferred_charge_to: float | None = Field(default=None, ge=0, le=1)
    max_detour_minutes: int | None = Field(default=None, ge=0, le=MAX_DETOUR_MINUTES)
    preferred_networks: list[str] | None = Field(default=None, max_length=MAX_PREFERRED_NETWORKS)
    primary_vehicle_id: uuid.UUID | None = None

    @field_validator(
        "units", "reserve_soc", "preferred_charge_to", "max_detour_minutes", "preferred_networks"
    )
    @classmethod
    def _not_null(cls, value: object) -> object:
        # Pydantic skips validators for defaults, so this only fires when the
        # client sent an explicit null.
        if value is None:
            raise ValueError("may be omitted but not null.")
        return value

    @field_validator("reserve_soc", "preferred_charge_to")
    @classmethod
    def _storage_precision(cls, value: float | None) -> float | None:
        # Stored as numeric(4, 3): finer values would be rounded by Postgres
        # after the ordering check, e.g. 0.7999 -> 0.800 == preferred_charge_to.
        if value is not None and round(value, SOC_DECIMAL_PLACES) != value:
            raise ValueError(f"use at most {SOC_DECIMAL_PLACES} decimal places.")
        return value

    @field_validator("preferred_networks")
    @classmethod
    def _clean_networks(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = [name.strip() for name in value]
        if any(not name for name in cleaned):
            raise ValueError("network names must not be empty.")
        if any(len(name) > MAX_NETWORK_NAME_LENGTH for name in cleaned):
            raise ValueError(f"network names must be at most {MAX_NETWORK_NAME_LENGTH} chars.")
        if len({name.casefold() for name in cleaned}) != len(cleaned):
            raise ValueError("network names must be distinct.")
        return cleaned


class MeResponse(BaseModel):
    """Response body for ``GET /api/v1/me``.

    Matches ``docs/api-contracts.md`` section 3.1.
    """

    id: uuid.UUID
    display_name: str | None = None
    email: str | None = None
    created_at: datetime
    preferences: UserPreferencesOut


__all__ = ["MeResponse", "UserPreferencesOut", "UserPreferencesUpdate"]
