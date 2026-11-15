from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.email_outbox import encrypt_payload
from app.models.email_outbox import EmailOutbox
from app.models.password_reset_token import PasswordResetToken
from app.models.security_event import SecurityEvent
from app.models.users import User
import app.workers.email_outbox as worker
from cryptography.fernet import Fernet

@pytest.fixture(autouse=True)
def test_outbox_encryption_key(monkeypatch):
    monkeypatch.setattr(
        worker.settings,
        "email_outbox_encryption_key",
        Fernet.generate_key().decode("ascii"),
    )

def _new_user(db, pg_cleanup):
    suffix = uuid4().hex
    user = User(
        username=f"pg-outbox-{suffix}",
        full_name="Outbox Integration Test",
        password_hash="not-a-real-login-hash",
        email=f"{suffix}@integration.example.test",
        email_verified_at=datetime.now(timezone.utc),
        pending_email=None,
        is_active=True,
        is_platform_admin=False,
        platform_role=None,
    )
    db.add(user)
    db.flush()
    pg_cleanup(user.id)
    return user


def _new_job(db, *, payload=None, status="pending", attempts=0,
             max_attempts=4, locked_at=None, available_at=None):
    job = EmailOutbox(
        message_type="password_reset",
        encrypted_payload=encrypt_payload(
            payload or {"recipient": "outbox-test@example.test"}
        ),
        status=status,
        attempts=attempts,
        max_attempts=max_attempts,
        available_at=available_at or datetime.now(timezone.utc) - timedelta(seconds=1),
        locked_at=locked_at,
        worker_id="old-worker" if status == "processing" else None,
    )
    db.add(job)
    db.flush()
    return job


@pytest.fixture
def worker_session_factory(monkeypatch, pg_session_factory):
    monkeypatch.setattr(worker, "SessionLocal", pg_session_factory)
    return pg_session_factory


@pytest.fixture
def outbox_cleanup(pg_session_factory):
    ids = []
    yield ids
    if ids:
        with pg_session_factory() as db:
            db.execute(delete(EmailOutbox).where(EmailOutbox.id.in_(ids)))
            db.commit()


def _track(ids, job):
    ids.append(job.id)
    return job


def test_two_workers_cannot_claim_the_same_job(
    worker_session_factory, pg_session_factory, outbox_cleanup, monkeypatch
):
    monkeypatch.setattr(
        worker.settings, "email_outbox_encryption_key",
        worker.settings.email_outbox_encryption_key,
    )
    with pg_session_factory() as db:
        job = _track(outbox_cleanup, _new_job(db))
        db.commit()

    barrier = Barrier(2)

    def claim(worker_id):
        barrier.wait(timeout=10)
        return worker._claim_one(worker_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["worker-a", "worker-b"]))

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0]["id"] == job.id

    with pg_session_factory() as db:
        stored = db.get(EmailOutbox, job.id)
        assert stored.status == "processing"
        assert stored.attempts == 1
        assert stored.worker_id in {"worker-a", "worker-b"}


@pytest.mark.parametrize("locked_at", [None, datetime.now(timezone.utc) - timedelta(hours=1)])
def test_stale_or_unlocked_processing_job_is_reclaimed(
    worker_session_factory, pg_session_factory, outbox_cleanup, locked_at
):
    with pg_session_factory() as db:
        job = _track(
            outbox_cleanup,
            _new_job(db, status="processing", attempts=1, locked_at=locked_at),
        )
        db.commit()

    claimed = worker._claim_one("replacement-worker")
    assert claimed is not None
    assert claimed["id"] == job.id
    assert claimed["attempts"] == 2

    with pg_session_factory() as db:
        stored = db.get(EmailOutbox, job.id)
        assert stored.status == "processing"
        assert stored.worker_id == "replacement-worker"
        assert stored.locked_at is not None


def test_exhausted_processing_lease_is_failed_and_payload_cleared(
    worker_session_factory, pg_session_factory, outbox_cleanup, monkeypatch
):
    with pg_session_factory() as db:
        job = _track(
            outbox_cleanup,
            _new_job(
                db, status="processing", attempts=3, max_attempts=3,
                locked_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ),
        )
        db.commit()

    assert worker._claim_one("another-worker") is None

    with pg_session_factory() as db:
        stored = db.get(EmailOutbox, job.id)
        assert stored.status == "failed"
        assert stored.encrypted_payload == ""
        assert stored.locked_at is None
        assert stored.worker_id is None
        assert stored.last_error_type == "WorkerLeaseExpired"


def test_failure_schedules_retry_with_exponential_backoff(
    worker_session_factory, pg_session_factory, outbox_cleanup
):
    with pg_session_factory() as db:
        job = _track(outbox_cleanup, _new_job(db))
        db.commit()

    claimed = worker._claim_one("retry-worker")
    assert claimed is not None
    before = datetime.now(timezone.utc)
    worker._finish_failure(claimed, "retry-worker", None, RuntimeError("SMTP down"))

    with pg_session_factory() as db:
        stored = db.get(EmailOutbox, job.id)
        assert stored.status == "pending"
        assert stored.attempts == 1
        assert stored.worker_id is None
        assert stored.locked_at is None
        delay = (stored.available_at - before).total_seconds()
        assert 4 <= delay <= 7
        assert stored.last_error_type == "RuntimeError"


def test_success_finalizer_marks_sent_and_erases_encrypted_payload(
    worker_session_factory, pg_session_factory, outbox_cleanup
):
    with pg_session_factory() as db:
        job = _track(outbox_cleanup, _new_job(db))
        db.commit()

    claimed = worker._claim_one("success-worker")
    assert claimed is not None
    worker._finish_success(job.id, "success-worker")

    with pg_session_factory() as db:
        stored = db.get(EmailOutbox, job.id)
        assert stored.status == "sent"
        assert stored.sent_at is not None
        assert stored.encrypted_payload == ""
        assert stored.worker_id is None
        assert stored.locked_at is None


def test_exhausted_delivery_failure_invalidates_reset_token(
    worker_session_factory, pg_session_factory, pg_cleanup, outbox_cleanup
):
    raw_token = uuid4().hex + uuid4().hex
    token_hash = sha256(raw_token.encode("utf-8")).hexdigest()

    with pg_session_factory() as db:
        user = _new_user(db, pg_cleanup)
        token = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        db.add(token)
        job = _track(
            outbox_cleanup,
            _new_job(
                db,
                payload={
                    "user_id": user.id,
                    "recipient": user.email,
                    "raw_token": raw_token,
                },
                status="processing",
                attempts=3,
                max_attempts=3,
                locked_at=datetime.now(timezone.utc),
            ),
        )
        db.commit()

    snapshot = {
        "id": job.id,
        "attempts": 3,
        "max_attempts": 3,
    }
    worker._finish_failure(
        snapshot,
        "old-worker",
        {"user_id": user.id, "raw_token": raw_token},
        RuntimeError("SMTP permanently unavailable"),
    )

    with pg_session_factory() as db:
        stored_job = db.get(EmailOutbox, job.id)
        stored_token = db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.token_hash == token_hash,
            )
        ).scalar_one()
        events = db.execute(
            select(SecurityEvent).where(
                SecurityEvent.user_id == user.id,
                SecurityEvent.event_type == "password_reset_email_delivery_failed",
            )
        ).scalars().all()

        assert stored_job.status == "failed"
        assert stored_job.encrypted_payload == ""
        assert stored_token.used_at is not None
        assert len(events) == 1


def test_outbox_and_reset_token_rollback_together(
    pg_session_factory, pg_cleanup, outbox_cleanup
):
    raw_token = uuid4().hex + uuid4().hex
    token_hash = sha256(raw_token.encode("utf-8")).hexdigest()

    with pg_session_factory() as db:
        user = _new_user(db, pg_cleanup)
        token = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        job = _track(
            outbox_cleanup,
            _new_job(
                db,
                payload={
                    "user_id": user.id,
                    "recipient": user.email,
                    "raw_token": raw_token,
                },
            ),
        )
        db.add(token)
        db.flush()
        db.rollback()

    with pg_session_factory() as db:
        assert db.get(EmailOutbox, job.id) is None
        assert db.execute(
            select(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.token_hash == token_hash,
            )
        ).scalar_one_or_none() is None
def test_process_one_successfully_sends_password_reset_email(
    worker_session_factory,
    pg_session_factory,
    outbox_cleanup,
    monkeypatch,
):
    raw_token = uuid4().hex + uuid4().hex

    with pg_session_factory() as db:
        job = _track(
            outbox_cleanup,
            _new_job(
                db,
                payload={
                    "recipient": "delivery-test@example.test",
                    "raw_token": raw_token,
                    "user_id": 999999,
                },
            ),
        )
        db.commit()

    sent = {}

    def fake_send_email(**kwargs):
        sent.update(kwargs)

    monkeypatch.setattr(worker, "send_email", fake_send_email)

    assert worker.process_one("delivery-worker") is True

    assert sent["recipient"] == "delivery-test@example.test"
    assert sent["subject"] == "Reset your CampusID password"
    assert raw_token in sent["text_body"]
    assert raw_token in sent["html_body"]

    with pg_session_factory() as db:
        stored = db.get(EmailOutbox, job.id)

        assert stored.status == "sent"
        assert stored.sent_at is not None
        assert stored.encrypted_payload == ""
        assert stored.worker_id is None
        assert stored.locked_at is None


def test_process_one_retries_after_smtp_failure(
    worker_session_factory,
    pg_session_factory,
    outbox_cleanup,
    monkeypatch,
):
    with pg_session_factory() as db:
        job = _track(
            outbox_cleanup,
            _new_job(
                db,
                payload={
                    "recipient": "outbox-test@example.test",
                    "raw_token": uuid4().hex + uuid4().hex,
                },
            ),
        )
        db.commit()

    def fail_send_email(**kwargs):
        raise RuntimeError("Simulated SMTP failure")

    monkeypatch.setattr(worker, "send_email", fail_send_email)

    assert worker.process_one("failure-worker") is True

    with pg_session_factory() as db:
        stored = db.get(EmailOutbox, job.id)

        assert stored.status == "pending"
        assert stored.attempts == 1
        assert stored.last_error_type == "RuntimeError"
        assert stored.worker_id is None
        assert stored.locked_at is None
        assert stored.encrypted_payload != ""


def test_process_one_does_not_claim_job_when_queue_is_empty(
    worker_session_factory,
):
    assert worker.process_one("empty-worker") is False

def test_exhausted_lease_handles_malformed_reset_payload(
    worker_session_factory,
    pg_session_factory,
    outbox_cleanup,
):
    with pg_session_factory() as db:
        job = _track(
            outbox_cleanup,
            _new_job(
                db,
                payload={
                    "recipient": "outbox-test@example.test",
                    "raw_token": 12345,
                    "user_id": "not-a-valid-user-id",
                },
                status="processing",
                attempts=3,
                max_attempts=3,
                locked_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ),
        )
        db.commit()

    # The malformed payload must not prevent lease finalization.
    assert worker._claim_one("malformed-payload-worker") is None

    with pg_session_factory() as db:
        stored = db.get(EmailOutbox, job.id)

        assert stored.status == "failed"
        assert stored.encrypted_payload == ""
        assert stored.locked_at is None
        assert stored.worker_id is None
        assert stored.last_error_type == "WorkerLeaseExpired"


def test_exhausted_delivery_handles_malformed_reset_payload(
    worker_session_factory,
    pg_session_factory,
    outbox_cleanup,
):
    with pg_session_factory() as db:
        job = _track(
            outbox_cleanup,
            _new_job(
                db,
                max_attempts=1,
            ),
        )
        db.commit()

    claimed = worker._claim_one("malformed-delivery-worker")
    assert claimed is not None
    assert claimed["attempts"] == 1

    # The malformed payload must not break the final failure transaction.
    worker._finish_failure(
        claimed,
        "malformed-delivery-worker",
        {
            "recipient": "outbox-test@example.test",
            "raw_token": 12345,
            "user_id": "not-a-valid-user-id",
        },
        RuntimeError("Simulated permanent delivery failure"),
    )

    with pg_session_factory() as db:
        stored = db.get(EmailOutbox, job.id)

        assert stored.status == "failed"
        assert stored.encrypted_payload == ""
        assert stored.locked_at is None
        assert stored.worker_id is None
        assert stored.last_error_type == "RuntimeError"

@pytest.mark.parametrize("key", ["", "not-a-valid-fernet-key"])
def test_invalid_encryption_key_does_not_claim_or_consume_attempts(
    worker_session_factory,
    pg_session_factory,
    outbox_cleanup,
    monkeypatch,
    key,
):
    with pg_session_factory() as db:
        job = _track(outbox_cleanup, _new_job(db))
        db.commit()

    monkeypatch.setattr(worker.settings, "email_outbox_encryption_key", key)

    from app.core.email_outbox import OutboxConfigurationError
    with pytest.raises(OutboxConfigurationError):
        worker.process_one("misconfigured-worker")

    with pg_session_factory() as db:
        stored = db.get(EmailOutbox, job.id)
        assert stored.status == "pending"
        assert stored.attempts == 0
        assert stored.worker_id is None
        assert stored.locked_at is None
        assert stored.encrypted_payload != ""
