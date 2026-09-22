from datetime import timedelta

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    upstream_base_url: str = "http://localhost:8001"
    database_url: str = "postgresql+asyncpg://postgres:devpassword@localhost:5432/gate"
    session_ttl: timedelta = timedelta(days=14)
