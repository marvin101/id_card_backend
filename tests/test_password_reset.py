import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.password_reset import (
    issue_password_reset_token,
    consume_password_reset_token,
)
from app.core.security import hash_password, verify_password
from app.models.auth_session import AuthSession
from app.models.password_reset_token import PasswordResetToken
from app.models.security_event import SecurityEvent
from app.models.trusted_device import TrustedDevice
from app.models.users import User


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.connect() as connection:
        connection.connection.create_function(
            "gen_random_uuid", 0, lambda: uuid4().hex
        )
    for model in (
        User, PasswordResetToken, SecurityEvent, AuthSession, TrustedDevice
    ):
        model.__table__.create(engine)
    db = Session(engine, expire_on_commit=False)
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def create_user(db, **overrides):
    values = {
        "uuid": uuid4(),
        "username": "reset-user",
        "full_name": "Reset User",
        "password_hash": hash_password("Original-password-123"),
        "email": "reset@example.test",
        "email_verified_at": datetime.now(timezone.utc),
        "pending_email": None,
        "is_active": True,
        "is_platform_admin": False,
        "platform_role": None,
    }
    values.update(overrides)
    user = User(**values)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_issued_token_is_hashed_and_only_one_outstanding_token_remains(db_session):
    user = create_user(db_session)
    first = issue_password_reset_token(db_session, user)
    db_session.commit()
    first_row = db_session.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == digest(first)
        )
    ).scalar_one()
    assert first_row.token_hash != first

    second = issue_password_reset_token(db_session, user)
    db_session.commit()
    db_session.refresh(first_row)
    assert first_row.used_at is not None
    assert consume_password_reset_token(
        db_session, first, "New-password-456"
    ) is False
    db_session.rollback()
    assert consume_password_reset_token(
        db_session, second, "New-password-456"
    ) is True


def test_successful_reset_changes_password_and_revokes_sessions_and_devices(db_session):
    user = create_user(db_session)
    now = datetime.now(timezone.utc)
    session = AuthSession(
        id=uuid4(), user_id=user.id, refresh_digest="a" * 64,
        expires_at=now + timedelta(hours=1), revoked_at=None,
        created_at=now, last_seen_at=now,
    )
    device = TrustedDevice(
        id=uuid4(), user_id=user.id, token_hash="b" * 64,
        device_name="Test device", created_at=now,
        expires_at=now + timedelta(days=1), last_used_at=now, revoked_at=None,
    )
    db_session.add_all([session, device])
    raw_token = issue_password_reset_token(db_session, user)
    db_session.commit()

    assert consume_password_reset_token(
        db_session, raw_token, "Replacement-password-456",
        ip_address="192.0.2.5", user_agent="Password reset test",
    ) is True
    db_session.commit()
    db_session.refresh(user)
    db_session.refresh(session)
    db_session.refresh(device)

    assert verify_password("Replacement-password-456", user.password_hash)
    assert not verify_password("Original-password-123", user.password_hash)
    assert session.revoked_at is not None
    assert device.revoked_at is not None
    event = db_session.execute(
        select(SecurityEvent).where(
            SecurityEvent.user_id == user.id,
            SecurityEvent.event_type == "password_reset_completed",
        )
    ).scalar_one()
    assert event.details == {
        "all_sessions_revoked": True, "trusted_devices_revoked": True
    }


def test_reset_token_cannot_be_reused(db_session):
    user = create_user(db_session)
    raw_token = issue_password_reset_token(db_session, user)
    db_session.commit()

    assert consume_password_reset_token(
        db_session, raw_token, "Replacement-password-456"
    ) is True
    db_session.commit()
    assert consume_password_reset_token(
        db_session, raw_token, "Another-password-789"
    ) is False


def test_expired_token_is_rejected(db_session):
    user = create_user(db_session)
    raw_token = "expired-reset-token-value"
    db_session.add(PasswordResetToken(
        user_id=user.id, token_hash=digest(raw_token),
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    ))
    db_session.commit()

    assert consume_password_reset_token(
        db_session, raw_token, "Replacement-password-456"
    ) is False
    db_session.rollback()
    db_session.refresh(user)
    assert verify_password("Original-password-123", user.password_hash)


def test_invalid_token_does_not_change_password(db_session):
    user = create_user(db_session)
    assert consume_password_reset_token(
        db_session, "not-a-real-reset-token", "Replacement-password-456"
    ) is False
    db_session.rollback()
    db_session.refresh(user)
    assert verify_password("Original-password-123", user.password_hash)


def test_inactive_account_token_is_rejected(db_session):
    user = create_user(db_session, is_active=False)
    raw_token = issue_password_reset_token(db_session, user)
    db_session.commit()
    assert consume_password_reset_token(
        db_session, raw_token, "Replacement-password-456"
    ) is False
    db_session.rollback()
    db_session.refresh(user)
    assert verify_password("Original-password-123", user.password_hash)
