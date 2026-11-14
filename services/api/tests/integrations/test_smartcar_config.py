from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("mode", ["live", "simulated"])
def test_valid_smartcar_modes(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTCAR_MODE", mode)
    assert Settings(_env_file=None).smartcar_mode == mode  # type: ignore[call-arg]


@pytest.mark.parametrize("mode", ["test", "LIVE", ""])
def test_invalid_smartcar_mode_is_rejected(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # "test" was the V2 value; under V3 it completes Connect but authorizes no vehicle.
    monkeypatch.setenv("SMARTCAR_MODE", mode)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_smartcar_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SMARTCAR_MODE", "SMARTCAR_API_BASE_URL", "TELEMETRY_STALE_AFTER_MINUTES"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.smartcar_mode == "live"
    assert settings.smartcar_api_base_url == "https://vehicle.api.smartcar.com/v3"
    assert settings.telemetry_stale_after_minutes == 15


def test_stale_threshold_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEMETRY_STALE_AFTER_MINUTES", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
