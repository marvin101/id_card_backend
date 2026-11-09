from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.email_claim import EmailClaim
from app.models.users import User


class EmailClaimConflict(Exception):
    """The email address is already claimed by another account."""


class EmailClaimIntegrityError(Exception):
    """The email claim records are inconsistent."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _lock_user(db: Session, user_id: int) -> User:
    user = db.execute(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
    ).scalar_one_or_none()

    if user is None:
        raise EmailClaimIntegrityError("User does not exist.")

    return user


def reserve_current_email(
    db: Session,
    user_id: int,
    email: str,
) -> None:
    """Reserve the user's current email address."""

    _lock_user(db, user_id)
    email_key = normalize_email(email)

    existing = db.execute(
        select(EmailClaim)
        .where(EmailClaim.email_key == email_key)
        .with_for_update()
    ).scalar_one_or_none()

    if existing is not None:
        if existing.user_id == user_id and existing.claim_type == "current":
            return

        raise EmailClaimConflict(
            "This email address is already in use."
        )

    try:
        with db.begin_nested():
            db.add(
                EmailClaim(
                    email_key=email_key,
                    user_id=user_id,
                    claim_type="current",
                )
            )
            db.flush()
    except IntegrityError:
        raise EmailClaimConflict(
            "This email address is already in use."
        ) from None


def reserve_pending_email(
    db: Session,
    user: User,
    email: str,
) -> None:
    """
    Reserve a replacement email.

    The caller must update user.pending_email in the same transaction.
    """

    _lock_user(db, user.id)

    email_key = normalize_email(email)

    if not email_key:
        raise ValueError("Email address cannot be empty.")

    current_key = (
        normalize_email(user.email)
        if user.email
        else None
    )

    if email_key == current_key:
        raise EmailClaimConflict(
            "The new email address is already the current address."
        )

    existing = db.execute(
        select(EmailClaim)
        .where(EmailClaim.email_key == email_key)
        .with_for_update()
    ).scalar_one_or_none()

    if existing is not None:
        if existing.user_id != user.id:
            raise EmailClaimConflict(
                "This email address is already in use."
            )

        if existing.claim_type == "current":
            raise EmailClaimConflict(
                "This email address is already the current address."
            )

        if existing.claim_type == "pending":
            # Idempotent: preserve the existing pending claim.
            return

    try:
        with db.begin_nested():
            db.execute(
                delete(EmailClaim).where(
                    EmailClaim.user_id == user.id,
                    EmailClaim.claim_type == "pending",
                )
            )

            db.add(
                EmailClaim(
                    email_key=email_key,
                    user_id=user.id,
                    claim_type="pending",
                )
            )

            db.flush()

    except IntegrityError:
        raise EmailClaimConflict(
            "This email address is already in use."
        ) from None


def promote_pending_email(
    db: Session,
    user_id: int,
    email: str,
) -> None:
    """Promote a verified pending email to current."""

    _lock_user(db, user_id)

    email_key = normalize_email(email)

    pending_claim = db.execute(
        select(EmailClaim)
        .where(
            EmailClaim.user_id == user_id,
            EmailClaim.email_key == email_key,
            EmailClaim.claim_type == "pending",
        )
        .with_for_update()
    ).scalar_one_or_none()

    if pending_claim is None:
        raise EmailClaimIntegrityError(
            "The pending email claim does not exist."
        )

    db.execute(
        delete(EmailClaim).where(
            EmailClaim.user_id == user_id,
            EmailClaim.claim_type == "current",
        )
    )

    pending_claim.claim_type = "current"
    db.flush()


def release_pending_email(
    db: Session,
    user_id: int,
) -> None:
    """Release the pending email claim."""

    _lock_user(db, user_id)

    db.execute(
        delete(EmailClaim).where(
            EmailClaim.user_id == user_id,
            EmailClaim.claim_type == "pending",
        )
    )


def release_email_claims(
    db: Session,
    user_id: int,
) -> None:
    """Release all email claims belonging to a user."""

    _lock_user(db, user_id)

    db.execute(
        delete(EmailClaim).where(
            EmailClaim.user_id == user_id,
        )
    )