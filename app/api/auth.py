from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.rate_limit import enforce_login_rate_limit
from app.core.security import create_access_token, verify_password
from app.models.users import User
from app.schemas.auth import LoginRequest, TokenResponse


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

    db.commit()

    # ------------------------------------------------------
    # Generate access token
    # ------------------------------------------------------

    access_token = create_access_token(
        str(user.uuid)
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
    }
