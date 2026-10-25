import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import pyotp
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_EXPIRY_DAYS = 365


def _get_fernet() -> Fernet:
    """Return the Fernet instance used to encrypt MFA secrets."""
    key = settings.mfa_encryption_key.strip()

    if not key:
        raise RuntimeError("MFA_ENCRYPTION_KEY is not configured.")

    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError(
            "MFA_ENCRYPTION_KEY is invalid."
        ) from exc


def generate_totp_secret() -> str:
    """Generate a new secret for an authenticator application."""
    return pyotp.random_base32()


def encrypt_totp_secret(secret: str) -> str:
    """Encrypt a TOTP secret before database storage."""
    if not secret:
        raise ValueError("TOTP secret cannot be empty.")

    encrypted = _get_fernet().encrypt(secret.encode("utf-8"))
    return encrypted.decode("ascii")


def decrypt_totp_secret(encrypted_secret: str) -> str:
    """Decrypt a previously encrypted TOTP secret."""
    try:
        decrypted = _get_fernet().decrypt(
            encrypted_secret.encode("ascii")
        )
        return decrypted.decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise ValueError(
            "Unable to decrypt the stored MFA secret."
        ) from exc


def generate_totp_provisioning_uri(
    secret: str,
    username: str,
    issuer: str = "CampusID",
) -> str:
    """Generate a URI that authenticator applications can import."""
    if not username.strip():
        raise ValueError("Username cannot be empty.")

    if not issuer.strip():
        raise ValueError("Issuer cannot be empty.")

    totp = pyotp.TOTP(secret)

    return totp.provisioning_uri(
        name=username,
        issuer_name=issuer,
    )


def verify_totp_code(
    secret: str,
    code: str,
    valid_window: int = 1,
) -> bool:
    """Verify a six-digit TOTP code, allowing a small clock difference."""
    if not secret or not code or not code.isdigit() or len(code) != 6:
        return False

    if valid_window < 0:
        raise ValueError("valid_window cannot be negative.")

    return pyotp.TOTP(secret).verify(
        code,
        valid_window=valid_window,
    )



def matching_totp_counter(
    secret: str,
    code: str,
    valid_window: int = 0,
    now: Optional[datetime] = None,
) -> Optional[int]:
    """Return the matching TOTP time-step, or None.

    By default, only the current 30-second time-step is accepted. This rejects
    codes from an earlier step after they expire. Callers may explicitly pass a
    wider valid_window when clock-skew tolerance is required. The caller must
    persist the returned counter and reject counters already accepted so the
    database transaction can enforce replay prevention atomically.
    """
    if not secret or not code or not code.isdigit() or len(code) != 6:
        return None
    if valid_window < 0:
        raise ValueError("valid_window cannot be negative.")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    totp = pyotp.TOTP(secret)
    current_counter = int(current.timestamp() // totp.interval)
    matches = [
        counter
        for counter in range(current_counter - valid_window, current_counter + valid_window + 1)
        if counter >= 0 and hmac.compare_digest(totp.generate_otp(counter), code)
    ]
    return max(matches) if matches else None

def generate_recovery_codes(
    count: int = RECOVERY_CODE_COUNT,
) -> list[str]:
    """Generate high-entropy, single-use recovery codes."""
    if count < 1 or count > 50:
        raise ValueError("Recovery code count must be between 1 and 50.")

    return [
        secrets.token_hex(8).upper()
        for _ in range(count)
    ]


def hash_recovery_code(code: str) -> str:
    """Hash a recovery code before storing it."""
    normalized = code.strip().upper()

    if not normalized:
        raise ValueError("Recovery code cannot be empty.")

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def verify_recovery_code(
    supplied_code: str,
    stored_hash: str,
) -> bool:
    """Compare a recovery code with its stored hash."""
    if not supplied_code or not stored_hash:
        return False

    candidate_hash = hash_recovery_code(supplied_code)

    return hmac.compare_digest(candidate_hash, stored_hash)


def recovery_code_expiry(
    now: Optional[datetime] = None,
) -> datetime:
    """Return the UTC expiry timestamp for a recovery-code batch."""
    current_time = now or datetime.now(timezone.utc)

    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    return current_time + timedelta(days=RECOVERY_CODE_EXPIRY_DAYS)