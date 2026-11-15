"""Encryption and enqueue helpers for the transactional email outbox."""
import json
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.email_outbox import EmailOutbox


class OutboxConfigurationError(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = settings.email_outbox_encryption_key
    if not key:
        raise OutboxConfigurationError("EMAIL_OUTBOX_ENCRYPTION_KEY is not configured")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        raise OutboxConfigurationError("EMAIL_OUTBOX_ENCRYPTION_KEY is invalid") from None


def validate_encryption_key() -> None:
    """Fail fast when the dedicated outbox encryption key is missing or invalid."""
    _fernet()


def encrypt_payload(payload: dict) -> str:
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return _fernet().encrypt(encoded).decode("ascii")


def decrypt_payload(ciphertext: str) -> dict:
    try:
        raw = _fernet().decrypt(ciphertext.encode("ascii"))
        value = json.loads(raw.decode("utf-8"))
    except (InvalidToken, ValueError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Outbox payload cannot be decrypted") from None
    if not isinstance(value, dict):
        raise ValueError("Outbox payload must be a JSON object")
    return value


def enqueue_password_reset_email(
    db: Session, *, user_id: int, recipient: str, raw_token: str,
) -> EmailOutbox:
    """Add an encrypted job. Caller commits it with the reset-token row."""
    job = EmailOutbox(
        message_type="password_reset",
        encrypted_payload=encrypt_payload({
            "user_id": user_id,
            "recipient": recipient,
            "raw_token": raw_token,
        }),
        status="pending",
        attempts=0,
        max_attempts=settings.email_outbox_max_attempts,
        available_at=datetime.now(timezone.utc),
    )
    db.add(job)
    return job
