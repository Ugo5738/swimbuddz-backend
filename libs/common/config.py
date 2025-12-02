from functools import lru_cache
from typing import Literal, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Global application settings."""

    # Application
    ENVIRONMENT: Literal["local", "development", "production"] = "local"
    LOG_LEVEL: str = "INFO"
    ADMIN_EMAIL: str = "admin@admin.com"
    TIMEZONE: str = "Africa/Lagos"

    # Database
    DATABASE_URL: str
    DB_POOL_SIZE: int = 40  # Increased for better performance
    DB_MAX_OVERFLOW: int = 20  # Increased for better performance
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800

    # Supabase
    # Default placeholder values keep local/test runs from failing when Supabase
    # credentials are not required. Real deployments should override via env.
    SUPABASE_URL: str = "http://localhost"
    SUPABASE_ANON_KEY: str = "test-anon-key"
    SUPABASE_SERVICE_ROLE_KEY: str = "test-service-role-key"
    SUPABASE_JWT_SECRET: str = "test-jwt-secret"
    SUPABASE_PROJECT_ID: str = "test-project-id"

    # Gateway
    GATEWAY_URL: str = "http://localhost:8000"

    # Microservices URLs
    MEMBERS_SERVICE_URL: str = "http://members-service:8001"
    SESSIONS_SERVICE_URL: str = "http://sessions-service:8002"
    ATTENDANCE_SERVICE_URL: str = "http://attendance-service:8003"
    COMMUNICATIONS_SERVICE_URL: str = "http://communications-service:8004"
    PAYMENTS_SERVICE_URL: str = "http://payments-service:8005"
    ACADEMY_SERVICE_URL: str = "http://academy-service:8006"
    MEDIA_SERVICE_URL: str = "http://media-service:8008"
    EVENTS_SERVICE_URL: str = "http://events-service:8007"
    TRANSPORT_SERVICE_URL: str = "http://transport-service:8009"

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @field_validator("DATABASE_URL")
    @classmethod
    def assemble_db_connection(cls, v: Optional[str]) -> str:
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
