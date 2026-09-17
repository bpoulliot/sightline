"""The preferences that are choices rather than credentials, and who wins when they disagree.

Precedence is the credential store's — stored beats environment beats default — and it matters
here for a reason a credential does not have: switching the fresh-export request off is how an
institution with a no-writes policy runs this tool. A setting that quietly reverted to the
environment value on the next restart would be a policy that silently stops holding.
"""

from __future__ import annotations

import pytest

from sightline.config import Settings
from sightline.llm.errors import ModelHostNotConfiguredError
from sightline.paths import DataPaths
from sightline.preferences import (
    MODEL_HOST_ENDPOINT,
    REQUEST_CONTENT_EXPORT,
    REVIEW_MODEL,
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


# ---------------------------------------------------------------------------------------------
# The model host
# ---------------------------------------------------------------------------------------------

ENDPOINT = "http://model-host.example.lan:11434"


def test_no_model_host_is_configured_by_default(store: RunStore) -> None:
    """No default that points at a host. A hardcoded LAN address here would be a bug (SPEC §04)."""
    preference = PreferenceStore(store).model_host()
    assert preference.endpoint is None
    assert not preference.configured
    assert preference.source is PreferenceSource.DEFAULT


def test_a_stored_endpoint_beats_the_environment(store: RunStore) -> None:
    preferences = PreferenceStore(
        store, env_model_host_endpoint="http://from-the-env.example.lan:11434"
    )
    assert preferences.model_host().source is PreferenceSource.ENVIRONMENT
    preferences.set_model_host(ENDPOINT)
    assert preferences.model_host().endpoint == ENDPOINT
    assert preferences.model_host().source is PreferenceSource.STORED


def test_forgetting_the_endpoint_returns_to_the_environment(store: RunStore) -> None:
    preferences = PreferenceStore(
        store, env_model_host_endpoint="http://from-the-env.example.lan:11434"
    )
    preferences.set_model_host(ENDPOINT)
    assert preferences.clear_model_host() is True
    assert preferences.model_host().source is PreferenceSource.ENVIRONMENT
    assert preferences.clear_model_host() is False


def test_an_endpoint_is_normalized_on_the_way_in(store: RunStore) -> None:
    """Stored as it will be called, so the row and the log line are the same string."""
    preferences = PreferenceStore(store)
    preferences.set_model_host("model-host.example.lan:11434/api/tags")
    assert store.get_setting(MODEL_HOST_ENDPOINT) == ENDPOINT
    assert preferences.model_host().host == "model-host.example.lan:11434"


def test_a_malformed_endpoint_is_not_stored(store: RunStore) -> None:
    preferences = PreferenceStore(store)
    with pytest.raises(ModelHostNotConfiguredError):
        preferences.set_model_host("nonsense here")
    assert store.get_setting(MODEL_HOST_ENDPOINT) is None


# ---------------------------------------------------------------------------------------------
# The model chosen to do the work
# ---------------------------------------------------------------------------------------------


def test_no_model_is_chosen_by_default(store: RunStore) -> None:
    preference = PreferenceStore(store).review_model()
    assert preference.name is None
    assert not preference.chosen


def test_choosing_a_model_stores_its_name(store: RunStore) -> None:
    preferences = PreferenceStore(store)
    preferences.set_review_model("  gemma4:e4b  ")
    assert store.get_setting(REVIEW_MODEL) == "gemma4:e4b"
    assert preferences.review_model().source is PreferenceSource.STORED
    assert preferences.clear_review_model() is True
    assert not preferences.review_model().chosen


def test_choosing_nothing_is_refused_with_a_remedy(store: RunStore) -> None:
    with pytest.raises(ModelHostNotConfiguredError) as caught:
        PreferenceStore(store).set_review_model("   ")
    assert caught.value.remedy


def test_a_model_name_is_not_capability_checked_here(store: RunStore) -> None:
    """Capabilities are a live reading of the host; this store cannot make one (SPEC §06).

    Storing an uninstalled name is therefore allowed on purpose. The screen offers only models
    that passed the filter and the client checks again at call time; a third answer taken here
    from data that had to be stale to exist would be the one that is wrong.
    """
    preferences = PreferenceStore(store)
    preferences.set_review_model("nothing-installed:1b")
    assert preferences.review_model().name == "nothing-installed:1b"
