import logging
import secrets
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.custom_fields import replace_student_custom_fields, validate_student_custom_fields
from app.core.database import get_db
from app.core.file_storage import (
    ALLOWED_IMAGE_TYPES,
    MAX_STUDENT_PHOTO_SIZE,
    StorageError,
    delete_storage_object,
    download_storage_object,
    get_storage_public_url,
    managed_student_photo_storage_path,
    save_student_photo,
    save_bulk_photo_temp,
)
from app.core.rate_limit import enforce_public_form_rate_limit
from app.core.school_access import get_active_school, require_school_admin
from app.core.security import get_current_user
from app.core.public_credentials import initialize_public_credential
from app.core.student_audit import record_student_audit
from app.core.student_field_config import (
    BUILTIN_STUDENT_FIELDS,
    effective_student_field_map,
    effective_student_fields,
    reject_disabled_student_fields,
    validate_required_student_fields,
)
from app.models.academic_session import AcademicSession
from app.models.custom_field import CustomFieldDefinition
from app.models.public_form import PublicForm, PublicFormSubmission
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
    PublicSubmissionItem,
    PublicSubmissionList,
    PublicSubmissionReject,
    PublicStudentInput,
)
from app.schemas.student import StudentCustomFieldInput

logger = logging.getLogger(__name__)

management_router = APIRouter(prefix="/schools/{school_uuid}/public-form", tags=["Public Forms"])
public_router = APIRouter(prefix="/public/forms", tags=["Public Forms"])

SYSTEM_FIELDS = {key: (field.label, field.data_type) for key, field in BUILTIN_STUDENT_FIELDS.items()}


def _active_form(db: Session, token: str) -> PublicForm:
    form = db.execute(
        select(PublicForm).where(PublicForm.public_token == token, PublicForm.is_active.is_(True))
    ).scalar_one_or_none()
    if form is None or not form.is_active or (form.expires_at is not None and form.expires_at <= datetime.now(timezone.utc)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Public form not found")
    if not form.school.is_active:
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
    config = effective_student_field_map(db, school.id)
    disabled = sorted(key for key in payload.selected_system_fields if not config[key].enabled)
    if disabled:
        raise HTTPException(status_code=422, detail=f"Built-in student field is disabled: {disabled[0]}")
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
    selected_system = set(form.selected_system_fields)
    effective_system = [
        field for field in effective_student_fields(db, form.school_id)
        if field.enabled and (field.key in selected_system or field.required)
    ]
    fields = [
        PublicField(
            key=field.key,
            label=field.label,
            data_type=field.data_type,
            required=field.required or form.require_all_fields,
            kind="system",
            options=option_map.get(field.key),
        )
        for field in effective_system
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
    enforce_public_form_rate_limit(request, submission=False, token=token)
    form = _active_form(db, token)
    school = form.school
    return PublicFormView(
        school_name=school.school_name,
        school_logo_url=get_storage_public_url(school.logo_path),
        title=form.title,
        instructions=form.instructions,
        fields=_public_fields(db, form),
        allow_photo=form.allow_photo,
        photo_required=form.allow_photo and form.require_all_fields,
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
    enforce_public_form_rate_limit(request, submission=True, token=token)
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > settings.public_form_max_request_bytes:
        raise HTTPException(status_code=413, detail="Submission is too large")
    form = _active_form(db, token)
    try:
        payload = PublicStudentInput.model_validate_json(student_data_json)
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid student data") from exc

    if form.require_all_fields and form.allow_photo and photo is None:
        raise HTTPException(status_code=422, detail="Photo is required")

    field_config = effective_student_field_map(db, form.school_id)
    selected_system = {
        key for key in form.selected_system_fields
        if key in field_config and field_config[key].enabled
    }
    selected_system.update(
        key for key, field in field_config.items() if field.enabled and field.required
    )
    supplied_system = payload.model_fields_set - {"custom_fields"}
    unexpected = supplied_system - selected_system
    if unexpected:
        raise HTTPException(status_code=422, detail=f"Field is not enabled for this form: {sorted(unexpected)[0]}")
    for key in selected_system:
        if field_config[key].required or form.require_all_fields:
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

    submission_uuid = uuid4()
    submission = PublicFormSubmission(
        uuid=submission_uuid,
        form_id=form.id,
        school_id=form.school_id,
        payload=payload.model_dump(mode="json"),
        status="pending",
        reference=f"PF-{secrets.token_hex(6).upper()}",
    )
    uploaded_path = None
    try:
        db.add(submission)
        if photo is not None:
            content = await photo.read(MAX_STUDENT_PHOTO_SIZE + 1)
            if not content:
                raise HTTPException(status_code=422, detail="Uploaded photo is empty")
            try:
                submission.photo_path = save_bulk_photo_temp(
                    school_uuid=form.school.uuid,
                    upload_uuid=submission_uuid,
                    content=content,
                    content_type=photo.content_type,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except StorageError as exc:
                logger.error("Public-form photo storage failed", exc_info=True)
                raise HTTPException(
                    status_code=502,
                    detail="Photo storage is currently unavailable.",
                ) from exc
            uploaded_path = submission.photo_path
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
    return PublicSubmissionResponse(
        message=form.success_message or "Thank you. Your submission is pending review.",
        reference=submission.reference,
    )


def _submission_item(submission: PublicFormSubmission) -> PublicSubmissionItem:
    return PublicSubmissionItem(
        uuid=submission.uuid,
        reference=submission.reference,
        status=submission.status,
        payload=submission.payload,
        photo_url=get_storage_public_url(submission.photo_path),
        created_at=submission.created_at,
        reviewed_at=submission.reviewed_at,
        rejection_note=submission.rejection_note,
        student_uuid=submission.student.uuid if submission.student_id and getattr(submission, "student", None) else None,
    )


@management_router.get("/submissions", response_model=PublicSubmissionList)
def list_public_form_submissions(
    school_uuid: UUID,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 25,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    school = _manager(db, school_uuid, current_user)
    if status_filter not in {None, "pending", "approved", "rejected"}:
        raise HTTPException(status_code=422, detail="Invalid submission status")
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    filters = [PublicFormSubmission.school_id == school.id]
    if status_filter:
        filters.append(PublicFormSubmission.status == status_filter)
    total = db.scalar(select(func.count()).select_from(PublicFormSubmission).where(*filters)) or 0
    items = db.execute(
        select(PublicFormSubmission).where(*filters)
        .order_by(PublicFormSubmission.created_at.desc(), PublicFormSubmission.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return PublicSubmissionList(items=[_submission_item(item) for item in items], total=total, page=page, page_size=page_size)


def _managed_submission(db: Session, school_id: int, submission_uuid: UUID, *, lock: bool = False) -> PublicFormSubmission:
    query = select(PublicFormSubmission).where(
        PublicFormSubmission.uuid == submission_uuid,
        PublicFormSubmission.school_id == school_id,
    )
    if lock:
        query = query.with_for_update()
    submission = db.execute(query).scalar_one_or_none()
    if submission is None:
        raise HTTPException(status_code=404, detail="Submission not found")
    return submission


@management_router.get("/submissions/{submission_uuid}", response_model=PublicSubmissionItem)
def get_public_form_submission(school_uuid: UUID, submission_uuid: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    school = _manager(db, school_uuid, current_user)
    return _submission_item(_managed_submission(db, school.id, submission_uuid))


@management_router.post("/submissions/{submission_uuid}/approve", response_model=PublicSubmissionItem)
def approve_public_form_submission(school_uuid: UUID, submission_uuid: UUID, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    school = _manager(db, school_uuid, current_user)
    submission = _managed_submission(db, school.id, submission_uuid, lock=True)
    if submission.status != "pending":
        raise HTTPException(status_code=409, detail="Submission has already been processed")
    payload = PublicStudentInput.model_validate(submission.payload)
    field_config = effective_student_field_map(db, school.id)
    reject_disabled_student_fields(field_config, payload.model_fields_set)
    validate_required_student_fields(field_config, payload.model_dump())
    custom_inputs = [StudentCustomFieldInput(field_uuid=item.field_uuid, value=item.value) for item in payload.custom_fields]
    validated_custom = validate_student_custom_fields(db, school.id, custom_inputs, require_all=True)
    session = db.execute(select(AcademicSession).where(AcademicSession.uuid == payload.session_uuid, AcademicSession.school_id == school.id)).scalar_one_or_none()
    school_class = db.execute(select(SchoolClass).where(SchoolClass.uuid == payload.class_uuid, SchoolClass.school_id == school.id)).scalar_one_or_none()
    if session is None or school_class is None:
        raise HTTPException(status_code=409, detail="Academic session or class is no longer available")
    section = db.execute(select(Section).where(Section.uuid == payload.section_uuid, Section.class_id == school_class.id)).scalar_one_or_none()
    if section is None:
        raise HTTPException(status_code=409, detail="Section is no longer available")
    if db.execute(select(Student).where(Student.school_id == school.id, Student.admission_no == payload.admission_no)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Admission number already exists in this school")
    if payload.roll_no and db.execute(select(Student).where(Student.school_id == school.id, Student.session_id == session.id, Student.class_id == school_class.id, Student.roll_no == payload.roll_no)).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Roll number already exists for this class in this academic session")
    student = Student(
        school_id=school.id, session_id=session.id, class_id=school_class.id, section_id=section.id,
        admission_no=payload.admission_no, roll_no=payload.roll_no, stream=payload.stream,
        full_name=payload.full_name, father_name=payload.father_name, mother_name=payload.mother_name,
        dob=payload.dob, gender=payload.gender, blood_group=payload.blood_group,
        mobile=payload.mobile, aadhaar=payload.aadhaar, address=payload.address, photo_path=None,
    )
    initialize_public_credential(student, getattr(school, "public_verification_validity_days", 365))
    promoted_path = None
    try:
        db.add(student)
        replace_student_custom_fields(db, student, validated_custom)
        db.flush()
        if submission.photo_path:
            content = download_storage_object(submission.photo_path)
            suffix = submission.photo_path.rsplit(".", 1)[-1].lower()
            content_type = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}.get(suffix)
            student.photo_path = save_student_photo(student.uuid, content, content_type)
            promoted_path = managed_student_photo_storage_path(student.photo_path, student.uuid)
        record_student_audit(db, student=student, actor=current_user, event_type="student_created", new_value={"admission_no": student.admission_no, "full_name": student.full_name, "source": "public_form"}, note=f"Approved public submission {submission.reference}")
        submission.status = "approved"
        submission.reviewed_by_user_id = current_user.id
        submission.reviewed_at = datetime.now(timezone.utc)
        submission.student_id = student.id
        old_temp_path = submission.photo_path
        submission.photo_path = None
        db.commit()
        db.refresh(submission)
    except IntegrityError as exc:
        db.rollback()
        if promoted_path:
            delete_storage_object(promoted_path)
        raise HTTPException(status_code=409, detail="Student identity now conflicts with an existing record") from exc
    except Exception:
        db.rollback()
        if promoted_path:
            delete_storage_object(promoted_path)
        raise
    if old_temp_path:
        try:
            delete_storage_object(old_temp_path)
        except Exception:
            logger.warning("Could not clean approved public-form temporary photo", exc_info=True)
    return _submission_item(submission)


@management_router.post("/submissions/{submission_uuid}/reject", response_model=PublicSubmissionItem)
def reject_public_form_submission(school_uuid: UUID, submission_uuid: UUID, payload: PublicSubmissionReject, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    school = _manager(db, school_uuid, current_user)
    submission = _managed_submission(db, school.id, submission_uuid, lock=True)
    if submission.status != "pending":
        raise HTTPException(status_code=409, detail="Submission has already been processed")
    old_temp_path = submission.photo_path
    submission.status = "rejected"
    submission.reviewed_by_user_id = current_user.id
    submission.reviewed_at = datetime.now(timezone.utc)
    submission.rejection_note = payload.note.strip() if payload.note and payload.note.strip() else None
    submission.photo_path = None
    db.commit()
    db.refresh(submission)
    if old_temp_path:
        try:
            delete_storage_object(old_temp_path)
        except Exception:
            logger.warning("Could not clean rejected public-form temporary photo", exc_info=True)
    return _submission_item(submission)
