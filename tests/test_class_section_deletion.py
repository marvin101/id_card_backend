from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import classes as classes_api
from app.api import sections as sections_api


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _SequenceSession:
    def __init__(self, *results):
        self._results = list(results)
        self.statements = []
        self.deleted = []
        self.commits = 0

    def execute(self, statement):
        self.statements.append(statement)

        if not self._results:
            raise AssertionError("Unexpected database query")

        return _Result(self._results.pop(0))

    def delete(self, value):
        self.deleted.append(value)

    def commit(self):
        self.commits += 1


def _patch_school_admin(monkeypatch, api_module, school):
    monkeypatch.setattr(
        api_module,
        "get_active_school",
        lambda _db, _school_uuid: school,
    )

    monkeypatch.setattr(
        api_module,
        "require_school_admin",
        lambda *_args, **_kwargs: None,
    )


def _assert_active_student_filter(statement):
    sql = str(statement).lower()

    assert "students.is_active is true" in sql


# ==========================================================
# Section deletion
# ==========================================================


def test_delete_section_ignores_inactive_students(monkeypatch):
    school_uuid = uuid4()
    class_uuid = uuid4()
    section_uuid = uuid4()

    school = SimpleNamespace(
        id=10,
        uuid=school_uuid,
        is_active=True,
    )

    school_class = SimpleNamespace(
        id=20,
        uuid=class_uuid,
        school_id=school.id,
    )

    section = SimpleNamespace(
        id=30,
        uuid=section_uuid,
        class_id=school_class.id,
    )

    # The student-existence query returns None because only inactive
    # students reference this section and the query must filter them out.
    session = _SequenceSession(
        school_class,
        section,
        None,
    )

    _patch_school_admin(
        monkeypatch,
        sections_api,
        school,
    )

    result = sections_api.delete_section(
        school_uuid=school_uuid,
        class_uuid=class_uuid,
        section_uuid=section_uuid,
        db=session,
        current_user=SimpleNamespace(id=1),
    )

    assert result is None
    assert session.deleted == [section]
    assert session.commits == 1

    student_guard_query = session.statements[-1]
    _assert_active_student_filter(student_guard_query)


def test_delete_section_rejects_active_students(monkeypatch):
    school_uuid = uuid4()
    class_uuid = uuid4()
    section_uuid = uuid4()

    school = SimpleNamespace(
        id=10,
        uuid=school_uuid,
        is_active=True,
    )

    school_class = SimpleNamespace(
        id=20,
        uuid=class_uuid,
        school_id=school.id,
    )

    section = SimpleNamespace(
        id=30,
        uuid=section_uuid,
        class_id=school_class.id,
    )

    # A matching active student is found by the guarded query.
    session = _SequenceSession(
        school_class,
        section,
        999,
    )

    _patch_school_admin(
        monkeypatch,
        sections_api,
        school,
    )

    with pytest.raises(HTTPException) as exc_info:
        sections_api.delete_section(
            school_uuid=school_uuid,
            class_uuid=class_uuid,
            section_uuid=section_uuid,
            db=session,
            current_user=SimpleNamespace(id=1),
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail
        == "Cannot delete section because students are assigned to it"
    )

    assert session.deleted == []
    assert session.commits == 0

    student_guard_query = session.statements[-1]
    _assert_active_student_filter(student_guard_query)


# ==========================================================
# Class deletion
# ==========================================================


def test_delete_class_ignores_inactive_students(monkeypatch):
    school_uuid = uuid4()
    class_uuid = uuid4()

    school = SimpleNamespace(
        id=10,
        uuid=school_uuid,
        is_active=True,
    )

    school_class = SimpleNamespace(
        id=20,
        uuid=class_uuid,
        school_id=school.id,
    )

    # The student-existence query returns None because only inactive
    # students reference this class and the query must filter them out.
    session = _SequenceSession(
        school_class,
        None,
    )

    _patch_school_admin(
        monkeypatch,
        classes_api,
        school,
    )

    result = classes_api.delete_class(
        school_uuid=school_uuid,
        class_uuid=class_uuid,
        db=session,
        current_user=SimpleNamespace(id=1),
    )

    assert result is None
    assert session.deleted == [school_class]
    assert session.commits == 1

    student_guard_query = session.statements[-1]
    _assert_active_student_filter(student_guard_query)


def test_delete_class_rejects_active_students(monkeypatch):
    school_uuid = uuid4()
    class_uuid = uuid4()

    school = SimpleNamespace(
        id=10,
        uuid=school_uuid,
        is_active=True,
    )

    school_class = SimpleNamespace(
        id=20,
        uuid=class_uuid,
        school_id=school.id,
    )

    # A matching active student is found by the guarded query.
    session = _SequenceSession(
        school_class,
        999,
    )

    _patch_school_admin(
        monkeypatch,
        classes_api,
        school,
    )

    with pytest.raises(HTTPException) as exc_info:
        classes_api.delete_class(
            school_uuid=school_uuid,
            class_uuid=class_uuid,
            db=session,
            current_user=SimpleNamespace(id=1),
        )

    assert exc_info.value.status_code == 409
    assert (
        exc_info.value.detail
        == "Cannot delete class because students are assigned to it"
    )

    assert session.deleted == []
    assert session.commits == 0

    student_guard_query = session.statements[-1]
    _assert_active_student_filter(student_guard_query)