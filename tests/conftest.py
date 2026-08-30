import os


# Importing the application constructs Settings. Keep tests independent from
# developer machines and production secrets by supplying inert local values.
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "campusid_test")
os.environ.setdefault("DB_USER", "campusid_test")
os.environ.setdefault("DB_PASSWORD", "campusid_test")
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-not-for-production")
os.environ.setdefault("ALGORITHM", "HS256")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SECRET_KEY", "test-only-supabase-key")
os.environ.setdefault("AUTH_RATE_LIMIT_TRUSTED_PROXY_HOPS", "0")
