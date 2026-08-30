import unittest

from pydantic import ValidationError
from sqlalchemy import UniqueConstraint

from app.models.school_access_request import SchoolAccessRequest
from app.schemas.auth import UserCreate


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

    def test_only_one_request_exists_per_user_and_school(self) -> None:
        unique_columns = {
            tuple(column.name for column in constraint.columns)
            for constraint in SchoolAccessRequest.__table__.constraints
            if isinstance(constraint, UniqueConstraint)
        }

        self.assertIn(("user_id", "school_id"), unique_columns)


if __name__ == "__main__":
    unittest.main()
