import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from app.core.school_access import (
    CARD_OPERATOR_ROLE,
    LEGACY_SCHOOL_ADMIN_ROLE,
    PLATFORM_ADMIN_ROLE,
    SCHOOL_ADMIN_ROLE,
    STAFF_ROLE,
    TEACHER_ROLE,
    is_platform_admin,
    require_card_data_access,
    require_card_operator,
    require_school_access,
    require_school_admin,
    require_school_role_management,
)


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Database:
    def __init__(self, access=None):
        self.access = access

    def execute(self, _statement):
        return _Result(self.access)


def _user(*, platform_role=None, legacy_platform_admin=False):
    return SimpleNamespace(
        id=1,
        platform_role=platform_role,
        is_platform_admin=legacy_platform_admin,
    )


def _access(role, *, school_id=10):
    return SimpleNamespace(user_id=1, school_id=school_id, role=role)


class AuthorizationMatrixTests(unittest.TestCase):
    def assert_forbidden(self, operation):
        with self.assertRaises(HTTPException) as raised:
            operation()
        self.assertEqual(raised.exception.status_code, 403)

    def test_platform_admin_bypasses_school_membership(self):
        user = _user(platform_role=PLATFORM_ADMIN_ROLE)
        db = _Database()

        self.assertTrue(is_platform_admin(user))
        self.assertIsNone(require_school_access(db, user, 10))
        self.assertIsNone(require_card_data_access(db, user, 10))
        self.assertIsNone(require_school_admin(db, user, 10, "forbidden"))
        self.assertIsNone(
            require_school_role_management(
                db,
                user,
                10,
                "forbidden",
                requested_role=CARD_OPERATOR_ROLE,
            )
        )

    def test_legacy_platform_admin_flag_remains_compatible(self):
        self.assertTrue(is_platform_admin(_user(legacy_platform_admin=True)))

    def test_school_admin_can_manage_card_data_and_ordinary_roles(self):
        user = _user()
        access = _access(SCHOOL_ADMIN_ROLE)
        db = _Database(access)

        self.assertIs(require_school_access(db, user, 10), access)
        self.assertIs(require_card_data_access(db, user, 10), access)
        self.assertIs(require_school_admin(db, user, 10, "forbidden"), access)
        self.assertIsNone(
            require_school_role_management(
                db,
                user,
                10,
                "forbidden",
                existing_role=TEACHER_ROLE,
                requested_role=STAFF_ROLE,
            )
        )

    def test_school_admin_cannot_manage_elevated_roles(self):
        user = _user()
        db = _Database(_access(SCHOOL_ADMIN_ROLE))

        self.assert_forbidden(
            lambda: require_school_role_management(
                db,
                user,
                10,
                "forbidden",
                requested_role=CARD_OPERATOR_ROLE,
            )
        )
        self.assert_forbidden(
            lambda: require_school_role_management(
                db,
                user,
                10,
                "forbidden",
                existing_role=SCHOOL_ADMIN_ROLE,
            )
        )

    def test_legacy_school_admin_keeps_admin_permissions(self):
        user = _user()
        access = _access(LEGACY_SCHOOL_ADMIN_ROLE)
        db = _Database(access)

        self.assertIs(require_card_data_access(db, user, 10), access)
        self.assertIs(require_school_admin(db, user, 10, "forbidden"), access)

    def test_card_operator_can_work_with_cards_but_not_administer_school(self):
        user = _user()
        access = _access(CARD_OPERATOR_ROLE)
        db = _Database(access)

        self.assertIs(require_card_data_access(db, user, 10), access)
        self.assertIs(require_card_operator(db, user, 10), access)
        self.assert_forbidden(
            lambda: require_school_admin(db, user, 10, "forbidden")
        )
        self.assert_forbidden(
            lambda: require_school_role_management(
                db,
                user,
                10,
                "forbidden",
                requested_role=TEACHER_ROLE,
            )
        )

    def test_teacher_and_staff_have_membership_without_card_data_access(self):
        user = _user()
        for role in (TEACHER_ROLE, STAFF_ROLE):
            with self.subTest(role=role):
                access = _access(role)
                db = _Database(access)
                self.assertIs(require_school_access(db, user, 10), access)
                self.assert_forbidden(
                    lambda: require_card_data_access(db, user, 10)
                )

    def test_missing_or_cross_school_membership_is_denied(self):
        user = _user()
        db = _Database()

        self.assert_forbidden(lambda: require_school_access(db, user, 20))
        self.assert_forbidden(lambda: require_card_data_access(db, user, 20))
        self.assert_forbidden(lambda: require_card_operator(db, user, 20))


if __name__ == "__main__":
    unittest.main()
