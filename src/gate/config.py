from datetime import timedelta
from typing import Any

from pydantic import SecretStr, TypeAdapter, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Used only when DEV is set and SECRET_KEY isn't. It's public, so anything
# signed with it can be forged: never for a deployment anyone else can reach.
DEV_SECRET_KEY = "dev-only-insecure-secret-key-never-use-in-production"
SECRET_KEY_MIN_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    upstream_base_url: str = "http://localhost:8001"
    database_url: str = "postgresql+asyncpg://postgres:devpassword@localhost:5432/gate"
    session_ttl: timedelta = timedelta(days=14)
    # Local development mode; currently it only permits DEV_SECRET_KEY.
    dev: bool = False
    # Signs pagination cursors. Required, and must be the same across restarts
    # and instances, or cursors already handed out stop working.
    secret_key: SecretStr

    @model_validator(mode="before")
    @classmethod
    def _default_secret_key_in_dev(cls, data: Any) -> Any:
        if not isinstance(data, dict) or data.get("secret_key"):
            return data
        if TypeAdapter(bool).validate_python(data.get("dev") or False):
            return {**data, "secret_key": DEV_SECRET_KEY}
        raise ValueError(
            "SECRET_KEY must be set. Generate one with: python -c 'import secrets; "
            "print(secrets.token_urlsafe(32))'. For local development only, "
            "DEV=true runs with a fixed, insecure key instead."
        )

    @field_validator("secret_key")
    @classmethod
    def _secret_key_is_long_enough(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < SECRET_KEY_MIN_LENGTH:
            raise ValueError(
                f"SECRET_KEY must be at least {SECRET_KEY_MIN_LENGTH} characters"
            )
        return value
