from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global application settings."""

    # Application
    ENVIRONMENT: Literal["local", "development", "production"] = "local"
    LOG_LEVEL: str = "INFO"
    ADMIN_EMAILS: list[str] = ["admin@swimbuddz.com"]

    # Database
    DATABASE_URL: str

    # Supabase
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_JWT_SECRET: str
    SUPABASE_PROJECT_ID: str
    
    # Gateway
    GATEWAY_URL: str = "http://localhost:8000"
    
    # Microservices URLs
    MEMBERS_SERVICE_URL: str = "http://members-service:8001"
    SESSIONS_SERVICE_URL: str = "http://sessions-service:8002"
    ATTENDANCE_SERVICE_URL: str = "http://attendance-service:8003"
    COMMUNICATIONS_SERVICE_URL: str = "http://communications-service:8004"
    PAYMENTS_SERVICE_URL: str = "http://payments-service:8005"
    ACADEMY_SERVICE_URL: str = "http://academy-service:8006"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @field_validator("DATABASE_URL")
    @classmethod
    def assemble_db_connection(cls, v: str | None) -> str:
        if isinstance(v, str):
            if v.startswith("postgresql://"):
                return v.replace("postgresql://", "postgresql+psycopg://", 1)
        return v


@lru_cache
def get_settings() -> Settings:
    """
    Return the global settings instance, cached.
    """
    return Settings()
