"""The committed fixtures are synthetic, and this is what says so mechanically.

This repository is public. A fixture that traces back to a real course, a real person or a real
instance is irreversible once pushed, and git history keeps it after a later deletion. So the
guarantee is not "we were careful"; it is this file, which fails the build over a fixture that
carries a real hostname, an authorization header, a token shape, an address at a real domain, or a
name outside a declared cast.

The one deliberate exception is spelled out below: the literal ``Bearer realm="canvas-lms"`` in
``invalid-token.json``. That is the header Canvas sends with a rejected token, it is the thing the
client keys off to tell a bad token from a missing permission, and it contains no credential. The
check that would otherwise catch it is narrowed to "the word Bearer followed by something
credential-shaped" rather than dropped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from canvas_recordings import EXAMPLE_HOST, fixture_files, load
from sightline.canvas.sanitize import verify

#: Every personal name a fixture may contain. Deliberately a short, obviously invented cast: a
#: name outside this list is either a real person or a new fixture that has not been reviewed,
#: and both should fail.
SYNTHETIC_CAST = frozenset(
    {"Reviewer A", "Reviewer B", "A, Reviewer", "reviewer.a", "reviewer.a@example.edu"}
)

#: Keys whose value is a person's name or address.
PERSON_KEYS = frozenset(
    {"name", "short_name", "sortable_name", "display_name", "login_id", "primary_email", "email"}
)

#: A course's ``name`` is not a person's, and these fixtures use one obvious shape for it.
COURSE_NAME = re.compile(r"^Sample Course \d+$")

#: The only place the word "Bearer" may appear, and it carries no credential.
ALLOWED_BEARER = 'Bearer realm="canvas-lms"'

_BEARER_WITH_CREDENTIAL = re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]{16,}")
_HOST = re.compile(r"https?://([^/\s\"']+)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")


def fixture_ids() -> list[str]:
    return [path.stem for path in fixture_files()]


def test_there_are_fixtures_to_examine() -> None:
    """A guard that examined nothing is not a guard."""
    assert len(fixture_files()) >= 5


@pytest.mark.parametrize("name", fixture_ids())
def test_a_fixture_names_no_host_but_the_example_one(name: str) -> None:
    text = _text(name)
    hosts = {match.group(1) for match in _HOST.finditer(text)}
    assert hosts <= {EXAMPLE_HOST}, f"{name} names {sorted(hosts - {EXAMPLE_HOST})}"


@pytest.mark.parametrize("name", fixture_ids())
def test_a_fixture_carries_no_credential(name: str) -> None:
    text = _text(name)
    assert not _BEARER_WITH_CREDENTIAL.search(text)
    assert "authorization" not in text.lower().replace("www-authenticate", "")
    # The check runs on decoded values, not the raw file: a JSON file escapes the quotes inside
    # the one allowed header value, so a raw-text comparison would never match it.
    bearers = [
        value for _key, value in _named_values(json.loads(text)) if "bearer" in value.lower()
    ]
    assert all(value == ALLOWED_BEARER for value in bearers), bearers


@pytest.mark.parametrize("name", fixture_ids())
def test_a_fixture_carries_no_token_shape(name: str) -> None:
    assert not re.search(r"\b\d{3,6}~[A-Za-z0-9]{20,}\b", _text(name))
    assert not re.search(r"\bsk-[A-Za-z0-9\-_]{16,}\b", _text(name))


@pytest.mark.parametrize("name", fixture_ids())
def test_a_fixture_addresses_nobody_at_a_real_domain(name: str) -> None:
    for match in _EMAIL.finditer(_text(name)):
        assert match.group(0).endswith("@example.edu"), f"{name}: {match.group(0)}"


@pytest.mark.parametrize("name", fixture_ids())
def test_every_personal_name_comes_from_the_declared_cast(name: str) -> None:
    """A name outside the cast is either a real person or an unreviewed fixture."""
    offenders = [
        f"{key}={value!r}"
        for key, value in _named_values(_interactions(name))
        if key in PERSON_KEYS
        and value not in SYNTHETIC_CAST
        and not COURSE_NAME.match(value)
        and not value.startswith("Synthetic ")
    ]
    assert not offenders, f"{name}: {offenders}"


@pytest.mark.parametrize("name", fixture_ids())
def test_the_sanitizers_verify_pass_finds_nothing_in_a_committed_fixture(name: str) -> None:
    """The tool that gates a captured fixture is run against the hand-written ones too.

    If it flagged these it would be useless on a real capture, and if it passed a fixture this
    file rejects the two guards would have drifted apart.
    """
    assert verify(json.loads(_text(name))) == []


@pytest.mark.parametrize("name", fixture_ids())
def test_a_fixture_describes_itself(name: str) -> None:
    """A recording with no description is one nobody can tell is still recording the right thing."""
    recording = load(name)
    assert len(recording.description) > 40
    assert recording.interactions


def test_the_cast_check_would_catch_a_real_name() -> None:
    document = {"name": "Jordan Ellison", "id": 4021}
    offenders = [
        key
        for key, value in _named_values(document)
        if key in PERSON_KEYS and value not in SYNTHETIC_CAST
    ]
    assert offenders == ["name"]


def _text(name: str) -> str:
    return (_dir() / f"{name}.json").read_text(encoding="utf-8")


def _interactions(name: str) -> object:
    """The recorded traffic only.

    The envelope's own ``name`` and ``description`` are this repository's prose about the fixture,
    not anything Canvas returned, and walking them makes a fixture called "rate-limited" look like
    a person called "rate-limited".
    """
    return json.loads(_text(name))["interactions"]


def _dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "canvas"


def _named_values(node: object, key: str | None = None) -> list[tuple[str, str]]:
    if isinstance(node, dict):
        found: list[tuple[str, str]] = []
        for name, value in node.items():
            found.extend(_named_values(value, name))
        return found
    if isinstance(node, list):
        found = []
        for item in node:
            found.extend(_named_values(item, key))
        return found
    if isinstance(node, str) and key is not None:
        return [(key, node)]
    return []
