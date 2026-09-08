"""Shared fixtures.

Two things matter here and they are both about isolation. Tests never read the developer's
``.env`` — a suite that picks up a real Canvas URL is a suite that can leak one into an assertion
message. And every test gets its own data root under ``tmp_path``, well outside the repository, so
the containment guarantees are exercised against a root that is not the default.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sightline.config import Settings, reset_settings_cache
from sightline.paths import DataPaths
from sightline.redaction import clear_registry

#: Every environment variable the settings model reads. Cleared so the host environment cannot
#: change a test result.
SETTING_ENV_VARS = (
    "CANVAS_BASE_URL",
    "CANVAS_API_TOKEN",
    "SIGHTLINE_DATA_DIR",
    "OLLAMA_BASE_URL",
    "OLLAMA_CONCURRENCY",
    "SIGHTLINE_ESCALATION_BASE_URL",
    "SIGHTLINE_ESCALATION_API_KEY",
    "SIGHTLINE_SECRET_KEY",
    "SIGHTLINE_TOKENS_CSS",
    "SIGHTLINE_HOST",
    "SIGHTLINE_PORT",
    "SIGHTLINE_LOG_LEVEL",
    "SIGHTLINE_LOG_FORMAT",
)


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for name in SETTING_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    clear_registry()
    reset_settings_cache()
    yield
    clear_registry()
    reset_settings_cache()


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    """A data root outside the repository, as a real deployment would have."""
    return tmp_path / "sightline-data"


@pytest.fixture
def paths(data_root: Path) -> DataPaths:
    return DataPaths.from_setting(data_root).ensure()


@pytest.fixture
def settings(data_root: Path, repo_root: Path) -> Settings:
    """Settings with no ``.env`` behind them and an isolated data root."""
    return Settings(
        _env_file=None,
        data_dir=data_root,
        tokens_css_path=repo_root / "docs" / "tokens.css",
        log_format="json",
    )
