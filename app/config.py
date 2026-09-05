"""Application settings, loaded once from the environment.

Every process in this system — the MCP server, the FastAPI app, the Streamlit
fallback UI, the eval runner and the build scripts — imports this module rather
than reading ``os.environ`` directly, so there is exactly one place where a
configuration key is named and typed.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved from this file rather than the working directory: the MCP server, the API
# and Streamlit are each launched from different cwds, and a relative DB path that
# silently resolves differently per process is a debugging trap we do not want.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Typed configuration for every entry point in the project."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # The .env is shared with the Next.js frontend and docker compose, which
        # contribute keys this model does not model. Ignoring them keeps one .env
        # rather than forcing a second file to drift out of sync.
        extra="ignore",
    )

    # --- LLM ---------------------------------------------------------------
    google_api_key: SecretStr | None = None
    llm_model: str = "gemini-2.5-flash"
    llm_temperature: Annotated[float, Field(ge=0.0, le=2.0)] = 0.2
    llm_max_retries: Annotated[int, Field(ge=1, le=10)] = 3

    # --- LLM fallback ------------------------------------------------------
    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-120b"
    llm_provider_order: str = "google,groq"

    # --- MCP ---------------------------------------------------------------
    mcp_host: str = "127.0.0.1"
    mcp_port: Annotated[int, Field(ge=1, le=65535)] = 8765
    mcp_server_url: str = "http://127.0.0.1:8765/mcp"

    # --- Data --------------------------------------------------------------
    db_path: Path = Path("app/data/financials.db")

    # --- Observability -----------------------------------------------------
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"

    # --- API ---------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: Annotated[int, Field(ge=1, le=65535)] = 8000
    cors_origins: str = "http://localhost:3000"
    rate_limit_per_minute: Annotated[int, Field(ge=1)] = 30
    log_level: str = "INFO"

    @field_validator(
        "google_api_key",
        "groq_api_key",
        "langfuse_public_key",
        "langfuse_secret_key",
        mode="before",
    )
    @classmethod
    def _blank_secret_is_absent(cls, value: object) -> object | None:
        """Treat an empty env var as an unset one.

        ``.env.example`` ships these keys with empty values, so a copied-but-unfilled
        ``.env`` would otherwise yield ``SecretStr("")`` — an object that is truthy,
        which would make "is a key configured?" checks answer yes for a blank key and
        push the failure downstream into an opaque 401 from the provider.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def db_file(self) -> Path:
        """The database location as an absolute path."""
        path = self.db_path
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins parsed from the comma-separated env var."""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def provider_order(self) -> list[str]:
        """LLM providers to try, in order, filtered to those actually configured.

        A provider named in the order but missing its key is dropped rather than
        raising: the reviewer should be able to run this with only a Gemini key, and a
        fallback that is not configured is simply not a fallback.
        """
        available = {"google": self.google_api_key, "groq": self.groq_api_key}
        requested = [name.strip().lower() for name in self.llm_provider_order.split(",")]
        return [name for name in requested if available.get(name) is not None]

    @property
    def langfuse_enabled(self) -> bool:
        """Whether tracing can be wired up.

        Both keys are required, so a half-configured project degrades to tracing-off
        rather than failing at request time. The reviewer must be able to run this
        repo without creating a Langfuse account.
        """
        return self.langfuse_public_key is not None and self.langfuse_secret_key is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, reading the environment only once."""
    return Settings()


settings: Settings = get_settings()
