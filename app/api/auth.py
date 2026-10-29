from datetime import datetime, timedelta, timezone
from hashlib import sha256
import secrets
import hmac
import logging
from uuid import UUID, uuid4

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth_sessions import (
    decode_refresh_token, invalid_session, refresh_session,
    require_live_session, start_session,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.mfa import decrypt_totp_secret, hash_recovery_code, matching_totp_counter
from app.core.rate_limit import enforce_login_rate_limit, enforce_refresh_rate_limit
from app.core.security import verify_password
from app.models.security_event import SecurityEvent
from app.models.trusted_device import TrustedDevice
from app.models.user_mfa_recovery_code import UserMfaRecoveryCode
from app.models.user_mfa_secret import UserMfaSecret
from app.models.user_mfa_challenge import UserMfaChallenge
from app.models.users import User
from app.schemas.auth import (
    LoginRequest, MfaChallengeResponse, MfaChallengeVerifyRequest,
    RefreshRequest, TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)

MFA_CHALLENGE_TTL_MINUTES = 5
_INVALID_MFA_DETAIL = "Invalid or expired MFA challenge or verification code."


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

@router.post("/login", response_model=TokenResponse | MfaChallengeResponse)
def login(
    login_data: LoginRequest,
    request: Request,
    _: None = Depends(enforce_login_rate_limit),
    db: Session = Depends(get_db),
):
    user = db.execute(select(User).where(User.username == login_data.username)).scalar_one_or_none()
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
