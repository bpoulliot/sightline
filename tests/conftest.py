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

from sightline.canvas.exports import CEILING_SECONDS, PollPacing
from sightline.config import Settings, reset_settings_cache
from sightline.paths import DataPaths
from sightline.redaction import clear_registry

#: Every environment variable the settings model reads. Cleared so the host environment cannot
#: change a test result.
SETTING_ENV_VARS = (
    "CANVAS_BASE_URL",
    "CANVAS_API_TOKEN",
    "SIGHTLINE_REQUEST_CONTENT_EXPORT",
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


class SimulatedClock:
    """A clock that moves only when something sleeps on it.

    Waiting for a content export is the one thing in Sightline measured in minutes, and the poll's
    ceiling has to be tested. A test that actually waited for it is a test somebody deletes, and a
    deleted test is how a ceiling stops working — so the wait is simulated and the elapsed time is
    real arithmetic over fake seconds.
    """

    def __init__(self) -> None:
        self.now = 0.0
        #: Every delay slept, in order. Assertions about backoff read this.
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def pacing(self, ceiling_seconds: float = CEILING_SECONDS) -> PollPacing:
        return PollPacing(ceiling_seconds=ceiling_seconds, sleep=self.sleep, clock=self)


@pytest.fixture
def simulated_clock() -> SimulatedClock:
    return SimulatedClock()


@pytest.fixture
def instant_pacing(simulated_clock: SimulatedClock) -> PollPacing:
    """Poll pacing whose waits cost nothing, for the tests that are not about the ceiling."""
    return simulated_clock.pacing()
