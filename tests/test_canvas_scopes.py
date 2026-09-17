"""The documented scopes and the code cannot drift apart, and the writes stay at one.

The item asks for the minimum read scopes to be determined and documented so an institution can
issue a least-privilege token. A README that says one thing while the client calls another is
worse than no documentation: it is a promise the tool breaks silently. So the README is checked
against :data:`sightline.canvas.scopes.REQUIRED_SCOPES`, in both directions, and every declared
scope is checked to correspond to a request the client actually makes.

Since item 23 this file also carries the guard on the one thing Sightline sends that is not a
``GET``: the request that asks Canvas to package a copy of the course under review. The guard is a
**count** rather than a ban, and the test below it fires that guard over a tampered tuple instead
of assuming it works.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sightline.canvas.scopes import (
    REQUIRED_PERMISSIONS,
    REQUIRED_SCOPES,
    Scope,
    scope_strings,
    write_scopes,
)

SCOPE_PATTERN = re.compile(r"url:(?:GET|POST)\|/api/v1/[^\s`|]+")

#: The one write, spelled out here rather than derived from the tuple. Deriving it would make the
#: test agree with whatever the code says, which is not a test.
CONTENT_EXPORT_POST = "url:POST|/api/v1/courses/:course_id/content_exports"


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
        assert scope.scope.startswith(("url:GET|", "url:POST|"))


def test_exactly_one_scope_is_a_write_and_it_is_the_content_export_post() -> None:
    """Sightline does not modify course content; it asks Canvas to package a copy of the course it
    is reviewing. That is one ``POST``, and this is the count that keeps it at one.

    This test used to assert that every scope was a ``GET``. Item 23 inverted it rather than
    deleting it, deliberately: a blanket ban that gets removed the first time it is inconvenient
    teaches nothing, while a number that must stay at one makes the next write a thing somebody
    has to change in front of a reviewer.
    """
    writes = write_scopes()
    assert [scope.scope for scope in writes] == [CONTENT_EXPORT_POST]
    assert writes[0].request == "POST /api/v1/courses/{id}/content_exports"
    assert all(scope.scope.startswith("url:GET|") for scope in REQUIRED_SCOPES if not scope.writes)
    assert all(scope.request.startswith("GET ") for scope in REQUIRED_SCOPES if not scope.writes)


def test_the_write_count_guard_fires_on_a_second_write() -> None:
    """A guard nobody has seen fail is indistinguishable from a guard that cannot fail.

    So the invariant is run over a tuple with a second write added, and the count is checked to
    move. If this passes while the test above is broken, the test above proves nothing.
    """
    second_write = Scope(
        scope="url:POST|/api/v1/courses/:course_id/pages",
        request="POST /api/v1/courses/{id}/pages",
        reason="A hypothetical second write, which exists only inside this test.",
    )
    tampered = (*REQUIRED_SCOPES, second_write)
    assert len(write_scopes(tampered)) == 2
    assert [scope.scope for scope in write_scopes(tampered)] != [CONTENT_EXPORT_POST]


def test_a_row_whose_two_spellings_disagree_counts_as_a_write() -> None:
    """``writes`` reads the scope string and the request. A row edited halfway — the scope
    changed to a POST, the request still saying GET — must not read as a GET."""
    halfway = Scope(
        scope="url:POST|/api/v1/courses/:course_id/pages",
        request="GET /api/v1/courses/{id}/pages",
        reason="A half-edited row, which exists only inside this test.",
    )
    assert halfway.writes
