from sqlalchemy import CheckConstraint

import app.models  # noqa: F401 - populate Base.metadata with every model
from app.core.database import Base, NAMING_CONVENTION
from app.core.postgres_patch_level import assess_postgres_version


def test_metadata_has_stable_constraint_naming_convention():
    assert set(NAMING_CONVENTION) == {"ix", "uq", "ck", "fk", "pk"}
    unnamed_checks = [
        f"{table.name}: {constraint.sqltext}"
        for table in Base.metadata.sorted_tables
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint) and not constraint.name
    ]
    assert unnamed_checks == []


def test_postgres_august_2026_security_floor():
    assert not assess_postgres_version(140023).is_secure
    assert assess_postgres_version(140024).is_secure
    assert not assess_postgres_version(150018).is_secure
    assert assess_postgres_version(150019).is_secure
    assert not assess_postgres_version(160014).is_secure
    assert assess_postgres_version(160015).is_secure
    assert not assess_postgres_version(170010).is_secure
    assert assess_postgres_version(170011).is_secure
    assert not assess_postgres_version(180005).is_secure
    assert assess_postgres_version(180006).is_secure
    assert not assess_postgres_version(130022).is_secure
    assert not assess_postgres_version(190000).is_secure
