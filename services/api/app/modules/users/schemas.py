"""Pydantic response schemas for the users module."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserPreferencesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    units: Literal["imperial", "metric"] = "imperial"
    reserve_soc: float = Field(ge=0, le=1)
    preferred_charge_to: float = Field(ge=0, le=1)
    max_detour_minutes: int = Field(ge=0)
    preferred_networks: list[str] = Field(default_factory=list)
    primary_vehicle_id: uuid.UUID | None = None


class MeResponse(BaseModel):
    """Response body for ``GET /api/v1/me``.

    Matches ``docs/api-contracts.md`` section 3.1.
    """

    id: uuid.UUID
    display_name: str | None = None
    email: str | None = None
    created_at: datetime
    preferences: UserPreferencesOut


__all__ = ["MeResponse", "UserPreferencesOut"]
