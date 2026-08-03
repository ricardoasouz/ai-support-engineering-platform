"""Environment-based application settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(validation_alias="DATABASE_URL", min_length=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
    )


@lru_cache
def get_settings() -> Settings:
    """Return one validated settings object per process."""
    return Settings()
