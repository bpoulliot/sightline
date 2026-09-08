"""The documented scopes and the code cannot drift apart.

The item asks for the minimum read scopes to be determined and documented so an institution can
issue a least-privilege token. A README that says one thing while the client calls another is
worse than no documentation: it is a promise the tool breaks silently. So the README is checked
against :data:`sightline.canvas.scopes.REQUIRED_SCOPES`, in both directions, and every declared
scope is checked to correspond to a request the client actually makes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sightline.canvas.scopes import REQUIRED_PERMISSIONS, REQUIRED_SCOPES, scope_strings

SCOPE_PATTERN = re.compile(r"url:GET\|/api/v1/[^\s`|]+")


@pytest.fixture
def readme(repo_root: Path) -> str:
    # A bare ``|`` splits a markdown table cell even inside backticks, so the scope strings are
    # written escaped in the table. Unescape before comparing, or the check compares spelling
    # rather than content.
    return (repo_root / "README.md").read_text(encoding="utf-8").replace("\\|", "|")


def test_there_are_scopes_to_check() -> None:
    assert len(REQUIRED_SCOPES) >= 4


def test_no_scope_is_declared_twice() -> None:
    """Two rows granting the same scope means the README table has a duplicate in it too."""
    strings = scope_strings()
    assert len(set(strings)) == len(strings), sorted(
        scope for scope in strings if strings.count(scope) > 1
    )


def test_the_readme_lists_exactly_the_scopes_the_code_requires(readme: str) -> None:
    documented = set(SCOPE_PATTERN.findall(readme))
    declared = set(scope_strings())
    assert documented == declared, {
        "documented but not required": sorted(documented - declared),
        "required but not documented": sorted(declared - documented),
    }


def test_the_readme_names_the_permissions_a_scope_does_not_grant(readme: str) -> None:
    """A scope grants access to a route. It does not grant the underlying Canvas permission."""
    for permission in REQUIRED_PERMISSIONS:
        assert permission in readme


def test_the_readme_says_a_personal_token_cannot_be_scoped(readme: str) -> None:
    """The most useful sentence in the section: it stops someone hunting for a setting that does
    not exist."""
    lowered = readme.lower()
    assert "personal access token" in lowered
    assert "cannot be scoped" in lowered


#: Modules that make Canvas requests. Item 02's requests are all in the client; item 03's
#: acquisition endpoints are declared in ``sightline.acquire`` and issued through the client's
#: ``get_json``/``get_list``, so the scan reads both. Narrowing it back to ``client.py`` would make
#: the check pass by looking in the wrong place.
def _request_sources(repo_root: Path) -> str:
    package = repo_root / "src" / "sightline"
    sources = [package / "canvas" / "client.py", *sorted((package / "acquire").glob("*.py"))]
    assert len(sources) > 1, "the scope check examined only one module"
    return "\n".join(source.read_text(encoding="utf-8") for source in sources)


def test_every_declared_scope_is_a_request_the_package_makes(repo_root: Path) -> None:
    """A scope list that asks for more than the code uses is not least privilege."""
    client_source = _request_sources(repo_root)
    for scope in REQUIRED_SCOPES:
        # The last *literal* segment of the route — the placeholders are spelled differently in
        # the source (``{slug}`` where the scope says ``{page_url}``), so matching on one of those
        # would check spelling rather than whether the call exists.
        route = scope.request.split(" ", 1)[1].removeprefix("/api/v1")
        segments = [
            segment for segment in route.split("/") if segment and not segment.startswith("{")
        ]
        fragment = segments[-1] if segments else "courses"
        assert fragment in client_source, f"{scope.scope} names a route the package never calls"


def test_every_scope_says_why_it_is_needed() -> None:
    for scope in REQUIRED_SCOPES:
        assert len(scope.reason) > 30
        assert scope.scope.startswith("url:GET|")


def test_the_scopes_are_all_read_only() -> None:
    """Sightline never writes to Canvas. A scope list with a POST in it would be a bug worth
    catching here rather than in a security review.

    Item 03 kept this true deliberately. Asking Canvas to build a content export is a ``POST``, so
    acquisition reads an export the course already has instead, and says what to do when there is
    none. Making that one write is a product decision, and it belongs to a person rather than to
    whichever item next finds it convenient."""
    assert all(scope.scope.startswith("url:GET|") for scope in REQUIRED_SCOPES)
    assert all(scope.request.startswith("GET ") for scope in REQUIRED_SCOPES)
