"""PostgreSQL integration tests for password-reset locking and transactions.

Opt-in; uses the isolated campusid_email_test database.
Run:
    $env:CAMPUSID_RUN_PG_INTEGRATION = "1"
    $env:DB_NAME = "campusid_email_test"
    python -m pytest tests/integration/test_password_reset_postgres.py -v -p no:cacheprovider
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

from sqlalchemy import func, select

import app.models  # noqa: F401
from app.core.password_reset import (
    consume_password_reset_token,
    issue_password_reset_token,
)
from app.core.security import hash_password, verify_password
from app.models.password_reset_token import PasswordResetToken
from app.models.security_event import SecurityEvent
from app.models.users import User


def _create_user(db, pg_cleanup):
    suffix = uuid4().hex
    user = User(
        username=f"pg-password-reset-{suffix}",
        full_name="Password Reset Integration Test",
        password_hash=hash_password("Original-password-123"),
        email=f"pg-password-reset-{suffix}@example.test",
        is_active=True,
        is_platform_admin=False,
        platform_role=None,
    )
    db.add(user)
    db.flush()
    pg_cleanup(user.id)
    return user


def test_concurrent_consumption_allows_exactly_one_success(
    pg_session_factory, pg_cleanup
):
    with pg_session_factory() as db:
        user = _create_user(db, pg_cleanup)
        raw_token = issue_password_reset_token(db, user)
        user_id = user.id
        db.commit()

    barrier = Barrier(2)

    def attempt(password):
        with pg_session_factory() as db:
            barrier.wait(timeout=10)
            result = consume_password_reset_token(db, raw_token, password)
            if result:
                db.commit()
                return "success"
            db.rollback()
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(
            attempt,
            ["Concurrent-password-A-123", "Concurrent-password-B-456"],
        ))

    assert outcomes.count("success") == 1
    assert outcomes.count("rejected") == 1

    with pg_session_factory() as db:
        user = db.get(User, user_id)
        token = db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user_id
            )
        ).scalar_one()
        events = db.execute(
            select(SecurityEvent).where(
                SecurityEvent.user_id == user_id,
                SecurityEvent.event_type == "password_reset_completed",
            )
        ).scalars().all()

        assert token.used_at is not None
        assert len(events) == 1
        assert (
            verify_password("Concurrent-password-A-123", user.password_hash)
            or verify_password("Concurrent-password-B-456", user.password_hash)
        )


def test_concurrent_issuance_leaves_only_latest_token_outstanding(
    pg_session_factory, pg_cleanup
):
    with pg_session_factory() as db:
        user = _create_user(db, pg_cleanup)
        user_id = user.id
        db.commit()

    barrier = Barrier(2)

    def issue():
        with pg_session_factory() as db:
            user = db.get(User, user_id)
            barrier.wait(timeout=10)
            raw_token = issue_password_reset_token(db, user)
            db.commit()
            return raw_token

    with ThreadPoolExecutor(max_workers=2) as pool:
        raw_tokens = list(pool.map(lambda _: issue(), range(2)))

    assert raw_tokens[0] != raw_tokens[1]

    with pg_session_factory() as db:
        rows = db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user_id
            )
        ).scalars().all()
        outstanding = [row for row in rows if row.used_at is None]

        assert len(rows) == 2
        assert len(outstanding) == 1
        assert outstanding[0].token_hash in {
            __import__("hashlib").sha256(raw.encode("utf-8")).hexdigest()
            for raw in raw_tokens
        }


def test_rollback_restores_token_and_password_state(
    pg_session_factory, pg_cleanup
):
    with pg_session_factory() as db:
        user = _create_user(db, pg_cleanup)
        raw_token = issue_password_reset_token(db, user)
        user_id = user.id
        original_hash = user.password_hash
        db.commit()

    with pg_session_factory() as db:
        assert consume_password_reset_token(
            db, raw_token, "Rollback-password-789"
        ) is True
        # Simulate a failure later in the endpoint before its transaction commits.
        db.rollback()

    with pg_session_factory() as db:
        user = db.get(User, user_id)
        token = db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user_id
            )
        ).scalar_one()
        events_count = db.execute(
            select(func.count(SecurityEvent.id)).where(
                SecurityEvent.user_id == user_id,
                SecurityEvent.event_type == "password_reset_completed",
            )
        ).scalar_one()

        assert user.password_hash == original_hash
        assert token.used_at is None
        assert events_count == 0
