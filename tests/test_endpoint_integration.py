from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

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
        self.rollbacks = 0
        self.added = []
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
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

    def rollback(self):
        self.rollbacks += 1

    def add(self, value):
        self.added.append(value)
        if isinstance(value, CardTemplate):
            self.template = value

    def refresh(self, _value):
        pass


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


def test_card_template_put_updates_one_school_template_and_returns_flutter_shape():
    current_user = _user()
    school = SimpleNamespace(id=10, uuid=uuid4(), is_active=True)
    access = SimpleNamespace(
        user_id=current_user.id,
        school_id=school.id,
        role="school_admin",
    )
    original_design = {"version": 1, "school_title": "Original"}
    template = SimpleNamespace(
        uuid=uuid4(),
        school_id=school.id,
        name="Original",
        design=original_design,
        updated_at=datetime.now(timezone.utc),
    )
    session = _EndpointSession(
        user=current_user,
        school=school,
        access=access,
        template=template,
    )
    _override_db(session)
    app.dependency_overrides[get_current_user] = lambda: current_user

    replacement = {"version": 1, "school_title": "Replacement"}
    with TestClient(app) as client:
        response = client.put(
            f"/schools/{school.uuid}/card-template",
            json={"name": "  Authoritative name  ", "design": replacement},
        )

    assert response.status_code == 200
    assert set(response.json()) == {
        "uuid",
        "name",
        "design",
        "back_design",
        "updated_at",
    }
    assert response.json()["name"] == "Authoritative name"
    assert response.json()["design"] == replacement
    assert response.json()["back_design"] is None
    assert template.name == "Authoritative name"
    assert template.design == replacement
    assert session.template is template
    assert session.commits == 1


def test_card_template_back_design_updates_and_legacy_omission_preserves_it():
    current_user = _user()
    school = SimpleNamespace(id=10, uuid=uuid4(), is_active=True)
    access = SimpleNamespace(
        user_id=current_user.id,
        school_id=school.id,
        role="school_admin",
    )
    original_back = {"version": 1, "school_title": "Original back"}
    template = SimpleNamespace(
        uuid=uuid4(),
        school_id=school.id,
        name="Two-sided",
        design={"version": 1, "school_title": "Front"},
        back_design=original_back,
        updated_at=datetime.now(timezone.utc),
    )
    session = _EndpointSession(
        user=current_user,
        school=school,
        access=access,
        template=template,
    )
    _override_db(session)
    app.dependency_overrides[get_current_user] = lambda: current_user

    replacement_back = {"version": 1, "school_title": "Replacement back"}
    with TestClient(app) as client:
        updated = client.put(
            f"/schools/{school.uuid}/card-template",
            json={
                "name": "Two-sided",
                "design": {"version": 1, "school_title": "Front updated"},
                "back_design": replacement_back,
            },
        )
        legacy = client.put(
            f"/schools/{school.uuid}/card-template",
            json={
                "name": "Legacy save",
                "design": {"version": 1, "school_title": "Front again"},
            },
        )

    assert updated.status_code == 200
    assert updated.json()["back_design"] == replacement_back
    assert legacy.status_code == 200
    assert legacy.json()["back_design"] == replacement_back
    assert template.back_design == replacement_back


def test_card_template_matching_token_succeeds_and_returns_a_new_token():
    current_user = _user()
    school = SimpleNamespace(id=10, uuid=uuid4(), is_active=True)
    access = SimpleNamespace(
        user_id=current_user.id, school_id=school.id, role="school_admin"
    )
    original_token = datetime(2026, 9, 8, 1, 2, 3, 456789, tzinfo=timezone.utc)
    template = SimpleNamespace(
        uuid=uuid4(),
        school_id=school.id,
        name="Original",
        design={"version": 1},
        updated_at=original_token,
    )
    session = _EndpointSession(
        user=current_user, school=school, access=access, template=template
    )
    _override_db(session)
    app.dependency_overrides[get_current_user] = lambda: current_user

    with TestClient(app) as client:
        response = client.put(
            f"/schools/{school.uuid}/card-template",
            json={
                "name": "Client A",
                "design": {"version": 1, "school_title": "A"},
                "expected_updated_at": "2026-09-08T01:02:03.456789Z",
            },
        )

    assert response.status_code == 200
    response_token = datetime.fromisoformat(response.json()["updated_at"])
    assert response_token > original_token
    assert template.updated_at == response_token
    assert "FOR UPDATE" in str(session.statements[-1])


def test_card_template_stale_token_conflicts_without_changing_stored_state():
    current_user = _user()
    school = SimpleNamespace(id=10, uuid=uuid4(), is_active=True)
    access = SimpleNamespace(
        user_id=current_user.id, school_id=school.id, role="school_admin"
    )
    stored_design = {"version": 1, "school_title": "Stored"}
    template = SimpleNamespace(
        uuid=uuid4(),
        school_id=school.id,
        name="Stored",
        design=stored_design,
        updated_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    session = _EndpointSession(
        user=current_user, school=school, access=access, template=template
    )
    _override_db(session)
    app.dependency_overrides[get_current_user] = lambda: current_user

    with TestClient(app) as client:
        response = client.put(
            f"/schools/{school.uuid}/card-template",
            json={
                "name": "Stale",
                "design": {"version": 1, "school_title": "Stale"},
                "expected_updated_at": "2026-09-07T00:00:00Z",
            },
        )

    assert response.status_code == 409
    assert "changed after it was loaded" in response.json()["detail"]
    assert template.name == "Stored"
    assert template.design is stored_design
    assert session.commits == 0


def test_card_template_two_clients_cannot_overwrite_each_other():
    current_user = _user()
    school = SimpleNamespace(id=10, uuid=uuid4(), is_active=True)
    access = SimpleNamespace(
        user_id=current_user.id, school_id=school.id, role="school_admin"
    )
    loaded_token = datetime(2026, 9, 8, tzinfo=timezone.utc)
    template = SimpleNamespace(
        uuid=uuid4(),
        school_id=school.id,
        name="Original",
        design={"version": 1},
        updated_at=loaded_token,
    )
    session = _EndpointSession(
        user=current_user, school=school, access=access, template=template
    )
    _override_db(session)
    app.dependency_overrides[get_current_user] = lambda: current_user
    payload = {
        "design": {"version": 1},
        "expected_updated_at": loaded_token.isoformat(),
    }

    with TestClient(app) as client:
        first = client.put(
            f"/schools/{school.uuid}/card-template",
            json={**payload, "name": "First client"},
        )
        second = client.put(
            f"/schools/{school.uuid}/card-template",
            json={**payload, "name": "Second client"},
        )

    assert first.status_code == 200
    assert second.status_code == 409
    assert template.name == "First client"
    assert session.commits == 1


@pytest.mark.parametrize(
    "token",
    ["not-a-timestamp", "2026-09-08T01:02:03"],
)
def test_card_template_malformed_or_timezone_less_token_is_rejected(token):
    current_user = _user()
    school = SimpleNamespace(id=10, uuid=uuid4(), is_active=True)
    access = SimpleNamespace(
        user_id=current_user.id, school_id=school.id, role="school_admin"
    )
    template = SimpleNamespace(
        uuid=uuid4(),
        school_id=school.id,
        name="Stored",
        design={"version": 1},
        updated_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
    )
    session = _EndpointSession(
        user=current_user, school=school, access=access, template=template
    )
    _override_db(session)
    app.dependency_overrides[get_current_user] = lambda: current_user

    with TestClient(app) as client:
        response = client.put(
            f"/schools/{school.uuid}/card-template",
            json={
                "name": "Rejected",
                "design": {"version": 1},
                "expected_updated_at": token,
            },
        )

    assert response.status_code == 422
    assert template.name == "Stored"
    assert session.commits == 0


def test_concurrent_first_template_create_returns_conflict_without_duplicate():
    class _ConcurrentCreateSession(_EndpointSession):
        def commit(self):
            raise IntegrityError("unique school template", {}, RuntimeError())

    current_user = _user()
    school = SimpleNamespace(id=10, uuid=uuid4(), is_active=True)
    access = SimpleNamespace(
        user_id=current_user.id, school_id=school.id, role="school_admin"
    )
    session = _ConcurrentCreateSession(
        user=current_user, school=school, access=access
    )
    _override_db(session)
    app.dependency_overrides[get_current_user] = lambda: current_user

    with TestClient(app) as client:
        response = client.put(
            f"/schools/{school.uuid}/card-template",
            json={"name": "First save", "design": {"version": 1}},
        )

    assert response.status_code == 409
    assert len(session.added) == 1
    assert session.rollbacks == 1


def test_card_template_validation_failure_leaves_previous_record_unchanged():
    current_user = _user()
    school = SimpleNamespace(id=10, uuid=uuid4(), is_active=True)
    access = SimpleNamespace(
        user_id=current_user.id,
        school_id=school.id,
        role="school_admin",
    )
    original_design = {"version": 1, "school_title": "Original"}
    template = SimpleNamespace(
        uuid=uuid4(),
        school_id=school.id,
        name="Original",
        design=original_design,
        updated_at=datetime.now(timezone.utc),
    )
    session = _EndpointSession(
        user=current_user,
        school=school,
        access=access,
        template=template,
    )
    _override_db(session)
    app.dependency_overrides[get_current_user] = lambda: current_user

    with TestClient(app) as client:
        response = client.put(
            f"/schools/{school.uuid}/card-template",
            json={
                "name": "Invalid replacement",
                "design": {"schema_version": 2, "elements": []},
            },
        )

    assert response.status_code == 422
    assert template.name == "Original"
    assert template.design is original_design
    assert session.commits == 0
