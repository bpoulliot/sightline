"""The Canvas client, against recorded fixtures — never a live instance.

A test suite that needs a real Canvas token is a test suite that does not run in CI. Everything
here replays ``tests/fixtures/canvas/*.json`` through :class:`httpx.MockTransport`, including the
four failure paths that matter: a rejected token (401 with ``WWW-Authenticate``), a refused call
(401 without it, and 403), a 404, and a course the token can see but not read.

The assertions are on the **messages**, not only on the exception types. "Error" alone is not
acceptable output for this item, and a test that only checks ``pytest.raises(CanvasError)`` would
pass against exactly that.
"""

from __future__ import annotations

import httpx
import pytest

from canvas_recordings import EXAMPLE_BASE_URL, EXAMPLE_HOST, FAKE_TOKEN, Recording, load
from sightline.canvas import (
    CanvasAuthError,
    CanvasClient,
    CanvasNotApiError,
    CanvasNotConfiguredError,
    CanvasNotFoundError,
    CanvasPermissionError,
    CanvasRateLimitedError,
    CanvasUnreachableError,
)
from sightline.canvas.client import parse_link_header


def client_for(recording: Recording, **kwargs: object) -> CanvasClient:
    return CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=recording.transport(), **kwargs  # type: ignore[arg-type]
    )


# -- configuration --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("base_url", "token"), [(None, FAKE_TOKEN), (EXAMPLE_BASE_URL, None), ("", "")]
)
def test_a_missing_credential_fails_before_any_request(
    base_url: str | None, token: str | None
) -> None:
    with pytest.raises(CanvasNotConfiguredError) as caught:
        CanvasClient(base_url, token)
    assert "Settings screen" in caught.value.remedy
    assert "CANVAS_BASE_URL" in caught.value.remedy


def test_a_base_url_with_a_path_is_trimmed_to_the_root() -> None:
    client = CanvasClient(f"{EXAMPLE_BASE_URL}/courses/4410", FAKE_TOKEN)
    assert client.base_url == EXAMPLE_BASE_URL
    assert client.host == EXAMPLE_HOST


def test_the_repr_carries_no_token_and_no_base_url() -> None:
    """A repr turns up in a traceback and in pytest failure output. Both leak secrets."""
    text = repr(CanvasClient(EXAMPLE_BASE_URL, FAKE_TOKEN))
    assert FAKE_TOKEN not in text
    assert EXAMPLE_BASE_URL not in text
    assert EXAMPLE_HOST in text


# -- the happy path --------------------------------------------------------------------------


async def test_the_token_travels_as_a_bearer_header_and_nowhere_else() -> None:
    recording = load("happy")
    async with client_for(recording) as client:
        await client.current_user()
    request = recording.calls[0]
    assert request.headers["authorization"] == f"Bearer {FAKE_TOKEN}"
    assert FAKE_TOKEN not in str(request.url)


async def test_current_user_names_the_token_holder() -> None:
    async with client_for(load("happy")) as client:
        user = await client.current_user()
    assert user.id == "4021"
    assert "Canvas user 4021" in user.label


async def test_visible_courses_follows_pagination_and_reports_an_exact_count() -> None:
    recording = load("happy")
    async with client_for(recording) as client:
        count = await client.visible_courses()
    assert (count.count, count.exact, count.pages_read) == (3, True, 2)
    assert count.label == "3 courses"
    assert recording.paths_called.count("/api/v1/courses") == 2


async def test_a_course_resolves_with_a_link_back_into_canvas() -> None:
    async with client_for(load("happy")) as client:
        course = await client.course("4410")
    assert course.id == "4410"
    assert course.html_url == f"{EXAMPLE_BASE_URL}/courses/4410"
    assert course.label == "Sample Course 4410 (SAMPLE-4410)"


@pytest.mark.parametrize(
    "reference",
    [
        "4410",
        f"{EXAMPLE_BASE_URL}/courses/4410",
        f"{EXAMPLE_BASE_URL}/courses/4410/pages/welcome",
        f"{EXAMPLE_BASE_URL}/courses/4410/assignments/9912",
    ],
)
async def test_every_input_form_resolves_to_the_same_course(reference: str) -> None:
    """The item's acceptance criterion, asserted through the client rather than the parser."""
    async with client_for(load("happy")) as client:
        resolved = await client.resolve_course(reference)
    assert resolved.course.id == "4410"
    assert resolved.access.readable


async def test_an_sis_reference_reaches_the_sis_route_and_returns_the_numeric_id() -> None:
    recording = load("happy")
    async with client_for(recording) as client:
        resolved = await client.resolve_course("sis_course_id:SAMPLE-4410")
    assert "/api/v1/courses/sis_course_id:SAMPLE-4410" in recording.paths_called
    assert resolved.course.id == "4410"


# -- failure paths ---------------------------------------------------------------------------


async def test_401_with_www_authenticate_is_a_bad_token() -> None:
    async with client_for(load("invalid-token")) as client:
        with pytest.raises(CanvasAuthError) as caught:
            await client.current_user()
    error = caught.value
    assert error.status_code == 401
    assert EXAMPLE_HOST in error.summary
    assert "New Access Token" in error.remedy
    assert "Invalid access token" in (error.detail or "")


async def test_401_without_www_authenticate_is_a_permission_problem() -> None:
    """Same status code, opposite remedy. Getting it wrong sends people to reissue a good token."""
    async with client_for(load("not-permitted")) as client:
        with pytest.raises(CanvasPermissionError) as caught:
            await client.visible_courses()
    assert caught.value.status_code == 401
    assert "not permitted" in caught.value.remedy
    assert "scope" in caught.value.remedy


async def test_403_is_a_permission_problem_and_names_the_scope_list() -> None:
    async with client_for(load("forbidden")) as client:
        with pytest.raises(CanvasPermissionError) as caught:
            await client.course("4410")
    assert caught.value.status_code == 403
    assert "Canvas token scopes" in caught.value.remedy


async def test_403_saying_rate_limit_is_not_reported_as_a_permission_problem() -> None:
    """Canvas throttles with 403 and says so only in the body."""
    async with client_for(load("rate-limited")) as client:
        with pytest.raises(CanvasRateLimitedError) as caught:
            await client.current_user()
    assert "throttling" in caught.value.summary
    assert "Wait a minute" in caught.value.remedy


async def test_404_says_it_cannot_tell_missing_from_invisible() -> None:
    async with client_for(load("not-found")) as client:
        with pytest.raises(CanvasNotFoundError) as caught:
            await client.course("9999")
    remedy = caught.value.remedy
    assert "does not exist" in remedy
    assert "cannot see" in remedy
    assert "enrolled" in remedy


async def test_an_html_login_page_is_reported_as_not_being_the_api() -> None:
    async with client_for(load("login-portal")) as client:
        with pytest.raises(CanvasNotApiError) as caught:
            await client.current_user()
    assert "text/html" in (caught.value.detail or "")
    assert "root of the instance" in caught.value.remedy


async def test_a_host_that_never_answers_is_a_reachability_problem() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=httpx.MockTransport(refuse)
    ) as client:
        with pytest.raises(CanvasUnreachableError) as caught:
            await client.current_user()
    assert EXAMPLE_HOST in caught.value.summary
    assert "DNS" in caught.value.remedy


async def test_a_timeout_names_the_firewall_case() -> None:
    def stall(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=httpx.MockTransport(stall)
    ) as client:
        with pytest.raises(CanvasUnreachableError) as caught:
            await client.current_user()
    assert "firewall" in caught.value.remedy


# -- visible but unreadable ------------------------------------------------------------------


async def test_a_course_visible_but_not_readable_is_reported_as_exactly_that() -> None:
    """The case a connection test that only calls /courses/:id reports as a success."""
    async with client_for(load("content-blocked")) as client:
        resolved = await client.resolve_course("4431")
    assert resolved.course.id == "4431"
    assert resolved.access.state == "refused"
    assert not resolved.access.readable
    assert "Course Content - view" in (resolved.access.remedy or "")


async def test_a_404_on_pages_is_unknown_rather_than_a_guess() -> None:
    async with client_for(load("pages-absent")) as client:
        access = await client.content_access("4438")
    assert access.state == "unknown"
    assert "does not distinguish" in access.detail
    assert access.remedy


# -- the connection test ---------------------------------------------------------------------


async def test_a_successful_connection_test_reports_host_user_and_count() -> None:
    async with client_for(load("happy")) as client:
        report = await client.test_connection()
    assert report.ok
    assert report.host == EXAMPLE_HOST
    summary = report.summary()
    assert EXAMPLE_HOST in summary
    assert "Canvas user 4021" in summary
    assert "3 courses" in summary
    assert [check.scope for check in report.checks] == [
        "url:GET|/api/v1/users/:id",
        "url:GET|/api/v1/courses",
    ]


async def test_the_course_count_check_warns_that_the_endpoint_lists_enrolments() -> None:
    """An account-level token legitimately sees zero here. Failing on it would be wrong."""
    async with client_for(load("happy")) as client:
        report = await client.test_connection()
    courses = next(check for check in report.checks if check.name == "Courses visible")
    assert "enrolled in" in courses.detail


async def test_a_connection_test_with_a_course_adds_two_checks() -> None:
    async with client_for(load("happy")) as client:
        report = await client.test_connection(course_reference=f"{EXAMPLE_BASE_URL}/courses/4410")
    assert report.ok
    names = [check.name for check in report.checks]
    assert names == ["Authenticated user", "Courses visible", "Course", "Course content"]
    assert "resolved to course 4410" in report.checks[2].detail


async def test_a_course_that_cannot_be_read_fails_that_check_and_only_that_check() -> None:
    async with client_for(load("content-blocked")) as client:
        report = await client.test_connection(course_reference="4431")
    assert not report.ok
    statuses = {check.name: check.status for check in report.checks}
    assert statuses == {
        "Authenticated user": "ok",
        "Courses visible": "ok",
        "Course": "ok",
        "Course content": "failed",
    }
    assert "Course Content - view" in (report.remedy or "")


async def test_a_failed_first_request_marks_the_rest_skipped_rather_than_omitting_them() -> None:
    """An absent check looks like a check nobody needed. SPEC §11: failures are never silent."""
    async with client_for(load("invalid-token")) as client:
        report = await client.test_connection(course_reference="4410")
    assert not report.ok
    statuses = [(check.name, check.status) for check in report.checks]
    assert statuses == [
        ("Authenticated user", "failed"),
        ("Courses visible", "skipped"),
        ("Course", "skipped"),
    ]
    assert all(
        "did not succeed" in check.detail for check in report.checks if check.status == "skipped"
    )
    assert "New Access Token" in (report.remedy or "")


async def test_a_connection_test_never_raises_for_a_canvas_side_failure() -> None:
    async with client_for(load("rate-limited")) as client:
        report = await client.test_connection()
    assert not report.ok
    assert "throttling" in report.checks[0].detail


async def test_every_check_carries_the_scope_it_exercises() -> None:
    """The scope list is a promise; the connection test is what verifies it against a real token."""
    async with client_for(load("happy")) as client:
        report = await client.test_connection(course_reference="4410")
    assert all(check.scope for check in report.checks)
    assert report.as_dict()["checks"][0]["scope"] == "url:GET|/api/v1/users/:id"


# -- pagination header -----------------------------------------------------------------------


def test_link_headers_are_parsed_by_relation() -> None:
    header = (
        '<https://canvas.example.edu/api/v1/courses?page=2>; rel="next", '
        '<https://canvas.example.edu/api/v1/courses?page=9>; rel="last"'
    )
    assert parse_link_header(header) == {
        "next": "https://canvas.example.edu/api/v1/courses?page=2",
        "last": "https://canvas.example.edu/api/v1/courses?page=9",
    }
    assert parse_link_header("") == {}


async def test_pagination_never_follows_a_link_onto_another_host() -> None:
    """A Canvas behind a misconfigured proxy emits absolute URLs elsewhere; following one would
    send the token there."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url.host))
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[], request=request)
        return httpx.Response(
            200,
            json=[{"id": 4410}],
            headers={"Link": '<https://evil.example.net/api/v1/courses?page=2>; rel="next"'},
            request=request,
        )

    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=httpx.MockTransport(handler)
    ) as client:
        await client.visible_courses()
    assert set(seen) == {EXAMPLE_HOST}


async def test_the_page_walk_stops_and_says_the_count_is_a_floor() -> None:
    def endless(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[{"id": 1}, {"id": 2}],
            headers={"Link": f'<{EXAMPLE_BASE_URL}/api/v1/courses?page=99>; rel="next"'},
            request=request,
        )

    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=httpx.MockTransport(endless)
    ) as client:
        count = await client.visible_courses()
    assert not count.exact
    assert count.label.startswith("at least")


# -- the fixture harness itself ----------------------------------------------------------------


async def test_an_unrecorded_request_fails_loudly() -> None:
    """A mock that answers anything lets a client call the wrong endpoint and still pass."""
    from canvas_recordings import UnrecordedRequest

    async with client_for(load("invalid-token")) as client:
        with pytest.raises(UnrecordedRequest):
            await client.course("4410")
