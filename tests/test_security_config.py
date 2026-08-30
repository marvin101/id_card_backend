from app.core.config import Settings


def test_access_token_expiry_minutes_can_be_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "45")

    configured = Settings(
        db_host="localhost",
        db_name="campusid_test",
        db_user="campusid_test",
        db_password="campusid_test",
        secret_key="test-only-secret",
        supabase_url="https://example.supabase.co",
        supabase_secret_key="test-only-key",
        _env_file=None,
    )

    assert configured.access_token_expire_minutes == 45
