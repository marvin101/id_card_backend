import os
from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

# Import the model package so all model mappings/relationships are registered.
import app.models  # noqa: F401
from app.core.config import settings


EXPECTED_DATABASE = "campusid_email_test"
REQUIRED_TABLES = {
    "users",
    "email_claims",
    "email_verification_tokens",
    "security_events",
}


@pytest.fixture(scope="session")
def pg_engine():
    if os.getenv("CAMPUSID_RUN_PG_INTEGRATION") != "1":
        pytest.skip(
            "PostgreSQL integration tests are opt-in. "
            "Set CAMPUSID_RUN_PG_INTEGRATION=1 to enable them."
        )

    # The normal test conftest supplies a safe default DB_NAME. The operator
    # must explicitly override it before pytest starts.
    if settings.db_name != EXPECTED_DATABASE:
        pytest.fail(
            f"Refusing PostgreSQL integration tests: configured database is "
            f"{settings.db_name!r}; expected {EXPECTED_DATABASE!r}. "
            "Set DB_NAME=campusid_email_test before starting pytest."
        )

    url = URL.create(
        drivername="postgresql+psycopg",
        username=settings.db_user,
        password=settings.db_password,
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
        query={"sslmode": settings.db_sslmode},
    )

    engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=8,
        max_overflow=4,
        pool_timeout=15,
    )

    try:
        with engine.connect() as conn:
            actual_database = conn.execute(
                text("SELECT current_database()")
            ).scalar_one()

            if actual_database != EXPECTED_DATABASE:
                pytest.fail(
                    "Refusing PostgreSQL integration tests: server reports "
                    f"database {actual_database!r}, expected "
                    f"{EXPECTED_DATABASE!r}."
                )

            missing = REQUIRED_TABLES - set(inspect(conn).get_table_names())
            if missing:
                pytest.fail(
                    "PostgreSQL integration schema is incomplete; missing "
                    f"tables: {', '.join(sorted(missing))}. "
                    "Apply migrations to the isolated test database first."
                )

        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def pg_session_factory(pg_engine):
    return sessionmaker(
        bind=pg_engine,
        class_=Session,
        expire_on_commit=False,
        autoflush=True,
    )


@pytest.fixture
def pg_cleanup(pg_session_factory):
    """Track test users and remove them after each test.

    Related email claims, verification tokens and security events are removed
    by their database ON DELETE CASCADE constraints.
    """
    user_ids: list[int] = []

    def track(user_id: int) -> int:
        user_ids.append(user_id)
        return user_id

    yield track

    if user_ids:
        with pg_session_factory() as db:
            from sqlalchemy import delete
            from app.models.users import User

            db.execute(delete(User).where(User.id.in_(user_ids)))
            db.commit()
