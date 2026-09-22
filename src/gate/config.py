import secrets
from datetime import timedelta

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    upstream_base_url: str = "http://localhost:8001"
    database_url: str = "postgresql+asyncpg://postgres:devpassword@localhost:5432/gate"
    session_ttl: timedelta = timedelta(days=14)
    # Signs pagination cursors. Set it in production: the random default
    # differs per process, so cursors wouldn't survive a restart or work
    # across instances.
    secret_key: SecretStr = Field(
        default_factory=lambda: SecretStr(secrets.token_urlsafe(32))
    )
