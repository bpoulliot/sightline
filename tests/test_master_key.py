"""Sign-in mode takes the credential master key from the environment, or does not start (item 50).

In local mode an unset ``SIGHTLINE_SECRET_KEY`` means a key file under the data root, and that is
unchanged. In sign-in mode the key encrypts every person's Canvas token, so it must not sit in the
volume beside them: the app refuses to start without it, saying which variable to set and how to
generate one, and exits non-zero. These tests hold both directions — refused when unset, blank or
malformed; started when set — and check that a refusal printed to the container log carries no
configured value with it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from sightline.app import create_app
from sightline.config import Settings
from sightline.paths import KEY_FILENAME

SECRET = "fake-proxy-secret-0000000000000000000000"
FAKE_BASE_URL = "https://canvas.example.edu"
GENERATE = "Fernet.generate_key()"


def signin_settings(data_root: Path, **extra: object) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=data_root,
        signin=True,
        signin_secret=SECRET,
        canvas_base_url=FAKE_BASE_URL,
        **extra,
    )


@pytest.mark.parametrize("key", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_sign_in_mode_without_a_master_key_is_refused_by_name(
    data_root: Path, key: str | None
) -> None:
    with pytest.raises(ValidationError) as caught:
        signin_settings(data_root, secret_key=key)
    message = str(caught.value)
    assert "SIGHTLINE_SECRET_KEY must be set" in message
    assert GENERATE in message
    assert "every person enters their Canvas token again" in message


def test_a_malformed_master_key_is_refused_without_quoting_it(data_root: Path) -> None:
    malformed = "not-a-fernet-key-0123456789"
    with pytest.raises(ValidationError) as caught:
        signin_settings(data_root, secret_key=malformed)
    message = str(caught.value)
    assert "SIGHTLINE_SECRET_KEY is set but is not a valid key" in message
    assert GENERATE in message
    assert malformed not in message
    # Pydantic appends the raw input to a refusal unless told not to; truncated, it is a lottery
    # which configured value survives. Hidden, none does.
    assert "input_value" not in message


def test_sign_in_mode_with_a_master_key_starts(data_root: Path) -> None:
    key = Fernet.generate_key().decode()
    settings = signin_settings(data_root, secret_key=key)
    assert settings.signin and settings.secret_key is not None


def test_local_mode_keeps_the_key_file(data_root: Path) -> None:
    """Local mode with no key is unchanged: no refusal, and a key file under the data root."""
    from fastapi.testclient import TestClient

    settings = Settings(_env_file=None, data_dir=data_root)
    with TestClient(create_app(settings)) as client:
        client.app.state.credentials.save_canvas(base_url=FAKE_BASE_URL, token="fake-token")
    assert (data_root / "secrets" / KEY_FILENAME).is_file()


def test_sign_in_mode_never_writes_a_key_file(data_root: Path) -> None:
    """The point of the requirement: in sign-in mode no key reaches the data volume."""
    from fastapi.testclient import TestClient

    settings = signin_settings(data_root, secret_key=Fernet.generate_key().decode())
    with TestClient(create_app(settings)) as client:
        credentials = client.app.state.credentials
        credentials.save_user_token("subject-fake-0001", "fake-token")
        assert credentials.canvas_for("subject-fake-0001").token == "fake-token"
    assert not (data_root / "secrets" / KEY_FILENAME).exists()


# -- the process, as the container runs it --------------------------------------------------------

#: What the image's ``uvicorn sightline.app:app`` does first: import the module, which builds the
#: app from the environment. Then, so "starts" means more than "imports", run the lifespan and
#: answer the health check, which sign-in mode serves without the proxy.
START = """
from fastapi.testclient import TestClient
import sightline.app
with TestClient(sightline.app.app) as client:
    print("healthz", client.get("/healthz").status_code)
"""


def _start(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("SIGHTLINE_", "CANVAS_"))}
    clean.update(
        {
            "SIGHTLINE_DATA_DIR": str(tmp_path / "data"),
            "SIGHTLINE_SIGNIN": "true",
            "SIGHTLINE_SIGNIN_SECRET": SECRET,
            "CANVAS_BASE_URL": FAKE_BASE_URL,
            **env,
        }
    )
    # cwd is the test's own directory, so no ``.env`` is read from the checkout.
    return subprocess.run(
        [sys.executable, "-c", START],
        cwd=tmp_path,
        env=clean,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_process_exits_non_zero_with_the_message_when_the_key_is_unset(
    tmp_path: Path,
) -> None:
    result = _start(tmp_path)
    assert result.returncode != 0
    assert "SIGHTLINE_SECRET_KEY must be set" in result.stderr
    assert GENERATE in result.stderr
    assert "input_value" not in result.stderr
    assert "canvas.example.edu" not in result.stderr + result.stdout
    assert "healthz" not in result.stdout


def test_the_process_starts_when_the_key_is_set(tmp_path: Path) -> None:
    result = _start(tmp_path, SIGHTLINE_SECRET_KEY=Fernet.generate_key().decode())
    assert result.returncode == 0, result.stderr[-2000:]
    assert "healthz 200" in result.stdout
