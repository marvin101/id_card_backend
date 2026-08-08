from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import hash_password
from app.models.users import User
from app.schemas.auth import UserCreate, UserResponse

from app.core.security import (
    get_current_user,
    hash_password,
)

router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
@router.get(
    "/me",
    response_model=UserResponse,
)
def get_my_profile(
    current_user: User = Depends(get_current_user),
):
    return current_user
def register_user(
    user_data: UserCreate,
    db: Session = Depends(get_db),
):
    # ------------------------------------------------------
    # Check whether username already exists
    # ------------------------------------------------------

    existing_user = db.scalar(
        select(User).where(User.username == user_data.username)
    )

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists.",
        )

    # ------------------------------------------------------
    # Create user
    # ------------------------------------------------------

    user = User(
        username=user_data.username,
        password_hash=hash_password(user_data.password),
        full_name=user_data.full_name,
        email=user_data.email,
        mobile=user_data.mobile,
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user