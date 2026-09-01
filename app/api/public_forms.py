import logging
import secrets
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.custom_fields import replace_student_custom_fields, validate_student_custom_fields
from app.core.database import get_db
from app.core.file_storage import (
    ALLOWED_IMAGE_TYPES,
    MAX_STUDENT_PHOTO_SIZE,
    delete_storage_object,
    get_storage_public_url,
    managed_student_photo_storage_path,
    save_student_photo,
)
from app.core.rate_limit import enforce_public_form_rate_limit
from app.core.school_access import get_active_school, require_school_admin
from app.core.security import get_current_user
from app.core.student_audit import record_student_audit
from app.models.academic_session import AcademicSession
from app.models.custom_field import CustomFieldDefinition
from app.models.public_form import PublicForm
from app.models.school_class import SchoolClass
from app.models.section import Section
from app.models.student import Student
from app.models.users import User
from app.schemas.public_form import (
    PublicField,
    PublicFormConfigResponse,
    PublicFormConfigWrite,
    PublicFormView,
    PublicSubmissionResponse,
    PublicStudentInput,
    REQUIRED_SYSTEM_FIELD_KEYS,
)
from app.schemas.student import StudentCustomFieldInput

logger = logging.getLogger(__name__)

management_router = APIRouter(prefix="/schools/{school_uuid}/public-form", tags=["Public Forms"])
public_router = APIRouter(prefix="/public/forms", tags=["Public Forms"])

SYSTEM_FIELDS = {
    "session_uuid": ("Academic session", "select"),
    "class_uuid": ("Class", "select"),
    "section_uuid": ("Section", "select"),
    "admission_no": ("Admission number", "text"),
    "roll_no": ("Roll number", "text"),
    "stream": ("Stream", "text"),
    "full_name": ("Full name", "text"),
    "father_name": ("Father's name", "text"),
    "mother_name": ("Mother's name", "text"),
    "dob": ("Date of birth", "date"),
    "gender": ("Gender", "text"),
    "blood_group": ("Blood group", "text"),
    "mobile": ("Mobile", "phone"),
    "aadhaar": ("Aadhaar", "text"),
    "address": ("Address", "multiline"),
}


def _active_form(db: Session, token: str) -> PublicForm:
    form = db.execute(
        select(PublicForm).where(PublicForm.public_token == token, PublicForm.is_active.is_(True))
    ).scalar_one_or_none()
    if form is None or not form.is_active or (form.expires_at is not None and form.expires_at <= datetime.now(timezone.utc)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Public form not found")
    return form


def _manager(db: Session, school_uuid: UUID, user: User):
    school = get_active_school(db, school_uuid)
    require_school_admin(db, user, school.id, "Only a platform or school administrator can manage public forms")
    return school


def _validate_custom_selection(db: Session, school_id: int, uuids: list[UUID]) -> list[str]:
    if not uuids:
        return []
    definitions = db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.uuid.in_(uuids),
            CustomFieldDefinition.school_id == school_id,
            CustomFieldDefinition.entity_type == "student",
            CustomFieldDefinition.is_active.is_(True),
        )
    ).scalars().all()
    if len(definitions) != len(uuids):
        raise HTTPException(status_code=422, detail="Unknown, inactive, or cross-school custom field selected")
    return [str(value) for value in uuids]


@management_router.get("", response_model=PublicFormConfigResponse | None)
def get_public_form_config(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = _manager(db, school_uuid, current_user)
    return db.execute(select(PublicForm).where(PublicForm.school_id == school.id)).scalar_one_or_none()


@management_router.put("", response_model=PublicFormConfigResponse)
def save_public_form_config(
    school_uuid: UUID,
    payload: PublicFormConfigWrite,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = _manager(db, school_uuid, current_user)
    selected_custom = _validate_custom_selection(db, school.id, payload.selected_custom_field_uuids)
    form = db.execute(select(PublicForm).where(PublicForm.school_id == school.id)).scalar_one_or_none()
    values = payload.model_dump(exclude={"selected_custom_field_uuids"})
    if form is None:
        form = PublicForm(
            school_id=school.id,
            public_token=secrets.token_urlsafe(32),
            created_by_user_id=current_user.id,
            **values,
        )
        db.add(form)
    else:
        for key, value in values.items():
            setattr(form, key, value)
    form.selected_custom_field_uuids = selected_custom
    form.updated_by_user_id = current_user.id
    db.commit()
    db.refresh(form)
    return form


@management_router.post("/regenerate-link", response_model=PublicFormConfigResponse)
def regenerate_public_form_link(
    school_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = _manager(db, school_uuid, current_user)
    form = db.execute(select(PublicForm).where(PublicForm.school_id == school.id)).scalar_one_or_none()
    if form is None:
        raise HTTPException(status_code=404, detail="Public form is not configured")
    form.public_token = secrets.token_urlsafe(32)
    form.updated_by_user_id = current_user.id
    db.commit()
    db.refresh(form)
    return form


def _public_fields(db: Session, form: PublicForm) -> list[PublicField]:
    sessions = db.execute(select(AcademicSession).where(AcademicSession.school_id == form.school_id)).scalars().all()
    classes = db.execute(select(SchoolClass).where(SchoolClass.school_id == form.school_id)).scalars().all()
    class_ids = [item.id for item in classes]
    sections = db.execute(select(Section).where(Section.class_id.in_(class_ids))).scalars().all() if class_ids else []
    class_uuid_by_id = {item.id: str(item.uuid) for item in classes}
    option_map = {
        "session_uuid": [{"value": str(item.uuid), "label": item.name} for item in sessions],
        "class_uuid": [{"value": str(item.uuid), "label": item.name} for item in classes],
        "section_uuid": [
            {"value": str(item.uuid), "label": item.name, "parent_uuid": class_uuid_by_id[item.class_id]}
            for item in sections if item.class_id in class_uuid_by_id
        ],
    }
    fields = [
        PublicField(
            key=key,
            label=SYSTEM_FIELDS[key][0],
            data_type=SYSTEM_FIELDS[key][1],
            required=key in REQUIRED_SYSTEM_FIELD_KEYS or form.require_all_fields,
            kind="system",
            options=option_map.get(key),
        )
        for key in form.selected_system_fields if key in SYSTEM_FIELDS
    ]
    selected = [UUID(value) for value in form.selected_custom_field_uuids]
    definitions = db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.uuid.in_(selected),
            CustomFieldDefinition.school_id == form.school_id,
            CustomFieldDefinition.entity_type == "student",
            CustomFieldDefinition.is_active.is_(True),
        ).order_by(CustomFieldDefinition.display_order)
    ).scalars().all() if selected else []
    fields.extend(
        PublicField(
            key=definition.field_key,
            field_uuid=definition.uuid,
            label=definition.label,
            data_type=definition.data_type,
            required=definition.is_required or form.require_all_fields,
            kind="custom",
        )
        for definition in definitions
    )
    return fields


@public_router.get("/{token}", response_model=PublicFormView)
def get_public_form(token: str, request: Request, db: Session = Depends(get_db)):
    enforce_public_form_rate_limit(request, submission=False)
    form = _active_form(db, token)
    school = form.school
    if not school.is_active:
        raise HTTPException(status_code=404, detail="Public form not found")
    return PublicFormView(
        school_name=school.school_name,
        school_logo_url=get_storage_public_url(school.logo_path),
        title=form.title,
        instructions=form.instructions,
        fields=_public_fields(db, form),
        allow_photo=form.allow_photo,
        supported_photo_types=list(ALLOWED_IMAGE_TYPES),
        max_photo_size_bytes=MAX_STUDENT_PHOTO_SIZE,
        success_message=form.success_message,
    )


def _required(value, label: str):
    if value is None or (isinstance(value, str) and not value.strip()):
        raise HTTPException(status_code=422, detail=f"{label} is required")
    return value


@public_router.post("/{token}/submissions", response_model=PublicSubmissionResponse, status_code=201)
async def submit_public_form(
    token: str,
    request: Request,
    student_data_json: str = Form(...),
    photo: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
):
    enforce_public_form_rate_limit(request, submission=True)
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > settings.public_form_max_request_bytes:
        raise HTTPException(status_code=413, detail="Submission is too large")
    form = _active_form(db, token)
    try:
        payload = PublicStudentInput.model_validate_json(student_data_json)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid student data") from exc

    selected_system = set(form.selected_system_fields)
    supplied_system = payload.model_fields_set - {"custom_fields"}
    unexpected = supplied_system - selected_system
    if unexpected:
        raise HTTPException(status_code=422, detail=f"Field is not enabled for this form: {sorted(unexpected)[0]}")
    for key in selected_system:
        if key in REQUIRED_SYSTEM_FIELD_KEYS or form.require_all_fields:
            _required(getattr(payload, key), SYSTEM_FIELDS[key][0])

    supplied_custom = {item.field_uuid for item in payload.custom_fields}
    selected_custom = {UUID(value) for value in form.selected_custom_field_uuids}
    if supplied_custom - selected_custom:
        raise HTTPException(status_code=422, detail="Custom field is not enabled for this form")

    session = db.execute(select(AcademicSession).where(AcademicSession.uuid == payload.session_uuid, AcademicSession.school_id == form.school_id)).scalar_one_or_none()
    school_class = db.execute(select(SchoolClass).where(SchoolClass.uuid == payload.class_uuid, SchoolClass.school_id == form.school_id)).scalar_one_or_none()
    if session is None or school_class is None:
        raise HTTPException(status_code=422, detail="Invalid academic selection")
    section = db.execute(select(Section).where(Section.uuid == payload.section_uuid, Section.class_id == school_class.id)).scalar_one_or_none()
    if section is None:
        raise HTTPException(status_code=422, detail="Invalid academic selection")

    if db.execute(select(Student).where(Student.school_id == form.school_id, Student.admission_no == payload.admission_no)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Admission number already exists in this school")
    if payload.roll_no and db.execute(select(Student).where(Student.school_id == form.school_id, Student.session_id == session.id, Student.class_id == school_class.id, Student.roll_no == payload.roll_no)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Roll number already exists for this class in this academic session")

    custom_inputs = [StudentCustomFieldInput(field_uuid=item.field_uuid, value=item.value) for item in payload.custom_fields]
    validated_custom = validate_student_custom_fields(db, form.school_id, custom_inputs, require_all=False)
    selected_definitions = {definition.uuid: definition for definition, _ in validated_custom}
    active_selected = db.execute(select(CustomFieldDefinition).where(
        CustomFieldDefinition.uuid.in_(selected_custom), CustomFieldDefinition.school_id == form.school_id,
        CustomFieldDefinition.entity_type == "student", CustomFieldDefinition.is_active.is_(True)
    )).scalars().all() if selected_custom else []
    for definition in active_selected:
        if (definition.is_required or form.require_all_fields) and definition.uuid not in selected_definitions:
            raise HTTPException(status_code=422, detail=f"{definition.label} is required")

    if photo is not None and not form.allow_photo:
        raise HTTPException(status_code=422, detail="Photo upload is not enabled for this form")

    student = Student(
        school_id=form.school_id, session_id=session.id, class_id=school_class.id, section_id=section.id,
        admission_no=payload.admission_no, roll_no=payload.roll_no, stream=payload.stream,
        full_name=payload.full_name, father_name=payload.father_name, mother_name=payload.mother_name,
        dob=payload.dob, gender=payload.gender, blood_group=payload.blood_group,
        mobile=payload.mobile, aadhaar=payload.aadhaar, address=payload.address,
        photo_path=None, verification_status="pending", correction_note=None,
        verified_at=None, verified_by_user_id=None, printed_at=None, printed_by_user_id=None, print_count=0,
    )
    uploaded_path = None
    try:
        db.add(student)
        replace_student_custom_fields(db, student, validated_custom)
        db.flush()
        if photo is not None:
            content = await photo.read(MAX_STUDENT_PHOTO_SIZE + 1)
            if not content:
                raise HTTPException(status_code=422, detail="Uploaded photo is empty")
            try:
                student.photo_path = save_student_photo(student.uuid, content, photo.content_type)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            uploaded_path = managed_student_photo_storage_path(student.photo_path, student.uuid)
        record_student_audit(
            db, student=student, actor=None, event_type="student_created",
            new_value={"admission_no": student.admission_no, "full_name": student.full_name, "source": "public_form"},
            note="Submitted through Public Form",
        )
        db.commit()
    except HTTPException:
        db.rollback()
        if uploaded_path:
            try: delete_storage_object(uploaded_path)
            except Exception: logger.warning("Could not clean public-form photo after rollback")
        raise
    except IntegrityError as exc:
        db.rollback()
        if uploaded_path:
            try: delete_storage_object(uploaded_path)
            except Exception: logger.warning("Could not clean public-form photo after failed commit")
        raise HTTPException(status_code=409, detail="Student number already exists") from exc
    except Exception:
        db.rollback()
        if uploaded_path:
            try: delete_storage_object(uploaded_path)
            except Exception: logger.warning("Could not clean public-form photo after failed commit")
        raise
    return PublicSubmissionResponse(message=form.success_message or "Thank you. Your submission is pending review.")
