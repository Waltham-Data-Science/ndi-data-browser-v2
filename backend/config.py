"""Application configuration.

All settings are validated at startup via pydantic-settings. Missing required
variables cause a fast failure rather than a silent default.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, HttpUrl, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Look in both the backend dir (canonical) and CWD (dev override).
        env_file=(str(_BACKEND_DIR / ".env"), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # --- Required ---
    NDI_CLOUD_URL: HttpUrl = Field(..., description="Base URL of ndi-cloud-node, e.g. https://api.ndi-cloud.com/v1")
    REDIS_URL: str = Field(..., description="redis://host:port/db")
    SESSION_ENCRYPTION_KEY: str = Field(..., min_length=32, description="Fernet key for encrypting stored tokens")
    CSRF_SIGNING_KEY: str = Field(..., min_length=32, description="HMAC key for CSRF signing (hex)")

    # --- CORS ---
    CORS_ORIGINS: str = Field(default="http://localhost:5173")

    # --- Session ---
    SESSION_IDLE_TTL_SECONDS: int = 2 * 60 * 60
    SESSION_ABSOLUTE_TTL_SECONDS: int = 24 * 60 * 60

    # --- Cloud client ---
    CLOUD_HTTP_TIMEOUT_SECONDS: float = 30.0
    CLOUD_MAX_RETRIES: int = 3
    CLOUD_CIRCUIT_BREAKER_THRESHOLD: int = 5
    CLOUD_CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = 30
    CLOUD_POOL_SIZE: int = 50

    # --- Download host allowlist (PR-6 security fix) ---
    # Comma-separated host suffixes where download_file() may forward the user's
    # Bearer token. Exact-match entries (e.g. "s3.amazonaws.com") match the host
    # exactly; wildcard entries starting with "*." match both the suffix itself
    # and any subdomain of it. The cloud hostname is added to the runtime
    # allowlist dynamically (always allowed). See ADR notes in backend/clients/
    # _url_allowlist.py.
    DOWNLOAD_HOST_ALLOWLIST: str = Field(
        default=(
            "s3.amazonaws.com,"
            "*.s3.amazonaws.com,"
            "*.s3.us-east-1.amazonaws.com,"
            "*.s3.us-east-2.amazonaws.com,"
            "*.s3.us-west-1.amazonaws.com,"
            "*.s3.us-west-2.amazonaws.com,"
            "*.cloudfront.net"
        ),
        description=(
            "Comma-separated host suffixes where downloads may forward the user's "
            "Bearer token. Cloud hostname is added dynamically at runtime."
        ),
    )
    DOWNLOAD_ALLOWLIST_ENFORCE: bool = Field(
        default=False,
        description=(
            "If true, strip Authorization header when downloading from non-allowlisted "
            "hosts. If false (default), log a warning but still forward. Flip to true "
            "in Railway env after reviewing phase-1 logs."
        ),
    )

    # --- Rate limits ---
    RATE_LIMIT_READS_PER_MIN: int = 120
    RATE_LIMIT_QUERY_PER_MIN: int = 30
    RATE_LIMIT_BULK_FETCH_PER_MIN: int = 10
    RATE_LIMIT_LOGIN_PER_IP_15MIN: int = 5
    RATE_LIMIT_LOGIN_PER_USER_HOUR: int = 10

    # --- Ontology cache ---
    ONTOLOGY_CACHE_DB_PATH: str = "/tmp/ndi-ontology.db"
    ONTOLOGY_CACHE_TTL_DAYS: int = 30

    # --- Observability ---
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "json"
    SENTRY_DSN: str = ""
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def download_host_allowlist_list(self) -> list[str]:
        """Parsed list of allowlist host patterns (exact or `*.suffix`)."""
        return [h.strip() for h in self.DOWNLOAD_HOST_ALLOWLIST.split(",") if h.strip()]

    @field_validator("LOG_LEVEL")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def cloud_base_url(self) -> str:
        """Normalize ndi-cloud-node base URL (strip trailing slash)."""
        return str(self.NDI_CLOUD_URL).rstrip("/")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
