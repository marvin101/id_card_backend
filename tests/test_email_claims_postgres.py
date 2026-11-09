"""PostgreSQL-only concurrency tests for email claim helpers.

These tests are opt-in. They refuse to run unless the configured database is
exactly the disposable local database ``campusid_email_test``.
Run with RUN_EMAIL_CLAIMS_POSTGRES=1 after configuring DB_* environment vars.
"""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import os

import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.email_claims import (
    EmailClaimConflict,
    promote_pending_email,
    reserve_current_email,
    reserve_pending_email,
)
from app.models.email_claim import EmailClaim
from app.models.users import User



@pytest.fixture(scope="module")
def postgres_sessions():
    if os.getenv("RUN_EMAIL_CLAIMS_POSTGRES") != "1":
        pytest.skip(
            "Set RUN_EMAIL_CLAIMS_POSTGRES=1 to run the isolated PostgreSQL tests."
        )

    # Hard safety boundary: never run this test module against idcard, Supabase,
    # or any database other than the explicitly created disposable test DB.
    if settings.db_name != "campusid_email_test":
        pytest.fail(
            "Refusing to run PostgreSQL email-claim tests: "
            f"DB_NAME is {settings.db_name!r}, expected 'campusid_email_test'."
        )

    if settings.db_host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail(
            "Refusing to run PostgreSQL email-claim tests against a non-local host."
        )

    engine_url = (
        f"postgresql+psycopg://{settings.db_user}:"
        f"{settings.db_password}@{settings.db_host}:"
        f"{settings.db_port}/{settings.db_name}"
    )
    # URL.create avoids parsing problems if the password contains URL characters.
    from sqlalchemy.engine import URL

    engine_url = URL.create(
        "postgresql+psycopg",
        username=settings.db_user,
        password=settings.db_password,
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
        query={"sslmode": settings.db_sslmode},
    )
    engine = create_engine(engine_url, pool_pre_ping=True, pool_size=5, max_overflow=2)

    try:
        with engine.connect() as connection:
            database_name = connection.execute(
                text("SELECT current_database()")
            ).scalar_one()
            revision = connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            ).scalar_one_or_none()

        if database_name != "campusid_email_test":
            pytest.fail(
                "Refusing to run: PostgreSQL connected to "
                f"{database_name!r}, not 'campusid_email_test'."
            )
        if revision != "d1c7ce923227":
            pytest.fail(
                "Refusing to run: expected Alembic revision "
                f"'d1c7ce923227', found {revision!r}."
            )

        yield sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    finally:
        engine.dispose()


@pytest.fixture
def make_users(postgres_sessions):
    """Create uniquely named disposable users and delete them after each test."""
    created_ids: list[int] = []
    suffix = uuid4().hex

    def create_user(label: str, email: str | None = None) -> User:
        with postgres_sessions() as db:
            user = User(
                uuid=uuid4(),
                username=f"email-claim-{label}-{suffix}",
                full_name=f"Email Claim {label}",
                password_hash="not-a-real-password-hash",
                email=email,
                email_verified_at=None,
                pending_email=None,
                is_active=True,
                is_platform_admin=False,
                platform_role=None,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            created_ids.append(user.id)
            return user

    yield create_user

    if created_ids:
        with postgres_sessions() as db:
            db.execute(delete(User).where(User.id.in_(created_ids)))
            db.commit()


def _run_two_workers(session_factory, operation):
    """Start two independent DB sessions at the same synchronization point."""
    barrier = Barrier(2)

    def worker(worker_number: int) -> str:
        with session_factory() as db:
            try:
                barrier.wait(timeout=15)
                operation(db, worker_number)
                db.commit()
                return "success"
            except EmailClaimConflict:
                db.rollback()
                return "conflict"
            except BaseException:
                db.rollback()
                raise

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(worker, 1),
            executor.submit(worker, 2),
        ]
        return [future.result(timeout=30) for future in futures]


def _claims_for_user(db: Session, user_id: int) -> list[EmailClaim]:
    return db.execute(
        select(EmailClaim)
        .where(EmailClaim.user_id == user_id)
        .order_by(EmailClaim.claim_type)
    ).scalars().all()


def test_concurrent_current_reservation_for_same_email_has_one_winner(
    postgres_sessions, make_users
):
    first = make_users("current-race-a")
    second = make_users("current-race-b")
    email = f"{uuid4().hex}@current-race.test"

    def operation(db: Session, worker_number: int) -> None:
        user_id = first.id if worker_number == 1 else second.id
        user = db.get(User, user_id)
        reserve_current_email(db, user_id, email)
        user.email = email

    outcomes = _run_two_workers(postgres_sessions, operation)

    assert sorted(outcomes) == ["conflict", "success"]

    with postgres_sessions() as db:
        claim = db.get(EmailClaim, email)
        assert claim is not None
        assert claim.claim_type == "current"
        assert claim.user_id in {first.id, second.id}
        assert sum(
            len(_claims_for_user(db, user_id))
            for user_id in (first.id, second.id)
        ) == 1


def test_concurrent_pending_reservation_for_same_email_has_one_winner(
    postgres_sessions, make_users
):
    first = make_users("pending-race-a", f"{uuid4().hex}@old.test")
    second = make_users("pending-race-b", f"{uuid4().hex}@old.test")
    email = f"{uuid4().hex}@pending-race.test"

    def operation(db: Session, worker_number: int) -> None:
        user_id = first.id if worker_number == 1 else second.id
        user = db.get(User, user_id)
        reserve_pending_email(db, user, email)
        user.pending_email = email

    outcomes = _run_two_workers(postgres_sessions, operation)

    assert sorted(outcomes) == ["conflict", "success"]

    with postgres_sessions() as db:
        claim = db.get(EmailClaim, email)
        assert claim is not None
        assert claim.claim_type == "pending"
        assert claim.user_id in {first.id, second.id}
        assert sum(
            len([row for row in _claims_for_user(db, user_id)
                 if row.email_key == email])
            for user_id in (first.id, second.id)
        ) == 1


def test_concurrent_pending_replacements_for_same_user_leave_one_consistent_claim(
    postgres_sessions, make_users
):
    current_email = f"{uuid4().hex}@current.test"
    user = make_users("pending-replacement", current_email)
    first_pending = f"{uuid4().hex}@pending.test"
    second_pending = f"{uuid4().hex}@pending.test"

    with postgres_sessions() as db:
        reserve_current_email(db, user.id, current_email)
        db.commit()

    def operation(db: Session, worker_number: int) -> None:
        account = db.get(User, user.id)
        target = first_pending if worker_number == 1 else second_pending
        reserve_pending_email(db, account, target)
        account.pending_email = target

    outcomes = _run_two_workers(postgres_sessions, operation)

    assert outcomes == ["success", "success"] or outcomes == ["success", "success"]

    with postgres_sessions() as db:
        db.refresh(user) if user in db else None
        account = db.get(User, user.id)
        rows = _claims_for_user(db, user.id)
        pending_rows = [row for row in rows if row.claim_type == "pending"]
        current_rows = [row for row in rows if row.claim_type == "current"]

        assert len(current_rows) == 1
        assert current_rows[0].email_key == current_email
        assert len(pending_rows) == 1
        assert pending_rows[0].email_key in {first_pending, second_pending}
        assert account.pending_email == pending_rows[0].email_key


def test_promotion_racing_with_another_users_current_reservation_is_exclusive(
    postgres_sessions, make_users
):
    current_email = f"{uuid4().hex}@old.test"
    pending_email = f"{uuid4().hex}@promotion-race.test"
    owner = make_users("promotion-owner", current_email)
    competitor = make_users("promotion-competitor")

    with postgres_sessions() as db:
        account = db.get(User, owner.id)
        reserve_current_email(db, owner.id, current_email)
        reserve_pending_email(db, account, pending_email)
        account.pending_email = pending_email
        db.commit()

    def operation(db: Session, worker_number: int) -> None:
        if worker_number == 1:
            account = db.get(User, owner.id)
            promote_pending_email(db, owner.id, pending_email)
            account.email = pending_email
            account.pending_email = None
        else:
            account = db.get(User, competitor.id)
            reserve_current_email(db, competitor.id, pending_email)
            account.email = pending_email

    outcomes = _run_two_workers(postgres_sessions, operation)

    assert sorted(outcomes) == ["conflict", "success"]

    with postgres_sessions() as db:
        promoted_claim = db.get(EmailClaim, pending_email)
        assert promoted_claim is not None
        assert promoted_claim.user_id == owner.id
        assert promoted_claim.claim_type == "current"
        assert db.get(EmailClaim, current_email) is None
        assert _claims_for_user(db, competitor.id) == []


def test_concurrent_idempotent_current_reservations_for_same_user_remain_single(
    postgres_sessions, make_users
):
    user = make_users("same-user-current")
    email = f"{uuid4().hex}@idempotent.test"

    def operation(db: Session, _worker_number: int) -> None:
        account = db.get(User, user.id)
        reserve_current_email(db, user.id, email)
        account.email = email

    outcomes = _run_two_workers(postgres_sessions, operation)

    assert outcomes == ["success", "success"]

    with postgres_sessions() as db:
        rows = _claims_for_user(db, user.id)
        assert len(rows) == 1
        assert rows[0].email_key == email
        assert rows[0].claim_type == "current"
