"""Pydantic models for Smartcar V3 payloads, plus signal normalization.

Shapes follow docs/smartcar-v3-notes.md and the fixtures in
``fixtures/smartcar/``. Models ignore unknown fields so additive Smartcar
changes don't break parsing.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    model_validator,
)
from pydantic.alias_generators import to_camel

logger = logging.getLogger(__name__)


class _SmartcarModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


class TokenResponse(_SmartcarModel):
    access_token: str
    # Smartcar returns ``bearer`` in lower case; compare case-insensitively.
    token_type: str
    expires_in: int


# ---------------------------------------------------------------------------
# Connections and vehicles
# ---------------------------------------------------------------------------


class VehicleAttributes(_SmartcarModel):
    make: str | None = None
    model: str | None = None
    year: int | None = None
    powertrain_type: str | None = None
    mode: str | None = None


class _ConnectionStatus(_SmartcarModel):
    name: str


class _ConnectionUser(_SmartcarModel):
    id: str
    external_id: str | None = None


class _ConnectionAttributes(_SmartcarModel):
    permissions: list[str] = Field(default_factory=list)
    status: _ConnectionStatus
    user: _ConnectionUser
    vehicle: VehicleAttributes = Field(default_factory=VehicleAttributes)


class _ResourceRef(_SmartcarModel):
    id: str
    type: str | None = None


class _Relationship(_SmartcarModel):
    data: _ResourceRef


class _ConnectionRelationships(_SmartcarModel):
    vehicle: _Relationship


class Connection(_SmartcarModel):
    """One Smartcar user ↔ vehicle authorization (``type: connection``)."""

    id: str
    attributes: _ConnectionAttributes
    relationships: _ConnectionRelationships

    @property
    def connection_id(self) -> str:
        return self.id

    @property
    def user_id(self) -> str:
        return self.attributes.user.id

    @property
    def vehicle_id(self) -> str:
        return self.relationships.vehicle.data.id

    @property
    def status(self) -> str:
        return self.attributes.status.name

    @property
    def is_active(self) -> bool:
        return self.status.upper() == "ACTIVE"


class PageMeta(_SmartcarModel):
    page_number: int = 1
    page_size: int | None = None
    total_count: int = 0


class ConnectionsPage(_SmartcarModel):
    data: list[Connection]
    meta: PageMeta = Field(default_factory=PageMeta)


class Vehicle(_SmartcarModel):
    id: str
    attributes: VehicleAttributes = Field(default_factory=VehicleAttributes)


class VehicleResponse(_SmartcarModel):
    data: Vehicle


# ---------------------------------------------------------------------------
# Signals (REST) and webhooks
# ---------------------------------------------------------------------------


class SignalError(_SmartcarModel):
    code: str | None = None
    type: str | None = None
    title: str | None = None


class SignalStatus(_SmartcarModel):
    value: str
    error: SignalError | None = None


class SignalMeta(_SmartcarModel):
    # ISO-8601 strings in REST responses, epoch milliseconds in webhooks.
    oem_updated_at: datetime | int | float | str | None = None
    retrieved_at: datetime | int | float | str | None = None


class _KeepsRawPayload(_SmartcarModel):
    """Keeps an untouched copy of the input dict.

    The typed fields drop anything we don't model (e.g. ``meta.ingestedAt``,
    an error's ``detail``/``resolution``); ``raw_payload`` is what Smartcar
    actually sent, for ``vehicle_telemetry.raw_payload``.
    """

    _raw_payload: dict[str, Any] = PrivateAttr(default_factory=dict)

    @model_validator(mode="wrap")
    @classmethod
    def _keep_raw_payload(cls, data: Any, handler: ModelWrapValidatorHandler[Any]) -> Any:
        model = handler(data)
        if isinstance(data, dict):
            model._raw_payload = copy.deepcopy(data)
        return model

    @property
    def raw_payload(self) -> dict[str, Any]:
        if self._raw_payload:
            return copy.deepcopy(self._raw_payload)
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class _RestSignalAttributes(_SmartcarModel):
    # Optional so a redacted placeholder (no code/status) parses and is skipped.
    code: str | None = None
    name: str | None = None
    group: str | None = None
    status: SignalStatus | None = None
    body: dict[str, Any] | None = None


class RestSignal(_KeepsRawPayload):
    id: str
    attributes: _RestSignalAttributes
    meta: SignalMeta = Field(default_factory=SignalMeta)


class SignalsResponse(_SmartcarModel):
    data: list[RestSignal]
    included: dict[str, Vehicle] = Field(default_factory=dict)
    meta: PageMeta = Field(default_factory=PageMeta)

    @property
    def vehicle(self) -> Vehicle | None:
        return self.included.get("vehicle")


class WebhookSignal(_KeepsRawPayload):
    code: str
    name: str | None = None
    group: str | None = None
    # Not confirmed whether webhook signals carry a status; treat a missing one
    # as SUCCESS when a body is present (see _raw_from_webhook).
    status: SignalStatus | None = None
    body: dict[str, Any] | None = None
    meta: SignalMeta = Field(default_factory=SignalMeta)


class _IdOnly(_SmartcarModel):
    id: str


class VehicleStateData(_SmartcarModel):
    user: _IdOnly
    vehicle: _IdOnly
    signals: list[WebhookSignal] = Field(default_factory=list)
    triggers: list[dict[str, Any]] = Field(default_factory=list)


class WebhookMeta(_SmartcarModel):
    delivery_id: str | None = None
    delivered_at: datetime | int | float | str | None = None
    webhook_id: str | None = None
    version: str | None = None
    mode: str | None = None
    sequence: int | None = None


class WebhookEvent(_SmartcarModel):
    """Envelope common to ``VERIFY``, ``VEHICLE_STATE`` and ``VEHICLE_ERROR``."""

    event_id: str | None = None
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    meta: WebhookMeta = Field(default_factory=WebhookMeta)

    @property
    def challenge(self) -> str | None:
        value = self.data.get("challenge")
        return value if isinstance(value, str) else None

    def vehicle_state(self) -> VehicleStateData:
        """Parse ``data`` as a ``VEHICLE_STATE`` body. Raises ``ValidationError``."""

        return VehicleStateData.model_validate(self.data)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

SignalValueKind = Literal["number", "bool", "text"]


@dataclass(frozen=True)
class _SignalSpec:
    code: str
    kind: SignalValueKind
    value_key: str = "value"
    # Fixed unit, or None to take ``body.unit`` as reported.
    unit: str | None = None


# Smartcar signal code → ChargeWaze signal code. docs/smartcar-v3-notes.md §4.
SIGNAL_MAP: dict[str, _SignalSpec] = {
    "tractionbattery-stateofcharge": _SignalSpec("soc", "number", unit="percent"),
    "tractionbattery-range": _SignalSpec("range", "number"),
    "odometer-traveleddistance": _SignalSpec("odometer", "number"),
    "charge-ischarging": _SignalSpec("charging_status", "bool", unit="bool"),
    "charge-detailedchargingstatus": _SignalSpec("charging_status_detail", "text"),
    # Watts as reported; the API layer converts to kW.
    "charge-wattage": _SignalSpec("charging_power", "number"),
    "charge-chargelimits": _SignalSpec(
        "charge_limit", "number", value_key="activeLimit", unit="percent"
    ),
    "charge-ischargingcableconnected": _SignalSpec("cable_connected", "bool", unit="bool"),
}

# Error codes meaning the vehicle can't (or we may not) provide the signal at all.
# Others (e.g. NOT_CHARGING) mean "supported, but empty right now".
UNSUPPORTED_ERROR_CODES = frozenset({"VEHICLE_NOT_CAPABLE", "UNKNOWN"})

SignalOutcome = Literal["ok", "error"]


@dataclass(frozen=True)
class NormalizedSignal:
    """One Smartcar signal mapped onto ChargeWaze's vocabulary.

    ``code`` is ``None`` for signals ChargeWaze doesn't map; they keep their
    ``smartcar_code`` and ``raw`` so nothing is lost. ``value`` is ``None``
    whenever the signal errored or has no numeric value (text signals keep
    theirs in ``text_value``). Booleans are 1/0 to match
    ``vehicle_telemetry.value``.
    """

    smartcar_code: str
    code: str | None
    outcome: SignalOutcome
    value: Decimal | None
    text_value: str | None
    unit: str | None
    recorded_at: datetime | None
    retrieved_at: datetime | None
    error_code: str | None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_known(self) -> bool:
        return self.code is not None


@dataclass(frozen=True)
class _RawSignal:
    code: str
    status: str
    error_code: str | None
    body: dict[str, Any] | None
    oem_updated_at: datetime | None
    retrieved_at: datetime | None
    raw: dict[str, Any]


def parse_timestamp(value: datetime | int | float | str | None) -> datetime | None:
    """Accept ISO-8601 strings, epoch milliseconds or datetimes; return aware UTC."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    text = value.strip()
    if text.isdigit():
        return datetime.fromtimestamp(int(text) / 1000, tz=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _raw_from_rest(signal: RestSignal) -> _RawSignal | None:
    attrs = signal.attributes
    if attrs.code is None or attrs.status is None:
        return None
    return _RawSignal(
        code=attrs.code,
        status=attrs.status.value.upper(),
        error_code=attrs.status.error.code if attrs.status.error else None,
        body=attrs.body,
        oem_updated_at=parse_timestamp(signal.meta.oem_updated_at),
        retrieved_at=parse_timestamp(signal.meta.retrieved_at),
        raw=signal.raw_payload,
    )


def _raw_from_webhook(signal: WebhookSignal) -> _RawSignal:
    if signal.status is not None:
        status = signal.status.value.upper()
        error_code = signal.status.error.code if signal.status.error else None
    else:
        status = "SUCCESS" if signal.body is not None else "ERROR"
        error_code = None
    return _RawSignal(
        code=signal.code,
        status=status,
        error_code=error_code,
        body=signal.body,
        oem_updated_at=parse_timestamp(signal.meta.oem_updated_at),
        retrieved_at=parse_timestamp(signal.meta.retrieved_at),
        raw=signal.raw_payload,
    )


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, int | float):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    return None


def _normalize_one(raw: _RawSignal) -> NormalizedSignal:
    spec = SIGNAL_MAP.get(raw.code)
    ok = raw.status == "SUCCESS" and raw.body is not None
    value: Decimal | None = None
    text_value: str | None = None
    unit: str | None = None

    if ok and raw.body is not None:
        reported_unit = raw.body.get("unit")
        unit = reported_unit if isinstance(reported_unit, str) else None
        if spec is not None:
            body_value = raw.body.get(spec.value_key)
            if spec.kind == "text":
                text_value = body_value if isinstance(body_value, str) else None
            elif spec.kind == "bool":
                value = _to_decimal(body_value) if isinstance(body_value, bool) else None
            else:
                value = None if isinstance(body_value, bool) else _to_decimal(body_value)
            unit = spec.unit or unit
            if value is None and text_value is None:
                # SUCCESS with an unusable body: treat as missing rather than guess.
                ok = False

    return NormalizedSignal(
        smartcar_code=raw.code,
        code=spec.code if spec else None,
        outcome="ok" if ok else "error",
        value=value,
        text_value=text_value,
        unit=unit if ok else None,
        recorded_at=raw.oem_updated_at or raw.retrieved_at,
        retrieved_at=raw.retrieved_at,
        error_code=raw.error_code,
        raw=raw.raw,
    )


def normalize_signals(
    signals: SignalsResponse | VehicleStateData | list[RestSignal] | list[WebhookSignal],
) -> list[NormalizedSignal]:
    """Normalize a REST signals response or a webhook ``VEHICLE_STATE`` body.

    Signals that errored come back with ``outcome="error"`` and no value —
    never a zero or ``False`` (PRD §7). Entries without a code (e.g. redacted
    placeholders) are skipped.
    """

    items: list[RestSignal] | list[WebhookSignal]
    if isinstance(signals, SignalsResponse):
        items = signals.data
    elif isinstance(signals, VehicleStateData):
        items = signals.signals
    else:
        items = signals

    out: list[NormalizedSignal] = []
    for item in items:
        raw = _raw_from_rest(item) if isinstance(item, RestSignal) else _raw_from_webhook(item)
        if raw is None:
            logger.debug("smartcar_signal_skipped", extra={"signal_id": _item_id(item)})
            continue
        out.append(_normalize_one(raw))
    return out


def _item_id(item: RestSignal | WebhookSignal) -> str:
    return item.id if isinstance(item, RestSignal) else item.code


def supported_signal_codes(signals: list[NormalizedSignal]) -> list[str]:
    """ChargeWaze codes the vehicle supports, for ``vehicles.supported_signals``.

    A signal counts as supported if it returned a value, or errored with a code
    that means "empty right now" (e.g. ``NOT_CHARGING``). ``VEHICLE_NOT_CAPABLE``
    and ``UNKNOWN`` (which Smartcar also uses for missing permissions) don't
    count. An empty first read (common right after Connect) yields ``[]``;
    recompute when data arrives.
    """

    codes: set[str] = set()
    for signal in signals:
        if signal.code is None:
            continue
        if signal.outcome == "ok" or (
            signal.error_code is not None and signal.error_code not in UNSUPPORTED_ERROR_CODES
        ):
            codes.add(signal.code)
    return sorted(codes)


__all__ = [
    "SIGNAL_MAP",
    "UNSUPPORTED_ERROR_CODES",
    "Connection",
    "ConnectionsPage",
    "NormalizedSignal",
    "RestSignal",
    "SignalsResponse",
    "TokenResponse",
    "Vehicle",
    "VehicleAttributes",
    "VehicleResponse",
    "VehicleStateData",
    "WebhookEvent",
    "WebhookMeta",
    "WebhookSignal",
    "normalize_signals",
    "parse_timestamp",
    "supported_signal_codes",
]
