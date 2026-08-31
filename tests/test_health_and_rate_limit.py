from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import auth_rate_limiter
from app.main import app


class _HealthResult:
    def mappings(self):
        return self

    def one(self):
        return {"current_database": "test", "current_user": "test"}


class _HealthyDatabase:
    def execute(self, _statement):
        return _HealthResult()


class _UnavailableDatabase:
    def execute(self, _statement):
        raise RuntimeError("database details that must not reach the response")


def _override_db(database):
    def dependency():
        yield database

    app.dependency_overrides[get_db] = dependency


@pytest.fixture(autouse=True)
def _clean_app_state():
    app.dependency_overrides.clear()
    auth_rate_limiter.reset()
    yield
    app.dependency_overrides.clear()
    auth_rate_limiter.reset()


def test_liveness_health_remains_available_without_a_database():
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_cors_exposes_download_filename_header_to_browser_clients():
    origin = settings.cors_origin_list[0]

    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": origin})

    exposed = {
        header.strip().casefold()
        for header in response.headers["access-control-expose-headers"].split(",")
    }
    assert "content-disposition" in exposed


def test_database_health_is_200_when_connected():
    _override_db(_HealthyDatabase())

    with TestClient(app) as client:
        response = client.get("/health/check")

    assert response.status_code == 200
    assert response.json()["database"] == "connected"


def test_database_health_is_503_without_leaking_exception_details(caplog):
    _override_db(_UnavailableDatabase())

    with TestClient(app) as client:
        response = client.get("/health/check")

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "api": "running",
        "database": "disconnected",
    }
    assert "database details" not in response.text
    assert "Database readiness check failed" in caplog.text
    assert "database details" not in caplog.text


def test_login_limit_uses_the_rightmost_trusted_forwarded_hop(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "auth_rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings, "login_rate_limit_requests", 2)
    monkeypatch.setattr(settings, "auth_rate_limit_trusted_proxy_hops", 1)

    class _NoUserDatabase:
        def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    _override_db(_NoUserDatabase())

    with TestClient(app) as client:
        first = client.post(
            "/auth/login",
            headers={"X-Forwarded-For": "198.51.100.1, 203.0.113.10"},
            json={"username": "nobody", "password": "not-a-password"},
        )
        second = client.post(
            "/auth/login",
            headers={"X-Forwarded-For": "198.51.100.2, 203.0.113.10"},
            json={"username": "nobody", "password": "not-a-password"},
        )
        limited = client.post(
            "/auth/login",
            headers={"X-Forwarded-For": "198.51.100.3, 203.0.113.10"},
            json={"username": "nobody", "password": "not-a-password"},
        )

    assert first.status_code == 401
    assert second.status_code == 401
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1


def test_auth_rate_limiting_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", False)
    monkeypatch.setattr(settings, "login_rate_limit_requests", 1)

    class _NoUserDatabase:
        def execute(self, _statement):
            return SimpleNamespace(scalar_one_or_none=lambda: None)

    _override_db(_NoUserDatabase())

    with TestClient(app) as client:
        responses = [
            client.post(
                "/auth/login",
                json={"username": "nobody", "password": "not-a-password"},
            )
            for _ in range(3)
        ]

    assert [response.status_code for response in responses] == [401, 401, 401]


def test_registration_has_an_independent_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(settings, "registration_rate_limit_requests", 1)
    monkeypatch.setattr(settings, "auth_rate_limit_trusted_proxy_hops", 0)

    class _ExistingUserDatabase:
        def scalar(self, _statement):
            return SimpleNamespace(id=1)

    _override_db(_ExistingUserDatabase())
    payload = {
        "username": "already-taken",
        "password": "a-valid-password",
        "full_name": "Test User",
        "designation": "Teacher",
        "school_name": "Test School",
    }

    with TestClient(app) as client:
        first = client.post("/users/register", json=payload)
        limited = client.post("/users/register", json=payload)

    assert first.status_code == 409
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) >= 1
