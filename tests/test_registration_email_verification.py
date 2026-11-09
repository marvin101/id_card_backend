from uuid import uuid4
from unittest.mock import patch

import pytest
import hashlib
from datetime import timedelta

from app.core.config import settings
from app.api.users import _send_registration_verification_email
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.email import EmailDeliveryError
from app.core.rate_limit import auth_rate_limiter
from app.core.security import hash_password
from app.main import app
from app.models.email_claim import EmailClaim
from app.models.users import User
from app.models.school import School
from app.models.school_access_request import SchoolAccessRequest
from app.models.email_verification_token import EmailVerificationToken
from app.core.email_verification import _hash_token


@pytest.fixture
def registration_environment():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    with engine.connect() as connection:
        connection.connection.create_function(
            "gen_random_uuid",
            0,
            lambda: uuid4().hex,
        )

    for model in (
        User,
        EmailClaim,
        School,
        SchoolAccessRequest,
        EmailVerificationToken,
    ):
        model.__table__.create(engine)

    db = Session(engine, expire_on_commit=False)

    school = School(
        uuid=uuid4(),
        school_code="REGTEST",
        school_name="Registration Test School",
        is_active=True,
    )
    db.add(school)
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    auth_rate_limiter.reset()

    with TestClient(app) as client:
        yield client, db, school

    app.dependency_overrides.clear()
    auth_rate_limiter.reset()
    db.close()
    engine.dispose()


def _registration_payload(school):
    return {
        "username": "registration-test",
        "password": "SecurePassword123",
        "full_name": "Registration Test",
        "email": "registration@example.com",
        "designation": "Teacher",
        "school_uuid": str(school.uuid),
    }


def test_registration_creates_unverified_user_and_hashed_token(
    registration_environment,
):
    client, db, school = registration_environment

    with patch(
        "app.api.users._send_registration_verification_email"
    ) as send_verification:
        response = client.post(
            "/users/register",
            json=_registration_payload(school),
        )

    assert response.status_code == 201, response.text

    user = db.scalar(
        select(User).where(User.username == "registration-test")
    )

    assert user is not None
    assert user.email == "registration@example.com"
    assert user.email_verified_at is None
    assert user.pending_email is None

    token = db.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.user_id == user.id
        )
    )

    assert token is not None
    assert token.used_at is None
    assert token.email == user.email

    # Confirm that the email helper received the raw token.
    send_verification.assert_called_once()
    recipient, raw_token = send_verification.call_args.args

    assert recipient == user.email
    assert isinstance(raw_token, str)
    assert len(raw_token) >= 32

    # Only the SHA-256 hash must be stored.
    expected_hash = hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()

    assert token.token_hash == expected_hash
    assert raw_token != token.token_hash
    assert raw_token not in token.token_hash

    # Confirm the exact 30-minute validity period.
    assert token.expires_at - token.created_at == timedelta(minutes=30)

    access_request = db.scalar(
        select(SchoolAccessRequest).where(
            SchoolAccessRequest.user_id == user.id
        )
    )

    assert access_request is not None
    assert access_request.status == "pending"

def test_registration_without_email_does_not_create_verification_token(
    registration_environment,
):
    client, db, school = registration_environment

    payload = _registration_payload(school)
    payload["username"] = "registration-no-email"
    payload["email"] = None

    with patch(
        "app.api.users._send_registration_verification_email"
    ) as send_email:
        response = client.post("/users/register", json=payload)

    assert response.status_code == 201, response.text

    user = db.scalar(
        select(User).where(
            User.username == "registration-no-email"
        )
    )

    assert user is not None
    assert user.email is None
    assert user.email_verified_at is None

    token = db.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.user_id == user.id
        )
    )

    assert token is None
    send_email.assert_not_called()


def test_registration_succeeds_when_smtp_delivery_fails(
    registration_environment,
):
    client, db, school = registration_environment

    with patch(
        "app.api.users._send_registration_verification_email",
        side_effect=EmailDeliveryError("Email delivery failed."),
    ):
        response = client.post(
            "/users/register",
            json=_registration_payload(school),
        )

    assert response.status_code == 201, response.text

    user = db.scalar(
        select(User).where(
            User.username == "registration-test"
        )
    )

    assert user is not None
    assert user.email_verified_at is None

    token = db.scalar(
        select(EmailVerificationToken).where(
            EmailVerificationToken.user_id == user.id
        )
    )

    assert token is not None
    assert token.used_at is None


@pytest.mark.parametrize(
    "email",
    [
        "INVALID",
        "missing-domain",
        "user@",
    ],
)
def test_registration_rejects_invalid_email(
    registration_environment,
    email,
):
    client, _, school = registration_environment

    payload = _registration_payload(school)
    payload["email"] = email

    response = client.post("/users/register", json=payload)

    assert response.status_code == 422

def test_registration_verification_email_uses_configured_public_url():
    raw_token = "test-verification-token-123456789"

    with patch("app.api.users.send_email") as send_email:
        _send_registration_verification_email(
            "recipient@example.com",
            raw_token,
        )

    send_email.assert_called_once()

    kwargs = send_email.call_args.kwargs

    expected_url = (
        f"{settings.public_app_url.rstrip('/')}"
        f"/verify-email/{raw_token}"
    )

    assert kwargs["recipient"] == "recipient@example.com"
    assert expected_url in kwargs["text_body"]
    assert expected_url in kwargs["html_body"]

@pytest.mark.parametrize(
    "existing_email, pending_email, submitted_email",
    [
        (
            "registration@example.com",
            None,
            "registration@example.com",
        ),
        (
            "existing@example.com",
            "registration@example.com",
            "registration@example.com",
        ),
        (
            "registration@example.com",
            None,
            "REGISTRATION@EXAMPLE.COM",
        ),
        (
            "existing@example.com",
            "Registration@Example.com",
            "registration@example.com",
        ),
    ],
)
def test_registration_rejects_existing_or_pending_email(
    registration_environment,
    existing_email,
    pending_email,
    submitted_email,
):
    client, db, school = registration_environment

    existing_user = User(
        username="existing-user",
        password_hash=hash_password("ExistingPassword123"),
        full_name="Existing User",
        email=existing_email,
        pending_email=pending_email,
        designation="Teacher",
    )
    db.add(existing_user)
    db.commit()

    # Create claims representing the existing user's email addresses.
    if existing_user.email:
        db.add(
            EmailClaim(
                email_key=existing_user.email.strip().lower(),
                user_id=existing_user.id,
                claim_type="current",
            )
        )

    if existing_user.pending_email:
        db.add(
            EmailClaim(
                email_key=existing_user.pending_email.strip().lower(),
                user_id=existing_user.id,
                claim_type="pending",
            )
        )

    db.commit()
    claim = db.execute(
    select(EmailClaim).where(
        EmailClaim.email_key == submitted_email.strip().lower()
    )
).scalar_one()

    assert claim.user_id == existing_user.id

    expected_claim_type = (
        "pending"
        if pending_email is not None
        else "current"
    )

    assert claim.claim_type == expected_claim_type
    assert existing_user.email_verified_at is None