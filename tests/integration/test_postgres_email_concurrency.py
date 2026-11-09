from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.email_claims import (
    EmailClaimConflict,
    promote_pending_email,
    reserve_current_email,
    reserve_pending_email,
)
from app.core.email_verification import (
    issue_email_verification_token,
    verify_email_token,
)
from app.models.email_claim import EmailClaim
from app.models.email_verification_token import EmailVerificationToken
from app.models.security_event import SecurityEvent
from app.models.users import User



def _new_user(db, *, email=None, verified_at=None, pending_email=None):
    suffix = uuid4().hex
    user = User(
        username=f"pg-email-test-{suffix}",
        full_name="PostgreSQL Email Integration Test",
        password_hash="not-a-real-login-hash",
        email=email,
        email_verified_at=verified_at,
        pending_email=pending_email,
        is_active=True,
        is_platform_admin=False,
        platform_role=None,
    )
    db.add(user)
    db.flush()

    if email:
        db.add(
            EmailClaim(
                email_key=email.strip().lower(),
                user_id=user.id,
                claim_type="current",
            )
        )
    if pending_email:
        db.add(
            EmailClaim(
                email_key=pending_email.strip().lower(),
                user_id=user.id,
                claim_type="pending",
            )
        )

    db.commit()
    db.refresh(user)
    return user


def _track(pg_cleanup, user):
    pg_cleanup(user.id)
    return user


def test_concurrent_current_email_claim_has_exactly_one_winner(
    pg_session_factory, pg_cleanup
):
    """Two distinct users cannot commit the same normalized current address."""
    shared_email = f"{uuid4().hex}@integration.example.test"

    with pg_session_factory() as setup:
        user_a = _track(pg_cleanup, _new_user(setup))
        user_b = _track(pg_cleanup, _new_user(setup))

    barrier = Barrier(2)

    def claim(user_id):
        with pg_session_factory() as db:
            barrier.wait(timeout=10)
            try:
                reserve_current_email(db, user_id, shared_email)
                db.commit()
                return "claimed"
            except EmailClaimConflict:
                db.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, [user_a.id, user_b.id]))

    assert sorted(outcomes) == ["claimed", "conflict"]

    with pg_session_factory() as db:
        claims = db.execute(
            select(EmailClaim).where(
                EmailClaim.email_key == shared_email.lower()
            )
        ).scalars().all()

    assert len(claims) == 1
    assert claims[0].user_id in {user_a.id, user_b.id}
    assert claims[0].claim_type == "current"


def test_concurrent_verification_of_same_token_succeeds_once(
    pg_session_factory, pg_cleanup
):
    """Two independent sessions cannot consume the same verification token."""
    email = f"{uuid4().hex}@integration.example.test"

    with pg_session_factory() as setup:
        user = _track(pg_cleanup, _new_user(setup, email=email))
        raw_token, _ = issue_email_verification_token(setup, user)
        setup.commit()

    barrier = Barrier(2)

    def verify():
        with pg_session_factory() as db:
            barrier.wait(timeout=10)
            result = verify_email_token(db, raw_token)
            if result:
                db.commit()
            else:
                # verify_email_token rolls back rejected attempts itself;
                # this explicit rollback also keeps the worker's lifecycle clear.
                db.rollback()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: verify(), range(2)))

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1

    with pg_session_factory() as db:
        refreshed = db.get(User, user.id)
        events = db.execute(
            select(SecurityEvent).where(
                SecurityEvent.user_id == user.id,
                SecurityEvent.event_type == "email_verified",
            )
        ).scalars().all()

    assert refreshed.email_verified_at is not None
    assert len(events) == 1


def test_concurrent_token_issuance_leaves_only_latest_token_usable(
    pg_session_factory, pg_cleanup
):
    """User-row locking serializes simultaneous verification-token issuance."""
    email = f"{uuid4().hex}@integration.example.test"

    with pg_session_factory() as setup:
        user = _track(pg_cleanup, _new_user(setup, email=email))

    barrier = Barrier(2)

    def issue():
        with pg_session_factory() as db:
            db_user = db.get(User, user.id)
            barrier.wait(timeout=10)
            raw_token, _ = issue_email_verification_token(db, db_user)
            db.commit()
            return raw_token

    with ThreadPoolExecutor(max_workers=2) as pool:
        raw_tokens = list(pool.map(lambda _: issue(), range(2)))

    with pg_session_factory() as db:
        records = db.execute(
            select(EmailVerificationToken).where(
                EmailVerificationToken.user_id == user.id
            )
        ).scalars().all()

    assert len(records) == 2
    assert sum(record.used_at is None for record in records) == 1

    # Exactly one of the two returned raw tokens should remain valid.
    results = []
    for raw_token in raw_tokens:
        with pg_session_factory() as db:
            result = verify_email_token(db, raw_token)
            if result:
                db.commit()
            else:
                db.rollback()
            results.append(result)

    assert results.count(True) == 1
    assert results.count(False) == 1


def test_email_change_racing_verification_preserves_claim_consistency(
    pg_session_factory, pg_cleanup
):
    """Either operation may serialize first, but no stale address is promoted."""
    email = f"{uuid4().hex}@integration.example.test"
    replacement = f"{uuid4().hex}@integration.example.test"

    with pg_session_factory() as setup:
        user = _track(pg_cleanup, _new_user(setup, email=email))
        raw_token, _ = issue_email_verification_token(setup, user)
        setup.commit()

    barrier = Barrier(2)

    def change_email():
        with pg_session_factory() as db:
            db_user = db.get(User, user.id)
            barrier.wait(timeout=10)
            reserve_pending_email(db, db_user, replacement)
            db_user.pending_email = replacement
            db.commit()
            return "changed"

    def verify_old_email():
        with pg_session_factory() as db:
            barrier.wait(timeout=10)
            result = verify_email_token(db, raw_token)
            if result:
                db.commit()
            else:
                db.rollback()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        change_future = pool.submit(change_email)
        verify_future = pool.submit(verify_old_email)
        assert change_future.result(timeout=20) == "changed"
        verification_result = verify_future.result(timeout=20)

    with pg_session_factory() as db:
        final_user = db.get(User, user.id)
        claims = db.execute(
            select(EmailClaim).where(EmailClaim.user_id == user.id)
        ).scalars().all()

    assert final_user.pending_email == replacement
    assert final_user.email == email

    claim_map = {claim.email_key: claim.claim_type for claim in claims}
    assert claim_map[email.lower()] == "current"
    assert claim_map[replacement.lower()] == "pending"

    # If verification acquired the user lock first, the original address may
    # legitimately have been verified before the email change. If the email
    # change acquired it first, the stale token must be rejected.
    if verification_result:
        assert final_user.email_verified_at is not None
    else:
        assert final_user.email_verified_at is None


def test_failed_pending_email_promotion_rolls_back_all_claim_changes(
    pg_session_factory, pg_cleanup, monkeypatch
):
    """A failure after claim promotion must not partially replace the address."""
    import app.core.email_verification as verification_module

    old_email = f"{uuid4().hex}@integration.example.test"
    new_email = f"{uuid4().hex}@integration.example.test"

    with pg_session_factory() as setup:
        user = _track(
            pg_cleanup,
            _new_user(
                setup,
                email=old_email,
                verified_at=datetime.now(timezone.utc),
                pending_email=new_email,
            ),
        )
        raw_token, _ = issue_email_verification_token(setup, user)
        setup.commit()

    original_promote = verification_module.promote_pending_email

    def promote_then_fail(db, user_id, email):
        original_promote(db, user_id, email)
        raise RuntimeError("Injected failure after claim promotion")

    monkeypatch.setattr(
        verification_module,
        "promote_pending_email",
        promote_then_fail,
    )

    with pg_session_factory() as db:
        with pytest.raises(RuntimeError, match="Injected failure"):
            verify_email_token(db, raw_token)
        db.rollback()

    with pg_session_factory() as db:
        final_user = db.get(User, user.id)
        claims = db.execute(
            select(EmailClaim).where(EmailClaim.user_id == user.id)
        ).scalars().all()
        token = db.execute(
            select(EmailVerificationToken).where(
                EmailVerificationToken.user_id == user.id
            )
        ).scalar_one()

    assert final_user.email == old_email
    assert final_user.pending_email == new_email
    assert { (c.email_key, c.claim_type) for c in claims } == {
        (old_email.lower(), "current"),
        (new_email.lower(), "pending"),
    }
    assert token.used_at is None
