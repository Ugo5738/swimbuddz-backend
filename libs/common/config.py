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
    DB_POOL_SIZE: int = 2  # Reduced to prevent MaxClientsInSessionMode error
    DB_MAX_OVERFLOW: int = 5  # Reduced to prevent MaxClientsInSessionMode error
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
    FRONTEND_URL: str = "http://localhost:3000"

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
    STORE_SERVICE_URL: str = "http://store-service:8010"
    AI_SERVICE_URL: str = "http://ai-service:8011"
    VOLUNTEER_SERVICE_URL: str = "http://volunteer-service:8012"
    WALLET_SERVICE_URL: str = "http://wallet-service:8013"

    # AI Service
    AI_DEFAULT_MODEL: str = "gpt-4o-mini"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Langfuse observability
    LANGFUSE_HOST: str = ""
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""

    # Payments / Pricing
    COMMUNITY_ANNUAL_FEE_NGN: int = 20000
    CLUB_QUARTERLY_FEE_NGN: int = 42500
    CLUB_BIANNUAL_FEE_NGN: int = 80000
    CLUB_ANNUAL_FEE_NGN: int = 150000
    WELCOME_BONUS_INCLUDE_COACHES: bool = False

    # Paystack (optional; used by payments_service)
    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_PUBLIC_KEY: str = ""
    PAYSTACK_API_BASE_URL: str = "https://api.paystack.co"
    # Where Paystack redirects the user after payment (webhook still does the real activation)
    PAYSTACK_CALLBACK_URL: Optional[str] = None

    # Flutterwave (optional; alternative payment provider)
    FLUTTERWAVE_SECRET_KEY: str = ""

    # Media Service / Storage
    STORAGE_BACKEND: str = "supabase"  # supabase or s3
    SUPABASE_STORAGE_BUCKET: str = "swimbuddz-media"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    # S3 bucket names (standardized naming)
    AWS_S3_BUCKET_PUBLIC: str = (
        ""  # For publicly accessible files (profiles, galleries)
    )
    AWS_S3_BUCKET_PRIVATE: str = ""  # For private files (documents, payment proofs)
    CLOUDFRONT_URL: str = ""  # CDN URL for public bucket

    # Admin configuration
    ADMIN_EMAILS: list[str] = ["admin@admin.com"]
    EMAIL_FROM_SUPPORT: str = ""
    EMAIL_FROM_BILLING: str = ""
    EMAIL_FROM_WELCOME: str = ""
    EMAIL_FROM_SALES: str = ""

    # Redis (for cross-service validation cache)
    REDIS_URL: str = "redis://localhost:6379"

    # SMTP / Email (Brevo)
    SMTP_HOST: str = "smtp-relay.brevo.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""  # Can also use BREVO_KEY
    BREVO_KEY: str = ""  # Alternative to SMTP_PASSWORD
    DEFAULT_FROM_EMAIL: str = "no-reply@swimbuddz.com"
    DEFAULT_FROM_NAME: str = "SwimBuddz"

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
