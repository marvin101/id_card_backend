import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.database import get_db
from app.core.file_storage import get_storage_public_url
from app.core.rate_limit import enforce_public_verification_rate_limit
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
    db.commit()
    return _settings_response(school)


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
    return StudentVerificationLinkResponse(
        enabled=student.public_verification_enabled,
        verification_url=student.verification_url,
    )


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
    return StudentVerificationLinkResponse(
        enabled=student.public_verification_enabled,
        verification_url=student.verification_url,
    )


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
    student.public_verification_token = secrets.token_urlsafe(32)
    student.public_verification_enabled = True
    record_student_audit(
        db,
        student=student,
        actor=current_user,
        event_type="public_verification_link_regenerated",
    )
    db.commit()
    return StudentVerificationLinkResponse(
        enabled=True,
        verification_url=student.verification_url,
    )


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
    if not 20 <= len(token) <= 96:
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
            Student.public_verification_token == token,
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
        fields=fields,
    )
