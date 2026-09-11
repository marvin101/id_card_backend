from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.file_storage import get_storage_public_url
from app.core.rate_limit import enforce_public_verification_rate_limit
from app.core.public_credentials import (
    PublicCredentialError,
    decode_public_credential,
    normalized_utc,
    public_credential_status,
    rotate_public_credential,
)
from app.core.school_access import get_active_school, require_school_admin
from app.core.security import get_current_user
from app.core.student_audit import record_student_audit
from app.models.school import School
from app.models.student import Student
from app.models.users import User
from app.schemas.public_verification import (
    DEFAULT_PUBLIC_VERIFICATION_FIELDS,
    PUBLIC_VERIFICATION_FIELDS,
    PublicStudentVerificationView,
    PublicVerificationFieldOption,
    PublicVerificationSchool,
    PublicVerificationSettingsResponse,
    PublicVerificationSettingsUpdate,
    PublicVerificationValue,
    StudentVerificationLinkResponse,
    StudentVerificationLinkUpdate,
)


management_router = APIRouter(
    prefix="/schools/{school_uuid}/public-verification",
    tags=["Public Student Verification"],
)
student_router = APIRouter(
    prefix="/schools/{school_uuid}/students/{student_uuid}/public-verification",
    tags=["Public Student Verification"],
)
public_router = APIRouter(
    prefix="/public/verifications",
    tags=["Public Student Verification"],
)
_ADMIN_DETAIL = "Only a school or platform administrator can manage public verification"
_NOT_FOUND_DETAIL = "Verification record not found"


def _settings_response(school: School) -> PublicVerificationSettingsResponse:
    return PublicVerificationSettingsResponse(
        enabled=school.public_verification_enabled,
        fields=school.public_verification_fields
        or DEFAULT_PUBLIC_VERIFICATION_FIELDS,
        validity_days=school.public_verification_validity_days,
        available_fields=[
            PublicVerificationFieldOption(key=key, label=label)
            for key, label in PUBLIC_VERIFICATION_FIELDS.items()
        ],
    )


def _student(db: Session, school_id: int, student_uuid: UUID) -> Student:
    value = db.execute(
        select(Student).where(
            Student.uuid == student_uuid,
            Student.school_id == school_id,
            Student.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if value is None:
        raise HTTPException(status_code=404, detail="Student not found")
    return value


@management_router.get("", response_model=PublicVerificationSettingsResponse)
def get_public_verification_settings(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(
        db,
        current_user,
        school.id,
        _ADMIN_DETAIL,
    )
    return _settings_response(school)


@management_router.put("", response_model=PublicVerificationSettingsResponse)
def update_public_verification_settings(
    school_uuid: UUID,
    payload: PublicVerificationSettingsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(
        db,
        current_user,
        school.id,
        _ADMIN_DETAIL,
    )
    school.public_verification_enabled = payload.enabled
    school.public_verification_fields = payload.fields
    if payload.validity_days is not None:
        school.public_verification_validity_days = payload.validity_days
    db.commit()
    return _settings_response(school)


def _link_response(student: Student) -> StudentVerificationLinkResponse:
    return StudentVerificationLinkResponse(
        enabled=student.public_verification_enabled,
        verification_url=student.verification_url,
        credential_status=public_credential_status(student),
        credential_version=student.public_credential_version,
        issued_at=student.public_credential_issued_at,
        expires_at=student.public_credential_expires_at,
    )


@student_router.get("", response_model=StudentVerificationLinkResponse)
def get_student_verification_link(
    school_uuid: UUID,
    student_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(db, current_user, school.id, _ADMIN_DETAIL)
    student = _student(db, school.id, student_uuid)
    return _link_response(student)


@student_router.put("", response_model=StudentVerificationLinkResponse)
def update_student_verification_link(
    school_uuid: UUID,
    student_uuid: UUID,
    payload: StudentVerificationLinkUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(db, current_user, school.id, _ADMIN_DETAIL)
    student = _student(db, school.id, student_uuid)
    previous = student.public_verification_enabled
    if payload.enabled and public_credential_status(student) == "expired":
        rotate_public_credential(student, school.public_verification_validity_days)
    else:
        student.public_verification_enabled = payload.enabled
    record_student_audit(
        db,
        student=student,
        actor=current_user,
        event_type="public_verification_enabled" if payload.enabled else "public_verification_disabled",
        field_name="public_verification_enabled",
        old_value=previous,
        new_value=payload.enabled,
    )
    db.commit()
    return _link_response(student)


@student_router.post(
    "/regenerate-link", response_model=StudentVerificationLinkResponse
)
def regenerate_student_verification_link(
    school_uuid: UUID,
    student_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = get_active_school(db, school_uuid)
    require_school_admin(db, current_user, school.id, _ADMIN_DETAIL)
    student = _student(db, school.id, student_uuid)
    rotate_public_credential(student, school.public_verification_validity_days)
    record_student_audit(
        db,
        student=student,
        actor=current_user,
        event_type="public_verification_link_regenerated",
    )
    db.commit()
    return _link_response(student)


def _public_value(student: Student, key: str) -> str:
    return {
        "full_name": student.full_name,
        "admission_no": student.admission_no,
        "roll_no": student.roll_no or "",
        "stream": student.stream or "",
        "session": student.academic_session.name,
        "class": student.school_class.name,
        "section": student.section.name,
    }.get(key, "")


@public_router.get("/{token}", response_model=PublicStudentVerificationView)
def get_public_student_verification(
    token: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    enforce_public_verification_rate_limit(request)
    response.headers["Cache-Control"] = "no-store"
    if not 20 <= len(token) <= 2048:
        raise HTTPException(
            status_code=404,
            detail=_NOT_FOUND_DETAIL,
            headers={"Cache-Control": "no-store"},
        )
    signed_payload = None
    lookup_token = token
    if token.startswith("c1."):
        try:
            signed_payload = decode_public_credential(token)
            lookup_token = signed_payload["jti"]
        except (PublicCredentialError, KeyError, TypeError, ValueError):
            raise HTTPException(
                status_code=404,
                detail=_NOT_FOUND_DETAIL,
                headers={"Cache-Control": "no-store"},
            )

    student = db.execute(
        select(Student)
        .options(
            selectinload(Student.school),
            selectinload(Student.academic_session),
            selectinload(Student.school_class),
            selectinload(Student.section),
        )
        .where(
            Student.public_verification_token == lookup_token,
            Student.public_verification_enabled.is_(True),
            Student.is_active.is_(True),
        )
    ).scalar_one_or_none()
    if (
        student is None
        or not student.school.is_active
        or not student.school.public_verification_enabled
    ):
        raise HTTPException(
            status_code=404,
            detail=_NOT_FOUND_DETAIL,
            headers={"Cache-Control": "no-store"},
        )

    if signed_payload is not None:
        expires_at = datetime.fromtimestamp(signed_payload["exp"], timezone.utc)
        if (
            signed_payload["ver"] != student.public_credential_version
            or int(expires_at.timestamp())
            != int(normalized_utc(student.public_credential_expires_at).timestamp())
        ):
            raise HTTPException(
                status_code=404,
                detail=_NOT_FOUND_DETAIL,
                headers={"Cache-Control": "no-store"},
            )
    elif public_credential_status(student) != "active":
        # Legacy opaque URLs remain valid during rollout, but they are still
        # constrained by the new server-side expiry.
        raise HTTPException(
            status_code=404,
            detail=_NOT_FOUND_DETAIL,
            headers={"Cache-Control": "no-store"},
        )

    configured = student.school.public_verification_fields or []
    fields = [
        PublicVerificationValue(
            key=key,
            label=PUBLIC_VERIFICATION_FIELDS[key],
            value=_public_value(student, key),
        )
        for key in configured
        if key in PUBLIC_VERIFICATION_FIELDS and key != "photo"
    ]
    return PublicStudentVerificationView(
        school=PublicVerificationSchool(
            school_name=student.school.school_name,
            school_code=student.school.school_code,
            logo_url=get_storage_public_url(student.school.logo_path),
        ),
        verification_status=student.verification_status,
        lifecycle_status=student.lifecycle_status,
        verified_at=student.verified_at,
        photo_url=(
            get_storage_public_url(student.photo_path)
            if "photo" in configured
            else None
        ),
        credential_status="active",
        credential_version=student.public_credential_version,
        credential_issued_at=student.public_credential_issued_at,
        credential_expires_at=student.public_credential_expires_at,
        signature_verified=signed_payload is not None,
        fields=fields,
    )
