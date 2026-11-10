"""PostgreSQL integration tests for single-use MFA recovery codes.

These tests exercise the real /auth/mfa/verify handler and PostgreSQL row
locking. They are opt-in and use the isolated campusid_email_test database.

Run:
    $env:CAMPUSID_RUN_PG_INTEGRATION = "1"
    $env:DB_NAME = "campusid_email_test"
    python -m pytest tests/integration/test_mfa_recovery.py -v
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import select

# Register all ORM models before using the integration session fixture.
import app.models  # noqa: F401
from app.api.auth import _create_mfa_challenge, verify_login_mfa
from app.core.mfa import (
    encrypt_totp_secret,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
)
from app.models.auth_session import AuthSession
from app.models.security_event import SecurityEvent
from app.models.user_mfa_recovery_code import UserMfaRecoveryCode
from app.models.user_mfa_secret import UserMfaSecret
from app.models.users import User
from app.schemas.auth import MfaChallengeVerifyRequest


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/auth/mfa/verify",
            "raw_path": b"/auth/mfa/verify",
            "query_string": b"",
            "headers": [(b"user-agent", b"CampusID MFA integration test")],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
    )


def _create_mfa_user(db, pg_cleanup):
    suffix = uuid4().hex
    user = User(
        username=f"pg-mfa-recovery-{suffix}",
        full_name="MFA Recovery Integration Test",
        password_hash="not-a-real-login-hash",
        is_active=True,
        is_platform_admin=False,
        platform_role=None,
    )
    db.add(user)
    db.flush()
    pg_cleanup(user.id)

    now = datetime.now(timezone.utc)
    db.add(
        UserMfaSecret(
            user_id=user.id,
            encrypted_secret=encrypt_totp_secret(generate_totp_secret()),
            issuer_label="CampusID",
            created_at=now,
            updated_at=now,
            last_totp_counter=None,
            is_enabled=True,
        )
    )
    return user


def _add_recovery_code(db, user_id: int, raw_code: str, *, expires_at=None):
    now = datetime.now(timezone.utc)
    db.add(
        UserMfaRecoveryCode(
            user_id=user_id,
            code_hash=hash_recovery_code(raw_code),
            used_at=None,
            created_at=now,
            expires_at=expires_at or now + timedelta(days=30),
        )
    )
    db.flush()


def _verify(db, challenge_token: str, code: str):
    return verify_login_mfa(
        MfaChallengeVerifyRequest(
            challenge_token=challenge_token,
            code=code,
        ),
        _request(),
        db,
    )


def test_recovery_code_login_succeeds_once_and_is_consumed(
    pg_session_factory, pg_cleanup
):
    recovery_code = generate_recovery_codes(1)[0]

    with pg_session_factory() as db:
        user = _create_mfa_user(db, pg_cleanup)
        _add_recovery_code(db, user.id, recovery_code)
        challenge = _create_mfa_challenge(user, db)
        user_id = user.id

    with pg_session_factory() as db:
        result = _verify(db, challenge, recovery_code)
        assert result["access_token"]
        assert result["refresh_token"]

    with pg_session_factory() as db:
        recovery = db.execute(
            select(UserMfaRecoveryCode).where(
                UserMfaRecoveryCode.user_id == user_id
            )
        ).scalar_one()
        sessions = db.execute(
            select(AuthSession).where(AuthSession.user_id == user_id)
        ).scalars().all()
        events = db.execute(
            select(SecurityEvent).where(
                SecurityEvent.user_id == user_id,
                SecurityEvent.event_type == "login_mfa_verified",
            )
        ).scalars().all()

        assert recovery.used_at is not None
        assert len(sessions) == 1
        assert len(events) == 1
        assert events[0].details["method"] == "recovery_code"


def test_consumed_recovery_code_cannot_be_reused(
    pg_session_factory, pg_cleanup
):
    recovery_code = generate_recovery_codes(1)[0]

    with pg_session_factory() as db:
        user = _create_mfa_user(db, pg_cleanup)
        _add_recovery_code(db, user.id, recovery_code)
        first_challenge = _create_mfa_challenge(user, db)
        second_challenge = _create_mfa_challenge(user, db)
        user_id = user.id

    with pg_session_factory() as db:
        _verify(db, first_challenge, recovery_code)

    with pg_session_factory() as db:
        with pytest.raises(HTTPException) as exc:
            _verify(db, second_challenge, recovery_code)
        assert exc.value.status_code == 401

    with pg_session_factory() as db:
        sessions = db.execute(
            select(AuthSession).where(AuthSession.user_id == user_id)
        ).scalars().all()
        failed_events = db.execute(
            select(SecurityEvent).where(
                SecurityEvent.user_id == user_id,
                SecurityEvent.event_type == "login_mfa_failed",
            )
        ).scalars().all()
        assert len(sessions) == 1
        assert len(failed_events) == 1


def test_expired_recovery_code_is_rejected_without_consumption(
    pg_session_factory, pg_cleanup
):
    recovery_code = generate_recovery_codes(1)[0]

    with pg_session_factory() as db:
        user = _create_mfa_user(db, pg_cleanup)
        _add_recovery_code(
            db,
            user.id,
            recovery_code,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        challenge = _create_mfa_challenge(user, db)
        user_id = user.id

    with pg_session_factory() as db:
        with pytest.raises(HTTPException) as exc:
            _verify(db, challenge, recovery_code)
        assert exc.value.status_code == 401

    with pg_session_factory() as db:
        recovery = db.execute(
            select(UserMfaRecoveryCode).where(
                UserMfaRecoveryCode.user_id == user_id
            )
        ).scalar_one()
        sessions = db.execute(
            select(AuthSession).where(AuthSession.user_id == user_id)
        ).scalars().all()
        assert recovery.used_at is None
        assert sessions == []


def test_invalid_recovery_code_does_not_consume_valid_code(
    pg_session_factory, pg_cleanup
):
    valid_code = generate_recovery_codes(1)[0]
    invalid_code = generate_recovery_codes(1)[0]

    with pg_session_factory() as db:
        user = _create_mfa_user(db, pg_cleanup)
        _add_recovery_code(db, user.id, valid_code)
        challenge = _create_mfa_challenge(user, db)
        user_id = user.id

    with pg_session_factory() as db:
        with pytest.raises(HTTPException) as exc:
            _verify(db, challenge, invalid_code)
        assert exc.value.status_code == 401

    with pg_session_factory() as db:
        recovery = db.execute(
            select(UserMfaRecoveryCode).where(
                UserMfaRecoveryCode.user_id == user_id
            )
        ).scalar_one()
        sessions = db.execute(
            select(AuthSession).where(AuthSession.user_id == user_id)
        ).scalars().all()
        assert recovery.used_at is None
        assert sessions == []


def test_concurrent_attempts_cannot_reuse_the_same_recovery_code(
    pg_session_factory, pg_cleanup
):
    """Independent PostgreSQL transactions race with separate MFA challenges."""
    recovery_code = generate_recovery_codes(1)[0]

    with pg_session_factory() as db:
        user = _create_mfa_user(db, pg_cleanup)
        _add_recovery_code(db, user.id, recovery_code)
        challenge_tokens = [
            _create_mfa_challenge(user, db),
            _create_mfa_challenge(user, db),
        ]
        user_id = user.id

    barrier = Barrier(2)

    def attempt(challenge_token):
        with pg_session_factory() as db:
            barrier.wait(timeout=10)
            try:
                _verify(db, challenge_token, recovery_code)
                return "success"
            except HTTPException as exc:
                assert exc.status_code == 401
                return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, challenge_tokens))

    assert outcomes.count("success") == 1
    assert outcomes.count("rejected") == 1

    with pg_session_factory() as db:
        sessions = db.execute(
            select(AuthSession).where(AuthSession.user_id == user_id)
        ).scalars().all()
        recovery = db.execute(
            select(UserMfaRecoveryCode).where(
                UserMfaRecoveryCode.user_id == user_id
            )
        ).scalar_one()
        verified_events = db.execute(
            select(SecurityEvent).where(
                SecurityEvent.user_id == user_id,
                SecurityEvent.event_type == "login_mfa_verified",
            )
        ).scalars().all()

        assert len(sessions) == 1
        assert recovery.used_at is not None
        assert len(verified_events) == 1
