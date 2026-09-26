"""Deployment configuration.

Nothing institution-specific is compiled in (SPEC §04). Every deployment-specific value is
configuration with an environment fallback and no default that points at any institution or host:
Canvas base URL, Canvas API token, Ollama endpoint, optional OpenAI-compatible escalation
endpoint. A hardcoded university domain or LAN address anywhere in this package is a bug.

One value here is a *preference* rather than a connection detail: whether Sightline asks Canvas to
build a fresh content export of the course it is reviewing. It lives here as the environment
fallback, and the value in force is resolved by :mod:`sightline.preferences`, which lets a person
set it on the Settings screen.

Constructing :class:`Settings` registers its secret values with :mod:`sightline.redaction`, so
they are scrubbed from logs and error surfaces without any call site opting in. That includes the
Canvas **base URL**, which is treated as a secret because it identifies the institution.

Because the base URL is registered, a connection test that wants to report *which host answered*
renders it deliberately from the credential rather than letting it travel through a log line —
see :meth:`sightline.canvas.client.CanvasClient.test_connection`.

These values are the **environment fallback**. From item 02 the base URL and token are normally
entered on the settings screen and held encrypted by :mod:`sightline.credentials`, which is what
:class:`sightline.credentials.CredentialStore` merges with what is here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .llm.client import DEFAULT_CONTEXT_TOKENS
from .paths import DataPaths
from .redaction import register_secret

#: How many courses one run may hold. SPEC §09 caps a batch at sixteen; typical batches are one or
#: two courses and the largest expected is eight to fifteen. The number is not an arbitrary limit
#: to be raised when somebody has more courses — the design behind it is that courses are reviewed
#: **one after another** against a single local model host (SPEC §04), so a larger batch buys a
#: longer wait rather than more throughput, and the thing that would change that is distributed
#: execution, which this project has decided against.
DEFAULT_BATCH_CAP = 16


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

    #: Whether to ask Canvas to build a fresh export of each course under review. The fallback
    #: for the preference of the same name; normally set on the Settings screen. Default on —
    #: without an export, every check that reads a document's own bytes has nothing to read.
    request_content_export: bool = Field(
        default=True, validation_alias="SIGHTLINE_REQUEST_CONTENT_EXPORT"
    )

    #: Whether the policy-link check may make outbound requests to addresses a course links to.
    #: **Default off**, and that is a decision rather than caution: it is the only traffic
    #: Sightline generates outside Canvas, a batch review would make it in bulk, and a check that
    #: quietly becomes a few hundred requests is one an institution's network team switches off
    #: rather than us. With it off the check reports which policy links are *present* and claims
    #: nothing about where they go. See :mod:`sightline.detect.links`.
    resolve_links: bool = Field(default=False, validation_alias="SIGHTLINE_RESOLVE_LINKS")

    # -- Working data ------------------------------------------------------------------------
    data_dir: Path = Field(default=Path("./data"), validation_alias="SIGHTLINE_DATA_DIR")

    #: A YAML file overriding entries of the shipped per-defect cost table, same shape as
    #: ``src/sightline/rubric/costs.yaml``. Unset means the shipped, uncalibrated table. The one
    #: way to change what a finding costs without a code change; see :mod:`sightline.rubric.effort`.
    effort_costs: Path | None = Field(default=None, validation_alias="SIGHTLINE_EFFORT_COSTS")

    #: Courses per run. Configurable because a deployment may want *fewer* — a reviewer who wants
    #: results this afternoon sets it to two — and bounded above because the sequential design
    #: behind :data:`DEFAULT_BATCH_CAP` does not stop being true at seventeen.
    batch_cap: int = Field(
        default=DEFAULT_BATCH_CAP, ge=1, le=64, validation_alias="SIGHTLINE_BATCH_CAP"
    )

    # -- Local model host --------------------------------------------------------------------
    #: The environment fallback for the endpoint. Normally set on the Settings screen, and
    #: resolved by :mod:`sightline.preferences` like the export preference above. Unlike the
    #: Canvas base URL it is **not** registered as a secret: it names a machine on the operator's
    #: own network, and every model call logs which host served it on purpose (SPEC §06).
    ollama_base_url: str | None = Field(default=None, validation_alias="OLLAMA_BASE_URL")

    #: Concurrent model calls. Default 1, and the ceiling is deliberately low: an earlier local-AI
    #: pipeline on this network died from 48-way concurrency against capacity that could not
    #: sustain it, and it died silently.
    ollama_concurrency: int = Field(default=1, ge=1, le=8, validation_alias="OLLAMA_CONCURRENCY")

    #: The context window pinned on every model call. Not a tuning knob for output quality — it
    #: is what stops Ollama sizing its allocation from a model's declared window, which on the
    #: target host refuses a 2 GB model for 52.2 GiB. See :mod:`sightline.llm.client`.
    ollama_context_tokens: int = Field(
        default=DEFAULT_CONTEXT_TOKENS, ge=512, le=1_048_576, validation_alias="OLLAMA_NUM_CTX"
    )

    # -- Optional escalation (OpenAI-compatible) ---------------------------------------------
    escalation_base_url: str | None = Field(
        default=None, validation_alias="SIGHTLINE_ESCALATION_BASE_URL"
    )
    escalation_api_key: SecretStr | None = Field(
        default=None, validation_alias="SIGHTLINE_ESCALATION_API_KEY"
    )

    # -- Credential encryption ---------------------------------------------------------------
    #: Key for the credentials entered on the settings screen. Unset means Sightline manages a
    #: key file under the data root; setting it keeps the key out of the data volume entirely.
    #: See :mod:`sightline.credentials` for what that does and does not protect against.
    secret_key: SecretStr | None = Field(default=None, validation_alias="SIGHTLINE_SECRET_KEY")

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
        if self.secret_key is not None:
            register_secret(self.secret_key.get_secret_value())

    # -- derived -----------------------------------------------------------------------------

    @property
    def paths(self) -> DataPaths:
        return DataPaths.from_setting(self.data_dir)

    @property
    def canvas_configured(self) -> bool:
        return bool(self.canvas_base_url and self.canvas_api_token)

    @property
    def request_content_export_from_env(self) -> bool | None:
        """The environment's answer, or ``None`` when the environment did not answer.

        A plain ``bool`` cannot carry that difference, and the difference is the whole of the
        precedence rule: an unset variable must fall through to the built-in default rather than
        looking like somebody deliberately chose it. ``model_fields_set`` is what pydantic records
        for exactly this.
        """
        if "request_content_export" not in self.model_fields_set:
            return None
        return self.request_content_export

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
                "note": (
                    "Read access, plus permission to export course content when Sightline is "
                    "asked to build one."
                ),
            },
            {
                "name": "Ollama endpoint",
                "env": "OLLAMA_BASE_URL",
                "state": state(bool(self.ollama_base_url)),
                "note": (
                    f"Concurrency {self.ollama_concurrency}, "
                    f"{self.ollama_context_tokens:,}-token context pinned on every call."
                ),
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
