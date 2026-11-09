import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.security import hash_password
from app.models.email_verification_token import EmailVerificationToken
from app.models.security_event import SecurityEvent
from app.models.users import User
from app.core.email_verification import (
    issue_email_verification_token,
    verify_email_token,
)
from app.models.email_claim import EmailClaim


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    # The User UUID column has a PostgreSQL server default. Register the
    # equivalent function for this SQLite test database.
    with engine.connect() as connection:
        connection.connection.create_function(
            "gen_random_uuid",
            0,
            lambda: str(uuid4()),
        )

    for model in (User, SecurityEvent, EmailVerificationToken, EmailClaim):
        model.__table__.create(engine)

    db = Session(engine, expire_on_commit=False)
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def create_user(db: Session, **overrides) -> User:
    values = {
        "uuid": uuid4(),
        "username": "test-user",
        "full_name": "Test User",
        "password_hash": hash_password("Test-password-123"),
        "email": "user@example.test",
        "email_verified_at": None,
        "pending_email": None,
        "is_active": True,
        "is_platform_admin": False,
        "platform_role": None,
    }
    values.update(overrides)
    user = User(**values)
    db.add(user)
    db.flush()

    if user.email:
        db.add(
            EmailClaim(
                email_key=user.email.strip().lower(),
                user_id=user.id,
                claim_type="current",
            )
        )

    db.commit()
    db.refresh(user)
    return user




def token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def test_successful_verification_marks_email_and_records_event(db_session):
    user = create_user(db_session)
    raw_token, target_email = issue_email_verification_token(db_session, user)
    db_session.commit()

    stored = db_session.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.user_id == user.id
        )
    ).scalar_one()

    assert target_email == user.email
    assert stored.token_hash == token_hash(raw_token)
    assert stored.token_hash != raw_token

    assert verify_email_token(
        db_session,
        raw_token,
        ip_address="192.0.2.10",
        user_agent="Email verification test",
    ) is True
    db_session.commit()
    db_session.refresh(user)

    assert user.email_verified_at is not None
    assert user.pending_email is None

    event = db_session.execute(
        select(SecurityEvent).where(
            SecurityEvent.user_id == user.id,
            SecurityEvent.event_type == "email_verified",
        )
    ).scalar_one()
    assert event.ip_address == "192.0.2.10"
    assert event.user_agent == "Email verification test"
    assert event.details == {"method": "email_link"}


def test_invalid_token_is_rejected_without_changing_user(db_session):
    user = create_user(db_session)

    assert verify_email_token(db_session, "not-a-valid-token") is False
    db_session.rollback()
    db_session.refresh(user)

    assert user.email_verified_at is None
    assert db_session.execute(select(SecurityEvent)).scalars().all() == []


def test_expired_token_is_rejected(db_session):
    user = create_user(db_session)
    raw_token = "expired-test-token"
    db_session.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=token_hash(raw_token),
            email=user.email,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    db_session.commit()

    assert verify_email_token(db_session, raw_token) is False
    db_session.rollback()
    db_session.refresh(user)

    assert user.email_verified_at is None
    assert db_session.execute(select(SecurityEvent)).scalars().all() == []


def test_verification_token_is_single_use(db_session):
    user = create_user(db_session)
    raw_token, _ = issue_email_verification_token(db_session, user)
    db_session.commit()

    assert verify_email_token(db_session, raw_token) is True
    db_session.commit()

    assert verify_email_token(db_session, raw_token) is False
    db_session.rollback()


def test_issuing_new_token_invalidates_previous_token(db_session):
    user = create_user(db_session)

    old_raw_token, _ = issue_email_verification_token(db_session, user)
    db_session.commit()

    new_raw_token, _ = issue_email_verification_token(db_session, user)
    db_session.commit()

    old_record = db_session.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == token_hash(old_raw_token)
        )
    ).scalar_one()
    assert old_record.used_at is not None

    assert verify_email_token(db_session, old_raw_token) is False
    db_session.rollback()

    assert verify_email_token(db_session, new_raw_token) is True
    db_session.commit()


def test_verification_promotes_pending_email_and_clears_it(db_session):
    user = create_user(
        db_session,
        email="old@example.test",
        email_verified_at=datetime.now(timezone.utc),
        pending_email="new@example.test",
    )

    # Reserve the replacement address as a pending claim.
    db_session.add(
        EmailClaim(
            email_key="new@example.test",
            user_id=user.id,
            claim_type="pending",
        )
    )
    db_session.commit()

    raw_token, target_email = issue_email_verification_token(
        db_session,
        user,
    )
    db_session.commit()

    assert target_email == "new@example.test"

    assert verify_email_token(db_session, raw_token) is True
    db_session.commit()
    db_session.refresh(user)

    assert user.email == "new@example.test"
    assert user.pending_email is None
    assert user.email_verified_at is not None

    # The old current claim must be replaced by the new current claim.
    claims = db_session.execute(
        select(EmailClaim).where(
            EmailClaim.user_id == user.id
        )
    ).scalars().all()

    assert len(claims) == 1
    assert claims[0].email_key == "new@example.test"
    assert claims[0].claim_type == "current"

def test_token_for_old_pending_email_is_rejected_after_pending_email_changes(db_session):
    user = create_user(db_session)
    raw_token, _ = issue_email_verification_token(db_session, user)
    db_session.commit()

    user.pending_email = "replacement@example.test"
    db_session.commit()

    assert verify_email_token(db_session, raw_token) is False
    db_session.rollback()
    db_session.refresh(user)

    assert user.email == "user@example.test"
    assert user.pending_email == "replacement@example.test"
    assert user.email_verified_at is None


def test_token_cannot_verify_an_already_verified_different_email(db_session):
    user = create_user(db_session)
    raw_token, _ = issue_email_verification_token(db_session, user)
    db_session.commit()

    user.email = "another@example.test"
    user.email_verified_at = datetime.now(timezone.utc)
    db_session.commit()

    assert verify_email_token(db_session, raw_token) is False
    db_session.rollback()
    db_session.refresh(user)

    assert user.email == "another@example.test"
    assert user.email_verified_at is not None
