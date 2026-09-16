from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.rate_limit import enforce_login_rate_limit, enforce_refresh_rate_limit
from app.core.security import verify_password
from app.core.auth_sessions import start_session, refresh_session, decode_refresh_token, require_live_session, invalid_session
from uuid import UUID
from app.models.users import User
from app.schemas.auth import LoginRequest, TokenResponse, RefreshRequest


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)
logger = logging.getLogger(__name__)


@router.post(
    "/login",
    response_model=TokenResponse,
)
def login(
    login_data: LoginRequest,
    _: None = Depends(enforce_login_rate_limit),
    db: Session = Depends(get_db),
):
    # ------------------------------------------------------
    # Find user
    # ------------------------------------------------------

    result = db.execute(
        select(User).where(
            User.username == login_data.username
        )
    )

    user = result.scalar_one_or_none()

    # ------------------------------------------------------
    # Validate username and password
    # ------------------------------------------------------

    if user is None or not verify_password(
        login_data.password,
        user.password_hash,
    ):
        logger.warning("Authentication failed: invalid credentials")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # ------------------------------------------------------
    # Check account status
    # ------------------------------------------------------

    if not user.is_active:
        logger.warning("Authentication failed: inactive account")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )

    # ------------------------------------------------------
    # Update last login
    # ------------------------------------------------------

    user.last_login = datetime.now(timezone.utc)

    tokens = start_session(db, user)
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenResponse, dependencies=[Depends(enforce_refresh_rate_limit)])
def refresh(data: RefreshRequest, db: Session = Depends(get_db)):
    return refresh_session(db, data.refresh_token)


@router.post("/logout", status_code=204, dependencies=[Depends(enforce_refresh_rate_limit)])
def logout(data: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_refresh_token(data.refresh_token)
    user = db.execute(select(User).where(User.uuid == UUID(payload["sub"])).with_for_update()).scalar_one_or_none()
    if user is None:
        raise invalid_session()
    session = require_live_session(db, payload, user, lock=True)
    # An unexpired signed credential for this session may revoke it even if
    # rotation completed concurrently. It can never renew using a stale digest.
    session.revoked_at = datetime.now(timezone.utc)
    db.commit()
