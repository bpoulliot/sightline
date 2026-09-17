"""The one preference that is a choice rather than a credential, and who wins when they disagree.

Precedence is the credential store's — stored beats environment beats default — and it matters
here for a reason a credential does not have: switching the fresh-export request off is how an
institution with a no-writes policy runs this tool. A setting that quietly reverted to the
environment value on the next restart would be a policy that silently stops holding.
"""

from __future__ import annotations

import pytest

from sightline.config import Settings
from sightline.paths import DataPaths
from sightline.preferences import (
    REQUEST_CONTENT_EXPORT,
    ExportPreference,
    PreferenceSource,
    PreferenceStore,
)
from sightline.store import RunStore


@pytest.fixture
def store(paths: DataPaths) -> RunStore:
    return RunStore.open(paths)


def test_the_default_is_on_and_says_it_is_the_default(store: RunStore) -> None:
    """On by default: without an export, every check that needs a document's own bytes is dead."""
    preference = PreferenceStore(store).request_content_export()
    assert preference.enabled is True
    assert preference.source is PreferenceSource.DEFAULT


def test_the_environment_answers_when_nothing_is_stored(store: RunStore) -> None:
    preference = PreferenceStore(store, env_request_content_export=False).request_content_export()
    assert preference.enabled is False
    assert preference.source is PreferenceSource.ENVIRONMENT


def test_a_stored_value_wins_over_the_environment(store: RunStore) -> None:
    """The screen is where a person decides, so the screen wins. Anything else means a restart
    silently reverses what somebody chose."""
    preferences = PreferenceStore(store, env_request_content_export=True)
    saved = preferences.set_request_content_export(False)
    assert saved.enabled is False
    assert saved.source is PreferenceSource.STORED
    assert preferences.request_content_export().enabled is False
    # ...and a fresh store over the same database reads the same answer, which is what "persists"
    # has to mean: the next process start must not quietly revert to the environment.
    reopened = PreferenceStore(store, env_request_content_export=True).request_content_export()
    assert reopened.enabled is False
    assert reopened.source is PreferenceSource.STORED


def test_forgetting_a_stored_value_falls_back_rather_than_defaulting(store: RunStore) -> None:
    preferences = PreferenceStore(store, env_request_content_export=False)
    preferences.set_request_content_export(True)
    assert preferences.clear_request_content_export() is True
    after = preferences.request_content_export()
    assert after.enabled is False
    assert after.source is PreferenceSource.ENVIRONMENT


def test_the_stored_value_is_readable_without_a_key(store: RunStore) -> None:
    """The opposite of the credential beside it, deliberately: "why did this deployment not ask
    for an export?" has to be answerable from the database that travels."""
    PreferenceStore(store).set_request_content_export(False)
    assert store.get_setting(REQUEST_CONTENT_EXPORT) == "off"


def test_an_unset_environment_variable_is_not_an_answer() -> None:
    """``False`` and "nobody said" are different, and a bool cannot carry the difference."""
    assert Settings(_env_file=None).request_content_export_from_env is None
    chosen = Settings(_env_file=None, request_content_export=False)
    assert chosen.request_content_export_from_env is False


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_the_environment_spellings_of_off_all_work(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Somebody writing this into a compose file will not guess which spelling was meant."""
    monkeypatch.setenv("SIGHTLINE_REQUEST_CONTENT_EXPORT", value)
    assert Settings(_env_file=None).request_content_export is False


def test_the_prose_is_specific_about_what_canvas_will_do() -> None:
    """A toggle is only as good as its sentence. These are rendered on the settings screen, in the
    README and in the CLI, so they are asserted rather than trusted."""
    on = ExportPreference(enabled=True, source=PreferenceSource.DEFAULT)
    off = ExportPreference(enabled=False, source=PreferenceSource.STORED)
    assert on.consequence == ExportPreference.when_on
    assert off.consequence == ExportPreference.when_off
    assert "export list" in on.consequence
    assert "No course content is modified" in on.consequence
    assert "had nothing to examine" in off.consequence
    # Never the framing the product forbids: it does not grade, and it does not "check" a course.
    for sentence in (on.consequence, off.consequence, ExportPreference.title):
        assert "grade" not in sentence.lower()
        assert "score" not in sentence.lower()
