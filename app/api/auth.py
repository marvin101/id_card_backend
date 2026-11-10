from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets
import hmac
import logging
from uuid import UUID, uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.auth_sessions import (
    decode_refresh_token, invalid_session, refresh_session,
    require_live_session, start_session,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.mfa import decrypt_totp_secret, hash_recovery_code, matching_totp_counter
from app.core.rate_limit import (
    enforce_login_rate_limit, enforce_refresh_rate_limit,
    enforce_email_verification_rate_limit, enforce_password_reset_rate_limit,
    get_client_address,
)
from app.core.security import verify_password, get_current_user
from app.models.security_event import SecurityEvent
from app.models.password_reset_token import PasswordResetToken
from app.models.trusted_device import TrustedDevice
from app.models.user_mfa_recovery_code import UserMfaRecoveryCode
from app.models.user_mfa_secret import UserMfaSecret
from app.models.user_mfa_challenge import UserMfaChallenge
from app.models.users import User
from app.schemas.auth import (
    LoginRequest, MfaChallengeResponse, MfaChallengeVerifyRequest,
    RefreshRequest, TokenResponse, EmailVerificationRequest,
    PasswordResetRequest, PasswordResetConfirmRequest,
)
from html import escape

from app.core.email import send_email, EmailDeliveryError
from app.core.password_reset import (
    issue_password_reset_token, consume_password_reset_token,
)
from app.core.email_verification import (
    issue_email_verification_token,
    verify_email_token,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)

MFA_CHALLENGE_TTL_MINUTES = 5
_INVALID_MFA_DETAIL = "Invalid or expired MFA challenge or verification code."
_EMAIL_VERIFICATION_SUCCESS = (
    "Your email address has been verified."
)

_EMAIL_VERIFICATION_INVALID = (
    "This verification link is invalid or has expired. "
    "Request a new verification email."
)


def _create_mfa_challenge(user: User, db: Session, *, remember_device: bool = False) -> str:
    now = datetime.now(timezone.utc)
    jti = str(uuid4())
    expires_at = now + timedelta(minutes=MFA_CHALLENGE_TTL_MINUTES)
    db.add(UserMfaChallenge(
        jti=jti, user_id=user.id, created_at=now, expires_at=expires_at, consumed_at=None,
        remember_device=remember_device,
    ))
    db.commit()
    return jwt.encode(
        {
            "sub": str(user.uuid),
            "typ": "mfa_challenge",
            "jti": jti,
            "iat": now,
            "exp": expires_at,
        },
        settings.secret_key,
        algorithm=settings.algorithm,
    )


def _decode_mfa_challenge(token: str) -> tuple[UUID, str]:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            options={"require": ["exp", "iat", "sub", "typ", "jti"]},
        )
        if payload.get("typ") != "mfa_challenge":
            raise jwt.InvalidTokenError("Wrong token purpose")
        return UUID(str(payload["sub"])), str(UUID(str(payload["jti"])))
    except (jwt.PyJWTError, TypeError, ValueError, KeyError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_MFA_DETAIL,
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


def _trusted_device(db: Session, user: User, token: str | None, now: datetime):
    if not token or len(token) < 32 or len(token) > 256:
        return None
    digest = sha256(token.encode("utf-8")).hexdigest()
    device = db.execute(
        select(TrustedDevice).where(
            TrustedDevice.user_id == user.id, TrustedDevice.token_hash == digest,
            TrustedDevice.revoked_at.is_(None), TrustedDevice.expires_at > now,
        ).with_for_update()
    ).scalar_one_or_none()
    if device is not None:
        device.last_used_at = now
    return device

def _rotate_trusted_device(device: TrustedDevice) -> str:
    """Rotate the one-time bearer credential after each successful use."""
    raw_token = secrets.token_urlsafe(48)
    device.token_hash = sha256(raw_token.encode("utf-8")).hexdigest()
    return raw_token

def _issue_trusted_device(db: Session, user: User, request: Request, device_name: str | None):
    now = datetime.now(timezone.utc)
    raw_token = secrets.token_urlsafe(48)
    agent = request.headers.get("user-agent", "")[:500] or None
    device = TrustedDevice(
        id=uuid4(), user_id=user.id, token_hash=sha256(raw_token.encode("utf-8")).hexdigest(),
        device_name=(device_name or "Trusted browser")[:120], user_agent=agent,
        created_at=now, expires_at=now + timedelta(days=settings.trusted_device_ttl_days),
        last_used_at=now, revoked_at=None,
    )
    db.add(device)
    db.add(SecurityEvent(user_id=user.id, event_type="trusted_device_added",
        ip_address=request.client.host if request.client else None, user_agent=agent,
        details={"expires_in_days": settings.trusted_device_ttl_days}))
    return raw_token

def _send_verification_email(
    recipient: str,
    raw_token: str,
) -> None:
    verification_url = (
        f"{settings.public_app_url.rstrip('/')}"
        f"/verify-email/{raw_token}"
    )

    send_email(
        recipient=recipient,
        subject="Verify your CampusID email address",
        text_body=(
            "Hello,\n\n"
            "Please verify your email address by opening this link:\n\n"
            f"{verification_url}\n\n"
            "This link expires in 30 minutes and can only be used once.\n\n"
            "If you did not request this, you can ignore this email."
        ),
        html_body=(
            "<html><body>"
            "<h2>Verify your CampusID email address</h2>"
            "<p>Please click the button below to verify your email.</p>"
            f'<p><a href="{escape(verification_url, quote=True)}">'
            "Verify email</a></p>"
            "<p>This link expires in 30 minutes and can only be used once.</p>"
            "<p>If you did not request this, you can ignore this email.</p>"
            "</body></html>"
        ),
    )

_PASSWORD_RESET_GENERIC_MESSAGE = (
    "If an eligible account exists for that email address, "
    "a password reset link will be sent."
)
_PASSWORD_RESET_INVALID = "This password reset link is invalid or has expired. Request a new link."


def _send_password_reset_email(recipient: str, raw_token: str) -> None:
    reset_url = (
        f"{settings.public_app_url.rstrip('/')}"
        f"/reset-password/{raw_token}"
    )
    send_email(
        recipient=recipient,
        subject="Reset your CampusID password",
        text_body=(
            "Hello,\n\n"
            "We received a request to reset your CampusID password. "
            "Open this link to choose a new password:\n\n"
            f"{reset_url}\n\n"
            "This link expires in 30 minutes and can only be used once. "
            "All existing sessions and trusted devices will be signed out "
            "after a successful reset.\n\n"
            "If you did not request this, you can ignore this email."
        ),
        html_body=(
            "<html><body><h2>Reset your CampusID password</h2>"
            "<p>We received a request to reset your password.</p>"
            f'<p><a href="{escape(reset_url, quote=True)}">Reset password</a></p>'
            "<p>This link expires in 30 minutes and can only be used once.</p>"
            "<p>All existing sessions and trusted devices will be signed out "
            "after a successful reset.</p>"
            "<p>If you did not request this, you can ignore this email.</p>"
            "</body></html>"
        ),
    )


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(
    data: PasswordResetRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    enforce_password_reset_rate_limit(
        request, action="request", identifier=data.email
    )
    user = db.execute(
        select(User).where(func.lower(User.email) == data.email).with_for_update()
    ).scalar_one_or_none()

    # Never disclose whether an account exists or whether its email is verified.
    if (user is not None and user.email and user.is_active
            and user.email_verified_at is not None):
        try:
            raw_token = issue_password_reset_token(db, user)
            db.add(SecurityEvent(
                user_id=user.id,
                event_type="password_reset_requested",
                ip_address=get_client_address(request),
                user_agent=request.headers.get("user-agent", "")[:500] or None,
                details={"delivery_requested": True},
            ))
            db.commit()
        except Exception:
            db.rollback()
            raise
        try:
            _send_password_reset_email(user.email, raw_token)
        except EmailDeliveryError:
            # Do not reveal delivery or account state. Invalidate the token so
            # an undelivered link cannot remain valid if logs leak it later.
            db.execute(
                update(PasswordResetToken)
                .where(
                    PasswordResetToken.token_hash == sha256(raw_token.encode("utf-8")).hexdigest(),
                    PasswordResetToken.used_at.is_(None),
                )
                .values(used_at=datetime.now(timezone.utc))
            )
            db.add(SecurityEvent(
                user_id=user.id,
                event_type="password_reset_email_delivery_failed",
                ip_address=get_client_address(request),
                user_agent=request.headers.get("user-agent", "")[:500] or None,
                details={"delivery_requested": True},
            ))
            db.commit()
            logger.error("Password reset email delivery failed for user UUID %s.", user.uuid)

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {"message": _PASSWORD_RESET_GENERIC_MESSAGE}


@router.post("/password-reset/confirm", status_code=status.HTTP_200_OK)
def confirm_password_reset(
    data: PasswordResetConfirmRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    enforce_password_reset_rate_limit(
        request, action="confirm", identifier=data.token
    )
    success = consume_password_reset_token(
        db,
        data.token,
        data.new_password,
        ip_address=get_client_address(request),
        user_agent=request.headers.get("user-agent", "")[:500] or None,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_PASSWORD_RESET_INVALID,
        )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {"message": "Your password has been reset. Sign in with your new password."}


@router.post("/login", response_model=TokenResponse | MfaChallengeResponse)
def login(
    login_data: LoginRequest,
    request: Request,
    _: None = Depends(enforce_login_rate_limit),
    db: Session = Depends(get_db),
):
    user = db.execute(
        select(User).where(User.username == login_data.username).with_for_update()
    ).scalar_one_or_none()
    if user is None or not verify_password(login_data.password, user.password_hash):
        # Record failures only for a known account. Never create an account-
        # enumeration side channel or store the submitted username/password.
        if user is not None:
            db.add(SecurityEvent(
                user_id=user.id,
                event_type="login_failed",
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                details={"stage": "password"},
            ))
            db.commit()
        logger.warning("Authentication failed: invalid credentials")
        raise HTTPException(status_code=401, detail="Invalid username or password")

    if not user.is_active:
        db.add(SecurityEvent(
            user_id=user.id,
            event_type="login_failed",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"stage": "account_status"},
        ))
        db.commit()
        raise HTTPException(status_code=403, detail="User account is inactive")

    mfa = db.execute(
        select(UserMfaSecret).where(
            UserMfaSecret.user_id == user.id,
            UserMfaSecret.is_enabled.is_(True),
        )
    ).scalar_one_or_none()
    if mfa is not None:
        now = datetime.now(timezone.utc)
        trusted = _trusted_device(db, user, login_data.trusted_device_token, now)
        if trusted is None:
            # No normal session is created until MFA succeeds.
            return MfaChallengeResponse(
                mfa_required=True,
                challenge_token=_create_mfa_challenge(user, db, remember_device=login_data.remember_device),
            )
        user.last_login = now
        db.add(SecurityEvent(user_id=user.id, event_type="trusted_device_login",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"), details={"trusted_device_id": str(trusted.id)}))
        tokens = start_session(db, user, request=request)
        tokens["trusted_device_token"] = _rotate_trusted_device(trusted)
        db.commit()
        return tokens

    user.last_login = datetime.now(timezone.utc)
    tokens = start_session(db, user, request=request)
    db.commit()
    return tokens


@router.post(
    "/mfa/verify",
    response_model=TokenResponse,
    dependencies=[Depends(enforce_login_rate_limit)],
)
def verify_login_mfa(
    data: MfaChallengeVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    user_uuid, challenge_jti = _decode_mfa_challenge(data.challenge_token)
    challenge = db.execute(
        select(UserMfaChallenge)
        .where(UserMfaChallenge.jti == challenge_jti)
        .with_for_update()
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if (
        challenge is None
        or challenge.consumed_at is not None
        or challenge.expires_at <= now
    ):
        raise HTTPException(status_code=401, detail=_INVALID_MFA_DETAIL)
    user = db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none()
    if user is None or not user.is_active or challenge.user_id != user.id:
        raise HTTPException(status_code=401, detail=_INVALID_MFA_DETAIL)

    mfa = db.execute(
        select(UserMfaSecret)
        .where(
            UserMfaSecret.user_id == user.id,
            UserMfaSecret.is_enabled.is_(True),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if mfa is None:
        raise HTTPException(status_code=401, detail=_INVALID_MFA_DETAIL)

    submitted = data.code.strip()
    method = None

    if len(submitted) == 6 and submitted.isdigit():
        try:
            secret = decrypt_totp_secret(mfa.encrypted_secret)
        except Exception:
            logger.exception("MFA secret decryption failed")
            raise HTTPException(
                status_code=503,
                detail="MFA verification is temporarily unavailable.",
            ) from None
        matched_counter = matching_totp_counter(secret, submitted, now=now)
        if matched_counter is not None and (
            mfa.last_totp_counter is None or matched_counter > mfa.last_totp_counter
        ):
            mfa.last_totp_counter = matched_counter
            method = "totp"

    if method is None:
        candidate_hash = hash_recovery_code(submitted)
        recovery_codes = db.scalars(
            select(UserMfaRecoveryCode)
            .where(
                UserMfaRecoveryCode.user_id == user.id,
                UserMfaRecoveryCode.used_at.is_(None),
                UserMfaRecoveryCode.expires_at > now,
            )
            .with_for_update()
        ).all()
        for recovery in recovery_codes:
            if hmac.compare_digest(recovery.code_hash, candidate_hash):
                recovery.used_at = now
                method = "recovery_code"
                break

    if method is None:
        # Undo any tentative changes (including a TOTP counter update), then
        # record the failed MFA attempt in a fresh transaction.
        db.rollback()
        db.add(SecurityEvent(
            user_id=user.id,
            event_type="login_mfa_failed",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"stage": "second_factor"},
        ))
        db.commit()
        logger.warning("MFA verification failed for user UUID %s", user.uuid)
        raise HTTPException(status_code=401, detail=_INVALID_MFA_DETAIL)

    # Consume the challenge in the same transaction as token/session creation.
    # The row lock ensures concurrent requests cannot both succeed.
    challenge.consumed_at = now
    user.last_login = now
    db.add(
        SecurityEvent(
            user_id=user.id,
            event_type="login_mfa_verified",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={"method": method},
        )
    )
    tokens = start_session(db, user, request=request)
    if challenge.remember_device:
        tokens["trusted_device_token"] = _issue_trusted_device(
            db, user, request, data.device_name
        )
    db.commit()
    return tokens


@router.post(
    "/refresh",
    response_model=TokenResponse,
    dependencies=[Depends(enforce_refresh_rate_limit)],
)
def refresh(data: RefreshRequest, db: Session = Depends(get_db)):
    return refresh_session(db, data.refresh_token)


@router.post(
    "/logout",
    status_code=204,
    dependencies=[Depends(enforce_refresh_rate_limit)],
)
def logout(data: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_refresh_token(data.refresh_token)
    user = db.execute(
        select(User).where(User.uuid == UUID(payload["sub"])).with_for_update()
    ).scalar_one_or_none()
    if user is None:
        raise invalid_session()
    session = require_live_session(db, payload, user, lock=True)
    session.revoked_at = datetime.now(timezone.utc)
    db.commit()

@router.post(
    "/email-verification/resend",
    status_code=status.HTTP_202_ACCEPTED,
)
def resend_email_verification(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = db.execute(
        select(User)
        .where(User.id == current_user.id)
        .with_for_update()
    ).scalar_one()

    target_email = user.pending_email or (
        user.email if user.email_verified_at is None else None
    )

    identifier = target_email or str(user.uuid)

    enforce_email_verification_rate_limit(
        request,
        action="issue",
        identifier=identifier,
    )

    if not target_email:
        return {
            "message": "There is no unverified email address to verify."
        }

    try:
        raw_token, recipient = issue_email_verification_token(db, user)
        db.commit()
    except Exception:
        db.rollback()
        raise

    try:
        _send_verification_email(recipient, raw_token)
    except EmailDeliveryError:
        logger.error(
            "Email verification delivery failed for user UUID %s.",
            user.uuid,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Email delivery is temporarily unavailable. Please try again later.",
        ) from None

    response.headers["Cache-Control"] = "no-store"
    return {
        "message": "If an unverified email address exists, a verification email has been sent."
    }

@router.post(
    "/email-verification/verify",
    status_code=status.HTTP_200_OK,
)
def verify_email_address(
    data: EmailVerificationRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    enforce_email_verification_rate_limit(
        request,
        action="verify",
        identifier=data.token,
    )

    success = verify_email_token(
        db,
        data.token,
        ip_address=get_client_address(request),
        user_agent=request.headers.get("user-agent", "")[:500] or None,
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_EMAIL_VERIFICATION_INVALID,
        )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    return {"message": _EMAIL_VERIFICATION_SUCCESS}