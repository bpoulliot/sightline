"""Shared fixtures.

Two things matter here and they are both about isolation. Tests never read the developer's
``.env`` — a suite that picks up a real Canvas URL is a suite that can leak one into an assertion
message. And every test gets its own data root under ``tmp_path``, well outside the repository, so
the containment guarantees are exercised against a root that is not the default.

The first of those is two separate leaks and used to be one. Settings arrive from the environment
*and* from a file, and ``_isolated_environment`` originally cleared only the environment while its
docstring promised both — so any test reaching :func:`sightline.config.get_settings` read the
repository's own ``.env``, because ``env_file=".env"`` resolves against the working directory and
the suite runs at the repository root. It was invisible: CI has no such file, so the guarantee
held exactly where nothing was at stake and failed exactly where the secret lives. Roadmap item 32.
``tests/test_env_isolation.py`` fires both halves rather than trusting this paragraph.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sightline.canvas.exports import CEILING_SECONDS, PollPacing
from sightline.config import Settings, reset_settings_cache
from sightline.paths import DataPaths
from sightline.redaction import clear_registry
from sightline.rubric.shipped import PACKS_DIR_ENV

#: Every environment variable :class:`sightline.config.Settings` reads. Cleared so the host
#: environment cannot change a test result.
#:
#: The list is hand-maintained and therefore has to be checked rather than trusted:
#: ``tests/test_env_isolation.py`` reads the validation aliases off the model and fails if one is
#: missing here. Adding a setting without adding it here is otherwise invisible.
SETTING_ENV_VARS = (
    "CANVAS_BASE_URL",
    "CANVAS_API_TOKEN",
    "SIGHTLINE_REQUEST_CONTENT_EXPORT",
    "SIGHTLINE_RESOLVE_LINKS",
    "SIGHTLINE_DATA_DIR",
    "SIGHTLINE_BATCH_CAP",
    "SIGHTLINE_EFFORT_COSTS",
    "OLLAMA_BASE_URL",
    "OLLAMA_CONCURRENCY",
    "OLLAMA_NUM_CTX",
    "SIGHTLINE_ESCALATION_BASE_URL",
    "SIGHTLINE_ESCALATION_API_KEY",
    "SIGHTLINE_SECRET_KEY",
    "SIGHTLINE_TOKENS_CSS",
    "SIGHTLINE_HOST",
    "SIGHTLINE_PORT",
    "SIGHTLINE_LOG_LEVEL",
    "SIGHTLINE_LOG_FORMAT",
)

#: Environment variables read outside the settings model, which the list above therefore cannot
#: find. Only one so far: :mod:`sightline.rubric.shipped` reads its packs directory straight from
#: the environment because locating a data file is not a deployment setting. Left in place, an
#: exported ``SIGHTLINE_RUBRIC_PACKS_DIR`` pointed anywhere else fails 43 tests — measured, not
#: supposed. Loud rather than silent, unlike the ``.env`` leak, but the same defect: the host
#: changing a test result.
OTHER_ENV_VARS = (PACKS_DIR_ENV,)

#: Everything the autouse fixture clears.
ISOLATED_ENV_VARS = SETTING_ENV_VARS + OTHER_ENV_VARS


@pytest.fixture(autouse=True)
def _isolated_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Neither the environment nor a ``.env`` file may change a test result.

    The file half is done on the class rather than at any call site, because the call sites are
    the point: ``Settings()`` is constructed by the app factory and by every ``python -m`` entry
    point, and a guard that each of those has to opt into is a guard the next module forgets.
    Neutralising ``env_file`` here covers ``Settings()`` wherever it is built, including inside
    code under test. Nothing else in the config is disturbed.

    Changing the working directory instead is the obvious alternative and is worse: the suite
    resolves ``examples/sample-course`` and ``docs/`` from the repository root, so a chdir here
    would break paths that have nothing to do with configuration.

    Production is untouched — reading ``.env`` is what :mod:`sightline.config` is for. The defect
    was only ever this harness's claim about what it isolates.
    """
    for name in ISOLATED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Settings, "model_config", {**Settings.model_config, "env_file": None})
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
