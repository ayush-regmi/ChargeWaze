"""Constraints and indexes on ``charging_stations`` (database-design.md §3.6)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.stations.models import (
    STATION_ACCESS,
    STATION_STATUSES,
    ChargingStation,
)


@pytest.fixture()
def session(db_connection: Connection) -> Iterator[Session]:
    s = Session(bind=db_connection, join_transaction_mode="create_savepoint", autoflush=False)
    try:
        yield s
    finally:
        s.close()


def _fields(**overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "provider": "afdc",
        "external_id": f"afdc-{uuid.uuid4()}",
        "name": "Test Station",
        "country": "US",
        "lat": Decimal("39.414269"),
        "lon": Decimal("-77.410541"),
        "raw_payload": {"id": 1},
    }
    fields.update(overrides)
    return fields


def _station(session: Session, **overrides: Any) -> ChargingStation:
    station = ChargingStation(**_fields(**overrides))
    session.add(station)
    session.flush()
    return station


def _assert_rejected(session: Session, **overrides: Any) -> None:
    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(ChargingStation(**_fields(**overrides)))
        session.flush()


def test_station_defaults(session: Session) -> None:
    station = _station(session)
    session.refresh(station)

    assert isinstance(station.id, uuid.UUID)
    assert station.connectors == []
    assert station.access == "unknown"
    assert station.status == "unknown"
    assert station.is_simulated is False
    assert station.fetched_at is not None
    assert station.created_at is not None
    # Missing provider values stay empty, never guessed (PRD §7).
    for column in ("operator", "network", "street_address", "pricing_text", "source_updated_at"):
        assert getattr(station, column) is None


def test_provider_external_id_is_unique(session: Session) -> None:
    _station(session, external_id="12345")
    # Same external ID from another provider is a different station.
    _station(session, provider="simulated", external_id="12345", is_simulated=True)

    _assert_rejected(session, external_id="12345")


def test_upsert_keeps_station_id_stable(session: Session) -> None:
    """The stations service (F5) relies on this upsert form for stable IDs (E5)."""

    def upsert(name: str) -> uuid.UUID:
        values = _fields(external_id="afdc-1", name=name)
        stmt = pg_insert(ChargingStation).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["provider", "external_id"],
            set_={"name": stmt.excluded.name, "fetched_at": func.now()},
        ).returning(ChargingStation.id)
        return session.scalars(stmt).one()

    first = upsert("Old name")
    second = upsert("New name")

    assert first == second
    session.expire_all()
    station = session.get(ChargingStation, first)
    assert station is not None
    assert station.name == "New name"
    count = session.scalar(
        select(func.count())
        .select_from(ChargingStation)
        .where(ChargingStation.external_id == "afdc-1")
    )
    assert count == 1


@pytest.mark.parametrize("provider", ["ocm", "nrcan", "tesla"])
def test_provider_must_be_known(session: Session, provider: str) -> None:
    _assert_rejected(session, provider=provider)


def test_access_must_be_known(session: Session) -> None:
    for access in STATION_ACCESS:
        _station(session, access=access)

    _assert_rejected(session, access="restricted")


def test_status_must_be_known(session: Session) -> None:
    for status in STATION_STATUSES:
        _station(session, status=status)

    _assert_rejected(session, status="closed")


@pytest.mark.parametrize("country", ["USA", "us", "C", "C1"])
def test_country_must_be_iso2(session: Session, country: str) -> None:
    _assert_rejected(session, country=country)


def test_country_may_be_unknown(session: Session) -> None:
    _station(session, country=None)


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (Decimal("90.5"), Decimal("0")),
        (Decimal("-91"), Decimal("0")),
        (Decimal("0"), Decimal("181")),
    ],
)
def test_coordinates_must_be_in_range(session: Session, lat: Decimal, lon: Decimal) -> None:
    _assert_rejected(session, lat=lat, lon=lon)


def test_simulated_flag_matches_provider(session: Session) -> None:
    _station(session, provider="simulated", is_simulated=True)

    _assert_rejected(session, provider="simulated", is_simulated=False)
    _assert_rejected(session, provider="afdc", is_simulated=True)


def test_connectors_keep_unknown_count_and_power(session: Session) -> None:
    connectors = [
        {"type": "CCS1", "count": 4, "power_kw": 150},
        {"type": "J1772", "count": None, "power_kw": None},
    ]
    station = _station(session, connectors=connectors)
    session.expire_all()

    reloaded = session.get(ChargingStation, station.id)
    assert reloaded is not None
    assert reloaded.connectors == connectors
