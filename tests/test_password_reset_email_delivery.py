from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api import auth as auth_api
from app.core.database import get_db
from app.main import app
from app.models.security_event import SecurityEvent


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _RequestSession:
    def __init__(self, user):
        self.user = user
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, _statement):
        return _Result(self.user)

    def add(self, value):
        self.added.append(value)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _eligible_user():
    return SimpleNamespace(
        id=41,
        uuid=uuid4(),
        email="verified@example.test",
        email_verified_at=datetime.now(timezone.utc),
        is_active=True,
    )


def test_password_reset_request_has_same_response_for_eligible_and_unknown_email(
    monkeypatch,
):
    enqueue_calls = []
    sessions = []

    monkeypatch.setattr(
        auth_api,
        "enforce_password_reset_rate_limit",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        auth_api,
        "issue_password_reset_token",
        lambda _db, _user: "opaque-test-token-value-1234567890123456",
    )

    def fake_enqueue(db, **kwargs):
        enqueue_calls.append((db, kwargs))
        return SimpleNamespace(status="pending")

    monkeypatch.setattr(
        auth_api,
        "enqueue_password_reset_email",
        fake_enqueue,
    )

    responses = []

    for user in (_eligible_user(), None):
        session = _RequestSession(user)
        sessions.append(session)

        def override_db(session=session):
            yield session

        app.dependency_overrides[get_db] = override_db

        with TestClient(app) as client:
            response = client.post(
                "/auth/password-reset/request",
                json={"email": "verified@example.test"},
            )

        responses.append((response.status_code, response.json()))

    assert responses[0] == responses[1]
    assert responses[0][0] == 202

    # Only the eligible account should generate an email job.
    assert len(enqueue_calls) == 1

    queued_db, queued_kwargs = enqueue_calls[0]

    assert isinstance(queued_db, _RequestSession)
    assert queued_kwargs["user_id"] == 41
    assert queued_kwargs["recipient"] == "verified@example.test"
    assert queued_kwargs["raw_token"] == (
        "opaque-test-token-value-1234567890123456"
    )

    # The eligible account's token, event and outbox job share one commit.
    assert queued_db.commits == 1
    assert queued_db.rollbacks == 0

    assert any(
        isinstance(item, SecurityEvent)
        and item.event_type == "password_reset_requested"
        for item in queued_db.added
    )

    # Unknown accounts must not enqueue or commit anything.
    assert sessions[1].commits == 0
    assert sessions[1].rollbacks == 0


def test_outbox_enqueue_failure_rolls_back_password_reset_transaction(
    monkeypatch,
):
    session = _RequestSession(_eligible_user())

    monkeypatch.setattr(
        auth_api,
        "enforce_password_reset_rate_limit",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        auth_api,
        "issue_password_reset_token",
        lambda _db, _user: "opaque-test-token-value-1234567890123456",
    )

    def fail_enqueue(_db, **kwargs):
        raise RuntimeError("Outbox database operation failed")

    monkeypatch.setattr(
        auth_api,
        "enqueue_password_reset_email",
        fail_enqueue,
    )

    def override_db():
        yield session

    app.dependency_overrides[get_db] = override_db

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/auth/password-reset/request",
            json={"email": "verified@example.test"},
        )

    assert response.status_code == 500

    # The token and security event must not be committed without the email job.
    assert session.commits == 0
    assert session.rollbacks == 1