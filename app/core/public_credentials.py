import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import settings


PUBLIC_CREDENTIAL_PREFIX = "c1"
PUBLIC_CREDENTIAL_TYPE = "public-student-verification"
PUBLIC_CREDENTIAL_SCHEMA = 1
_SIGNING_CONTEXT = b"campusid:public-student-verification:v1:"


class PublicCredentialError(ValueError):
    pass


class PublicCredentialExpiredError(PublicCredentialError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalized_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _base36(value: int) -> str:
    if value < 0:
        raise ValueError("Base36 values must be non-negative")
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    encoded = ""
    while value:
        value, remainder = divmod(value, 36)
        encoded = alphabet[remainder] + encoded
    return encoded


def _from_base36(value: str) -> int:
    try:
        return int(value, 36)
    except (TypeError, ValueError) as exc:
        raise PublicCredentialError("Invalid credential number") from exc


def _signature(payload: str) -> str:
    digest = hmac.new(
        settings.public_credential_signing_key.encode("utf-8"),
        _SIGNING_CONTEXT + payload.encode("ascii"),
        hashlib.sha256,
    ).digest()[:16]
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def issue_public_credential(
    *,
    token_id: str,
    version: int,
    expires_at: datetime,
) -> str:
    if not 20 <= len(token_id) <= 96 or "." in token_id:
        raise ValueError("Invalid credential token identifier")
    if version < 1:
        raise ValueError("Invalid credential version")
    expires = int(normalized_utc(expires_at).timestamp())
    payload = (
        f"{PUBLIC_CREDENTIAL_PREFIX}.{token_id}.{_base36(version)}.{_base36(expires)}"
    )
    return f"{payload}.{_signature(payload)}"


def decode_public_credential(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 5 or parts[0] != PUBLIC_CREDENTIAL_PREFIX:
        raise PublicCredentialError("Invalid credential format")
    _, token_id, encoded_version, encoded_expiry, signature = parts
    if not 20 <= len(token_id) <= 96:
        raise PublicCredentialError("Invalid credential token identifier")
    payload = ".".join(parts[:4])
    if not hmac.compare_digest(signature, _signature(payload)):
        raise PublicCredentialError("Invalid credential signature")
    version = _from_base36(encoded_version)
    expires = _from_base36(encoded_expiry)
    if version < 1:
        raise PublicCredentialError("Invalid credential version")
    if expires <= int(utc_now().timestamp()):
        raise PublicCredentialExpiredError("Credential expired")
    return {
        "typ": PUBLIC_CREDENTIAL_TYPE,
        "schema": PUBLIC_CREDENTIAL_SCHEMA,
        "jti": token_id,
        "ver": version,
        "exp": expires,
    }


def rotate_public_credential(student: object, validity_days: int) -> None:
    import secrets

    issued_at = utc_now()
    student.public_verification_token = secrets.token_urlsafe(32)
    student.public_credential_version = (
        int(getattr(student, "public_credential_version", 0) or 0) + 1
    )
    student.public_credential_issued_at = issued_at
    student.public_credential_expires_at = issued_at + timedelta(days=validity_days)
    student.public_verification_enabled = True


def initialize_public_credential(student: object, validity_days: int) -> None:
    issued_at = utc_now()
    student.public_credential_version = 1
    student.public_credential_issued_at = issued_at
    student.public_credential_expires_at = issued_at + timedelta(days=validity_days)


def public_credential_status(student: object, *, now: datetime | None = None) -> str:
    if not getattr(student, "public_verification_enabled", False):
        return "revoked"
    expires_at = getattr(student, "public_credential_expires_at", None)
    if expires_at is not None and normalized_utc(expires_at) <= (now or utc_now()):
        return "expired"
    return "active"
