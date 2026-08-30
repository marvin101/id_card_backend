import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api import schools as schools_api
from app.core.database import get_db
from app.core.file_storage import MAX_SCHOOL_LOGO_SIZE, StorageError, validate_school_logo
from app.core.security import get_current_user
from app.main import app
from app.models.school import School
from app.models.user_school_access import UserSchoolAccess


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ProfileSession:
    def __init__(self, school, access=None, *, fail_commit=False):
        self.school = school
        self.access = access
        self.fail_commit = fail_commit
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        params = set(statement.compile().params.values())
        if entity is School:
            return _Result(self.school if self.school.uuid in params else None)
        if entity is UserSchoolAccess:
            matches = self.access is not None and {
                self.access.user_id,
                self.access.school_id,
            }.issubset(params)
            return _Result(self.access if matches else None)
        raise AssertionError(f"Unexpected query entity: {entity}")

    def commit(self):
        if self.fail_commit:
            raise RuntimeError("database unavailable")
        self.commits += 1

    def refresh(self, _value):
        return None

    def rollback(self):
        self.rollbacks += 1


def _school(**overrides):
    values = {
        "id": 10,
        "uuid": uuid4(),
        "school_code": "CAMPUS-1",
        "school_name": "Campus School",
        "email": "office@example.test",
        "phone": None,
        "website": None,
        "address": None,
        "city": None,
        "district": None,
        "state": None,
        "country": "India",
        "postal_code": None,
        "logo_path": None,
        "principal_name": None,
        "is_active": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _user(*, platform=False):
    return SimpleNamespace(
        id=1,
        platform_role="platform_admin" if platform else None,
        is_platform_admin=platform,
    )


def _access(user, school, role, *, school_id=None):
    return SimpleNamespace(
        user_id=user.id,
        school_id=school.id if school_id is None else school_id,
        role=role,
    )


def _png_bytes():
    output = io.BytesIO()
    Image.new("RGBA", (4, 4), (0, 70, 140, 255)).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture(autouse=True)
def _clean_dependencies():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _configure(session, user):
    def db_dependency():
        yield session

    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.mark.parametrize(
    "role",
    ["school_admin", "admin", "card_operator", "teacher", "staff"],
)
def test_assigned_school_roles_can_read_profile(role, monkeypatch):
    school = _school(logo_path="schools/logo")
    user = _user()
    _configure(_ProfileSession(school, _access(user, school, role)), user)
    monkeypatch.setattr(
        schools_api,
        "get_storage_public_url",
        lambda path: f"https://media.test/{path}" if path else None,
    )

    with TestClient(app) as client:
        response = client.get(f"/schools/{school.uuid}/profile")

    assert response.status_code == 200
    assert response.json()["school_code"] == "CAMPUS-1"
    assert response.json()["logo_path"] == "schools/logo"
    assert response.json()["logo_url"] == "https://media.test/schools/logo"


def test_profile_read_does_not_accept_access_for_another_school():
    school = _school()
    user = _user()
    other_access = _access(user, school, "school_admin", school_id=20)
    _configure(_ProfileSession(school, other_access), user)

    with TestClient(app) as client:
        response = client.get(f"/schools/{school.uuid}/profile")

    assert response.status_code == 403


@pytest.mark.parametrize("platform,role", [(True, None), (False, "school_admin")])
def test_platform_and_school_admin_can_update_profile(platform, role):
    school = _school()
    user = _user(platform=platform)
    access = None if platform else _access(user, school, role)
    session = _ProfileSession(school, access)
    _configure(session, user)

    with TestClient(app) as client:
        response = client.patch(
            f"/schools/{school.uuid}/profile",
            json={
                "school_name": "Updated School",
                "email": " admin@example.test ",
                "website": "https://school.example.test",
                "city": "Pune",
            },
        )

    assert response.status_code == 200
    assert response.json()["school_name"] == "Updated School"
    assert response.json()["email"] == "admin@example.test"
    assert school.city == "Pune"
    assert session.commits == 1


@pytest.mark.parametrize("role", ["card_operator", "teacher", "staff"])
def test_non_admin_roles_cannot_update_profile(role):
    school = _school()
    user = _user()
    session = _ProfileSession(school, _access(user, school, role))
    _configure(session, user)

    with TestClient(app) as client:
        response = client.patch(
            f"/schools/{school.uuid}/profile",
            json={"school_name": "Unauthorized"},
        )

    assert response.status_code == 403
    assert school.school_name == "Campus School"
    assert session.commits == 0


def test_school_name_cannot_be_cleared():
    school = _school()
    user = _user(platform=True)
    session = _ProfileSession(school)
    _configure(session, user)

    with TestClient(app) as client:
        response = client.patch(
            f"/schools/{school.uuid}/profile",
            json={"school_name": None},
        )

    assert response.status_code == 422
    assert school.school_name == "Campus School"
    assert session.commits == 0


def test_logo_validation_rejects_size_mime_and_extension_mismatches():
    png = _png_bytes()
    with pytest.raises(ValueError, match="2 MB"):
        validate_school_logo(b"x" * (MAX_SCHOOL_LOGO_SIZE + 1), "image/png", "logo.png")
    with pytest.raises(ValueError, match="Only JPEG"):
        validate_school_logo(png, "image/gif", "logo.gif")
    with pytest.raises(ValueError, match="extension"):
        validate_school_logo(png, "image/png", "logo.jpg")


def test_logo_upload_replaces_path_then_deletes_old_object(monkeypatch):
    school = _school(logo_path=None)
    old_path = f"schools/{school.uuid}/logos/old.png"
    school.logo_path = old_path
    new_path = f"schools/{school.uuid}/logos/new.png"
    user = _user(platform=True)
    session = _ProfileSession(school)
    _configure(session, user)
    deleted = []
    monkeypatch.setattr(schools_api, "save_school_logo", lambda *args: new_path)
    monkeypatch.setattr(schools_api, "delete_storage_object", deleted.append)
    monkeypatch.setattr(
        schools_api,
        "get_storage_public_url",
        lambda path: f"https://media.test/{path}",
    )

    with TestClient(app) as client:
        response = client.post(
            f"/schools/{school.uuid}/logo",
            files={"logo": ("logo.png", _png_bytes(), "image/png")},
        )

    assert response.status_code == 200
    assert response.json()["logo_path"] == new_path
    assert response.json()["logo_url"] == f"https://media.test/{new_path}"
    assert deleted == [old_path]
    assert session.commits == 1


def test_storage_failure_preserves_existing_logo(monkeypatch):
    old_path = "schools/existing/logos/old.png"
    school = _school(logo_path=old_path)
    user = _user(platform=True)
    session = _ProfileSession(school)
    _configure(session, user)
    monkeypatch.setattr(
        schools_api,
        "save_school_logo",
        lambda *args: (_ for _ in ()).throw(StorageError("offline")),
    )

    with TestClient(app) as client:
        response = client.post(
            f"/schools/{school.uuid}/logo",
            files={"logo": ("logo.png", _png_bytes(), "image/png")},
        )

    assert response.status_code == 502
    assert school.logo_path == old_path
    assert session.commits == 0


def test_database_failure_cleans_up_new_logo_and_keeps_old_object(monkeypatch):
    school = _school()
    old_path = f"schools/{school.uuid}/logos/old.png"
    school.logo_path = old_path
    new_path = f"schools/{school.uuid}/logos/new.png"
    user = _user(platform=True)
    session = _ProfileSession(school, fail_commit=True)
    _configure(session, user)
    deleted = []
    monkeypatch.setattr(schools_api, "save_school_logo", lambda *args: new_path)
    monkeypatch.setattr(schools_api, "delete_storage_object", deleted.append)

    with pytest.raises(RuntimeError, match="database unavailable"):
        with TestClient(app) as client:
            client.post(
                f"/schools/{school.uuid}/logo",
                files={"logo": ("logo.png", _png_bytes(), "image/png")},
            )

    assert deleted == [new_path]
    assert session.rollbacks == 1
    assert old_path not in deleted
