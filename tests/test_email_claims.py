from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.email_claims import (
    EmailClaimConflict,
    EmailClaimIntegrityError,
    normalize_email,
    promote_pending_email,
    release_email_claims,
    release_pending_email,
    reserve_current_email,
    reserve_pending_email,
)
from app.core.security import hash_password
from app.models.email_claim import EmailClaim
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
            "gen_random_uuid", 0, lambda: str(uuid4())
        )

    User.__table__.create(engine)
    EmailClaim.__table__.create(engine)

    db = Session(engine, expire_on_commit=False)
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def create_user(db: Session, username: str, email: str | None) -> User:
    user = User(
        uuid=uuid4(),
        username=username,
        full_name=username,
        password_hash=hash_password("Test-password-123"),
        email=email,
        email_verified_at=None,
        pending_email=None,
        is_active=True,
        is_platform_admin=False,
        platform_role=None,
    )
    db.add(user)
    db.flush()
    return user


def claims_for_user(db: Session, user_id: int) -> list[EmailClaim]:
    return db.execute(
        select(EmailClaim)
        .where(EmailClaim.user_id == user_id)
        .order_by(EmailClaim.claim_type)
    ).scalars().all()


def test_normalize_email_trims_and_lowercases():
    assert normalize_email("  User.Name@Example.COM  ") == "user.name@example.com"


def test_reserve_current_email_normalizes_and_is_idempotent(db_session):
    user = create_user(db_session, "user-one", "User@Example.test")

    reserve_current_email(db_session, user.id, " USER@EXAMPLE.TEST ")
    db_session.commit()

    rows = claims_for_user(db_session, user.id)
    assert len(rows) == 1
    assert rows[0].email_key == "user@example.test"
    assert rows[0].claim_type == "current"

    # Repeating the same reservation must not create a duplicate.
    reserve_current_email(db_session, user.id, "user@example.test")
    db_session.commit()
    assert len(claims_for_user(db_session, user.id)) == 1


def test_reserve_current_email_rejects_address_claimed_by_another_user(db_session):
    owner = create_user(db_session, "owner", "shared@example.test")
    other = create_user(db_session, "other", None)
    reserve_current_email(db_session, owner.id, owner.email)
    db_session.commit()

    with pytest.raises(EmailClaimConflict):
        reserve_current_email(db_session, other.id, "SHARED@example.test")

    db_session.rollback()
    assert len(claims_for_user(db_session, owner.id)) == 1
    assert claims_for_user(db_session, other.id) == []


def test_reserve_pending_email_replaces_previous_pending_claim(db_session):
    user = create_user(db_session, "user-one", "current@example.test")
    reserve_current_email(db_session, user.id, user.email)
    db_session.commit()

    reserve_pending_email(db_session, user, "first@example.test")
    user.pending_email = "first@example.test"
    db_session.commit()

    reserve_pending_email(db_session, user, "second@example.test")
    user.pending_email = "second@example.test"
    db_session.commit()

    rows = claims_for_user(db_session, user.id)
    assert {(row.email_key, row.claim_type) for row in rows} == {
        ("current@example.test", "current"),
        ("second@example.test", "pending"),
    }


def test_reserve_pending_email_is_idempotent_for_same_pending_address(db_session):
    user = create_user(db_session, "user-one", "current@example.test")
    reserve_current_email(db_session, user.id, user.email)
    reserve_pending_email(db_session, user, "pending@example.test")
    user.pending_email = "pending@example.test"
    db_session.commit()

    reserve_pending_email(db_session, user, " PENDING@example.test ")
    db_session.commit()

    rows = claims_for_user(db_session, user.id)
    assert len(rows) == 2
    assert sum(row.claim_type == "pending" for row in rows) == 1


def test_reserve_pending_email_rejects_current_address(db_session):
    user = create_user(db_session, "user-one", "current@example.test")
    reserve_current_email(db_session, user.id, user.email)
    db_session.commit()

    with pytest.raises(EmailClaimConflict):
        reserve_pending_email(db_session, user, " CURRENT@example.test ")

    db_session.rollback()
    assert len(claims_for_user(db_session, user.id)) == 1


def test_reserve_pending_email_rejects_another_users_current_or_pending_claim(db_session):
    owner = create_user(db_session, "owner", "owner@example.test")
    other = create_user(db_session, "other", "other@example.test")
    reserve_current_email(db_session, owner.id, owner.email)
    reserve_current_email(db_session, other.id, other.email)
    reserve_pending_email(db_session, owner, "reserved-pending@example.test")
    owner.pending_email = "reserved-pending@example.test"
    db_session.commit()

    with pytest.raises(EmailClaimConflict):
        reserve_pending_email(db_session, other, "OWNER@example.test")

    db_session.rollback()
    with pytest.raises(EmailClaimConflict):
        reserve_pending_email(db_session, other, "reserved-pending@example.test")

    db_session.rollback()
    assert {
        (row.email_key, row.claim_type)
        for row in claims_for_user(db_session, owner.id)
    } == {
        ("owner@example.test", "current"),
        ("reserved-pending@example.test", "pending"),
    }


def test_promote_pending_email_replaces_current_claim(db_session):
    user = create_user(db_session, "user-one", "old@example.test")
    reserve_current_email(db_session, user.id, user.email)
    reserve_pending_email(db_session, user, "new@example.test")
    user.pending_email = "new@example.test"
    db_session.commit()

    promote_pending_email(db_session, user.id, " NEW@example.test ")
    user.email = user.pending_email
    user.pending_email = None
    db_session.commit()

    rows = claims_for_user(db_session, user.id)
    assert len(rows) == 1
    assert rows[0].email_key == "new@example.test"
    assert rows[0].claim_type == "current"


def test_promote_pending_email_fails_if_pending_claim_is_missing(db_session):
    user = create_user(db_session, "user-one", "old@example.test")
    reserve_current_email(db_session, user.id, user.email)
    db_session.commit()

    with pytest.raises(EmailClaimIntegrityError):
        promote_pending_email(db_session, user.id, "missing@example.test")

    db_session.rollback()
    rows = claims_for_user(db_session, user.id)
    assert len(rows) == 1
    assert rows[0].email_key == "old@example.test"


def test_release_pending_email_preserves_current_claim(db_session):
    user = create_user(db_session, "user-one", "current@example.test")
    reserve_current_email(db_session, user.id, user.email)
    reserve_pending_email(db_session, user, "pending@example.test")
    user.pending_email = "pending@example.test"
    db_session.commit()

    release_pending_email(db_session, user.id)
    user.pending_email = None
    db_session.commit()

    rows = claims_for_user(db_session, user.id)
    assert len(rows) == 1
    assert rows[0].email_key == "current@example.test"
    assert rows[0].claim_type == "current"


def test_release_email_claims_removes_current_and_pending_claims(db_session):
    user = create_user(db_session, "user-one", "current@example.test")
    reserve_current_email(db_session, user.id, user.email)
    reserve_pending_email(db_session, user, "pending@example.test")
    user.pending_email = "pending@example.test"
    db_session.commit()

    release_email_claims(db_session, user.id)
    user.email = None
    user.pending_email = None
    db_session.commit()

    assert claims_for_user(db_session, user.id) == []


def test_claim_helpers_reject_nonexistent_user(db_session):
    with pytest.raises(EmailClaimIntegrityError):
        reserve_current_email(db_session, 99999, "missing@example.test")

    db_session.rollback()
    with pytest.raises(EmailClaimIntegrityError):
        release_pending_email(db_session, 99999)

    db_session.rollback()
    with pytest.raises(EmailClaimIntegrityError):
        release_email_claims(db_session, 99999)
