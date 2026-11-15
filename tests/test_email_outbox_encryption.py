import pytest
from cryptography.fernet import Fernet

from app.core import email_outbox
import app.workers.email_outbox as worker


def test_outbox_payload_is_encrypted_and_round_trips(monkeypatch):
    monkeypatch.setattr(email_outbox.settings, "email_outbox_encryption_key", Fernet.generate_key().decode())
    payload = {"user_id": 42, "recipient": "person@example.com", "raw_token": "secret-reset-token"}

    encrypted = email_outbox.encrypt_payload(payload)

    assert "secret-reset-token" not in encrypted
    assert "person@example.com" not in encrypted
    assert email_outbox.decrypt_payload(encrypted) == payload


def test_outbox_requires_dedicated_encryption_key(monkeypatch):
    monkeypatch.setattr(email_outbox.settings, "email_outbox_encryption_key", "")
    with pytest.raises(email_outbox.OutboxConfigurationError):
        email_outbox.encrypt_payload({"raw_token": "secret"})


def test_outbox_rejects_tampered_ciphertext(monkeypatch):
    monkeypatch.setattr(email_outbox.settings, "email_outbox_encryption_key", Fernet.generate_key().decode())
    encrypted = email_outbox.encrypt_payload({"raw_token": "secret"})
    tampered = encrypted[:-2] + "AA"

    with pytest.raises(ValueError, match="cannot be decrypted"):
        email_outbox.decrypt_payload(tampered)


def test_validate_encryption_key_accepts_valid_key(monkeypatch):
    monkeypatch.setattr(
        email_outbox.settings,
        "email_outbox_encryption_key",
        Fernet.generate_key().decode("ascii"),
    )
    email_outbox.validate_encryption_key()


@pytest.mark.parametrize("key", ["", "not-a-valid-fernet-key", "invalid-☃"])
def test_validate_encryption_key_rejects_missing_or_invalid_key(monkeypatch, key):
    monkeypatch.setattr(email_outbox.settings, "email_outbox_encryption_key", key)
    with pytest.raises(email_outbox.OutboxConfigurationError):
        email_outbox.validate_encryption_key()


@pytest.mark.parametrize("key", ["", "not-a-valid-fernet-key"])
def test_process_one_rejects_bad_key_before_claiming_job(monkeypatch, key):
    monkeypatch.setattr(email_outbox.settings, "email_outbox_encryption_key", key)

    def unexpected_claim(_worker_id):
        pytest.fail("A job must not be claimed with an invalid encryption key")

    monkeypatch.setattr(worker, "_claim_one", unexpected_claim)
    with pytest.raises(email_outbox.OutboxConfigurationError):
        worker.process_one("test-worker")

@pytest.mark.parametrize("key", ["", "not-a-valid-fernet-key"])
def test_run_worker_rejects_bad_key_before_startup(monkeypatch, key):
    monkeypatch.setattr(
        email_outbox.settings,
        "email_outbox_encryption_key",
        key,
    )

    def unexpected_signal_registration(*args, **kwargs):
        pytest.fail("Worker must validate encryption key before registering signals")

    monkeypatch.setattr(
        worker.signal,
        "signal",
        unexpected_signal_registration,
    )

    with pytest.raises(email_outbox.OutboxConfigurationError):
        worker.run_worker()