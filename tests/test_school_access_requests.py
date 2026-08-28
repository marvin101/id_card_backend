import unittest
from types import SimpleNamespace
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy import UniqueConstraint

from app.api.users import _resolve_registration_school
from app.models.school_access_request import SchoolAccessRequest
from app.schemas.auth import UserCreate


class _QueryResult:
    def __init__(self, rows):
        self.rows = rows

    def scalar_one_or_none(self):
        return self.rows[0] if self.rows else None

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _RegistrationDb:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, _statement):
        return _QueryResult(self.rows)


class RegistrationRequestSchemaTests(unittest.TestCase):
    def test_registration_requires_school_and_designation(self) -> None:
        with self.assertRaises(ValidationError):
            UserCreate(
                username="new.user",
                password="password123",
                full_name="New User",
            )

    def test_registration_accepts_school_request_details(self) -> None:
        payload = UserCreate(
            username="new.user",
            password="password123",
            full_name="New User",
            designation="Teacher",
            school_name="Anita Intermediate College",
        )

        self.assertEqual(payload.designation, "Teacher")
        self.assertEqual(payload.school_name, "Anita Intermediate College")

    def test_registration_accepts_validated_school_uuid(self) -> None:
        payload = UserCreate(
            username="new.user",
            password="password123",
            full_name="New User",
            designation="Teacher",
            school_uuid="4be260c8-05bf-48bb-b7ec-f7a9e2d9cd3f",
        )

        self.assertEqual(
            payload.school_uuid,
            UUID("4be260c8-05bf-48bb-b7ec-f7a9e2d9cd3f"),
        )
        self.assertIsNone(payload.school_name)

    def test_valid_school_uuid_resolves_to_active_school(self) -> None:
        school = SimpleNamespace(id=7, school_name="Anita Intermediate College")
        payload = UserCreate(
            username="new.user",
            password="password123",
            full_name="New User",
            designation="Teacher",
            school_uuid="4be260c8-05bf-48bb-b7ec-f7a9e2d9cd3f",
        )

        self.assertIs(
            _resolve_registration_school(_RegistrationDb([school]), payload),
            school,
        )

    def test_missing_or_inactive_school_uuid_is_rejected(self) -> None:
        payload = UserCreate(
            username="new.user",
            password="password123",
            full_name="New User",
            designation="Teacher",
            school_uuid="4be260c8-05bf-48bb-b7ec-f7a9e2d9cd3f",
        )

        with self.assertRaises(HTTPException) as context:
            _resolve_registration_school(_RegistrationDb([]), payload)

        self.assertEqual(context.exception.status_code, status.HTTP_404_NOT_FOUND)

    def test_missing_legacy_school_name_is_rejected(self) -> None:
        payload = UserCreate(
            username="new.user",
            password="password123",
            full_name="New User",
            designation="Teacher",
            school_name="Missing School",
        )

        with self.assertRaises(HTTPException) as context:
            _resolve_registration_school(_RegistrationDb([]), payload)

        self.assertEqual(context.exception.status_code, status.HTTP_404_NOT_FOUND)

    def test_duplicate_legacy_school_name_is_rejected(self) -> None:
        payload = UserCreate(
            username="new.user",
            password="password123",
            full_name="New User",
            designation="Teacher",
            school_name="Duplicate School",
        )

        with self.assertRaises(HTTPException) as context:
            _resolve_registration_school(
                _RegistrationDb([SimpleNamespace(id=1), SimpleNamespace(id=2)]),
                payload,
            )

        self.assertEqual(context.exception.status_code, status.HTTP_409_CONFLICT)

    def test_only_one_request_exists_per_user_and_school(self) -> None:
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in SchoolAccessRequest.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }

        self.assertIn(("user_id", "school_id"), unique_columns)


if __name__ == "__main__":
    unittest.main()
