import hashlib

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core import rate_limit
from app.core.rate_limit import (
    enforce_password_reset_rate_limit,
    password_reset_rate_limiter,
)


def make_request(ip="203.0.113.20"):
    return Request({
        "type": "http", "asgi": {"version": "3.0"},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/auth/password-reset/request", "raw_path": b"/auth/password-reset/request",
        "query_string": b"", "headers": [], "client": (ip, 12345),
        "server": ("testserver", 80),
    })


@pytest.fixture(autouse=True)
def reset_limiter(monkeypatch):
    password_reset_rate_limiter.reset()
    monkeypatch.setattr(rate_limit.settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(rate_limit.settings, "password_reset_rate_limit_window_seconds", 900)
    monkeypatch.setattr(rate_limit.settings, "password_reset_request_rate_limit_requests", 2)
    monkeypatch.setattr(rate_limit.settings, "password_reset_confirm_rate_limit_requests", 4)
    yield
    password_reset_rate_limiter.reset()


def test_password_reset_request_is_limited_per_ip():
    request = make_request()
    for _ in range(2):
        enforce_password_reset_rate_limit(request, action="request", identifier="a@example.test")
    with pytest.raises(HTTPException) as exc:
        enforce_password_reset_rate_limit(request, action="request", identifier="b@example.test")
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_password_reset_request_is_also_limited_per_normalized_email():
    for ip in ("203.0.113.20", "203.0.113.21"):
        enforce_password_reset_rate_limit(
            make_request(ip), action="request", identifier="Person@Example.test"
        )
    with pytest.raises(HTTPException) as exc:
        enforce_password_reset_rate_limit(
            make_request("203.0.113.22"), action="request", identifier="person@example.test"
        )
    assert exc.value.status_code == 429


def test_raw_email_is_not_stored_in_limiter_key():
    email = "person@example.test"
    enforce_password_reset_rate_limit(make_request(), action="request", identifier=email)
    fingerprint = hashlib.sha256(email.encode()).hexdigest()
    keys = list(password_reset_rate_limiter._requests)
    assert any(fingerprint in key for key in keys)
    assert all(email not in key for key in keys)
