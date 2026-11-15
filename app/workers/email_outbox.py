"""Render Background Worker entry point for durable transactional email."""
import argparse
import logging
import signal
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4
from html import escape

from sqlalchemy import and_, or_, select, update

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.email import send_email
from app.core.email_outbox import decrypt_payload, validate_encryption_key
from app.models.email_outbox import EmailOutbox
from app.models.password_reset_token import PasswordResetToken
from app.models.security_event import SecurityEvent
from app.models.users import User

logger = logging.getLogger("campusid.email_outbox")
_STOP = False


def _stop(*_args) -> None:
    global _STOP
    _STOP = True

def _record_password_reset_delivery_failure(
    db,
    payload: dict | None,
    *,
    now: datetime,
    attempts: int,
    reason: str,
) -> None:
    """Invalidate a reset token and record failure when payload data is valid."""
    if not isinstance(payload, dict):
        return

    raw_token = payload.get("raw_token")
    raw_user_id = payload.get("user_id")

    if not isinstance(raw_token, str) or not raw_token:
        return

    # JSON payloads created by enqueue_password_reset_email contain an integer.
    # Reject floats, strings, and bools rather than coercing malformed values.
    if not isinstance(raw_user_id, int) or isinstance(raw_user_id, bool):
        return

    user_id = raw_user_id

    if user_id <= 0:
        return

    # Avoid inserting a SecurityEvent with a nonexistent user foreign key.
    user_exists = db.execute(
        select(User.id).where(User.id == user_id)
    ).scalar_one_or_none()

    if user_exists is None:
        return

    digest = sha256(raw_token.encode("utf-8")).hexdigest()

    db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.token_hash == digest,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
    )

    db.add(
        SecurityEvent(
            user_id=user_id,
            event_type="password_reset_email_delivery_failed",
            details={
                "delivery_requested": True,
                "attempts": attempts,
                "reason": reason,
            },
        )
    )

def _fail_exhausted_leases(db, lease_expiry: datetime) -> None:
    """Permanently fail jobs that exhausted their attempts and are no longer active."""
    expired_jobs = db.execute(
        select(EmailOutbox)
        .where(
            EmailOutbox.attempts >= EmailOutbox.max_attempts,
            or_(
                EmailOutbox.status == "pending",
                and_(
                    EmailOutbox.status == "processing",
                    or_(
                        EmailOutbox.locked_at.is_(None),
                        EmailOutbox.locked_at < lease_expiry,
                    ),
                ),
            ),
        )
        .with_for_update(skip_locked=True)
    ).scalars().all()

    now = datetime.now(timezone.utc)

    for row in expired_jobs:
        payload = None

        try:
            payload = decrypt_payload(row.encrypted_payload)
        except (ValueError, TypeError):
            pass

        row.status = "failed"
        row.locked_at = None
        row.worker_id = None
        row.encrypted_payload = ""
        row.last_error_type = "WorkerLeaseExpired"

        _record_password_reset_delivery_failure(
            db,
            payload,
            now=now,
            attempts=row.attempts,
            reason="worker_lease_expired",
        )
def _claim_one(worker_id: str) -> dict | None:
    now = datetime.now(timezone.utc)
    lease_expiry = now - timedelta(
        seconds=settings.email_outbox_lease_seconds
    )

    with SessionLocal() as db:
        # Finalize jobs whose workers exhausted all attempts and crashed.
        _fail_exhausted_leases(db, lease_expiry)

        job = db.execute(
            select(EmailOutbox)
            .where(
                EmailOutbox.attempts < EmailOutbox.max_attempts,
                or_(
                    and_(
                        EmailOutbox.status == "pending",
                        EmailOutbox.available_at <= now,
                ),
                and_(
                    EmailOutbox.status == "processing",
                    or_(
                        EmailOutbox.locked_at.is_(None),
                        EmailOutbox.locked_at < lease_expiry,
                    ),
                ),
            ),
        )
            .order_by(
                EmailOutbox.available_at,
                EmailOutbox.created_at,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        ).scalar_one_or_none()

        if job is None:
            db.commit()
            return None

        job.status = "processing"
        job.worker_id = worker_id
        job.locked_at = now
        job.attempts += 1

        snapshot = {
            "id": job.id,
            "message_type": job.message_type,
            "encrypted_payload": job.encrypted_payload,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
        }

        db.commit()
        return snapshot


def _send_password_reset(payload: dict) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Invalid password reset payload")

    recipient = payload.get("recipient")
    raw_token = payload.get("raw_token")

    if not isinstance(recipient, str) or not recipient.strip():
        raise ValueError("Invalid password reset recipient")

    if not isinstance(raw_token, str) or not raw_token:
        raise ValueError("Invalid password reset token")

    reset_url = (
        f"{settings.public_app_url.rstrip('/')}/reset-password/{raw_token}"
    )

    send_email(
        recipient=recipient,
        subject="Reset your CampusID password",
        text_body=(
            "Hello,\n\nWe received a request to reset your CampusID password. "
            "Open this link to choose a new password:\n\n"
            f"{reset_url}\n\nThis link expires in 30 minutes and can only be used once. "
            "All existing sessions and trusted devices will be signed out after a successful reset.\n\n"
            "If you did not request this, you can ignore this email."
        ),
        html_body=(
            "<html><body><h2>Reset your CampusID password</h2>"
            "<p>We received a request to reset your password.</p>"
            f'<p><a href="{escape(reset_url, quote=True)}">Reset password</a></p>'
            "<p>This link expires in 30 minutes and can only be used once.</p>"
            "<p>All existing sessions and trusted devices will be signed out after a successful reset.</p>"
            "<p>If you did not request this, you can ignore this email.</p></body></html>"
        ),
    )

def _finish_success(job_id, worker_id: str) -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        db.execute(
            update(EmailOutbox).where(
                EmailOutbox.id == job_id,
                EmailOutbox.status == "processing",
                EmailOutbox.worker_id == worker_id,
            ).values(
                status="sent", sent_at=now, locked_at=None, worker_id=None,
                encrypted_payload="", last_error_type=None,
            )
        )
        db.commit()


def _finish_failure(job: dict, worker_id: str, payload: dict | None, exc: Exception) -> None:
    now = datetime.now(timezone.utc)
    error_type = type(exc).__name__[:100]
    with SessionLocal() as db:
        row = db.execute(
            select(EmailOutbox).where(
                EmailOutbox.id == job["id"],
                EmailOutbox.status == "processing",
                EmailOutbox.worker_id == worker_id,
            ).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            return
        row.last_error_type = error_type
        row.locked_at = None
        row.worker_id = None
        if row.attempts < row.max_attempts:
            # 5, 10, 20, ... seconds, capped at one hour.
            delay = min(3600, 5 * (2 ** min(row.attempts - 1, 10)))
            row.status = "pending"
            row.available_at = now + timedelta(seconds=delay)
        else:
            row.status = "failed"
            row.encrypted_payload = ""
            _record_password_reset_delivery_failure(
                db,
                payload,
                now=now,
                attempts=row.attempts,
                reason="delivery_attempts_exhausted",
            )
        db.commit()


def process_one(worker_id: str) -> bool:
    # Validate before claiming a row: configuration errors must never consume retries.
    validate_encryption_key()
    job = _claim_one(worker_id)
    if job is None:
        return False
    payload = None
    try:
        payload = decrypt_payload(job["encrypted_payload"])
        if job["message_type"] != "password_reset":
            raise ValueError("Unsupported outbox message type")
        _send_password_reset(payload)
        _finish_success(job["id"], worker_id)
    except Exception as exc:  # SMTP and payload failures are retried, never leaked to logs.
        logger.error("Outbox job failed (job_id=%s, exception_type=%s, attempt=%s).",
                     job["id"], type(exc).__name__, job["attempts"])
        _finish_failure(job, worker_id, payload, exc)
    return True


def run_worker(*, poll_seconds: int | None = None) -> None:
    global _STOP
    # Fail at startup instead of entering a loop with a broken encryption config.
    validate_encryption_key()
    worker_id = f"render-email-{uuid4()}"
    interval = poll_seconds or settings.email_outbox_poll_seconds
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info("Email outbox worker started (worker_id=%s).", worker_id)
    while not _STOP:
        try:
            if not process_one(worker_id):
                time.sleep(interval)
        except Exception:
            logger.exception("Email outbox worker iteration failed.")
            time.sleep(interval)
    logger.info("Email outbox worker stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Process at most one outbox job and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.once:
        process_one(f"manual-{uuid4()}")
    else:
        run_worker()
