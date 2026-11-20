"""``GET /api/v1/me/privacy``, ``POST /api/v1/me/consents`` and consent_records (P6-F5)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.users.models import Profile
from app.modules.users.privacy_models import ConsentRecord
from tests.conftest import TEST_USER_ID
from tests.privacy.conftest import OTHER_USER_ID

CONSENTS = "/api/v1/me/consents"
PRIVACY = "/api/v1/me/privacy"
CURRENT = "2026-11-19"


def _grant(api: TestClient, headers: dict[str, str], *, granted: bool = True) -> dict[str, object]:
    response = api.post(
        CONSENTS,
        headers=headers,
        json={"kind": "privacy_notice", "version": CURRENT, "granted": granted},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_unanswered_consent_reads_as_null_and_writes_nothing(
    api: TestClient, db: Session, auth: Callable[..., dict[str, str]]
) -> None:
    response = api.get(PRIVACY, headers=auth())
    assert response.status_code == 200
    assert response.json() == {
        "consents": [
            {
                "kind": "privacy_notice",
                "current_version": CURRENT,
                "granted": None,
                "version": None,
                "recorded_at": None,
                "is_current": False,
            }
        ],
        "retention": {"telemetry_days": 730, "unsaved_plan_days": 30, "refresh_log_days": 30},
    }
    assert db.scalar(select(func.count()).select_from(Profile)) == 0


def test_granting_records_an_audit_row_and_reads_back_current(
    api: TestClient, db: Session, auth: Callable[..., dict[str, str]]
) -> None:
    body = _grant(api, auth())
    assert body["granted"] is True
    assert body["version"] == CURRENT
    assert body["is_current"] is True
    assert body["recorded_at"] is not None

    rows = db.scalars(select(ConsentRecord)).all()
    assert [(r.user_id, r.kind, r.version, r.granted, r.source) for r in rows] == [
        (TEST_USER_ID, "privacy_notice", CURRENT, True, "web")
    ]
    assert api.get(PRIVACY, headers=auth()).json()["consents"][0]["is_current"] is True


def test_withdrawing_appends_and_the_latest_decision_wins(
    api: TestClient, db: Session, auth: Callable[..., dict[str, str]]
) -> None:
    _grant(api, auth())
    body = _grant(api, auth(), granted=False)
    assert body["granted"] is False
    assert body["is_current"] is False
    # History is kept: two rows, never an update.
    assert db.scalar(select(func.count()).select_from(ConsentRecord)) == 2


def test_an_old_version_is_refused(
    api: TestClient, db: Session, auth: Callable[..., dict[str, str]]
) -> None:
    response = api.post(
        CONSENTS,
        headers=auth(),
        json={"kind": "privacy_notice", "version": "2025-01-01", "granted": True},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"]["current_version"] == CURRENT
    assert db.scalar(select(func.count()).select_from(ConsentRecord)) == 0


def test_a_bumped_notice_version_needs_a_new_answer(
    api: TestClient, auth: Callable[..., dict[str, str]], settings: object
) -> None:
    _grant(api, auth())
    settings.privacy_notice_version = "2027-01-01"  # type: ignore[attr-defined]
    consent = api.get(PRIVACY, headers=auth()).json()["consents"][0]
    assert consent["version"] == CURRENT
    assert consent["current_version"] == "2027-01-01"
    assert consent["is_current"] is False


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "marketing", "version": CURRENT, "granted": True},
        {"kind": "privacy_notice", "version": "", "granted": True},
        {"kind": "privacy_notice", "version": CURRENT},
        {"kind": "privacy_notice", "version": CURRENT, "granted": True, "extra": 1},
    ],
)
def test_bad_bodies_are_422(
    api: TestClient, auth: Callable[..., dict[str, str]], body: dict[str, object]
) -> None:
    response = api.post(CONSENTS, headers=auth(), json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_consents_are_per_user(api: TestClient, auth: Callable[..., dict[str, str]]) -> None:
    _grant(api, auth())
    other = api.get(PRIVACY, headers=auth(OTHER_USER_ID)).json()["consents"][0]
    assert other["granted"] is None


def test_consents_require_a_token(api: TestClient) -> None:
    assert api.get(PRIVACY).status_code == 401
    assert api.post(CONSENTS, json={}).status_code == 401


def test_table_check_constraints(db: Session) -> None:
    db.add(Profile(id=TEST_USER_ID))
    db.flush()
    for values in (
        {"kind": "marketing", "version": CURRENT, "source": "web"},
        {"kind": "privacy_notice", "version": CURRENT, "source": "email"},
        {"kind": "privacy_notice", "version": "", "source": "web"},
    ):
        nested = db.begin_nested()
        db.add(ConsentRecord(user_id=TEST_USER_ID, granted=True, **values))
        with pytest.raises(IntegrityError):
            db.flush()
        nested.rollback()
