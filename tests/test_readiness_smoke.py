"""HTTP smoke journeys with real routing/role checks and isolated persistence."""
import io
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.core.student_imports import _read_xlsx, _column_index
from app.main import app
from app.models.school import School
from app.models.student import Student
from app.models.personnel import Personnel
from app.models.public_form import PublicForm
from app.models.user_school_access import UserSchoolAccess
from test_student_lifecycle import _student
from test_personnel_workflow import _personnel
from test_student_imports import _xlsx_bytes


class Result:
    def __init__(self, value=None):
        self.value = value
    def scalar_one_or_none(self):
        return self.value
    def scalars(self):
        return self
    def all(self):
        return [] if self.value is None else [self.value]


class Database:
    def __init__(self, role='school_admin', kind='student', active=True):
        self.school = SimpleNamespace(id=3, uuid=uuid4(), is_active=active)
        self.user = SimpleNamespace(id=5, platform_role=None, is_platform_admin=False)
        self.access = SimpleNamespace(user_id=5, school_id=3, role=role)
        self.record = _student() if kind == 'student' else _personnel()
        if kind != 'student':
            self.record.personnel_type = kind
        self.record.created_at = datetime.now(timezone.utc)
        self.record.updated_at = self.record.created_at
        self.record.session_id = 1
        self.record.class_id = 2
        self.record.section_id = 4
        self.record.custom_field_values = []
        self.added = []
        self.commits = 0
        self.form = SimpleNamespace(is_active=True, expires_at=None, school=self.school)
    def execute(self, statement):
        entity = statement.column_descriptions[0]['entity']
        params = set(statement.compile().params.values())
        if entity is School:
            return Result(self.school if self.school.uuid in params and self.school.is_active else None)
        if entity is UserSchoolAccess:
            return Result(self.access if {self.user.id, self.school.id}.issubset(params) else None)
        if entity in {Student, Personnel}:
            return Result(self.record if {self.record.uuid, self.school.id}.issubset(params) else None)
        if entity is PublicForm:
            return Result(self.form)
        raise AssertionError(f'Unexpected entity {entity}')
    def add(self, event):
        self.added.append(event)
    def commit(self):
        self.commits += 1
    def refresh(self, record):
        pass


@pytest.fixture
def client_for():
    def make(db):
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: db.user
        return TestClient(app)
    yield make
    app.dependency_overrides.clear()


@pytest.mark.parametrize('value', [False, True, None])
def test_operator_cannot_change_student_activation(value, client_for):
    db = Database(role='card_operator')
    with client_for(db) as client:
        response = client.put(f'/schools/{db.school.uuid}/students/{db.record.uuid}', json={'is_active': value})
    assert response.status_code == 403
    assert db.record.is_active is True
    assert db.commits == 0 and db.added == []


def test_admin_activation_is_audited_and_null_is_rejected(client_for):
    db = Database()
    url = f'/schools/{db.school.uuid}/students/{db.record.uuid}'
    with client_for(db) as client:
        assert client.put(url, json={'is_active': None}).status_code == 422
        assert db.commits == 0
        assert client.put(url, json={'is_active': False}).status_code == 200
        assert db.record.is_active is False
        assert client.put(url, json={'is_active': True}).status_code == 200
    assert db.record.is_active is True
    assert db.commits == 2
    assert len([event for event in db.added if event.field_name == 'is_active']) == 2


def test_operator_cannot_edit_inactive_student(client_for):
    db = Database(role='card_operator')
    db.record.is_active = False
    with client_for(db) as client:
        response = client.put(f'/schools/{db.school.uuid}/students/{db.record.uuid}', json={'full_name': 'Changed'})
    assert response.status_code == 403
    assert db.record.full_name == 'Asha Singh' and db.commits == 0


@pytest.mark.parametrize('method', ['get', 'post'])
def test_public_form_inactive_school_rejects_before_writes(method, client_for, monkeypatch):
    db = Database(active=False)
    def forbidden(*args, **kwargs):
        raise AssertionError('storage must not be touched')
    monkeypatch.setattr('app.api.public_forms.save_student_photo', forbidden)
    with client_for(db) as client:
        if method == 'get':
            response = client.get('/public/forms/readiness-test-token')
        else:
            response = client.post('/public/forms/readiness-test-token/submissions', data={'student_data_json': '{}'})
    assert response.status_code == 404
    assert response.json() == {'detail': 'Public form not found'}
    assert db.commits == 0 and db.added == []


@pytest.mark.parametrize('kind', ['student', 'teacher', 'staff'])
def test_http_correction_verification_and_print_journey(kind, client_for):
    db = Database(kind=kind)
    area = 'students' if kind == 'student' else 'personnel'
    url = f'/schools/{db.school.uuid}/{area}/{db.record.uuid}'
    with client_for(db) as client:
        assert client.post(url + '/mark-printed').status_code == 409
        correction = client.patch(url + '/verification', json={'status': 'needs_correction', 'note': 'Fix spelling'})
        assert correction.status_code == 200 and correction.json()['correction_note'] == 'Fix spelling'
        verified = client.patch(url + '/verification', json={'status': 'verified'})
        assert verified.status_code == 200 and verified.json()['verification_status'] == 'verified'
        assert db.record.print_count == 0
        assert client.post(url + '/mark-printed').json()['print_count'] == 1
        assert client.post(url + '/mark-printed').json()['print_count'] == 2
    assert db.record.printed_at is not None
    assert any(event.event_type == 'reprinted' for event in db.added)


@pytest.mark.parametrize('kind', ['student', 'teacher', 'staff'])
def test_teacher_role_cannot_verify_or_print_identity(kind, client_for):
    db = Database(role='teacher', kind=kind)
    area = 'students' if kind == 'student' else 'personnel'
    url = f'/schools/{db.school.uuid}/{area}/{db.record.uuid}'
    with client_for(db) as client:
        assert client.patch(url + '/verification', json={'status': 'verified'}).status_code == 403
        assert client.post(url + '/mark-printed').status_code == 403
    assert db.commits == 0


@pytest.mark.parametrize('reference,status', [('ZZZZZZ1', 422), ('AAA1', 413), ('A0', 422), ('A1junk', 422), ('A1048577', 422)])
def test_xlsx_coordinate_is_bounded_before_allocation(reference, status):
    with pytest.raises(HTTPException) as raised:
        _column_index(reference)
    assert raised.value.status_code == status
    assert _column_index('IV1') == 255


def _replace_sheet(sheet):
    source = zipfile.ZipFile(io.BytesIO(_xlsx_bytes()))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, 'w') as archive:
        for name in source.namelist():
            archive.writestr(name, sheet if name == 'xl/worksheets/sheet1.xml' else source.read(name))
    return output.getvalue()


def test_xlsx_sparse_coordinate_is_rejected_in_real_parser():
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="ZZZZZZ1"><v>1</v></c></row></sheetData></worksheet>'
    with pytest.raises(HTTPException):
        _read_xlsx(_replace_sheet(sheet))


def test_xlsx_row_and_total_cell_limits_are_enforced_during_parse(monkeypatch):
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row><c r="C1"><v>1</v></c></row><row><c r="A2"><v>2</v></c></row></sheetData></worksheet>'
    content = _replace_sheet(sheet)
    monkeypatch.setattr('app.core.student_imports.MAX_IMPORT_CELLS', 2)
    with pytest.raises(HTTPException, match='cell limit'):
        _read_xlsx(content)
    monkeypatch.setattr('app.core.student_imports.MAX_IMPORT_CELLS', 100)
    monkeypatch.setattr('app.core.student_imports.MAX_IMPORT_ROWS', 0)
    with pytest.raises(HTTPException, match='row limit'):
        _read_xlsx(content)


@pytest.mark.parametrize('admissions', [('ABC', 'abc'), (' ABC ', 'abc')])
def test_bulk_student_photos_reject_ambiguous_admissions(admissions):
    from app.api.bulk_student_photos import _student_lookup
    records = [SimpleNamespace(admission_no=value) for value in admissions]
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: records))
    db = SimpleNamespace(execute=lambda statement: result)
    with pytest.raises(HTTPException) as raised:
        _student_lookup(db, 3)
    assert raised.value.status_code == 409


def test_bulk_student_photos_keep_unique_case_insensitive_matching():
    from app.api.bulk_student_photos import _student_lookup
    record = SimpleNamespace(admission_no=' ABC ')
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [record]))
    db = SimpleNamespace(execute=lambda statement: result)
    assert _student_lookup(db, 3) == {'abc': record}
