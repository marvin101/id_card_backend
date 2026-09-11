from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.api.card_templates import public_router
from app.core.database import get_db
from app.core.rate_limit import public_design_rate_limiter
from app.core.security import get_current_user
from app.main import app
from app.models.card_template import CardTemplate
from app.models.school import School
from app.models.user_school_access import UserSchoolAccess


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Database:
    def __init__(self, school, template, access):
        self.school = school
        self.template = template
        self.access = access
        self.commits = 0

    def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        params = set(statement.compile().params.values())
        if entity is School:
            return _Result(self.school if self.school.uuid in params else None)
        if entity is UserSchoolAccess:
            return _Result(
                self.access
                if {self.access.user_id, self.access.school_id}.issubset(params)
                else None
            )
        if entity is CardTemplate:
            if self.school.id in params:
                return _Result(self.template)
            if (
                self.template.public_enabled
                and self.template.public_token in params
            ):
                return _Result(self.template)
            return _Result()
        raise AssertionError(f"Unexpected query entity: {entity}")

    def commit(self):
        self.commits += 1


def _fixture(*, enabled=True, role="school_admin"):
    school = SimpleNamespace(
        id=10,
        uuid=uuid4(),
        school_code="SCH-01",
        school_name="Campus School",
        email="school@example.test",
        phone="1234567890",
        website="https://school.example.test",
        address="School Road",
        city="Ranchi",
        district="Ranchi",
        state="Jharkhand",
        country="India",
        postal_code="834001",
        principal_name="Principal Name",
        logo_path=None,
        is_active=True,
    )
    template = SimpleNamespace(
        school_id=school.id,
        school=school,
        name="Standard card",
        design={"version": 1, "school_title": "Campus School"},
        public_token="opaque-public-token",
        public_enabled=enabled,
    )
    user = SimpleNamespace(id=1, is_platform_admin=False, platform_role=None)
    access = SimpleNamespace(user_id=user.id, school_id=school.id, role=role)
    return school, template, user, _Database(school, template, access)


@pytest.fixture(autouse=True)
def _clean_app_state():
    app.dependency_overrides.clear()
    public_design_rate_limiter.reset()
    yield
    app.dependency_overrides.clear()
    public_design_rate_limiter.reset()


def _override(db, user=None):
    def database_dependency():
        yield db

    app.dependency_overrides[get_db] = database_dependency
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user


def test_public_design_route_is_anonymous_and_returns_no_student_or_token_data():
    _, _, _, db = _fixture()
    _override(db)
    with TestClient(app) as client:
        response = client.get("/public/designs/opaque-public-token")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["name"] == "Standard card"
    assert response.json()["school"]["school_name"] == "Campus School"
    assert "public_token" not in response.text
    assert "student" not in response.text.lower()
    route = next(route for route in public_router.routes if "GET" in route.methods)
    assert all(
        dependency.call.__name__ != "get_current_user"
        for dependency in route.dependant.dependencies
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_missing_or_disabled_public_design_returns_generic_404(enabled):
    _, template, _, db = _fixture(enabled=enabled)
    if enabled:
        template.public_token = "different-token"
    _override(db)
    with TestClient(app) as client:
        response = client.get("/public/designs/opaque-public-token")
    assert response.status_code == 404
    assert response.json() == {"detail": "Public design not found"}


def test_inactive_school_public_design_returns_generic_404():
    school, _, _, db = _fixture()
    school.is_active = False
    _override(db)
    with TestClient(app) as client:
        response = client.get("/public/designs/opaque-public-token")
    assert response.status_code == 404
    assert response.json() == {"detail": "Public design not found"}


def test_anonymous_caller_cannot_mutate_public_design_settings():
    school, _, _, db = _fixture()
    _override(db)
    with TestClient(app) as client:
        response = client.put(
            f"/schools/{school.uuid}/card-template/public-share",
            json={"enabled": False},
        )
    assert response.status_code == 401
    assert db.commits == 0


def test_admin_can_enable_disable_and_regenerate_the_share_link():
    school, template, user, db = _fixture(enabled=False)
    template.public_token = None
    template.updated_at = object()
    original_updated_at = template.updated_at
    _override(db, user)
    with TestClient(app) as client:
        enabled = client.put(
            f"/schools/{school.uuid}/card-template/public-share",
            json={"enabled": True},
        )
        first_token = enabled.json()["public_token"]
        regenerated = client.post(
            f"/schools/{school.uuid}/card-template/public-share/regenerate-link"
        )
        old_preview = client.get(f"/public/designs/{first_token}")
        new_preview = client.get(
            f"/public/designs/{regenerated.json()['public_token']}"
        )
        disabled = client.put(
            f"/schools/{school.uuid}/card-template/public-share",
            json={"enabled": False},
        )
        disabled_preview = client.get(
            f"/public/designs/{regenerated.json()['public_token']}"
        )

    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert len(first_token) >= 32
    assert regenerated.status_code == 200
    assert regenerated.json()["public_token"] != first_token
    assert old_preview.status_code == 404
    assert new_preview.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled_preview.status_code == 404
    assert template.updated_at is original_updated_at
    assert db.commits == 3


def test_non_admin_cannot_manage_public_design_link():
    school, _, user, db = _fixture(role="card_operator")
    _override(db, user)
    with TestClient(app) as client:
        response = client.get(
            f"/schools/{school.uuid}/card-template/public-share"
        )
    assert response.status_code == 403


def test_public_design_migration_is_revocable_and_rls_hardening_is_head():
    root = Path(__file__).parents[1]
    migration = (
        root
        / "migrations"
        / "versions"
        / "b8e2f14c9a70_add_public_card_design_sharing.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: Union[str, Sequence[str], None] = "a7c91e42d6b3"' in migration
    assert "public_enabled" in migration
    assert "public_token" in migration
    assert "unique=True" in migration

    rls_migration = (
        root
        / "migrations"
        / "versions"
        / "e4c7a91d2f60_enable_rls_on_bulk_photo_imports.py"
    ).read_text(encoding="utf-8")
    assert 'down_revision: Union[str, Sequence[str], None] = "b8e2f14c9a70"' in rls_migration
    assert 'ALTER TABLE public."bulk_photo_imports" ENABLE ROW LEVEL SECURITY' in rls_migration

    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    assert ScriptDirectory.from_config(config).get_heads() == ["c6d2e9f4a731"]
