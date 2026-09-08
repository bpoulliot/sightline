"""Credentials: encrypted at rest, environment as fallback, never rendered back.

The claim this file has to substantiate is narrow and worth stating precisely, because overstating
it would be worse than not encrypting at all. Encrypting the credential means the **run store** —
the artifact that gets copied for a bug report, backed up, and opened in a SQLite browser — does
not carry a plaintext Canvas token. It does not defend against someone who can read the whole data
root, because by default the key is a file in it. ``SIGHTLINE_SECRET_KEY`` is what moves the key
out.

So the assertions are: the token is not in the database bytes, the key is not in the database, the
key file is owner-only, and a wrong key fails loudly rather than falling through to the
environment.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from canvas_recordings import EXAMPLE_BASE_URL, EXAMPLE_HOST, FAKE_TOKEN
from sightline.credentials import (
    CANVAS_SECRET_NAME,
    CredentialError,
    CredentialSource,
    CredentialStore,
)
from sightline.paths import DataPaths
from sightline.redaction import registered_secrets
from sightline.store import RunStore


@pytest.fixture
def store(paths: DataPaths) -> RunStore:
    opened = RunStore.open(paths)
    yield opened
    opened.dispose()


@pytest.fixture
def credentials(store: RunStore, paths: DataPaths) -> CredentialStore:
    return CredentialStore(store, paths)


# -- reading and writing ----------------------------------------------------------------------


def test_nothing_configured_reports_unset_rather_than_guessing(
    credentials: CredentialStore,
) -> None:
    current = credentials.canvas()
    assert current.base_url is None and current.token is None
    assert not current.complete
    assert current.base_url_source is CredentialSource.UNSET
    assert current.token_source is CredentialSource.UNSET


def test_a_saved_credential_round_trips(credentials: CredentialStore) -> None:
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    current = credentials.canvas()
    assert current.base_url == EXAMPLE_BASE_URL
    assert current.token == FAKE_TOKEN
    assert current.host == EXAMPLE_HOST
    assert current.complete
    assert current.base_url_source is CredentialSource.STORED
    assert current.stored_at is not None


def test_the_ui_wins_over_the_environment(store: RunStore, paths: DataPaths) -> None:
    """The environment is the fallback: a value typed on screen is not undone by a restart."""
    from_env = CredentialStore(
        store, paths, env_base_url="https://canvas.env.example", env_token="env-token-value"
    )
    assert from_env.canvas().base_url == "https://canvas.env.example"
    assert from_env.canvas().base_url_source is CredentialSource.ENVIRONMENT

    from_env.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    current = from_env.canvas()
    assert current.base_url == EXAMPLE_BASE_URL
    assert current.token == FAKE_TOKEN
    assert current.base_url_source is CredentialSource.STORED


def test_forgetting_the_stored_value_falls_back_to_the_environment(
    store: RunStore, paths: DataPaths
) -> None:
    credentials = CredentialStore(
        store, paths, env_base_url="https://canvas.env.example", env_token="env-token-value"
    )
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    assert credentials.clear_canvas() is True
    current = credentials.canvas()
    assert current.base_url == "https://canvas.env.example"
    assert current.base_url_source is CredentialSource.ENVIRONMENT


def test_a_blank_token_leaves_the_stored_one_alone(credentials: CredentialStore) -> None:
    """A form that wipes a working token whenever somebody edits the URL teaches people not to
    touch it."""
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    credentials.save_canvas(base_url="https://canvas.other.example", token=None)
    current = credentials.canvas()
    assert current.token == FAKE_TOKEN
    assert current.base_url == "https://canvas.other.example"


def test_an_empty_string_clears_a_field(credentials: CredentialStore) -> None:
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    credentials.save_canvas(token="")
    assert credentials.canvas().token is None
    assert credentials.canvas().base_url == EXAMPLE_BASE_URL


def test_a_base_url_is_normalized_and_the_caller_is_told(credentials: CredentialStore) -> None:
    stored = credentials.save_canvas(base_url=f"{EXAMPLE_BASE_URL}/courses/4410/pages/welcome")
    assert stored == EXAMPLE_BASE_URL
    assert credentials.canvas().base_url == EXAMPLE_BASE_URL


def test_an_unusable_base_url_is_refused_with_a_remedy(credentials: CredentialStore) -> None:
    with pytest.raises(CredentialError) as caught:
        credentials.save_canvas(base_url="ftp://canvas.example.edu")
    assert caught.value.remedy


# -- encryption at rest -----------------------------------------------------------------------


def test_the_token_is_not_in_the_database_bytes(
    credentials: CredentialStore, paths: DataPaths
) -> None:
    """The run store is the artifact that travels. This is the whole point of encrypting."""
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    blob = b"".join(
        path.read_bytes() for path in paths.runs.glob("sightline.sqlite*") if path.is_file()
    )
    assert blob, "no database file was written, so this test proved nothing"
    assert FAKE_TOKEN.encode() not in blob
    assert EXAMPLE_BASE_URL.encode() not in blob
    assert EXAMPLE_HOST.encode() not in blob


def test_the_key_is_not_in_the_database(credentials: CredentialStore, paths: DataPaths) -> None:
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    key = paths.credentials_key_file.read_bytes()
    blob = b"".join(
        path.read_bytes() for path in paths.runs.glob("sightline.sqlite*") if path.is_file()
    )
    assert key not in blob


def test_the_key_file_is_owner_only(credentials: CredentialStore, paths: DataPaths) -> None:
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    key_file = paths.credentials_key_file
    assert key_file.is_file()
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.secrets_dir.stat().st_mode) == 0o700


def test_the_key_is_created_once_and_reused(credentials: CredentialStore, paths: DataPaths) -> None:
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    first = paths.credentials_key_file.read_bytes()
    credentials.save_canvas(token="a-different-token")
    assert paths.credentials_key_file.read_bytes() == first


def test_a_supplied_key_keeps_the_key_out_of_the_data_root(
    store: RunStore, paths: DataPaths
) -> None:
    key = Fernet.generate_key()
    credentials = CredentialStore(store, paths, key=key)
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    assert not paths.credentials_key_file.exists()
    assert credentials.canvas().token == FAKE_TOKEN


def test_the_stored_value_is_unreadable_with_a_different_key(
    store: RunStore, paths: DataPaths
) -> None:
    CredentialStore(store, paths, key=Fernet.generate_key()).save_canvas(
        base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN
    )
    with pytest.raises(CredentialError) as caught:
        CredentialStore(store, paths, key=Fernet.generate_key()).canvas()
    assert "does not match" in caught.value.remedy
    assert "SIGHTLINE_SECRET_KEY" in caught.value.remedy


def test_a_wrong_key_fails_loudly_instead_of_falling_back_to_the_environment(
    store: RunStore, paths: DataPaths
) -> None:
    """Silently using the environment would leave a designer editing a value nothing reads."""
    CredentialStore(store, paths, key=Fernet.generate_key()).save_canvas(
        base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN
    )
    other = CredentialStore(
        store,
        paths,
        key=Fernet.generate_key(),
        env_base_url="https://canvas.env.example",
        env_token="env-token-value",
    )
    with pytest.raises(CredentialError):
        other.canvas()


def test_a_malformed_key_says_how_to_make_a_good_one(store: RunStore, paths: DataPaths) -> None:
    credentials = CredentialStore(store, paths, key="not-a-fernet-key")
    with pytest.raises(CredentialError) as caught:
        credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    assert "Fernet.generate_key" in caught.value.remedy


# -- redaction --------------------------------------------------------------------------------


def test_saving_registers_the_value_for_scrubbing(credentials: CredentialStore) -> None:
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    registered = registered_secrets()
    assert FAKE_TOKEN in registered
    assert EXAMPLE_BASE_URL in registered
    assert EXAMPLE_HOST in registered


def test_reading_registers_too_so_a_restart_is_covered(store: RunStore, paths: DataPaths) -> None:
    """Nothing calls save on a restart, so registration cannot only happen there."""
    key = Fernet.generate_key()
    CredentialStore(store, paths, key=key).save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    from sightline.redaction import clear_registry

    clear_registry()
    CredentialStore(store, paths, key=key).canvas()
    assert FAKE_TOKEN in registered_secrets()
    assert EXAMPLE_HOST in registered_secrets()


def test_presence_rows_never_carry_a_value(credentials: CredentialStore) -> None:
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    rendered = repr(credentials.canvas().presence())
    assert FAKE_TOKEN not in rendered
    assert EXAMPLE_HOST not in rendered
    assert "stored" in rendered


def test_no_partial_token_is_ever_offered(credentials: CredentialStore) -> None:
    """A last-four is borrowed from payment forms, where the number is printed on the card the
    user is holding. A Canvas token is printed nowhere, so it buys nothing."""
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    rendered = repr(credentials.canvas().presence())
    for length in (4, 6, 8):
        assert FAKE_TOKEN[:length] not in rendered
        assert FAKE_TOKEN[-length:] not in rendered


def test_the_secret_row_name_is_stable(credentials: CredentialStore, store: RunStore) -> None:
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    assert store.get_secret_blob(CANVAS_SECRET_NAME) is not None
    assert store.get_secret_blob("nothing-here") is None


def test_the_key_helper_refuses_an_empty_generator(paths: DataPaths) -> None:
    from sightline.paths import DataPathError

    with pytest.raises(DataPathError):
        paths.read_or_create_key(lambda: b"")


def test_the_key_helper_writes_inside_the_data_root(paths: DataPaths, tmp_path: Path) -> None:
    material = paths.read_or_create_key(Fernet.generate_key)
    assert paths.contains(paths.credentials_key_file)
    assert paths.read_or_create_key(Fernet.generate_key) == material
