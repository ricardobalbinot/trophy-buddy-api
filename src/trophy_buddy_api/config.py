"""Load server configuration without requiring credentials during builds or tests."""

from functools import lru_cache
from pathlib import Path
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Reuse the current Firebase project while keeping backend secrets server-only."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    firebase_project_id: str = ""
    google_application_credentials: str = ""
    firebase_client_email: str = ""
    firebase_private_key: SecretStr = SecretStr("")
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])


@lru_cache
def get_settings() -> Settings:
    """Load environment once per process; environment changes require a restart."""

    return Settings()
