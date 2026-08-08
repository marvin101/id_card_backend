from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, verify_password
from app.models.users import User
from app.schemas.auth import LoginRequest, TokenResponse


router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


@router.post(
    "/login",
    response_model=TokenResponse,
)
def login(
    login_data: LoginRequest,
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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # ------------------------------------------------------
    # Check account status
    # ------------------------------------------------------

    if not user.is_active:
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