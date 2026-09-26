from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.api import classes as classes_api


class _Result:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _Session:
    def __init__(self, classes):
        self.classes = classes
        self.commits = 0

    def execute(self, _statement):
        return _Result(self.classes)

    def commit(self):
        self.commits += 1


def _school(monkeypatch):
    school = SimpleNamespace(id=7, uuid=uuid4())
    monkeypatch.setattr(classes_api, "get_active_school", lambda *_args: school)
    monkeypatch.setattr(classes_api, "require_school_admin", lambda *_args: None)
    return school


def test_reorder_persists_every_class_atomically(monkeypatch):
    school = _school(monkeypatch)
    first = SimpleNamespace(uuid=uuid4(), school_id=school.id, sort_order=0)
    second = SimpleNamespace(uuid=uuid4(), school_id=school.id, sort_order=1)
    session = _Session([first, second])
    result = classes_api.reorder_classes(
        school_uuid=school.uuid,
        payload=classes_api.SchoolClassReorder(class_uuids=[second.uuid, first.uuid]),
        db=session,
        current_user=SimpleNamespace(id=1),
    )
    assert result == [second, first]
    assert (second.sort_order, first.sort_order) == (0, 1)
    assert session.commits == 1


def test_reorder_rejects_partial_or_duplicate_lists(monkeypatch):
    school = _school(monkeypatch)
    item = SimpleNamespace(uuid=uuid4(), school_id=school.id, sort_order=0)
    for order in ([item.uuid, item.uuid], []):
        session = _Session([item])
        with pytest.raises(HTTPException) as error:
            classes_api.reorder_classes(
                school_uuid=school.uuid,
                payload=classes_api.SchoolClassReorder(class_uuids=order),
                db=session,
                current_user=SimpleNamespace(id=1),
            )
        assert error.value.status_code == 422
        assert session.commits == 0


def test_reorder_preserves_admin_authorization(monkeypatch):
    school = SimpleNamespace(id=7, uuid=uuid4())
    monkeypatch.setattr(classes_api, "get_active_school", lambda *_args: school)

    def denied(*_args):
        raise HTTPException(status_code=403, detail="denied")

    monkeypatch.setattr(classes_api, "require_school_admin", denied)
    with pytest.raises(HTTPException) as error:
        classes_api.reorder_classes(
            school_uuid=school.uuid,
            payload=classes_api.SchoolClassReorder(class_uuids=[]),
            db=_Session([]),
            current_user=SimpleNamespace(id=1),
        )
    assert error.value.status_code == 403
