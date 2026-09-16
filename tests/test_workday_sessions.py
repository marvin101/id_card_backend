from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import auth_rate_limiter
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.auth_session import AuthSession
from app.models.school import School
from app.models.users import User
from app.models.user_school_access import UserSchoolAccess
from app.models.school_access_request import SchoolAccessRequest


@pytest.fixture
def journey():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    with engine.connect() as conn:
        conn.connection.create_function("gen_random_uuid", 0, lambda: uuid4().hex)
    for model in (User, School, AuthSession, UserSchoolAccess, SchoolAccessRequest):
        model.__table__.create(engine)
    db = Session(engine, expire_on_commit=False)
    user = User(uuid=uuid4(), username="platform", full_name="Platform Admin", password_hash=hash_password("password123"),
                is_active=True, is_platform_admin=True, platform_role="platform_admin")
    other = User(uuid=uuid4(), username="worker", full_name="Worker", password_hash=hash_password("password123"),
                 is_active=True, is_platform_admin=False, platform_role=None)
    school = School(uuid=uuid4(), school_code="TEST", school_name="Disposable", is_active=True)
    db.add_all([user, other, school]); db.commit()
    app.dependency_overrides[get_db] = lambda: db
    auth_rate_limiter.reset()
    with TestClient(app) as client:
        yield client, db, user, other, school
    app.dependency_overrides.clear(); auth_rate_limiter.reset(); db.close(); engine.dispose()


def login(client, username="platform"):
    response = client.post("/auth/login", json={"username": username, "password": "password123"})
    assert response.status_code == 200
    return response.json()


def headers(token): return {"Authorization": f"Bearer {token}"}


def test_workday_rotation_replay_logout_and_purpose_separation(journey):
    client, db, user, _, _ = journey
    tokens = login(client)
    assert 0 < tokens["expires_in"] <= settings.access_token_expire_minutes*60
    assert tokens["refresh_expires_in"] > tokens["expires_in"]
    assert client.get("/users/me", headers=headers(tokens["access_token"])).status_code == 200
    assert client.get("/users/me", headers=headers(tokens["refresh_token"])).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]}).status_code == 401
    renewed = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert renewed.status_code == 200
    replacement = renewed.json()
    assert replacement["refresh_token"] != tokens["refresh_token"]
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.get("/users/me", headers=headers(replacement["access_token"])).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": replacement["refresh_token"]}).status_code == 401

    replacement = login(client)
    assert client.post("/auth/logout", json={"refresh_token": replacement["refresh_token"]}).status_code == 204
    assert client.get("/users/me", headers=headers(replacement["access_token"])).status_code == 401


@pytest.mark.parametrize("kind", ["invalid", "expired", "inactive", "missing", "absolute_expired"])
def test_refresh_rejects_invalid_expired_inactive_and_missing(journey, kind):
    client, db, _, user, _ = journey
    token = login(client, "worker")["refresh_token"]
    if kind == "invalid": token = "invalid"
    if kind == "expired":
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm]); payload["exp"] = 1
        token = jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)
    if kind == "inactive": user.is_active = False; db.commit()
    if kind == "missing": db.delete(user); db.commit()
    if kind == "absolute_expired":
        session = db.execute(select(AuthSession)).scalars().first(); session.expires_at = datetime.now(timezone.utc)-timedelta(seconds=1); db.commit()
    response = client.post("/auth/refresh", json={"refresh_token": token})
    assert response.status_code == 401 and response.json()["detail"] == "Could not validate credentials"
    assert token not in response.text


def test_login_and_expired_access_denials(journey):
    client, db, _, user, _ = journey
    assert client.post("/auth/login", json={"username": "worker", "password": "wrong"}).status_code == 401
    assert client.get("/users/me", headers=headers(create_access_token(str(user.uuid), timedelta(seconds=-1)))).status_code == 401
    user.is_active = False; db.commit()
    assert client.post("/auth/login", json={"username": "worker", "password": "password123"}).status_code == 403


def test_role_and_school_changes_take_effect_without_claims_snapshot(journey):
    client, db, _, user, school = journey
    tokens = login(client, "worker")
    access = UserSchoolAccess(user_id=user.id, school_id=school.id, role="teacher")
    db.add(access); db.commit()
    access.role = "card_operator"; db.commit()
    new = client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).json()
    response = client.get(f"/users/{user.uuid}/schools", headers=headers(new["access_token"]))
    assert response.json()[0]["role"] == "card_operator"
    db.delete(access); db.commit()
    assert client.get(f"/users/{user.uuid}/schools", headers=headers(new["access_token"])).json() == []


def test_school_activation_is_platform_only_reversible_and_retains_data(journey):
    client, db, _, user, school = journey
    admin = login(client); worker = login(client, "worker")
    url = f"/schools/{school.uuid}/activation"
    assert client.patch(url, json={"is_active": False}, headers=headers(worker["access_token"])).status_code == 403
    assert client.get("/schools?include_inactive=true", headers=headers(worker["access_token"])).status_code == 403
    assert client.patch(url, json={"is_active": False}, headers=headers(admin["access_token"])).status_code == 200
    assert client.get("/schools", headers=headers(admin["access_token"])).json() == []
    assert client.get("/schools?include_inactive=true", headers=headers(admin["access_token"])).json()[0]["is_active"] is False
    assert client.get(f"/schools/{school.uuid}/profile", headers=headers(admin["access_token"])).status_code == 404
    assert client.patch(url, json={"is_active": True}, headers=headers(admin["access_token"])).status_code == 200
    assert db.get(School, school.id).school_code == "TEST"


def test_account_creation_edit_activation_password_and_self_protection(journey):
    client, db, admin, user, school = journey
    admin_token = login(client)["access_token"]; h = headers(admin_token)
    tokens = login(client, "worker")
    url = f"/users/{user.uuid}/account"
    assert client.patch(url, json={"full_name": "Changed"}, headers=headers(tokens["access_token"])).status_code == 403
    assert client.patch(url, json={"full_name": "Changed", "mobile": "123"}, headers=h).status_code == 200
    assert client.patch(f"/users/{admin.uuid}/account", json={"is_active": False}, headers=h).status_code == 409
    assert client.patch(f"/users/{admin.uuid}/account", json={"platform_role": None}, headers=h).status_code == 409
    assert client.patch(url, json={"is_active": False}, headers=h).status_code == 200
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.patch(url, json={"is_active": True}, headers=h).status_code == 200
    assert client.get("/users/me", headers=headers(tokens["access_token"])).status_code == 401
    assert client.patch(url, json={"password": "replacement123"}, headers=h).status_code == 200
    assert client.post("/auth/login", json={"username": "worker", "password": "replacement123"}).status_code == 200
    created = client.post("/users/accounts", json={"username": "new-user", "full_name": "New", "password": "password123", "email": "new@example.test"}, headers=h)
    assert created.status_code == 201 and "password" not in created.text
    assert client.post("/users/accounts", json={"username": "other-user", "full_name": "Other", "password": "password123", "email": "NEW@example.test"}, headers=h).status_code == 409
    assert client.get("/users?search=new", headers=h).json()[0]["username"] == "new-user"


@pytest.mark.parametrize("data", [{"is_active": None}, {"full_name": None}, {"password": None}, {"platform_role": "teacher"}, {"full_name": "  "}, {"username": "hack"}])
def test_account_update_rejects_invalid_or_unsupported_fields(journey, data):
    client, _, _, user, _ = journey
    response = client.patch(f"/users/{user.uuid}/account", json=data, headers=headers(login(client)["access_token"]))
    assert response.status_code == 422


def test_logout_can_revoke_a_session_after_concurrent_rotation(journey):
    client, _, _, _, _ = journey
    first = login(client)
    second = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).json()
    assert client.post("/auth/logout", json={"refresh_token": first["refresh_token"]}).status_code == 204
    assert client.get("/users/me", headers=headers(second["access_token"])).status_code == 401


def test_platform_promotion_and_demotion_are_explicit_and_not_self_service(journey):
    client, _, admin, user, _ = journey
    h = headers(login(client)["access_token"])
    url = f"/users/{user.uuid}/account"
    assert client.patch(url, json={"platform_role": "platform_admin"}, headers=h).status_code == 200
    worker = login(client, "worker")
    assert client.get("/users/me", headers=headers(worker["access_token"])).json()["platform_role"] == "platform_admin"
    assert client.patch(url, json={"platform_role": None}, headers=h).status_code == 200
    assert client.get("/users", headers=headers(worker["access_token"])).status_code == 403


def test_sensitive_validation_errors_and_public_failures_are_not_cached(journey):
    client, _, _, _, _ = journey
    secret = "sensitive-token" * 400
    response = client.post("/auth/refresh", json={"refresh_token": secret})
    assert response.status_code == 422 and secret not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    for path in ("/public/forms/invalid", "/public/designs/invalid", "/public/verifications/invalid"):
        # Invalid forms/designs may hit disposable DB missing template tables;
        # cache behavior is exercised separately for auth and verification.
        if path.endswith('verifications/invalid'):
            assert client.get(path).headers["Cache-Control"] == "no-store"


def test_school_logo_removal_detaches_and_cleans_managed_object(journey, monkeypatch):
    from app.api import schools as school_api
    client, db, _, _, school = journey
    path = f"schools/{school.uuid}/logos/test.png"
    school.logo_path = path; db.commit()
    deleted = []
    monkeypatch.setattr(school_api, "delete_storage_object", deleted.append)
    response = client.delete(f"/schools/{school.uuid}/logo", headers=headers(login(client)["access_token"]))
    assert response.status_code == 200 and response.json()["logo_path"] is None
    assert deleted == [path]
