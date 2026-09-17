"""The suite's own isolation guarantee, fired rather than asserted in a docstring.

``Settings.model_config`` sets ``env_file=".env"``, and that path is relative to the *working
directory* — which, when the suite runs, is the repository root, where a developer keeps a real
one. Clearing the environment does not cover a file, so before roadmap item 32 every test whose
code path reached :func:`sightline.config.get_settings` read a real ``CANVAS_BASE_URL`` (a secret
by this project's own rule, SPEC §11), a real token and a real model endpoint. Item 10's CLI test
for *"no model host is configured"* found the endpoint that way and began calling it.

It was silent because it failed only where it mattered. CI has no ``.env`` — ``.dockerignore``
keeps it out of the build context — so the guarantee held exactly where nothing was at stake.
These tests therefore construct the condition rather than waiting for a machine that has it: each
one writes its own ``.env`` into its own working directory, so they fire identically on a
developer's checkout and on a runner that has never seen a secret.

**Nothing here may print what it found.** An assertion is rewritten into a message, and a message
naming the host is the leak SPEC §11 is about. Every check below reduces to a bool first and
asserts on the bool.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import SETTING_ENV_VARS
from sightline.config import Settings, get_settings, reset_settings_cache
from sightline.rubric.shipped import PACKS_DIR_ENV

#: Obviously fake, and shaped like the thing that must never arrive: an institutional hostname.
#: ``.invalid`` is reserved by RFC 2606 and resolves nowhere, so a test that wrongly *used* this
#: value fails on DNS rather than reaching a machine belonging to somebody.
FORBIDDEN_URL = "https://canvas.not-a-real-institution.invalid"

#: Token-*ish*, not token-shaped, and assembled rather than written — the convention the other
#: test modules already follow. ``scripts/scan-secrets.sh`` greps the tree for a Canvas token's
#: shape (digits, tilde, 20+ alphanumerics), and a literal that matched would fail the secrets CI
#: job on a value that is not a secret. The underscores are what keep it out of that pattern; it
#: is the same placeholder ``.env.example`` ships, for the same reason.
FORBIDDEN_TOKEN = "0000" + "~" + "this_token_is_fake_and_must_never_be_read"


def _write_dotenv(directory: Path) -> None:
    """A ``.env`` of the shape a developer's own holds, in *directory*."""
    (directory / ".env").write_text(
        "\n".join(
            (
                f"CANVAS_BASE_URL={FORBIDDEN_URL}",
                f"CANVAS_API_TOKEN={FORBIDDEN_TOKEN}",
                f"OLLAMA_BASE_URL={FORBIDDEN_URL}:11434",
                "OLLAMA_CONCURRENCY=7",
            )
        )
        + "\n",
        encoding="utf-8",
    )


def test_a_dotenv_in_the_working_directory_cannot_reach_get_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole item, in one test.

    ``monkeypatch.chdir`` puts a ``.env`` exactly where ``env_file=".env"`` looks for it. Without
    the isolation in ``conftest.py`` this reads the file and every assertion below fails.
    """
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)
    reset_settings_cache()

    settings = get_settings()
    assert settings.canvas_base_url is None
    assert settings.canvas_api_token is None
    assert settings.ollama_base_url is None
    # A non-secret value from the same file, so a failure here says "the file was read" without a
    # secret having to be the thing that proves it.
    assert settings.ollama_concurrency == 1


def test_a_dotenv_cannot_reach_a_directly_constructed_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not only :func:`get_settings`.

    Code under test constructs ``Settings()`` directly as well, and the isolation is on the class
    so that both are covered by one line. The ``settings`` fixture's explicit ``_env_file=None``
    is correct and stays; it is just not what most code under test calls.
    """
    _write_dotenv(tmp_path)
    monkeypatch.chdir(tmp_path)

    assert Settings().canvas_base_url is None


def test_the_environment_still_reaches_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """The isolation neutralises the *file*, not the environment.

    Worth pinning: a fix that switched off the environment source too would make every test that
    sets a variable pass for no reason, and several do.
    """
    monkeypatch.setenv("OLLAMA_CONCURRENCY", "3")
    reset_settings_cache()

    assert get_settings().ollama_concurrency == 3


def test_the_developers_own_dotenv_is_not_read() -> None:
    """The real condition, on the one machine that has it.

    The two tests above build the situation; this one checks the situation as it actually occurs —
    the suite running in a checkout whose root holds the developer's own file. It **skips** where
    there is no such file rather than failing, which is CI and any fresh clone: a test that needs a
    credential to mean anything skips without one.

    Every check is reduced to a bool before it is asserted. ``assert settings.canvas_base_url is
    None`` would put the institution's hostname into the pytest output on failure, which is the
    leak this whole item is about.
    """
    if not Path(".env").is_file():
        pytest.skip("no .env in the working directory, so there is nothing to be isolated from")

    reset_settings_cache()
    settings = get_settings()

    leaked = {
        "canvas_base_url": settings.canvas_base_url is not None,
        "canvas_api_token": settings.canvas_api_token is not None,
        "ollama_base_url": settings.ollama_base_url is not None,
        "escalation_base_url": settings.escalation_base_url is not None,
        "escalation_api_key": settings.escalation_api_key is not None,
        "secret_key": settings.secret_key is not None,
    }
    assert not [name for name, was_read in leaked.items() if was_read]


def test_the_isolation_is_visible_on_the_class() -> None:
    """A structural statement of the same thing, so a later refactor of the fixture is caught.

    The behavioural tests above would also catch it, but they would report it as "a ``.env`` was
    read" rather than as "the fixture stopped doing the one thing it claims", and the second is
    what somebody reading the failure needs.
    """
    assert Settings.model_config["env_file"] is None
    # ...and the rest of the configuration survived being replaced.
    assert Settings.model_config["extra"] == "ignore"
    assert Settings.model_config["populate_by_name"] is True


def test_every_setting_the_model_reads_is_on_the_cleared_list() -> None:
    """The list in ``conftest.py`` is written by hand, so it is checked rather than trusted.

    A new field on :class:`Settings` that nobody adds to ``SETTING_ENV_VARS`` is invisible: the
    suite goes on passing, and the only symptom is that whatever the developer happens to have
    exported reaches the tests for that one setting. Reading the aliases off the model makes that
    a failure the same day the field lands.
    """
    aliases = {
        field.validation_alias
        for field in Settings.model_fields.values()
        if isinstance(field.validation_alias, str)
    }
    assert aliases, "no validation aliases found — this test would pass vacuously"
    assert aliases - set(SETTING_ENV_VARS) == set()


def test_the_packs_directory_override_is_cleared_too() -> None:
    """The one environment variable Sightline reads without going through :class:`Settings`.

    Found while sweeping for this item. :mod:`sightline.rubric.shipped` reads it with
    ``os.environ.get``, so the settings-derived list above cannot see it, and a developer with it
    exported failed 43 tests in ``test_rubric_packs.py`` alone. The autouse fixture clears it, and
    this asserts the clearing rather than the intention.

    Reduced to a bool for the reason at the top of this module, and that is not theoretical here:
    ``assert PACKS_DIR_ENV not in os.environ`` prints the *entire* environment on failure, which on
    the machine this fires on is the one place the secrets are.
    """
    still_set = PACKS_DIR_ENV in os.environ
    assert not still_set
