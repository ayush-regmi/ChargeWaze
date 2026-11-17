"""Provider-neutral station types and the ``StationProvider`` protocol.

The stations module (F5) only sees these types. Each provider adapter (AFDC
today) translates its own JSON into ``ProviderStation``. Fields the provider
doesn't give stay ``None``: availability, power, counts and pricing are never
guessed (PRD §7, phase3_plan.md D9).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.core.geo import LatLon

ConnectorType = Literal["NACS", "CCS1", "J1772", "CHAdeMO"]
ChargingLevel = Literal["level2", "dc_fast"]
StationAccess = Literal["public", "private", "unknown"]
StationStatus = Literal["open", "planned", "temporarily_unavailable", "unknown"]

# Stable display order for connector lists.
CONNECTOR_ORDER: tuple[ConnectorType, ...] = ("NACS", "CCS1", "CHAdeMO", "J1772")

# AFDC pages are at most 200 stations (notes §1.3).
MAX_PAGE_SIZE = 200


class StationConnector(BaseModel):
    """One connector type at a station, at its highest known power.

    ``type`` appears once per station. ``count`` is how many connectors of this
    type the provider lists at ``power_kw`` (a shared dispenser can list two
    types on one port). Both are ``None`` when unknown.
    """

    model_config = ConfigDict(frozen=True)

    type: ConnectorType
    count: int | None = None
    power_kw: float | None = None


class ProviderStation(BaseModel):
    """One station, normalized. Maps 1:1 onto ``charging_stations`` (F1)."""

    provider: Literal["afdc"]
    external_id: str
    name: str
    network: str | None = None
    street_address: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    # ISO 3166-1 alpha-2 ('US', 'CA'), or None if the provider gave something else.
    country: str | None = None
    lat: float
    lon: float
    connectors: list[StationConnector] = Field(default_factory=list)
    access: StationAccess = "unknown"
    access_detail: str | None = None
    # AFDC ``restricted_access``: public but limited (e.g. a dealer). Searches drop these.
    restricted_access: bool = False
    status: StationStatus = "unknown"
    pricing_text: str | None = None
    source_updated_at: datetime | None = None
    # Provider's distance from the search point or route, if it reports one.
    # Not used for ordering along a route (notes §1.3): F5 computes that itself.
    provider_distance_km: float | None = None
    raw: dict[str, Any]

    @property
    def connector_types(self) -> set[ConnectorType]:
        return {c.type for c in self.connectors}

    @property
    def max_power_kw(self) -> float | None:
        """Highest *known* connector power, or None if no connector's power is known."""

        known = [c.power_kw for c in self.connectors if c.power_kw is not None]
        return max(known) if known else None


class StationFilters(BaseModel):
    """Search filters every provider understands.

    Power is deliberately absent: ``min_power_kw`` is applied by the stations
    module on ``max_power_kw`` so unknown-power stations stay listed (D10).
    """

    model_config = ConfigDict(frozen=True)

    connector_types: tuple[ConnectorType, ...] = ()
    # Provider network names/codes, passed through as given.
    networks: tuple[str, ...] = ()
    charging_level: ChargingLevel | None = None
    status: Literal["open", "planned", "temporarily_unavailable", "all"] = "open"
    access: Literal["public", "private", "all"] = "public"
    # Restricted stations are left out of results unless asked for.
    include_restricted: bool = False
    limit: int = Field(default=50, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


class StationPage(BaseModel):
    """One page of search results. ``total_results`` counts every match."""

    stations: list[ProviderStation]
    total_results: int
    offset: int
    # Stations the provider returned that couldn't be normalized (logged, not raised).
    skipped: int = 0
    # Restricted stations removed from this page (``total_results`` still counts them).
    excluded_restricted: int = 0


class StationProvider(Protocol):
    """What the stations module needs from a station data provider."""

    def search_nearby(
        self, lat: float, lon: float, radius_km: float, filters: StationFilters
    ) -> StationPage: ...

    def search_along_route(
        self, points: Sequence[LatLon], corridor_km: float, filters: StationFilters
    ) -> StationPage: ...

    def get(self, external_id: str) -> ProviderStation | None: ...


__all__ = [
    "CONNECTOR_ORDER",
    "MAX_PAGE_SIZE",
    "ChargingLevel",
    "ConnectorType",
    "ProviderStation",
    "StationAccess",
    "StationConnector",
    "StationFilters",
    "StationPage",
    "StationProvider",
    "StationStatus",
]
