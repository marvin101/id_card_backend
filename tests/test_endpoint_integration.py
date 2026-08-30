from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.rate_limit import auth_rate_limiter
from app.core.security import create_access_token, get_current_user, hash_password
from app.main import app
from app.models.card_template import CardTemplate
from app.models.school import School
from app.models.user_school_access import UserSchoolAccess
from app.models.users import User


class _Result:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = list(values or [])

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.values

    def mappings(self):
        return self

    def one(self):
        if self.value is None:
            raise RuntimeError("No result")
        return self.value


class _EndpointSession:
    """Isolated persistence boundary for exercising the complete HTTP stack."""

    def __init__(self, *, user=None, school=None, access=None, template=None):
        self.user = user
        self.school = school
        self.access = access
        self.template = template
        self.commits = 0

    def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        params = set(statement.compile().params.values())

        if entity is User:
            if self.user is None:
                return _Result()
            identifiers = {self.user.username, self.user.uuid}
            return _Result(self.user if identifiers & params else None)

        if entity is School:
            return _Result(
                self.school
                if self.school is not None and self.school.uuid in params
                else None
            )

        if entity is UserSchoolAccess:
            if self.access is None:
                return _Result()
            matches_scope = {
                self.access.user_id,
                self.access.school_id,
            }.issubset(params)
            return _Result(self.access if matches_scope else None)

        if entity is CardTemplate:
            return _Result(
                self.template
                if self.template is not None and self.template.school_id in params
                else None
            )

        raise AssertionError(f"Unexpected query entity: {entity}")

    def commit(self):
        self.commits += 1


def _user(*, username="operator", active=True):
    return SimpleNamespace(
        id=1,
        uuid=uuid4(),
        username=username,
        password_hash=hash_password("correct horse battery staple"),
        full_name="Test Operator",
        email=None,
        mobile=None,
        designation="Card Operator",
        platform_role=None,
        is_platform_admin=False,
        is_active=active,
        last_login=None,
    )


@pytest.fixture(autouse=True)
def _clean_app_state():
    app.dependency_overrides.clear()
    auth_rate_limiter.reset()
    yield
    app.dependency_overrides.clear()
    auth_rate_limiter.reset()


def _override_db(session):
    def dependency():
        yield session

    app.dependency_overrides[get_db] = dependency


def test_login_and_authenticated_profile_use_the_http_authentication_path():
    user = _user()
    session = _EndpointSession(user=user)
    _override_db(session)

    with TestClient(app) as client:
        login_response = client.post(
            "/auth/login",
            json={
                "username": user.username,
                "password": "correct horse battery staple",
            },
        )
        assert login_response.status_code == 200
        assert login_response.json()["token_type"] == "bearer"
        assert user.last_login is not None
        assert session.commits == 1

        profile_response = client.get(
            "/users/me",
            headers={
                "Authorization": f"Bearer {login_response.json()['access_token']}"
            },
        )

    assert profile_response.status_code == 200
    assert profile_response.json()["uuid"] == str(user.uuid)
    assert profile_response.json()["username"] == user.username


def test_expired_access_token_is_rejected_by_authenticated_endpoint():
    user = _user()
    _override_db(_EndpointSession(user=user))
    expired_token = create_access_token(
        str(user.uuid),
        expires_delta=timedelta(seconds=-1),
    )

    with TestClient(app) as client:
        response = client.get(
            "/users/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Could not validate credentials"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_invalid_login_is_generic_and_does_not_log_credentials(caplog):
    user = _user(username="sensitive-user")
    _override_db(_EndpointSession(user=user))

    with TestClient(app) as client:
        response = client.post(
            "/auth/login",
            json={"username": user.username, "password": "sensitive-password"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid username or password"}
    assert user.username not in caplog.text
    assert "sensitive-password" not in caplog.text


@pytest.mark.parametrize(
    "assignment_state",
    ["pending", "revoked", "unassigned"],
)
def test_non_active_assignment_states_cannot_read_a_card_template(assignment_state):
    """Pending/revoked requests never substitute for an active access row."""
    current_user = _user()
    school = SimpleNamespace(id=10, uuid=uuid4(), is_active=True)
    session = _EndpointSession(user=current_user, school=school, access=None)
    session.assignment_state = assignment_state
    _override_db(session)
    app.dependency_overrides[get_current_user] = lambda: current_user

    with TestClient(app) as client:
        response = client.get(f"/schools/{school.uuid}/card-template")

    assert response.status_code == 403
    assert response.json()["detail"] == "You do not have access to this school"


def test_card_template_read_is_allowed_only_for_the_assigned_school():
    current_user = _user()
    school = SimpleNamespace(id=10, uuid=uuid4(), is_active=True)
    access = SimpleNamespace(
        user_id=current_user.id,
        school_id=school.id,
        role="card_operator",
    )
    template = SimpleNamespace(
        uuid=uuid4(),
        school_id=school.id,
        name="Standard portrait",
        design={"width": 638, "height": 1011},
        updated_at=datetime.now(timezone.utc),
    )
    _override_db(
        _EndpointSession(
            user=current_user,
            school=school,
            access=access,
            template=template,
        )
    )
    app.dependency_overrides[get_current_user] = lambda: current_user

    with TestClient(app) as client:
        response = client.get(f"/schools/{school.uuid}/card-template")

    assert response.status_code == 200
    assert response.json()["uuid"] == str(template.uuid)
    assert response.json()["design"]["width"] == 638


def test_access_for_another_school_does_not_authorize_template_read():
    current_user = _user()
    requested_school = SimpleNamespace(id=10, uuid=uuid4(), is_active=True)
    other_school_access = SimpleNamespace(
        user_id=current_user.id,
        school_id=20,
        role="school_admin",
    )
    _override_db(
        _EndpointSession(
            user=current_user,
            school=requested_school,
            access=other_school_access,
        )
    )
    app.dependency_overrides[get_current_user] = lambda: current_user

    with TestClient(app) as client:
        response = client.get(f"/schools/{requested_school.uuid}/card-template")

    assert response.status_code == 403
