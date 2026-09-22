from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, get_current_user, hash_password, verify_password
from app.main import app
from app.models.user_school_access import UserSchoolAccess
from app.models.users import User


class _Result:
    def __init__(self, value=None, rows=None):
        self.value = value
        self.rows = list(rows or [])

    def scalar_one(self):
        assert self.value is not None
        return self.value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def first(self):
        return self.value

    def all(self):
        return self.rows


class _ProfileSession:
    def __init__(self, user, *, school_rows=(), identity_match=None):
        self.user = user
        self.school_rows = list(school_rows)
        self.identity_match = identity_match
        self.commits = 0
        self.rollbacks = 0
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        descriptions = getattr(statement, "column_descriptions", [])
        entity = descriptions[0].get("entity") if descriptions else None
        if entity is User:
            return _Result(self.user)
        if entity is UserSchoolAccess:
            return _Result(rows=self.school_rows)
        return _Result()

    def scalar(self, _statement):
        return self.identity_match

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, _value):
        pass


def _user(**overrides):
    now = datetime.now(timezone.utc)
    values = {
        "id": 1,
        "uuid": uuid4(),
        "username": "profile-user",
        "password_hash": hash_password("old-password"),
        "full_name": "Profile User",
        "email": "profile@example.com",
        "mobile": None,
        "designation": "Teacher",
        "platform_role": None,
        "is_platform_admin": False,
        "is_active": True,
        "profile_photo_path": None,
        "last_login": now,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def _clean_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _client_for(user, session):
    def db_override():
        yield session

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_authenticated_user_can_read_only_safe_self_profile_fields():
    user = _user()
    session = _ProfileSession(user)
    with _client_for(user, session) as client:
        response = client.get("/users/me")

    assert response.status_code == 200
    body = response.json()
    assert body["uuid"] == str(user.uuid)
    assert body["email"] == user.email
    assert body["school_contexts"] == []
    assert "password_hash" not in body


def test_self_profile_rejects_unauthenticated_requests():
    user = _user()
    session = _ProfileSession(user)

    def db_override():
        yield session

    app.dependency_overrides[get_db] = db_override
    with TestClient(app) as client:
        response = client.get("/users/me")

    assert response.status_code in {401, 403}


def test_patch_updates_editable_profile_fields_and_preserves_other_user():
    user = _user()
    other_user = _user(id=2, username="other", full_name="Other User")
    session = _ProfileSession(user)
    with _client_for(user, session) as client:
        response = client.patch(
            "/users/me",
            json={
                "username": "  updated-user  ",
                "full_name": "  Updated Name  ",
                "email": " updated@example.com ",
                "mobile": " +44 20 1234 5678 ",
            },
        )

    assert response.status_code == 200
    assert response.json()["username"] == "updated-user"
    assert response.json()["full_name"] == "Updated Name"
    assert response.json()["email"] == "updated@example.com"
    assert response.json()["mobile"] == "+44 20 1234 5678"
    assert user.full_name == "Updated Name"
    assert other_user.full_name == "Other User"
    assert session.commits == 1


@pytest.mark.parametrize(
    "field,value",
    [("username", "other-user"), ("email", "OTHER@example.com")],
)
def test_patch_rejects_duplicate_username_or_email(field, value):
    user = _user()
    other_user = _user(id=2, username="other-user", email="other@example.com")
    session = _ProfileSession(user, identity_match=other_user)
    with _client_for(user, session) as client:
        response = client.patch("/users/me", json={field: value})

    assert response.status_code == 409
    assert session.commits == 0


@pytest.mark.parametrize("field,value", [
    ("platform_role", "platform_admin"),
    ("is_platform_admin", True),
    ("is_active", False),
    ("school_uuid", str(uuid4())),
    ("permissions", ["all"]),
])
def test_patch_cannot_mutate_privileged_or_read_only_fields(field, value):
    user = _user()
    session = _ProfileSession(user)
    with _client_for(user, session) as client:
        response = client.patch("/users/me", json={field: value})

    assert response.status_code == 422
    assert session.commits == 0


def test_avatar_upload_replace_and_remove(monkeypatch):
    user = _user(profile_photo_path=f"users/{uuid4()}/avatars/invalid.png")
    user.profile_photo_path = f"users/{user.uuid}/avatars/{uuid4().hex}.png"
    session = _ProfileSession(user)
    deleted = []
    new_path = f"users/{user.uuid}/avatars/{uuid4().hex}.png"
    monkeypatch.setattr("app.api.users.save_user_profile_photo", lambda *args: new_path)
    monkeypatch.setattr("app.api.users.get_storage_public_url", lambda path: f"https://cdn/{path}" if path else None)
    monkeypatch.setattr("app.api.users.delete_storage_object", deleted.append)

    with _client_for(user, session) as client:
        uploaded = client.post(
            "/users/me/profile-photo",
            files={"photo": ("avatar.png", b"valid-image", "image/png")},
        )
        removed = client.delete("/users/me/profile-photo")

    assert uploaded.status_code == 200
    assert uploaded.json()["profile_photo_url"] == f"https://cdn/{new_path}"
    assert removed.status_code == 200
    assert removed.json()["profile_photo_url"] is None
    assert user.profile_photo_path is None
    assert len(deleted) == 2


def test_invalid_avatar_upload_is_rejected(monkeypatch):
    user = _user()
    session = _ProfileSession(user)
    monkeypatch.setattr(
        "app.api.users.save_user_profile_photo",
        lambda *args: (_ for _ in ()).throw(ValueError("Only JPEG, PNG and WebP profile photos are allowed.")),
    )
    with _client_for(user, session) as client:
        response = client.post(
            "/users/me/profile-photo",
            files={"photo": ("avatar.txt", b"nope", "text/plain")},
        )

    assert response.status_code == 400
    assert session.commits == 0


def test_change_password_rehashes_and_revokes_other_sessions():
    user = _user()
    old_hash = user.password_hash
    session = _ProfileSession(user)
    token = create_access_token(str(user.uuid), session_id=uuid4())
    with _client_for(user, session) as client:
        response = client.post(
            "/users/me/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": "old-password", "new_password": "new-password"},
        )

    assert response.status_code == 204
    assert user.password_hash != old_hash
    assert not verify_password("old-password", user.password_hash)
    assert verify_password("new-password", user.password_hash)
    assert any("UPDATE auth_sessions" in str(statement) for statement in session.statements)
    assert session.commits == 1


def test_change_password_rejects_wrong_current_password():
    user = _user()
    session = _ProfileSession(user)
    token = create_access_token(str(user.uuid), session_id=uuid4())
    with _client_for(user, session) as client:
        response = client.post(
            "/users/me/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": "wrong-password", "new_password": "new-password"},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Current password is incorrect."
    assert verify_password("old-password", user.password_hash)
    assert session.commits == 0


def test_change_password_enforces_existing_minimum_length():
    user = _user()
    session = _ProfileSession(user)
    token = create_access_token(str(user.uuid), session_id=uuid4())
    with _client_for(user, session) as client:
        response = client.post(
            "/users/me/change-password",
            headers={"Authorization": f"Bearer {token}"},
            json={"current_password": "old-password", "new_password": "short"},
        )

    assert response.status_code == 422
    assert session.commits == 0
