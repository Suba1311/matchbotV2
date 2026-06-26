"""Environment-driven settings.

These are *operational* concerns that change per environment / per developer and
must never be hardcoded. The DB schema in particular is read purely from
``DB_SCHEMA`` — no schema name appears anywhere in the code or SQL except as
this variable. Switching schemas later is a one-line env change.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Operational settings, sourced from environment variables / ``.env``.

    All variables are prefixed ``MATCHBOT_`` except the database ones, which use
    conventional names so existing tooling recognizes them.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Database -----------------------------------------------------------
    database_url: str = Field(
        default="postgresql://localhost:5432/matchbot",
        alias="DATABASE_URL",
        description="SQLAlchemy/psycopg connection URL for the writable store.",
    )
    db_schema: str = Field(
        default="public",
        alias="DB_SCHEMA",
        description="Postgres schema for all MatchBot tables. Code never "
        "hardcodes this — switch schemas by changing the env var.",
    )
    # Optional independent location for the read-only Member Universe. Defaults
    # to the same database/schema when unset.
    member_universe_url: str | None = Field(
        default=None,
        alias="MEMBER_UNIVERSE_URL",
        description="Optional separate connection URL for the Member Universe.",
    )

    # --- Runtime ------------------------------------------------------------
    runtime: str = Field(
        default="local",
        alias="MATCHBOT_RUNTIME",
        description="Runtime adapter: local | fargate | glue | snowflake.",
    )

    # --- Paths --------------------------------------------------------------
    config_dir: Path = Field(
        default=Path("config"),
        alias="MATCHBOT_CONFIG_DIR",
        description="Directory holding global.yaml and providers/.",
    )

    # --- Logging ------------------------------------------------------------
    log_level: str = Field(default="INFO", alias="MATCHBOT_LOG_LEVEL")
    log_json: bool = Field(
        default=False,
        alias="MATCHBOT_LOG_JSON",
        description="Emit JSON logs (prod) vs. human-readable console (dev).",
    )

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()

    @field_validator("db_schema")
    @classmethod
    def _validate_schema(cls, v: str) -> str:
        v = v.strip()
        if not v.replace("_", "").isalnum():
            raise ValueError(f"DB_SCHEMA must be alphanumeric/underscores, got {v!r}")
        return v

    @property
    def effective_member_universe_url(self) -> str:
        """Member Universe URL, falling back to the main database."""
        return self.member_universe_url or self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
