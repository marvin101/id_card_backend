import hashlib

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core import rate_limit
from app.core.rate_limit import (
    email_verification_rate_limiter,
    enforce_email_verification_rate_limit,
)


def make_request(ip: str = "203.0.113.10") -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/email/verification",
            "raw_path": b"/email/verification",
            "query_string": b"",
            "headers": [],
            "client": (ip, 12345),
            "server": ("testserver", 80),
        }
    )


@pytest.fixture(autouse=True)
def reset_limiter(monkeypatch):
    email_verification_rate_limiter.reset()
    monkeypatch.setattr(rate_limit.settings, "auth_rate_limit_enabled", True)
    monkeypatch.setattr(rate_limit.settings, "auth_rate_limit_window_seconds", 900)
    monkeypatch.setattr(
        rate_limit.settings, "email_verification_issue_rate_limit_requests", 3
    )
    monkeypatch.setattr(
        rate_limit.settings, "email_verification_verify_rate_limit_requests", 20
    )
    yield
    email_verification_rate_limiter.reset()


def test_issue_limit_is_applied_per_ip():
    request = make_request()

    for _ in range(3):
        enforce_email_verification_rate_limit(
            request, action="issue", identifier="person@example.test"
        )

    with pytest.raises(HTTPException) as exc:
        enforce_email_verification_rate_limit(
            request, action="issue", identifier="person@example.test"
        )

    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_issue_limit_is_also_applied_per_normalized_email():
    for ip in ("203.0.113.10", "203.0.113.11", "203.0.113.12"):
        enforce_email_verification_rate_limit(
            make_request(ip), action="issue", identifier="Person@Example.test"
        )

    with pytest.raises(HTTPException) as exc:
        enforce_email_verification_rate_limit(
            make_request("203.0.113.13"),
            action="issue",
            identifier="person@example.test",
        )

    assert exc.value.status_code == 429


def test_raw_identifier_is_not_used_as_limiter_key():
    identifier = "person@example.test"
    enforce_email_verification_rate_limit(
        make_request(), action="issue", identifier=identifier
    )

    expected_fingerprint = hashlib.sha256(
        identifier.strip().casefold().encode("utf-8")
    ).hexdigest()

    keys = list(email_verification_rate_limiter._requests)
    assert any(expected_fingerprint in key for key in keys)
    assert all(identifier not in key for key in keys)


def test_verify_limit_is_applied_per_ip():
    request = make_request()

    for _ in range(20):
        enforce_email_verification_rate_limit(
            request, action="verify", identifier="some-token-value"
        )

    with pytest.raises(HTTPException) as exc:
        enforce_email_verification_rate_limit(
            request, action="verify", identifier="some-token-value"
        )

    assert exc.value.status_code == 429


def test_rate_limit_can_be_disabled():
    rate_limit.settings.auth_rate_limit_enabled = False
    request = make_request()

    for _ in range(25):
        enforce_email_verification_rate_limit(
            request, action="verify", identifier="some-token-value"
        )
