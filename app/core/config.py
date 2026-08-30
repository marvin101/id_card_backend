from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    db_host: str
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: str

    # Authentication
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=30, ge=1)

    # Supabase
    supabase_url: str
    supabase_secret_key: str

    # CORS
    cors_origins: str = (
        "http://localhost:49567,"
        "http://127.0.0.1:49567"
    )

    # Public authentication throttling. Limits are intentionally per process;
    # set enabled=false when throttling is provided by the edge instead.
    auth_rate_limit_enabled: bool = True
    auth_rate_limit_window_seconds: int = Field(default=60, ge=1)
    login_rate_limit_requests: int = Field(default=10, ge=1)
    registration_rate_limit_requests: int = Field(default=5, ge=1)

    # Only trust forwarded client addresses when this many known proxies sit
    # directly in front of the application. Keep at 0 for direct exposure.
    auth_rate_limit_trusted_proxy_hops: int = Field(default=0, ge=0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> List[str]:
        return [
            origin.strip()
            for origin in self.cors_origins.split(",")
            if origin.strip()
        ]


settings = Settings()
