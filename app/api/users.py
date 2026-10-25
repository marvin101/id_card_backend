from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select, or_, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from uuid import UUID
from app.core.database import get_db
from app.core.rate_limit import enforce_registration_rate_limit
from app.core.auth_sessions import revoke_user_sessions
from app.core.file_storage import (
    StorageError,
    delete_storage_object,
    get_storage_public_url,
    managed_user_profile_photo_storage_path,
    save_user_profile_photo,
)
from app.core.security import (
    decode_access_token,
    get_current_user,
    hash_password,
    security,
    verify_password,
)
from app.core.school_access import (
    LEGACY_SCHOOL_ADMIN_ROLE,
    SCHOOL_ADMIN_ROLE,
    is_platform_admin,
    require_platform_admin,
    require_school_admin,
    require_school_role_management,
)
from app.core.mfa import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    generate_totp_provisioning_uri,
    generate_totp_secret,
    hash_recovery_code,
    matching_totp_counter,
    verify_recovery_code,
    recovery_code_expiry,
)
from app.models.users import User
from app.models.school import School
from app.models.school_access_request import SchoolAccessRequest
from app.models.user_school_access import UserSchoolAccess
from app.models.security_event import SecurityEvent
from app.models.auth_session import AuthSession
from app.models.user_mfa_secret import UserMfaSecret
from app.models.user_mfa_recovery_code import UserMfaRecoveryCode
from app.schemas.auth import  (
    SchoolAccessCreate, 
    SchoolAccessResponse, 
    SchoolAccessUpdate, 
    SchoolUserAssignmentResponse,
    RegistrationSchoolResponse,
    UserCreate, 
    UserResponse, AdminUserCreate, AdminUserUpdate,
    ChangePasswordRequest, SelfProfileResponse, SelfProfileSchool,
    SelfProfileUpdate,
    SecurityEventResponse,
    SecurityEventListResponse,
    AuthSessionResponse,
    AuthSessionListResponse,
    MfaEnrollmentStartResponse,
    MfaEnrollmentVerifyRequest,
    MfaRecoveryCodesResponse,
    MfaStatusResponse,
    MfaDisableRequest,
    )
logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/users",
    tags=["Users"],
)


def _self_profile_response(db: Session, user: User) -> SelfProfileResponse:
    school_contexts = db.execute(
        select(UserSchoolAccess, School)
        .join(School, School.id == UserSchoolAccess.school_id)
        .where(UserSchoolAccess.user_id == user.id)
        .order_by(School.school_name, School.uuid)
    ).all()
    photo_path = managed_user_profile_photo_storage_path(
        user.profile_photo_path,
        user.uuid,
    )
    return SelfProfileResponse(
        uuid=user.uuid,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        mobile=user.mobile,
        designation=user.designation,
        platform_role=user.platform_role,
        is_platform_admin=user.is_platform_admin,
        is_active=user.is_active,
        profile_photo_url=get_storage_public_url(photo_path),
        last_login=user.last_login,
        created_at=user.created_at,
        updated_at=user.updated_at,
        school_contexts=[
            SelfProfileSchool(
                school_uuid=school.uuid,
                school_name=school.school_name,
                role=access.role,
            )
            for access, school in school_contexts
        ],
    )

@router.post(
    "/me/mfa/enrollment",
    response_model=MfaEnrollmentStartResponse,
    summary="Start MFA enrollment",
)
def start_mfa_enrollment(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Lock the user row to serialize enrollment requests for this account.
    user = db.execute(
        select(User)
        .where(User.id == current_user.id)
        .with_for_update()
    ).scalar_one()

    existing = db.execute(
        select(UserMfaSecret)
        .where(UserMfaSecret.user_id == user.id)
        .with_for_update()
    ).scalar_one_or_none()

    if existing is not None and existing.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is already enabled for this account.",
        )

    secret = generate_totp_secret()
    encrypted_secret = encrypt_totp_secret(secret)

    now = datetime.now(timezone.utc)

    if existing is None:
        record = UserMfaSecret(
            user_id=user.id,
            encrypted_secret=encrypted_secret,
            issuer_label="CampusID",
            is_enabled=False,
            created_at=now,
            updated_at=now,
        )
        db.add(record)
    else:
        # Replace a pending enrollment, but never replace an enabled secret.
        existing.encrypted_secret = encrypted_secret
        existing.issuer_label = "CampusID"
        existing.updated_at = now

    provisioning_uri = generate_totp_provisioning_uri(
        secret=secret,
        username=user.username,
        issuer="CampusID",
    )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return MfaEnrollmentStartResponse(
        provisioning_uri=provisioning_uri,
        message=(
            "Scan the provisioning URI with your authenticator app, "
            "then verify a code to enable MFA."
        ),
    )
@router.post(
    "/me/mfa/enrollment/verify",
    response_model=MfaRecoveryCodesResponse,
    summary="Verify and enable MFA",
)
def verify_mfa_enrollment(
    data: MfaEnrollmentVerifyRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Serialize verification and activation for this user.
    user = db.execute(
        select(User)
        .where(User.id == current_user.id)
        .with_for_update()
    ).scalar_one()

    record = db.execute(
        select(UserMfaSecret)
        .where(UserMfaSecret.user_id == user.id)
        .with_for_update()
    ).scalar_one_or_none()

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA enrollment has not been started.",
        )

    if record.is_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MFA is already enabled for this account.",
        )

    secret = decrypt_totp_secret(record.encrypted_secret)

    matched_counter = matching_totp_counter(secret, data.code)
    if matched_counter is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid authenticator code.",
        )

    now = datetime.now(timezone.utc)

    recovery_codes = generate_recovery_codes()
    expires_at = recovery_code_expiry(now)

    # Remove any stale codes before creating a new recovery-code batch.
    db.execute(
        select(UserMfaRecoveryCode)
        .where(UserMfaRecoveryCode.user_id == user.id)
        .with_for_update()
    ).scalars().all()

    db.query(UserMfaRecoveryCode).filter(
        UserMfaRecoveryCode.user_id == user.id
    ).delete(synchronize_session=False)

    for code in recovery_codes:
        db.add(
            UserMfaRecoveryCode(
                user_id=user.id,
                code_hash=hash_recovery_code(code),
                used_at=None,
                created_at=now,
                expires_at=expires_at,
            )
        )

    record.is_enabled = True
    record.last_totp_counter = matched_counter
    record.updated_at = now

    db.add(
        SecurityEvent(
            user_id=user.id,
            event_type="mfa_enabled",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            details={
                "method": "totp",
                "recovery_codes_generated": len(recovery_codes),
            },
        )
    )

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    return MfaRecoveryCodesResponse(
        recovery_codes=recovery_codes,
        message=(
            "MFA is enabled. Save these recovery codes securely. "
            "They will not be displayed again."
        ),
    )

@router.post(
    "/me/mfa/recovery-codes/acknowledge",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Record acknowledgement that recovery codes were saved",
)
def acknowledge_recovery_codes_saved(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    mfa = db.execute(select(UserMfaSecret).where(
        UserMfaSecret.user_id == current_user.id,
        UserMfaSecret.is_enabled.is_(True),
    )).scalar_one_or_none()
    if mfa is None:
        raise HTTPException(status_code=409, detail="Two-factor authentication is not enabled.")
    db.add(SecurityEvent(
        user_id=current_user.id,
        event_type="recovery_codes_saved_acknowledged",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={"acknowledged": True},
    ))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


@router.post(
    "/me/mfa/recovery-codes/regenerate",
    response_model=MfaRecoveryCodesResponse,
    summary="Regenerate recovery codes after all existing codes are used",
)
def regenerate_mfa_recovery_codes(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Lock the user row to serialize concurrent regeneration requests on PostgreSQL.
    user = db.execute(select(User).where(User.id == current_user.id).with_for_update()).scalar_one()
    mfa = db.execute(select(UserMfaSecret).where(
        UserMfaSecret.user_id == user.id,
        UserMfaSecret.is_enabled.is_(True),
    ).with_for_update()).scalar_one_or_none()
    if mfa is None:
        raise HTTPException(status_code=409, detail="Two-factor authentication is not enabled.")
    existing = db.execute(select(UserMfaRecoveryCode).where(
        UserMfaRecoveryCode.user_id == user.id
    ).with_for_update()).scalars().all()
    if len(existing) != 10 or any(row.used_at is None for row in existing):
        raise HTTPException(status_code=409, detail="You can generate new recovery codes only after all 10 existing codes have been used.")

    now = datetime.now(timezone.utc)
    codes = generate_recovery_codes()
    expires_at = recovery_code_expiry(now)
    db.query(UserMfaRecoveryCode).filter(UserMfaRecoveryCode.user_id == user.id).delete(synchronize_session=False)
    for code in codes:
        db.add(UserMfaRecoveryCode(
            user_id=user.id, code_hash=hash_recovery_code(code), used_at=None,
            created_at=now, expires_at=expires_at,
        ))
    db.add(SecurityEvent(
        user_id=user.id,
        event_type="mfa_recovery_codes_regenerated",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={"count": len(codes)},
    ))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return MfaRecoveryCodesResponse(recovery_codes=codes, message="New recovery codes generated. Save them securely; they will not be displayed again.")


@router.get(
    "/me/mfa/status",
    response_model=MfaStatusResponse,
    summary="Get current user's MFA status",
)
def get_mfa_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    mfa = db.execute(
        select(UserMfaSecret).where(UserMfaSecret.user_id == current_user.id)
    ).scalar_one_or_none()
    enabled = bool(mfa and mfa.is_enabled)
    remaining = 0
    recovery_codes_can_regenerate = False
    if enabled:
        rows = db.execute(
            select(UserMfaRecoveryCode).where(
                UserMfaRecoveryCode.user_id == current_user.id,
            )
        ).scalars().all()
        now = datetime.now(timezone.utc)
        remaining = sum(1 for row in rows if row.used_at is None and row.expires_at > now)
        recovery_codes_can_regenerate = len(rows) == 10 and all(row.used_at is not None for row in rows)
    return MfaStatusResponse(
        enabled=enabled,
        recovery_codes_remaining=int(remaining),
        recovery_codes_can_regenerate=recovery_codes_can_regenerate,
    )


@router.post(
    "/me/mfa/disable",
    response_model=MfaStatusResponse,
    summary="Disable MFA after password and second-factor verification",
)
def disable_mfa(
    data: MfaDisableRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = db.execute(
        select(User).where(User.id == current_user.id).with_for_update()
    ).scalar_one()
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid password or MFA code.")

    mfa = db.execute(
        select(UserMfaSecret)
        .where(UserMfaSecret.user_id == user.id)
        .with_for_update()
    ).scalar_one_or_none()
    if mfa is None or not mfa.is_enabled:
        raise HTTPException(status_code=409, detail="MFA is not enabled.")

    try:
        secret = decrypt_totp_secret(mfa.encrypted_secret)
        matched_counter = matching_totp_counter(secret, data.code)
    except (RuntimeError, ValueError):
        logger.exception("MFA secret could not be verified while disabling MFA")
        raise HTTPException(
            status_code=503,
            detail="MFA verification is temporarily unavailable.",
        ) from None

    now = datetime.now(timezone.utc)
    valid_totp = (
        matched_counter is not None
        and (mfa.last_totp_counter is None or matched_counter > mfa.last_totp_counter)
    )
    if valid_totp:
        mfa.last_totp_counter = matched_counter
    else:
        recovery_rows = db.execute(
            select(UserMfaRecoveryCode)
            .where(
                UserMfaRecoveryCode.user_id == user.id,
                UserMfaRecoveryCode.used_at.is_(None),
                UserMfaRecoveryCode.expires_at > now,
            )
            .with_for_update()
        ).scalars().all()
        recovery = next(
            (row for row in recovery_rows if verify_recovery_code(data.code, row.code_hash)),
            None,
        )
        if recovery is None:
            raise HTTPException(status_code=401, detail="Invalid password or MFA code.")
        recovery.used_at = now

    db.execute(delete(UserMfaRecoveryCode).where(UserMfaRecoveryCode.user_id == user.id))
    db.delete(mfa)
    db.add(SecurityEvent(
        user_id=user.id,
        event_type="mfa_disabled",
        details={"method": "totp" if valid_totp else "recovery_code"},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    ))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return MfaStatusResponse(enabled=False, recovery_codes_remaining=0)


def _resolve_registration_school(db: Session, user_data: UserCreate) -> School:
    """Resolve a registration request to exactly one active school."""
    if user_data.school_uuid is not None:
        school = db.execute(
            select(School).where(
                School.uuid == user_data.school_uuid,
                School.is_active.is_(True),
            )
        ).scalar_one_or_none()
        if school is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The selected school is no longer available.",
            )
        return school

    # Retain exact-name lookup temporarily so an already-deployed frontend can
    # continue registering while the backend-first rollout is in progress.
    assert user_data.school_name is not None
    normalized_school_name = user_data.school_name.strip()
    schools = db.execute(
        select(School).where(
            func.lower(School.school_name) == normalized_school_name.lower(),
            School.is_active.is_(True),
        )
    ).scalars().all()

    if not schools:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active school matches that name.",
        )

    if len(schools) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Multiple schools share that name. Contact the platform "
                "administrator before registering."
            ),
        )

    return schools[0]


@router.get(
    "/registration-schools",
    response_model=list[RegistrationSchoolResponse],
    summary="List active schools available during registration",
)
def list_registration_schools(
    db: Session = Depends(get_db),
):
    return db.execute(
        select(School)
        .where(School.is_active.is_(True))
        .order_by(School.school_name, School.uuid)
    ).scalars().all()


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_user(
    user_data: UserCreate,
    _: None = Depends(enforce_registration_rate_limit),
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

    school = _resolve_registration_school(db, user_data)

    user = User(
        username=user_data.username,
        password_hash=hash_password(user_data.password),
        full_name=user_data.full_name,
        email=user_data.email,
        mobile=user_data.mobile,
        designation=user_data.designation.strip(),
        platform_role=None,
    )

    db.add(user)
    db.flush()
    db.add(
        SchoolAccessRequest(
            user_id=user.id,
            school_id=school.id,
            status="pending",
        )
    )
    db.commit()
    db.refresh(user)

    return user


@router.get(
    "/me",
    response_model=SelfProfileResponse,
)
def get_my_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _self_profile_response(db, current_user)


@router.patch("/me", response_model=SelfProfileResponse)
def update_my_profile(
    data: SelfProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    values = data.model_dump(exclude_unset=True)
    _check_account_identity(
        db,
        username=values.get("username"),
        email=values.get("email"),
        exclude_id=current_user.id,
    )
    for field, value in values.items():
        setattr(current_user, field, value)
    try:
        db.commit()
        db.refresh(current_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Username or email already exists") from None
    return _self_profile_response(db, current_user)


@router.post("/me/profile-photo", response_model=SelfProfileResponse)
async def upload_my_profile_photo(
    photo: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    content = await photo.read()
    old_path = managed_user_profile_photo_storage_path(
        current_user.profile_photo_path,
        current_user.uuid,
    )
    try:
        new_path = save_user_profile_photo(
            current_user.uuid,
            content,
            photo.content_type,
            photo.filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except StorageError as exc:
        raise HTTPException(
            status_code=502,
            detail="Profile photo storage is temporarily unavailable.",
        ) from exc

    current_user.profile_photo_path = new_path
    try:
        db.commit()
        db.refresh(current_user)
    except Exception:
        db.rollback()
        try:
            delete_storage_object(new_path)
        except StorageError:
            logger.warning("Could not clean up newly uploaded profile photo", exc_info=True)
        raise

    if old_path and old_path != new_path:
        try:
            delete_storage_object(old_path)
        except StorageError:
            logger.warning("Could not remove replaced profile photo", exc_info=True)
    return _self_profile_response(db, current_user)


@router.delete("/me/profile-photo", response_model=SelfProfileResponse)
def remove_my_profile_photo(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    old_path = managed_user_profile_photo_storage_path(
        current_user.profile_photo_path,
        current_user.uuid,
    )
    current_user.profile_photo_path = None
    db.commit()
    db.refresh(current_user)
    if old_path:
        try:
            delete_storage_object(old_path)
        except StorageError:
            logger.warning("Profile photo detached; object cleanup failed", exc_info=True)
    return _self_profile_response(db, current_user)


@router.post("/me/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_my_password(
    data: ChangePasswordRequest,
    request: Request,
    response: Response,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    user = db.execute(
        select(User).where(User.id == current_user.id).with_for_update()
    ).scalar_one()
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )

    user.password_hash = hash_password(data.new_password)
    session_id = None
    payload = decode_access_token(credentials.credentials)
    if payload.get("sid"):
        try:
            session_id = UUID(payload["sid"])
        except (TypeError, ValueError, AttributeError):
            session_id = None
    revoke_user_sessions(db, user.id, except_session_id=session_id)

    security_event = SecurityEvent(
        user_id=user.id,
        event_type="password_changed",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={
            "other_sessions_revoked": True,
            "current_session_preserved": session_id is not None,
        },
    )
    db.add(security_event)

    db.commit()
    response.headers["Cache-Control"] = "no-store"

@router.get(
    "/me/sessions",
    response_model=AuthSessionListResponse,
    summary="List the current user's active login sessions",
)
def list_my_sessions(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        payload = decode_access_token(credentials.credentials)
        current_session_id = UUID(str(payload["sid"])) if payload.get("sid") else None
    except (KeyError, TypeError, ValueError, AttributeError):
        current_session_id = None
    now = datetime.now(timezone.utc)
    sessions = db.scalars(
        select(AuthSession)
        .where(
            AuthSession.user_id == current_user.id,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
        .order_by(AuthSession.last_seen_at.desc(), AuthSession.created_at.desc())
    ).all()
    return AuthSessionListResponse(items=[
        AuthSessionResponse(
            session_id=session.id,
            ip_address=session.ip_address,
            user_agent=session.user_agent,
            created_at=session.created_at,
            last_seen_at=session.last_seen_at,
            expires_at=session.expires_at,
            is_current=session.id == current_session_id,
        ) for session in sessions
    ])


@router.delete(
    "/me/sessions/{session_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke one of the current user's login sessions",
)
def revoke_my_session(
    session_uuid: UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = db.execute(
        select(AuthSession).where(
            AuthSession.id == session_uuid,
            AuthSession.user_id == current_user.id,
            AuthSession.revoked_at.is_(None),
        ).with_for_update()
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Active session not found.")

    session.revoked_at = datetime.now(timezone.utc)
    db.add(SecurityEvent(
        user_id=current_user.id,
        event_type="session_revoked",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        details={"revoked_session_id": str(session.id), "self_revoked": True},
    ))
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me/security-events",
    response_model=SecurityEventListResponse,
    summary="List the current user's security events",
)
def list_my_security_events(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not 1 <= limit <= 100 or offset < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Limit must be between 1 and 100, and offset must be non-negative.",
        )

    base_query = select(SecurityEvent).where(
        SecurityEvent.user_id == current_user.id
    )

    total = db.scalar(
        select(func.count()).select_from(base_query.subquery())
    ) or 0

    events = db.execute(
        base_query
        .order_by(SecurityEvent.created_at.desc(), SecurityEvent.id.desc())
        .offset(offset)
        .limit(limit)
    ).scalars().all()

    return SecurityEventListResponse(
        items=[
            SecurityEventResponse(
                uuid=event.uuid,
                event_type=event.event_type,
                ip_address=event.ip_address,
                user_agent=event.user_agent,
                details=event.details,
                created_at=event.created_at,
            )
            for event in events
        ],
        total=total,
        limit=limit,
        offset=offset,
    )

# ==========================================================
# List Users and Their Assignment State for a School
# ==========================================================

@router.get(
    "/schools/{school_uuid}/assignments",
    response_model=list[SchoolUserAssignmentResponse],
    summary="List users and assignment status for a school",
    description=(
        "Lists active users assigned to the selected school plus users with "
        "a pending access request for that school. Platform "
        "administrators may view any school, while school administrators may "
        "view only schools they administer."
    ),
    responses={
        401: {"description": "Authentication is required."},
        403: {"description": "The user cannot view this school's assignments."},
        404: {"description": "School not found."},
    },
)
def list_school_user_assignments(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return a school-scoped user directory without exposing other memberships."""
    school = db.execute(
        select(School).where(
            School.uuid == school_uuid,
            School.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if school is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="School not found",
        )

    require_school_admin(
        db,
        current_user,
        school.id,
        "Only a school administrator can view assignments in this school",
    )

    assigned_rows = db.execute(
        select(User, UserSchoolAccess.role)
        .join(
            UserSchoolAccess,
            (UserSchoolAccess.user_id == User.id)
            & (UserSchoolAccess.school_id == school.id),
        )
        .where(User.is_active.is_(True))
    ).all()

    pending_rows = db.execute(
        select(User)
        .join(
            SchoolAccessRequest,
            (SchoolAccessRequest.user_id == User.id)
            & (SchoolAccessRequest.school_id == school.id),
        )
        .outerjoin(
            UserSchoolAccess,
            (UserSchoolAccess.user_id == User.id)
            & (UserSchoolAccess.school_id == school.id),
        )
        .where(
            User.is_active.is_(True),
            SchoolAccessRequest.status == "pending",
            UserSchoolAccess.id.is_(None),
        )
    ).scalars().all()

    assignments = [
        SchoolUserAssignmentResponse(
            user_uuid=user.uuid,
            username=user.username,
            full_name=user.full_name,
            email=user.email,
            mobile=user.mobile,
            designation=user.designation,
            role=role,
            assignment_status="assigned",
        )
        for user, role in assigned_rows
    ]

    assignments.extend(
        SchoolUserAssignmentResponse(
            user_uuid=user.uuid,
            username=user.username,
            full_name=user.full_name,
            email=user.email,
            mobile=user.mobile,
            designation=user.designation,
            role=None,
            assignment_status="pending_assignment",
        )
        for user in pending_rows
    )

    return sorted(
        assignments,
        key=lambda item: (item.full_name.casefold(), item.username.casefold()),
    )

# ==========================================================
# Grant User Access to School
# ==========================================================

@router.post(
    "/{user_uuid}/schools/{school_uuid}",
    status_code=status.HTTP_201_CREATED,
)
def grant_school_access(
    user_uuid: UUID,
    school_uuid: UUID,
    access_data: SchoolAccessCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ------------------------------------------------------
    # Find school first so all authorization is evaluated against the exact
    # school represented by the URL.
    # ------------------------------------------------------
    school = db.execute(
        select(School).where(
            School.uuid == school_uuid,
            School.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if school is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="School not found",
        )

    require_school_role_management(
        db,
        current_user,
        school.id,
        "Only a school administrator can grant access in this school",
        requested_role=access_data.role,
    )

    # ------------------------------------------------------
    # Find target user
    # ------------------------------------------------------

    user = db.execute(
        select(User).where(
            User.uuid == user_uuid,
            User.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    access_request = db.execute(
        select(SchoolAccessRequest).where(
            SchoolAccessRequest.user_id == user.id,
            SchoolAccessRequest.school_id == school.id,
            SchoolAccessRequest.status == "pending",
        )
    ).scalar_one_or_none()

    if not is_platform_admin(current_user) and access_request is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user has not requested access to this school",
        )

    # ------------------------------------------------------
    # Check existing access
    # ------------------------------------------------------

    existing_access = db.execute(
        select(UserSchoolAccess).where(
            UserSchoolAccess.user_id == user.id,
            UserSchoolAccess.school_id == school.id,
        )
    ).scalar_one_or_none()

    if existing_access is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already has access to this school",
        )

    # ------------------------------------------------------
    # Create access
    # ------------------------------------------------------

    access = UserSchoolAccess(
        user_id=user.id,
        school_id=school.id,
        role=access_data.role,
    )

    db.add(access)
    if access_request is not None:
        access_request.status = "approved"
    db.commit()
    db.refresh(access)

    return {
        "user_uuid": user.uuid,
        "school_uuid": school.uuid,
        "role": access.role,
    }
# ==========================================================
# List User's School Access
# ==========================================================

@router.get(
    "/{user_uuid}/schools",
    response_model=list[SchoolAccessResponse],
)
def list_user_school_access(
    user_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ------------------------------------------------------
    # Find target user
    # ------------------------------------------------------

    user = db.execute(
        select(User).where(
            User.uuid == user_uuid,
            User.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # ------------------------------------------------------
    # Check permission
    # ------------------------------------------------------

    managed_school_ids: list[int] | None = None
    if current_user.id != user.id and not is_platform_admin(current_user):
        # School admins may inspect memberships only for schools they
        # administer. This permits local user management without exposing a
        # person's access at other schools.
        managed_school_ids = db.execute(
            select(UserSchoolAccess.school_id).where(
                UserSchoolAccess.user_id == current_user.id,
                UserSchoolAccess.role.in_(
                    {SCHOOL_ADMIN_ROLE, LEGACY_SCHOOL_ADMIN_ROLE}
                ),
            )
        ).scalars().all()

    # ------------------------------------------------------
    # Find school access records
    # ------------------------------------------------------

    statement = (
        select(
            UserSchoolAccess,
            School,
        )
        .join(
            School,
            School.id == UserSchoolAccess.school_id,
        )
        .where(
            UserSchoolAccess.user_id == user.id,
            School.is_active.is_(True),
        )
        .order_by(School.school_name)
    )

    if managed_school_ids is not None:
        statement = statement.where(
            UserSchoolAccess.school_id.in_(managed_school_ids)
        )

    result = db.execute(statement)

    # ------------------------------------------------------
    # Build response
    # ------------------------------------------------------

    return [
        SchoolAccessResponse(
            user_uuid=user.uuid,
            school_uuid=school.uuid,
            school_name=school.school_name,
            role=access.role,
        )
        for access, school in result.all()
    ]
# ==========================================================
# Update User School Access
# ==========================================================

@router.put(
    "/{user_uuid}/schools/{school_uuid}",
    response_model=SchoolAccessResponse,
)
def update_school_access(
    user_uuid: UUID,
    school_uuid: UUID,
    access_data: SchoolAccessUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ------------------------------------------------------
    # ------------------------------------------------------
    # Find target user
    # ------------------------------------------------------

    user = db.execute(
        select(User).where(
            User.uuid == user_uuid,
            User.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # ------------------------------------------------------
    # Find school
    # ------------------------------------------------------

    school = db.execute(
        select(School).where(
            School.uuid == school_uuid,
            School.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if school is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="School not found",
        )

    # ------------------------------------------------------
    # Find existing access
    # ------------------------------------------------------

    access = db.execute(
        select(UserSchoolAccess).where(
            UserSchoolAccess.user_id == user.id,
            UserSchoolAccess.school_id == school.id,
        )
    ).scalar_one_or_none()

    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User does not have access to this school",
        )

    require_school_role_management(
        db,
        current_user,
        school.id,
        "Only a school administrator can update access in this school",
        existing_role=access.role,
        requested_role=access_data.role,
    )

    # ------------------------------------------------------
    # Update role
    # ------------------------------------------------------

    access.role = access_data.role

    db.commit()
    db.refresh(access)

    return SchoolAccessResponse(
        user_uuid=user.uuid,
        school_uuid=school.uuid,
        school_name=school.school_name,
        role=access.role,
    )
# ==========================================================
# Revoke User School Access
# ==========================================================

@router.delete(
    "/{user_uuid}/schools/{school_uuid}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_school_access(
    user_uuid: UUID,
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # ------------------------------------------------------
    # ------------------------------------------------------
    # Find target user
    # ------------------------------------------------------

    user = db.execute(
        select(User).where(
            User.uuid == user_uuid,
            User.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # ------------------------------------------------------
    # Find school
    # ------------------------------------------------------

    school = db.execute(
        select(School).where(
            School.uuid == school_uuid,
            School.is_active.is_(True),
        )
    ).scalar_one_or_none()

    if school is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="School not found",
        )

    # ------------------------------------------------------
    # Find existing access
    # ------------------------------------------------------

    access = db.execute(
        select(UserSchoolAccess).where(
            UserSchoolAccess.user_id == user.id,
            UserSchoolAccess.school_id == school.id,
        )
    ).scalar_one_or_none()

    if access is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User does not have access to this school",
        )

    require_school_role_management(
        db,
        current_user,
        school.id,
        "Only a school administrator can revoke access in this school",
        existing_role=access.role,
    )

    # ------------------------------------------------------
    # Revoke access
    # ------------------------------------------------------

    db.delete(access)
    db.commit()

    return None


# Global account changes affect every school; only platform administrators may
# perform them. School administrators retain existing school-scoped role APIs.
@router.get("", response_model=list[UserResponse])
def list_accounts(offset: int = 0, limit: int = 100, search: str = "",
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    require_platform_admin(current_user)
    if offset < 0 or not 1 <= limit <= 200 or len(search) > 150:
        raise HTTPException(422, "Invalid directory pagination")
    query = select(User).order_by(User.full_name, User.id).offset(offset).limit(limit)
    if search.strip():
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(or_(User.username.ilike(f"%{term}%", escape="\\"),
                               User.full_name.ilike(f"%{term}%", escape="\\")))
    return db.execute(query).scalars().all()


def _check_account_identity(db, username=None, email=None, exclude_id=None):
    conditions = []
    if username is not None:
        conditions.append(User.username == username)
    if email:
        local, separator, domain = email.rpartition("@")
        if not separator or not local or "." not in domain:
            raise HTTPException(422, "Enter a valid email address")
        conditions.append(func.lower(User.email) == email.lower())
    if conditions:
        query = select(User).where(or_(*conditions))
        if exclude_id is not None:
            query = query.where(User.id != exclude_id)
        if db.scalar(query) is not None:
            raise HTTPException(409, "Username or email already exists")


@router.post("/accounts", response_model=UserResponse, status_code=201)
def create_account(data: AdminUserCreate, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    require_platform_admin(current_user)
    _check_account_identity(db, data.username, data.email)
    values = data.model_dump(exclude={"password"})
    values["full_name"] = values["full_name"].strip()
    if not values["full_name"]:
        raise HTTPException(422, "Full name cannot be empty")
    user = User(
        **values,
        password_hash=hash_password(data.password),
        is_active=True,
        is_platform_admin=data.platform_role == "platform_admin",
    )
    db.add(user)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Username or email already exists") from None
    return user


@router.patch("/{user_uuid}/account", response_model=UserResponse)
def update_account(user_uuid: UUID, data: AdminUserUpdate, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    require_platform_admin(current_user)
    # Lock critical administrators in a stable order before the target. This
    # serializes concurrent demotions/deactivations and protects the last admin.
    admins = db.execute(select(User).where(User.is_active.is_(True),
        or_(User.platform_role == "platform_admin", User.is_platform_admin.is_(True)))
        .order_by(User.id).with_for_update()).scalars().all()
    user = db.execute(select(User).where(User.uuid == user_uuid).with_for_update()).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "User not found")
    values = data.model_dump(exclude_unset=True)
    loses_admin = values.get("is_active") is False or ("platform_role" in values and values["platform_role"] is None)
    if user.is_active and is_platform_admin(user) and loses_admin and len(admins) <= 1:
        raise HTTPException(409, "The last active platform administrator must be retained")
    if current_user.id == user.id and (values.get("is_active") is False or "platform_role" in values):
        raise HTTPException(409, "You cannot deactivate yourself or change your own platform role")
    _check_account_identity(db, email=values.get("email"), exclude_id=user.id)
    if "password" in values:
        user.password_hash = hash_password(values.pop("password"))
    if "platform_role" in values:
        user.is_platform_admin = values["platform_role"] == "platform_admin"
    for name, value in values.items():
        setattr(user, name, value.strip() if isinstance(value, str) else value)
    if "is_active" in data.model_fields_set or "password" in data.model_fields_set:
        from app.core.auth_sessions import revoke_user_sessions
        revoke_user_sessions(db, user.id)
    try:
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Username or email already exists") from None
    return user
