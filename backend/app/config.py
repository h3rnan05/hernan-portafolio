"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ─── Modeling constants ─────────────────────────────────────────────────────
# Shared defaults used by the refit pipeline (CLI, API, feature selection).
# Keep them here so a single edit propagates to every caller.
K_PER_STOCK = 3


class Settings(BaseSettings):
    """Pydantic settings — loaded from .env or process env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = Field(
        ...,
        description="Async PG URL (asyncpg driver). Used by the app at runtime.",
    )
    database_url_sync: str = Field(
        ...,
        description="Sync PG URL (psycopg2 driver). Used by Alembic only.",
    )

    # Data providers
    fred_api_key: str = Field(..., description="FRED API key (mandatory)")
    eodhd_api_key: str = ""  # primary for global EOD prices ($19.99/mo)
    polygon_api_key: str = ""
    alpha_vantage_api_key: str = ""
    twelve_data_api_key: str = ""  # fallback for international markets
    stooq_api_key: str = ""  # legacy — Stooq paywalled CSV downloads in 2026
    http_user_agent: str = "Mozilla/5.0"

    # Modeling
    lag_days: int = 1  # brief §4.1 — t-1 lag matches the Excel
    refit_min_obs: int = 60  # min training rows before we'll attempt a fit

    # Broker
    capital_api_key: str = ""
    capital_identifier: str = ""
    capital_api_password: str = ""
    capital_base_url: str = "https://demo-api-capital.backend-capital.com"

    # App
    admin_bearer_token: str = "dev-token-change-me"
    allowed_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    @field_validator("admin_bearer_token", mode="after")
    @classmethod
    def _strip_admin_token(cls, v: str) -> str:
        # Render's env var editor (and copy/paste in general) can leave a
        # trailing newline/whitespace on secret values, which silently breaks
        # exact-match comparisons in require_admin. Strip defensively.
        return v.strip()

    # Observability
    sentry_dsn: str = ""
    slack_webhook_url: str = ""

    # AI assistant (Claude). Empty disables the /chat endpoint.
    anthropic_api_key: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton — instantiated once per process."""
    return Settings()  # type: ignore[call-arg]
