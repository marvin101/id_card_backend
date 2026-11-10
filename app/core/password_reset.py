"""Password-reset token issuance and atomic one-time consumption."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.auth_sessions import revoke_user_sessions
from app.core.security import hash_password
from app.models.password_reset_token import PasswordResetToken
from app.models.security_event import SecurityEvent
from app.models.trusted_device import TrustedDevice
from app.models.users import User

PASSWORD_RESET_TOKEN_TTL_MINUTES = 30


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_password_reset_token(db: Session, user: User) -> str:
    """Issue a token while serializing issuance for this user. Caller commits."""
    locked_user = db.execute(
        select(User).where(User.id == user.id).with_for_update()
    ).scalar_one()
    now = datetime.now(timezone.utc)
    db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == locked_user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
        .execution_options(synchronize_session="fetch")
    )
    raw_token = secrets.token_urlsafe(48)
    db.add(PasswordResetToken(
        user_id=locked_user.id,
        token_hash=_hash_token(raw_token),
        created_at=now,
        expires_at=now + timedelta(minutes=PASSWORD_RESET_TOKEN_TTL_MINUTES),
    ))
    return raw_token


def consume_password_reset_token(
    db: Session,
    raw_token: str,
    new_password: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> bool:
    """Consume a token and reset credentials in the caller's transaction.

    The user row is locked before the token row. This serializes concurrent
    requests for the same account and ensures only one request can succeed.
    Caller must commit on True; False makes no changes.
    """
    token_hash = _hash_token(raw_token)
    user_id = db.execute(
        select(PasswordResetToken.user_id).where(
            PasswordResetToken.token_hash == token_hash
        )
    ).scalar_one_or_none()
    if user_id is None:
        return False

    user = db.execute(
        select(User).where(User.id == user_id).with_for_update()
    ).scalar_one_or_none()
    if user is None or not user.is_active:
        return False

    token = db.execute(
        select(PasswordResetToken)
        .where(PasswordResetToken.token_hash == token_hash)
        .with_for_update()
    ).scalar_one_or_none()
    if token is None or token.user_id != user.id or token.used_at is not None:
        return False

    now = datetime.now(timezone.utc)
    expires_at = token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        return False

    # Use a conditional SQL UPDATE rather than mutating the ORM instance.
    # This avoids stale identity-map state when subsequent bulk updates run
    # in the same SQLAlchemy Session, and makes token consumption explicit.
    consumed = db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.id == token.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
        .execution_options(synchronize_session="fetch")
    )
    if consumed.rowcount != 1:
        return False

    user.password_hash = hash_password(new_password)

    # A password reset is a credential-compromise recovery action: revoke all
    # sessions and trusted-device credentials, not just refresh tokens.
    revoke_user_sessions(db, user.id)
    db.execute(
        update(TrustedDevice)
        .where(TrustedDevice.user_id == user.id, TrustedDevice.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.add(SecurityEvent(
        user_id=user.id,
        event_type="password_reset_completed",
        ip_address=ip_address,
        user_agent=user_agent,
        details={"all_sessions_revoked": True, "trusted_devices_revoked": True},
    ))
    # Any other outstanding reset links become unusable immediately.
    db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.id != token.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=now)
        .execution_options(synchronize_session="fetch")
    )
    return True
