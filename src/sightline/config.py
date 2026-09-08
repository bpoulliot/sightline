"""Deployment configuration.

Nothing institution-specific is compiled in (SPEC §04). Every deployment-specific value is
configuration with an environment fallback and no default that points at any institution or host:
Canvas base URL, Canvas API token, Ollama endpoint, optional OpenAI-compatible escalation
endpoint. A hardcoded university domain or LAN address anywhere in this package is a bug.

Constructing :class:`Settings` registers its secret values with :mod:`sightline.redaction`, so
they are scrubbed from logs and error surfaces without any call site opting in. That includes the
Canvas **base URL**, which is treated as a secret because it identifies the institution.

Note for item 02: because the base URL is registered, a connection test that wants to report
*which host answered* must render it deliberately from the setting rather than letting it travel
through a log line.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .paths import DataPaths
from .redaction import register_secret


class Settings(BaseSettings):
    """Runtime configuration, read from the environment and an optional ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # -- Canvas ------------------------------------------------------------------------------
    canvas_base_url: str | None = Field(default=None, validation_alias="CANVAS_BASE_URL")
    canvas_api_token: SecretStr | None = Field(default=None, validation_alias="CANVAS_API_TOKEN")

    # -- Working data ------------------------------------------------------------------------
    data_dir: Path = Field(default=Path("./data"), validation_alias="SIGHTLINE_DATA_DIR")

    # -- Local model host --------------------------------------------------------------------
    ollama_base_url: str | None = Field(default=None, validation_alias="OLLAMA_BASE_URL")
    ollama_concurrency: int = Field(default=1, ge=1, le=8, validation_alias="OLLAMA_CONCURRENCY")

    # -- Optional escalation (OpenAI-compatible) ---------------------------------------------
    escalation_base_url: str | None = Field(
        default=None, validation_alias="SIGHTLINE_ESCALATION_BASE_URL"
    )
    escalation_api_key: SecretStr | None = Field(
        default=None, validation_alias="SIGHTLINE_ESCALATION_API_KEY"
    )

    #: Absolute path to ``docs/tokens.css``. Empty means "find it" — see :mod:`sightline.assets`.
    tokens_css_path: Path | None = Field(default=None, validation_alias="SIGHTLINE_TOKENS_CSS")

    # -- Server ------------------------------------------------------------------------------
    host: str = Field(default="127.0.0.1", validation_alias="SIGHTLINE_HOST")
    port: int = Field(default=8000, ge=1, le=65535, validation_alias="SIGHTLINE_PORT")

    # -- Logging -----------------------------------------------------------------------------
    log_level: str = Field(default="INFO", validation_alias="SIGHTLINE_LOG_LEVEL")
    log_format: Literal["json", "console"] = Field(
        default="json", validation_alias="SIGHTLINE_LOG_FORMAT"
    )

    @field_validator("canvas_base_url", "ollama_base_url", "escalation_base_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip().rstrip("/")
        return text or None

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, value: str) -> str:
        return value.strip().upper()

    def model_post_init(self, _context: object) -> None:
        register_secret(self.canvas_base_url)
        register_secret(_host_of(self.canvas_base_url))
        if self.canvas_api_token is not None:
            register_secret(self.canvas_api_token.get_secret_value())
        if self.escalation_api_key is not None:
            register_secret(self.escalation_api_key.get_secret_value())

    # -- derived -----------------------------------------------------------------------------

    @property
    def paths(self) -> DataPaths:
        return DataPaths.from_setting(self.data_dir)

    @property
    def canvas_configured(self) -> bool:
        return bool(self.canvas_base_url and self.canvas_api_token)

    @property
    def ollama_configured(self) -> bool:
        return bool(self.ollama_base_url)

    @property
    def escalation_configured(self) -> bool:
        return bool(self.escalation_base_url and self.escalation_api_key)

    def status_rows(self) -> list[dict[str, str]]:
        """Configuration presence, for the status page.

        Reports whether each setting is present and which environment variable supplies it. It
        never reports a value: on a public-repo project the screenshot of a status page is as
        publishable as the repository, so nothing here may carry a hostname or a token.
        """

        def state(present: bool) -> str:
            return "configured" if present else "not configured"

        return [
            {
                "name": "Canvas base URL",
                "env": "CANVAS_BASE_URL",
                "state": state(bool(self.canvas_base_url)),
                "note": "Treated as a secret: it identifies the institution.",
            },
            {
                "name": "Canvas API token",
                "env": "CANVAS_API_TOKEN",
                "state": state(self.canvas_api_token is not None),
                "note": "Read-only access is sufficient.",
            },
            {
                "name": "Ollama endpoint",
                "env": "OLLAMA_BASE_URL",
                "state": state(bool(self.ollama_base_url)),
                "note": f"Concurrency {self.ollama_concurrency}.",
            },
            {
                "name": "Escalation endpoint",
                "env": "SIGHTLINE_ESCALATION_BASE_URL",
                "state": state(self.escalation_configured),
                "note": "Optional. Sightline runs fully without it.",
            },
        ]


def _host_of(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url if "//" in url else f"//{url}")
    return parsed.hostname


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached; call :func:`reset_settings_cache` in tests."""
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
