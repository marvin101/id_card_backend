import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.email_verification_token import EmailVerificationToken
from app.models.security_event import SecurityEvent
from app.models.users import User
from app.core.email_claims import (
    promote_pending_email,
    normalize_email,
    EmailClaimIntegrityError,
)
from app.models.email_claim import EmailClaim


EMAIL_VERIFICATION_TOKEN_TTL_MINUTES = 30


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

def _reject_verification(db: Session) -> bool:
    db.rollback()
    return False


def issue_email_verification_token(
    db: Session,
    user: User,
) -> tuple[str, str]:
    """
    Create a new verification token.

    The caller must commit the transaction after this function returns.
    Only the token hash is stored in the database.
    """

    # Serialize token issuance for this user.
    locked_user = db.execute(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
    ).scalar_one()

    if locked_user.pending_email:
        target_email = locked_user.pending_email
    elif locked_user.email and locked_user.email_verified_at is None:
        target_email = locked_user.email
    else:
        raise ValueError("There is no unverified email address.")

    now = datetime.now(timezone.utc)

    # Invalidate every previously issued verification token.
    db.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == locked_user.id,
            EmailVerificationToken.used_at.is_(None),
        )
        .values(used_at=now)
    )

    raw_token = secrets.token_urlsafe(32)

    db.add(
        EmailVerificationToken(
            user_id=locked_user.id,
            token_hash=_hash_token(raw_token),
            email=target_email,
            created_at=now,
            expires_at=now + timedelta(
                minutes=EMAIL_VERIFICATION_TOKEN_TTL_MINUTES
            ),
        )
    )

    return raw_token, target_email


def verify_email_token(
    db: Session,
    raw_token: str,
    *,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> bool:
    """
    Validate and consume a verification token.

    The caller must commit after successful verification.
    Returns False for invalid, expired, or previously used tokens.
    """

    token_hash = _hash_token(raw_token)

    token_reference = db.execute(
        select(EmailVerificationToken.user_id).where(
            EmailVerificationToken.token_hash == token_hash
        )
    ).scalar_one_or_none()

    if token_reference is None:
        return _reject_verification(db)

    user = db.execute(
        select(User)
        .where(User.id == token_reference)
        .with_for_update()
    ).scalar_one_or_none()

    if user is None:
        return _reject_verification(db)

    token = db.execute(
        select(EmailVerificationToken)
        .where(EmailVerificationToken.token_hash == token_hash)
        .with_for_update()
    ).scalar_one_or_none()

    if token is None or token.user_id != user.id:
        return _reject_verification(db)

    now = datetime.now(timezone.utc)

    if token.used_at is not None:
        return _reject_verification(db)

    expires_at = token.expires_at

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= now:
        return _reject_verification(db)

    token_email = normalize_email(token.email)

    if user.pending_email:
        if normalize_email(user.pending_email) != token_email:
            return _reject_verification(db)

        # Promote the pending claim before changing the User record.
        promote_pending_email(
            db,
            user.id,
            user.pending_email,
        )

        user.email = user.pending_email
        user.pending_email = None
        user.email_verified_at = now

    elif user.email:
        if normalize_email(user.email) != token_email:
            return _reject_verification(db)

        if user.email_verified_at is not None:
            return _reject_verification(db)

        # Initial registration: the current claim already exists.
        current_claim = db.execute(
            select(EmailClaim).where(
                EmailClaim.email_key == normalize_email(user.email),
                EmailClaim.user_id == user.id,
                EmailClaim.claim_type == "current",
            )
        ).scalar_one_or_none()

        if current_claim is None:
            raise EmailClaimIntegrityError(
                "The current email claim does not exist."
            )

        user.email_verified_at = now

    else:
        return _reject_verification(db)

    db.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == user.id,
            EmailVerificationToken.used_at.is_(None),
        )
        .values(used_at=now)
    )

    db.add(
        SecurityEvent(
            user_id=user.id,
            event_type="email_verified",
            ip_address=ip_address,
            user_agent=user_agent,
            details={"method": "email_link"},
        )
    )

    return True
    