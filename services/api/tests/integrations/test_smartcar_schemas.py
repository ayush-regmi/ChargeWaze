from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.integrations.smartcar.schemas import (
    ConnectionsPage,
    NormalizedSignal,
    SignalsResponse,
    WebhookEvent,
    normalize_signals,
    parse_timestamp,
    supported_signal_codes,
)
from tests.integrations.conftest import fixture_bytes, fixture_json


def _by_code(signals: list[NormalizedSignal]) -> dict[str, NormalizedSignal]:
    return {s.smartcar_code: s for s in signals}


# ---- connections ----------------------------------------------------------


def test_connections_fixture_parses() -> None:
    page = ConnectionsPage.model_validate(fixture_json("connections_list.json"))
    assert page.meta.total_count == 1
    [conn] = page.data
    assert conn.connection_id == "redacted-id-2"
    assert conn.user_id == "redacted-id-1"
    assert conn.vehicle_id == "redacted-id-3"
    assert conn.is_active
    assert conn.attributes.user.external_id == "chargewise-spike"
    assert conn.attributes.permissions == [
        "read_battery",
        "read_charge",
        "read_odometer",
        "read_vehicle_info",
    ]
    vehicle = conn.attributes.vehicle
    assert (vehicle.make, vehicle.model, vehicle.year) == ("TOYOTA", "bZ XLE AWD", 2026)
    assert vehicle.powertrain_type == "BEV"


# ---- REST signals (real bZ read) ------------------------------------------


@pytest.fixture()
def bz_signals() -> list[NormalizedSignal]:
    return normalize_signals(SignalsResponse.model_validate(fixture_json("signals_bz.json")))


def test_bz_signals_response_carries_vehicle() -> None:
    response = SignalsResponse.model_validate(fixture_json("signals_bz.json"))
    assert response.vehicle is not None
    assert response.vehicle.attributes.make == "TOYOTA"


def test_redacted_placeholder_is_skipped(bz_signals: list[NormalizedSignal]) -> None:
    # 27 entries in the fixture; the location placeholder has no code/status.
    assert len(bz_signals) == 26
    assert "location-preciselocation" not in _by_code(bz_signals)


def test_bz_known_signals_map_to_codes_and_units(bz_signals: list[NormalizedSignal]) -> None:
    by_code = _by_code(bz_signals)

    soc = by_code["tractionbattery-stateofcharge"]
    assert (soc.code, soc.outcome, soc.value, soc.unit) == ("soc", "ok", Decimal("61"), "percent")
    assert soc.recorded_at == datetime(2026, 11, 13, 21, 53, 26, tzinfo=UTC)

    rng = by_code["tractionbattery-range"]
    assert (rng.code, rng.value, rng.unit) == ("range", Decimal("230.14"), "km")

    odo = by_code["odometer-traveleddistance"]
    assert (odo.code, odo.value, odo.unit) == ("odometer", Decimal("24650.32"), "km")

    charging = by_code["charge-ischarging"]
    assert (charging.code, charging.value, charging.unit) == ("charging_status", Decimal(0), "bool")

    cable = by_code["charge-ischargingcableconnected"]
    assert (cable.code, cable.value) == ("cable_connected", Decimal(0))

    detail = by_code["charge-detailedchargingstatus"]
    assert detail.code == "charging_status_detail"
    assert detail.value is None
    assert detail.text_value == "NOT_CHARGING"


def test_recorded_at_prefers_oem_timestamp(bz_signals: list[NormalizedSignal]) -> None:
    soc = _by_code(bz_signals)["tractionbattery-stateofcharge"]
    assert soc.retrieved_at is not None and soc.recorded_at is not None
    assert soc.recorded_at < soc.retrieved_at


def test_error_signals_have_no_value(bz_signals: list[NormalizedSignal]) -> None:
    errored = [s for s in bz_signals if s.outcome == "error"]
    assert len(errored) == 19
    for signal in errored:
        assert signal.value is None
        assert signal.text_value is None
        assert signal.unit is None
        assert signal.error_code is not None


def test_unmapped_signals_keep_raw_payload(bz_signals: list[NormalizedSignal]) -> None:
    trunk = _by_code(bz_signals)["closure-reartrunk"]
    assert trunk.code is None
    assert trunk.raw["attributes"]["status"]["error"]["code"] == "VEHICLE_NOT_CAPABLE"


def test_supported_signals_for_bz(bz_signals: list[NormalizedSignal]) -> None:
    assert supported_signal_codes(bz_signals) == [
        "cable_connected",
        "charging_status",
        "charging_status_detail",
        "odometer",
        "range",
        "soc",
    ]


def test_empty_first_read_has_no_supported_signals() -> None:
    empty = SignalsResponse.model_validate({"data": [], "meta": {"totalCount": 0}})
    assert supported_signal_codes(normalize_signals(empty)) == []


def _rest_signal(code: str, status: dict, body: dict | None = None) -> dict:
    attrs: dict = {"code": code, "status": status}
    if body is not None:
        attrs["body"] = body
    return {"id": code, "type": "signal", "attributes": attrs, "meta": {}}


def test_supported_signals_error_code_rules() -> None:
    response = SignalsResponse.model_validate(
        {
            "data": [
                # "Empty right now" errors still count as supported.
                _rest_signal(
                    "charge-wattage", {"value": "ERROR", "error": {"code": "NOT_CHARGING"}}
                ),
                _rest_signal(
                    "charge-chargelimits",
                    {"value": "ERROR", "error": {"code": "VEHICLE_NOT_CAPABLE"}},
                ),
                _rest_signal("odometer-traveleddistance", {"value": "UNAVAILABLE"}),
                _rest_signal(
                    "tractionbattery-range", {"value": "ERROR", "error": {"code": "UNKNOWN"}}
                ),
            ]
        }
    )
    signals = normalize_signals(response)
    assert all(s.outcome == "error" and s.value is None for s in signals)
    assert supported_signal_codes(signals) == ["charging_power"]


def test_charge_limit_reads_active_limit_and_wattage_stays_watts() -> None:
    response = SignalsResponse.model_validate(
        {
            "data": [
                _rest_signal(
                    "charge-chargelimits",
                    {"value": "SUCCESS"},
                    {"activeLimit": 80, "values": []},
                ),
                _rest_signal(
                    "charge-wattage", {"value": "SUCCESS"}, {"unit": "watts", "value": 7200}
                ),
            ]
        }
    )
    by_code = _by_code(normalize_signals(response))
    assert (by_code["charge-chargelimits"].value, by_code["charge-chargelimits"].unit) == (
        Decimal(80),
        "percent",
    )
    assert (by_code["charge-wattage"].value, by_code["charge-wattage"].unit) == (
        Decimal(7200),
        "watts",
    )


def test_success_with_wrong_value_type_is_treated_as_missing() -> None:
    response = SignalsResponse.model_validate(
        {
            "data": [
                _rest_signal(
                    "tractionbattery-stateofcharge", {"value": "SUCCESS"}, {"value": "61"}
                ),
                _rest_signal("charge-ischarging", {"value": "SUCCESS"}, {"value": 1}),
            ]
        }
    )
    for signal in normalize_signals(response):
        assert signal.outcome == "error"
        assert signal.value is None


# ---- webhooks --------------------------------------------------------------


def test_vehicle_state_webhook_normalizes() -> None:
    event = WebhookEvent.model_validate_json(fixture_bytes("webhook_vehicle_state.json"))
    assert event.event_type == "VEHICLE_STATE"
    assert event.event_id == "evt-0002"
    assert event.meta.sequence == 2
    state = event.vehicle_state()
    assert (state.user.id, state.vehicle.id) == ("redacted-id-1", "redacted-id-3")

    by_code = _by_code(normalize_signals(state))
    soc = by_code["tractionbattery-stateofcharge"]
    assert (soc.code, soc.outcome, soc.value) == ("soc", "ok", Decimal(64))
    # Webhook timestamps are epoch milliseconds.
    assert soc.recorded_at == datetime(2026, 11, 9, 22, 1, tzinfo=UTC)
    assert by_code["charge-ischarging"].value == Decimal(1)


def test_out_of_order_fixture_is_older_but_delivered_later() -> None:
    newer = WebhookEvent.model_validate_json(fixture_bytes("webhook_vehicle_state.json"))
    older = WebhookEvent.model_validate_json(fixture_bytes("webhook_out_of_order_older.json"))
    newer_soc = _by_code(normalize_signals(newer.vehicle_state()))["tractionbattery-stateofcharge"]
    older_soc = _by_code(normalize_signals(older.vehicle_state()))["tractionbattery-stateofcharge"]
    assert newer_soc.recorded_at and older_soc.recorded_at
    assert older_soc.recorded_at < newer_soc.recorded_at
    assert parse_timestamp(older.meta.delivered_at) > parse_timestamp(newer.meta.delivered_at)  # type: ignore[operator]


def test_unknown_webhook_signal_is_preserved() -> None:
    event = WebhookEvent.model_validate_json(fixture_bytes("webhook_unknown_signal.json"))
    by_code = _by_code(normalize_signals(event.vehicle_state()))
    unknown = by_code["future-newsignal"]
    assert unknown.code is None
    assert not unknown.is_known
    assert unknown.value is None
    assert unknown.raw["body"] == {"value": "SOMETHING"}
    assert by_code["tractionbattery-stateofcharge"].code == "soc"


def test_webhook_signal_without_body_is_error() -> None:
    event = WebhookEvent.model_validate(
        {
            "eventId": "e",
            "eventType": "VEHICLE_STATE",
            "data": {
                "user": {"id": "u"},
                "vehicle": {"id": "v"},
                "signals": [{"code": "tractionbattery-stateofcharge", "meta": {}}],
            },
        }
    )
    [signal] = normalize_signals(event.vehicle_state())
    assert signal.outcome == "error"
    assert signal.value is None
    assert signal.recorded_at is None


def test_verify_event_has_no_vehicle_state() -> None:
    event = WebhookEvent.model_validate_json(fixture_bytes("webhook_verify_challenge.json"))
    assert event.challenge == "3f1c0a6e9b2d4c7f8e5a1b0d2c3e4f5a"
    with pytest.raises(ValidationError):
        event.vehicle_state()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-11-13T21:53:26.000Z", datetime(2026, 11, 13, 21, 53, 26, tzinfo=UTC)),
        (1794261600000, datetime(2026, 11, 9, 22, 0, tzinfo=UTC)),
        ("1794261600000", datetime(2026, 11, 9, 22, 0, tzinfo=UTC)),
        ("2026-11-13T21:53:26", datetime(2026, 11, 13, 21, 53, 26, tzinfo=UTC)),
        (None, None),
        ("", None),
        ("not a date", None),
        (True, None),
    ],
)
def test_parse_timestamp(value: object, expected: datetime | None) -> None:
    assert parse_timestamp(value) == expected  # type: ignore[arg-type]
