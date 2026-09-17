"""Secrets never reach a log line, an error message or a rendered page."""

from __future__ import annotations

import pytest

from sightline.config import Settings
from sightline.redaction import (
    REDACTED,
    clear_registry,
    register_secret,
    registered_secrets,
    scrub,
    scrub_object,
)

FAKE_CANVAS_URL = "https://canvas.example.edu"

# Assembled rather than written out. A literal of this shape in the tree is exactly what
# scripts/scan-secrets.sh is built to reject, and a test of the scrubber must not be the one thing
# that has to be allowlisted past it.
FAKE_CANVAS_TOKEN = "12345" + "~" + ("aBcDeFgH" * 5)
FAKE_OPENAI_KEY = "sk-" + ("abcdefghijklmnop" * 2)


def test_registered_values_are_scrubbed() -> None:
    register_secret(FAKE_CANVAS_URL)
    assert FAKE_CANVAS_URL not in scrub(f"GET {FAKE_CANVAS_URL}/api/v1/courses failed")
    assert REDACTED in scrub(f"GET {FAKE_CANVAS_URL}/api/v1/courses failed")


def test_short_values_are_not_registered() -> None:
    register_secret("abc")
    assert "abc" not in registered_secrets()
    assert scrub("abc def") == "abc def"


@pytest.mark.parametrize(
    "text",
    [
        f"token={FAKE_CANVAS_TOKEN}",
        f"Authorization: Bearer {FAKE_CANVAS_TOKEN}",
        f"the token is {FAKE_CANVAS_TOKEN} and it should not be here",
        f"api_key={FAKE_OPENAI_KEY}",
    ],
)
def test_token_shapes_are_scrubbed_without_registration(text: str) -> None:
    clear_registry()
    scrubbed = scrub(text)
    assert FAKE_CANVAS_TOKEN not in scrubbed
    assert FAKE_OPENAI_KEY not in scrubbed
    assert REDACTED in scrubbed


def test_placeholder_in_env_example_is_not_mistaken_for_a_token() -> None:
    """``.env.example`` ships an obviously fake value; scrubbing it would be noise."""
    assert scrub("0000~replace_me_with_a_real_token") == "0000~replace_me_with_a_real_token"


def test_a_url_origin_is_scrubbed_even_when_nothing_registered_it() -> None:
    """The registry covers the configured Canvas host. It cannot cover the storage back end a
    Canvas download redirects to: that host differs per deployment and nothing configures it, yet
    the HTTP client logs it by name. The path has to survive, or a log line stops saying what was
    fetched."""
    clear_registry()
    line = 'HTTP Request: GET https://content-storage.example/bundles/8801 "HTTP/1.1 200 OK"'
    scrubbed = scrub(line)
    assert "content-storage.example" not in scrubbed
    assert "/bundles/8801" in scrubbed
    assert "HTTP Request" in scrubbed


def test_a_download_link_credential_is_scrubbed() -> None:
    """A Canvas file link carries its credential in the query string, so the query string is a
    credential surface and not merely a URL."""
    clear_registry()
    scrubbed = scrub("/files/9101/download?download_frd=1&verifier=synthetic-not-a-real-one")
    assert "synthetic-not-a-real-one" not in scrubbed
    assert "download_frd=1" in scrubbed


def test_ordinary_prose_survives() -> None:
    clear_registry()
    sentence = "Examined 41 pages; 3 images have no alt text."
    assert scrub(sentence) == sentence


def test_nested_structures_are_scrubbed() -> None:
    register_secret(FAKE_CANVAS_URL)
    payload = {
        "url": FAKE_CANVAS_URL,
        "list": [FAKE_CANVAS_URL, 7],
        "nested": {"headers": {"Authorization": f"Bearer {FAKE_CANVAS_TOKEN}"}},
    }
    cleaned = scrub_object(payload)
    assert FAKE_CANVAS_URL not in repr(cleaned)
    assert FAKE_CANVAS_TOKEN not in repr(cleaned)


def test_settings_register_their_own_secrets() -> None:
    Settings(
        _env_file=None,
        canvas_base_url=FAKE_CANVAS_URL,
        canvas_api_token=FAKE_CANVAS_TOKEN,
    )
    message = f"connecting to {FAKE_CANVAS_URL} with {FAKE_CANVAS_TOKEN}"
    scrubbed = scrub(message)
    assert FAKE_CANVAS_URL not in scrubbed
    assert FAKE_CANVAS_TOKEN not in scrubbed


def test_settings_register_the_hostname_alone() -> None:
    """The base URL is a secret because of the host, so the bare host is scrubbed too."""
    Settings(_env_file=None, canvas_base_url=FAKE_CANVAS_URL)
    assert "canvas.example.edu" not in scrub("resolved canvas.example.edu to 203.0.113.7")


def test_traceback_text_is_scrubbed() -> None:
    """Stack traces are an error surface like any other."""
    import traceback

    Settings(_env_file=None, canvas_base_url=FAKE_CANVAS_URL)
    try:
        raise RuntimeError(f"could not reach {FAKE_CANVAS_URL}/api/v1")
    except RuntimeError:
        rendered = "".join(traceback.format_exc())
    assert FAKE_CANVAS_URL in rendered
    assert FAKE_CANVAS_URL not in scrub(rendered)
