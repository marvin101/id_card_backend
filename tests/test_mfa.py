import pyotp
import pytest
from cryptography.fernet import InvalidToken

from app.core.mfa import (
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_recovery_codes,
    generate_totp_provisioning_uri,
    generate_totp_secret,
    hash_recovery_code,
    recovery_code_expiry,
    verify_recovery_code,
    verify_totp_code,
)


def test_totp_secret_encryption_round_trip():
    secret = generate_totp_secret()

    encrypted = encrypt_totp_secret(secret)

    assert encrypted != secret
    assert decrypt_totp_secret(encrypted) == secret


def test_totp_secret_encryption_produces_different_ciphertext():
    secret = generate_totp_secret()

    encrypted_one = encrypt_totp_secret(secret)
    encrypted_two = encrypt_totp_secret(secret)

    assert encrypted_one != encrypted_two
    assert decrypt_totp_secret(encrypted_one) == secret
    assert decrypt_totp_secret(encrypted_two) == secret


def test_decryption_rejects_invalid_ciphertext():
    with pytest.raises(ValueError):
        decrypt_totp_secret("invalid-ciphertext")


def test_totp_verification_accepts_valid_code():
    secret = generate_totp_secret()
    code = pyotp.TOTP(secret).now()

    assert verify_totp_code(secret, code)


def test_totp_verification_rejects_invalid_codes():
    secret = generate_totp_secret()

    assert not verify_totp_code(secret, "")
    assert not verify_totp_code(secret, "123")
    assert not verify_totp_code(secret, "abcdef")
    assert not verify_totp_code(secret, "000000")


def test_totp_provisioning_uri():
    secret = generate_totp_secret()

    uri = generate_totp_provisioning_uri(
        secret=secret,
        username="test-user",
    )

    assert uri.startswith("otpauth://totp/")
    assert "test-user" in uri
    assert "CampusID" in uri


def test_recovery_code_generation():
    codes = generate_recovery_codes()

    assert len(codes) == 10
    assert len(set(codes)) == 10
    assert all(len(code) == 16 for code in codes)


def test_recovery_code_hash_and_verification():
    code = generate_recovery_codes(1)[0]
    stored_hash = hash_recovery_code(code)

    assert stored_hash != code
    assert verify_recovery_code(code, stored_hash)
    assert verify_recovery_code(code.lower(), stored_hash)
    assert not verify_recovery_code(
        "0000000000000000",
        stored_hash,
    )


def test_recovery_code_expiry_is_in_the_future():
    expiry = recovery_code_expiry()

    assert expiry.tzinfo is not None
    assert expiry > __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    )


def test_invalid_recovery_code_count():
    with pytest.raises(ValueError):
        generate_recovery_codes(0)

    with pytest.raises(ValueError):
        generate_recovery_codes(51)