from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.main import app
from app.models.card_template import CardTemplate
from app.models.custom_field import CustomFieldDefinition
from app.models.school import School
from app.models.user_school_access import UserSchoolAccess


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


class _CopySession:
    def __init__(self, *, schools, user, accesses=None, templates=None, fields=None):
        self.schools = {school.uuid: school for school in schools}
        self.user = user
        self.accesses = accesses or {}
        self.templates = templates or {}
        self.fields = fields or []
        self.commits = 0
        self.rollbacks = 0
        self.added = []

    def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        raw_params = list(statement.compile().params.values())
        params = []
        for value in raw_params:
            params.extend(value if isinstance(value, list) else [value])

        if entity is School:
            return _Result(next((s for key, s in self.schools.items() if key in params), None))
        if entity is UserSchoolAccess:
            school_id = next((key for key in self.accesses if key in params), None)
            return _Result(self.accesses.get(school_id))
        if entity is CardTemplate:
            school_id = next((key for key in self.templates if key in params), None)
            return _Result(self.templates.get(school_id))
        if entity is CustomFieldDefinition:
            school_ids = {field.school_id for field in self.fields}
            school_id = next((key for key in school_ids if key in params), None)
            values = [field for field in self.fields if field.school_id == school_id]
            requested_uuids = {value for value in params if value in {f.uuid for f in self.fields}}
            if requested_uuids:
                values = [field for field in values if field.uuid in requested_uuids]
            return _Result(values=values)
        raise AssertionError(f"Unexpected query entity: {entity}")

    def add(self, value):
        self.added.append(value)
        self.templates[value.school_id] = value

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, value):
        if value.uuid is None:
            value.uuid = uuid4()
        if value.updated_at is None:
            value.updated_at = datetime.now(timezone.utc)


def _school(identifier):
    return SimpleNamespace(id=identifier, uuid=uuid4(), is_active=True)


def _user(*, platform=False):
    return SimpleNamespace(
        id=1,
        platform_role="platform_admin" if platform else None,
        is_platform_admin=False,
    )


def _access(school_id, role="school_admin"):
    return SimpleNamespace(user_id=1, school_id=school_id, role=role)


def _template(school_id, design, *, back=None, token=None, enabled=False):
    return SimpleNamespace(
        uuid=uuid4(),
        school_id=school_id,
        name="Source card",
        design=design,
        back_design=back,
        public_token=token,
        public_enabled=enabled,
        updated_at=datetime(2026, 9, 24, 8, 0, tzinfo=timezone.utc),
    )


def _field(school_id, *, key, entity="student", data_type="text", active=True):
    return SimpleNamespace(
        uuid=uuid4(),
        school_id=school_id,
        field_key=key,
        label=key.replace("_", " ").title(),
        entity_type=entity,
        data_type=data_type,
        is_active=active,
    )


def _document(*field_uuids):
    return {
        "schema_version": 2,
        "canvas": {"width": 85.6, "height": 53.98},
        "elements": [
            {
                "id": "standard",
                "type": "bound_text",
                "data": {"field": "school_name"},
            },
            {
                "id": "custom",
                "type": "custom_field_text",
                "data": {"field_uuid": str(field_uuids[0])},
            },
            {
                "id": "qr",
                "type": "qr_code",
                "data": {
                    "fields": [
                        {"field": "full_name"},
                        *[
                            {"field_uuid": str(value), "label": f"Custom {index}"}
                            for index, value in enumerate(field_uuids)
                        ],
                    ]
                },
            },
            {
                "id": "barcode",
                "type": "barcode",
                "data": {"field_uuid": str(field_uuids[-1])},
            },
        ],
        "settings": {},
    }


@pytest.fixture(autouse=True)
def _clean_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _request(session, user, source, target, *, expected_marker="omit"):
    def database():
        yield session

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: user
    body = {"source_school_uuid": str(source.uuid)}
    if expected_marker != "omit":
        body["expected_updated_at"] = expected_marker
    with TestClient(app) as client:
        return client.post(f"/schools/{target.uuid}/card-template/copy", json=body)


def test_copy_creates_independent_front_and_back_and_does_not_copy_sharing_state():
    source, target = _school(10), _school(20)
    user = _user()
    design = {"version": 1, "school_title": "Source", "nested": {"value": 1}}
    back = {"version": 1, "school_title": "Back"}
    source_template = _template(
        source.id, design, back=back, token="source-secret", enabled=True
    )
    session = _CopySession(
        schools=[source, target],
        user=user,
        accesses={source.id: _access(source.id), target.id: _access(target.id)},
        templates={source.id: source_template},
    )

    response = _request(session, user, source, target)

    assert response.status_code == 200, response.text
    copied = session.templates[target.id]
    assert response.json()["design"] == design
    assert response.json()["back_design"] == back
    assert copied.uuid != source_template.uuid
    assert copied.school_id == target.id
    assert copied.design is not source_template.design
    assert copied.design["nested"] is not source_template.design["nested"]
    assert copied.public_token is None
    assert copied.public_enabled is not True
    assert source_template.design == design
    copied.design["nested"]["value"] = 9
    assert source_template.design["nested"]["value"] == 1


def test_copy_replaces_existing_destination_only_with_matching_version():
    source, target = _school(10), _school(20)
    user = _user()
    source_template = _template(source.id, {"version": 1, "value": "new"})
    target_template = _template(target.id, {"version": 1, "value": "old"})
    target_template.name = "Existing"
    target_template.public_token = "keep-me"
    target_template.public_enabled = True
    session = _CopySession(
        schools=[source, target], user=user,
        accesses={source.id: _access(source.id), target.id: _access(target.id)},
        templates={source.id: source_template, target.id: target_template},
    )

    stale = _request(
        session, user, source, target,
        expected_marker=(target_template.updated_at - timedelta(seconds=1)).isoformat(),
    )
    assert stale.status_code == 409
    assert target_template.design["value"] == "old"
    assert session.commits == 0

    success = _request(
        session, user, source, target,
        expected_marker=target_template.updated_at.isoformat(),
    )
    assert success.status_code == 200, success.text
    assert target_template.design["value"] == "new"
    assert target_template.public_token == "keep-me"
    assert target_template.public_enabled is True
    assert source_template.design["value"] == "new"


def test_existing_destination_requires_a_concurrency_token():
    source, target = _school(10), _school(20)
    user = _user()
    session = _CopySession(
        schools=[source, target], user=user,
        accesses={source.id: _access(source.id), target.id: _access(target.id)},
        templates={
            source.id: _template(source.id, {"version": 1}),
            target.id: _template(target.id, {"version": 1}),
        },
    )
    response = _request(session, user, source, target)
    assert response.status_code == 409
    assert session.commits == 0


def test_copy_rejects_same_school_and_missing_source_template():
    school = _school(10)
    user = _user()
    same_session = _CopySession(
        schools=[school], user=user, accesses={school.id: _access(school.id)}
    )
    same = _request(same_session, user, school, school)
    assert same.status_code == 400

    target = _school(20)
    missing_session = _CopySession(
        schools=[school, target], user=user,
        accesses={school.id: _access(school.id), target.id: _access(target.id)},
    )
    missing = _request(missing_session, user, school, target)
    assert missing.status_code == 404
    assert missing_session.commits == 0


@pytest.mark.parametrize(
    "accesses, expected_detail",
    [
        ({20: _access(20)}, "You do not have access to this school"),
        (
            {10: _access(10), 20: _access(20, "card_operator")},
            "Only a school or platform administrator can edit card templates",
        ),
    ],
)
def test_copy_enforces_source_and_destination_authorization(accesses, expected_detail):
    source, target = _school(10), _school(20)
    user = _user()
    session = _CopySession(
        schools=[source, target], user=user, accesses=accesses,
        templates={source.id: _template(source.id, {"version": 1})},
    )
    response = _request(session, user, source, target)
    assert response.status_code == 403
    assert response.json()["detail"] == expected_detail
    assert session.commits == 0


def test_platform_administrator_can_copy_between_schools_without_memberships():
    source, target = _school(10), _school(20)
    user = _user(platform=True)
    session = _CopySession(
        schools=[source, target], user=user,
        templates={source.id: _template(source.id, {"version": 1})},
    )
    response = _request(session, user, source, target)
    assert response.status_code == 200, response.text


def test_custom_fields_are_remapped_everywhere_and_standard_bindings_are_unchanged():
    source, target = _school(10), _school(20)
    user = _user()
    source_a = _field(source.id, key="house")
    source_b = _field(source.id, key="route", entity="staff", data_type="number")
    target_a = _field(target.id, key="house")
    target_b = _field(target.id, key="route", entity="staff", data_type="number")
    front = _document(source_a.uuid, source_b.uuid)
    back = deepcopy(front)
    session = _CopySession(
        schools=[source, target], user=user,
        accesses={source.id: _access(source.id), target.id: _access(target.id)},
        templates={source.id: _template(source.id, front, back=back)},
        fields=[source_a, source_b, target_a, target_b],
    )
    response = _request(session, user, source, target)
    assert response.status_code == 200, response.text
    for side in (response.json()["design"], response.json()["back_design"]):
        elements = side["elements"]
        assert elements[0]["data"]["field"] == "school_name"
        assert elements[1]["data"]["field_uuid"] == str(target_a.uuid)
        assert elements[2]["data"]["fields"][0]["field"] == "full_name"
        assert elements[2]["data"]["fields"][1]["field_uuid"] == str(target_a.uuid)
        assert elements[2]["data"]["fields"][2]["field_uuid"] == str(target_b.uuid)
        assert elements[3]["data"]["field_uuid"] == str(target_b.uuid)


@pytest.mark.parametrize("destination_variant", ["missing", "wrong_type", "inactive"])
def test_unmappable_custom_fields_fail_with_structured_details(destination_variant):
    source, target = _school(10), _school(20)
    user = _user()
    source_field = _field(source.id, key="house")
    target_fields = []
    if destination_variant == "wrong_type":
        target_fields = [_field(target.id, key="house", data_type="number")]
    elif destination_variant == "inactive":
        target_fields = [_field(target.id, key="house", active=False)]
    session = _CopySession(
        schools=[source, target], user=user,
        accesses={source.id: _access(source.id), target.id: _access(target.id)},
        templates={source.id: _template(source.id, _document(source_field.uuid))},
        fields=[source_field, *target_fields],
    )
    response = _request(session, user, source, target)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "unmapped_custom_fields"
    assert detail["unresolved_fields"][0]["field_key"] == "house"
    assert detail["unresolved_fields"][0]["locations"] == [
        "front.elements[1].data.field_uuid",
        "front.elements[2].data.fields[1].field_uuid",
        "front.elements[3].data.field_uuid",
    ]
    assert target.id not in session.templates
    assert session.commits == 0
