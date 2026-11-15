from fastapi.testclient import TestClient

from app.main import app


def test_public_password_reset_request_is_temporarily_disabled():
    with TestClient(app) as client:
        response = client.post(
            "/auth/password-reset/request",
            json={"email": "person@example.test"},
        )

    assert response.status_code == 410
    assert response.json()["detail"] == (
        "Self-service password reset is temporarily unavailable. "
        "Contact your Platform Administrator."
    )


def test_public_password_reset_confirmation_is_temporarily_disabled():
    with TestClient(app) as client:
        response = client.post(
            "/auth/password-reset/confirm",
            json={
                "token": "opaque-reset-token-value-1234567890",
                "new_password": "Replacement-password-456",
            },
        )

    assert response.status_code == 410
    assert response.json()["detail"] == (
        "Self-service password reset is temporarily unavailable. "
        "Contact your Platform Administrator."
    )
