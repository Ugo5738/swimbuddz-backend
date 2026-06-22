import os
from functools import lru_cache
from typing import Literal, Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# One env file per environment, selected by the ENV_FILE shell var. Matches
# the convention already in scripts/seed/all.sh, scripts/auth/*.py, and
# scripts/wallet/*.py. Defaults to .env.dev — production processes set
# ENV_FILE=.env.prod (or whatever the deploy decrypts to) before launch.
_ENV_FILE = os.environ.get("ENV_FILE", ".env.dev")


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
    POOLS_SERVICE_URL: str = "http://pools-service:8014"
    REPORTING_SERVICE_URL: str = "http://reporting-service:8015"
    CHAT_SERVICE_URL: str = "http://chat-service:8016"
    CORPORATE_SERVICE_URL: str = "http://corporate-service:8017"
    LEDGER_SERVICE_URL: str = "http://ledger-service:8018"

    # Ledger Service
    # SwimBuddz's own organization UUID in the multi-tenant ledger. Set per
    # environment (the org row is created by scripts/seed/ledger_org.py in PR-1).
    # Empty until seeded; emitters resolve org_id from this value.
    LEDGER_DEFAULT_ORG_ID: str = ""

    # Weather Service
    # Provider for forecast data. "open-meteo" (default) needs no API key, but
    # its free tier is NON-COMMERCIAL — point this at a commercial provider and
    # set WEATHER_API_KEY for production. See docs/design/WEATHER_SERVICE_DESIGN.md.
    WEATHER_PROVIDER: str = "open-meteo"
    WEATHER_API_KEY: str = ""
    WEATHER_FORECAST_DAYS: int = 14  # forecast horizon to cache (Open-Meteo max 16)
    WEATHER_CACHE_TTL_MINUTES: int = 180  # snapshot freshness window before refetch

    # AI Service
    AI_DEFAULT_MODEL: str = "gpt-4o-mini"
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""

    # Stroke Lab VLM coach (the new pipeline; provider-agnostic via LiteLLM).
    # Defaults are the eval-locked picks — override per-env without redeploying.
    STROKELAB_ENABLE_COACH: bool = True
    STROKELAB_COACH_GATE_MODEL: str = "o4-mini"  # view/usability gate (reasoning)
    STROKELAB_COACH_MODEL: str = "gpt-4o"  # holistic + per-instance coaching
    STROKELAB_COACH_SEGMENT_MODEL: str = "gpt-4o"  # per-frame phase classifier
    # Per-component on/off (the flow lives in pipeline/defaults.py; flip here).
    STROKELAB_COACH_SEGMENT: bool = True  # Stage-1 classify-every-frame + segment
    STROKELAB_COACH_RECOVERY: bool = True  # per-instance recovery coach
    STROKELAB_COACH_BODY_LINE: bool = False  # Stage-2 body-line aspect (off until eval)
    STROKELAB_COACH_ENTRY: bool = False  # Stage-2 entry/reach aspect (off until eval)
    STROKELAB_COACH_HEAD: bool = False  # Stage-2 head/breathing aspect (off until eval)
    STROKELAB_COACH_HOLISTIC: bool = True  # whole-clip coach
    STROKELAB_COACH_POSE_COUNT: bool = False  # Stage-1 deterministic pose recovery
    # count (yolov8-pose). OFF by default: runs the pose model on ~300 dense frames
    # per job (worker CPU) — enable per-env once the box is sized. Gates the
    # count/drilldown on detection confidence when on.
    STROKELAB_COACH_COLLATE: bool = True  # Stage-3 counts/metrics from instances
    STROKELAB_COACH_UNDERWATER: bool = (
        False  # dormant catch/pull/kick (underwater-only)
    )
    STROKELAB_COACH_SHARE_CARDS: bool = True  # render shareable per-finding cards
    STROKELAB_COACH_MAX_RECOVERIES: int = 1  # how many recoveries to coach up-front
    # Per-instance drilldown unlock (§12.5) is a CONFIG-DRIVEN accuracy gate, not a
    # hard flag: it unlocks when the last-measured segmentation accuracy meets the
    # bar. Raise the bar to ~80 when you trust it; lower it (e.g. 50) to preview the
    # per-stroke UI now. MEASURED is updated whenever validation/recovery_eval.py runs.
    STROKELAB_DRILLDOWN_MIN_ACCURACY_PCT: int = 80  # the bar to clear
    STROKELAB_DRILLDOWN_MEASURED_ACCURACY_PCT: int = 53  # last eval (within ±1)
    # Per-stroke inspect billing. OFF = comped (no credit charged) — honest while the
    # count isn't accuracy-validated. Flip ON for pay-per-inspect once accuracy clears.
    STROKELAB_INSPECT_BILLING: bool = False
    # The video-led timeline view (v2). OFF = the result page shows a LOCKED "Timeline"
    # tab. Flip ON once it's built + per-moment placement is accurate enough.
    STROKELAB_TIMELINE_VIEW: bool = False

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
    # Free club access granted to academy graduates (cohort end + N months).
    # Per docs/club/PRICING_STRATEGY.md: bridges the academy→club gap so habit
    # doesn't break. Admins can override per-member via /club/extend admin endpoint.
    POST_ACADEMY_FREE_CLUB_MONTHS: int = 1

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
    ADMIN_EMAILS: list[str] = ["admin@admin.com", "contactugodaniels@gmail.com"]
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
    BREVO_KEY: str = ""  # SMTP key (xsmtpsib-), used as the SMTP password
    # Brevo v3 HTTP API key (xkeysib-). Preferred transport: DigitalOcean (and
    # most cloud hosts) block outbound SMTP ports, so smtplib delivery times out.
    # When set, email is sent over HTTPS (api.brevo.com:443) instead of SMTP.
    BREVO_API_KEY: str = ""
    DEFAULT_FROM_EMAIL: str = "no-reply@swimbuddz.com"
    DEFAULT_FROM_NAME: str = "SwimBuddz"

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
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
