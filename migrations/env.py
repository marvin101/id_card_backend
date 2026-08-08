from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import Base

# Import all models so Alembic can detect every table.
from app.models import (
    AcademicSession,
    School,
    SchoolClass,
    Section,
    Student,
    User,
    UserSchoolAccess,
)


# ==========================================================
# Alembic Config
# ==========================================================

config = context.config


# ==========================================================
# Logging
# ==========================================================

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# ==========================================================
# SQLAlchemy Metadata
# ==========================================================

target_metadata = Base.metadata


# ==========================================================
# Database URL
# ==========================================================

database_url = URL.create(
    drivername="postgresql+psycopg",
    username=settings.db_user,
    password=settings.db_password,
    host=settings.db_host,
    port=settings.db_port,
    database=settings.db_name,
)


# ==========================================================
# Offline Migration
# ==========================================================

def run_migrations_offline() -> None:
    """Run migrations without a database connection."""

    context.configure(
        url=database_url.render_as_string(hide_password=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={
            "paramstyle": "named",
        },
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ==========================================================
# Online Migration
# ==========================================================

def run_migrations_online() -> None:
    """Run migrations using a live database connection."""

    connectable = create_engine(
        database_url,
        poolclass=NullPool,
    )

    with connectable.connect() as connection:

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# ==========================================================
# Run
# ==========================================================

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()