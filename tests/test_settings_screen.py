"""The settings screen and the course-resolution screen.

Both work with JavaScript switched off, so every assertion here is a plain form post. The Canvas
calls are answered from recorded fixtures through a patched client, never from a live instance.

Two rules are checked that pull against each other: a persistent status surface reports presence
and never a value, while the transient result of a connection test the user just asked for **does**
name the host — a test that cannot say which host answered is not a test.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from canvas_recordings import EXAMPLE_BASE_URL, EXAMPLE_HOST, FAKE_TOKEN, Recording, load
from sightline import views
from sightline.app import create_app
from sightline.config import Settings


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def canvas(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Recording]]:
    """Point every CanvasClient the views build at a recorded fixture."""
    state: dict[str, Recording] = {"recording": load("happy")}
    original = views.CanvasClient

    def build(base_url, token, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", state["recording"].transport())
        return original(base_url, token, **kwargs)

    monkeypatch.setattr(views, "CanvasClient", build)
    yield state


def configure(client: TestClient) -> None:
    response = client.post(
        "/settings",
        data={"canvas_base_url": EXAMPLE_BASE_URL, "canvas_token": FAKE_TOKEN},
        follow_redirects=False,
    )
    assert response.status_code == 303


# -- the settings screen ----------------------------------------------------------------------


def test_the_settings_screen_renders_with_nothing_configured(client: TestClient) -> None:
    body = client.get("/settings").text
    assert "Canvas connection" in body
    assert "not set" in body
    assert "not stored" in body
    assert "environment" in body


def test_saving_a_credential_reports_it_as_stored(client: TestClient) -> None:
    configure(client)
    body = client.get("/settings").text
    assert "stored" in body
    assert "Saved." in client.get("/settings?saved=yes").text


def test_the_token_is_never_rendered_back(client: TestClient) -> None:
    configure(client)
    body = client.get("/settings").text
    assert FAKE_TOKEN not in body
    for length in (6, 10):
        assert FAKE_TOKEN[:length] not in body
        assert FAKE_TOKEN[-length:] not in body


def test_the_base_url_is_prefilled_because_the_user_typed_it(client: TestClient) -> None:
    """Unlike the token, the base URL has to come back: an unfillable field cannot be corrected."""
    configure(client)
    assert f'value="{EXAMPLE_BASE_URL}"' in client.get("/settings").text


def test_a_base_url_with_a_path_is_trimmed_and_the_user_is_told(client: TestClient) -> None:
    response = client.post(
        "/settings",
        data={"canvas_base_url": f"{EXAMPLE_BASE_URL}/courses/4410", "canvas_token": FAKE_TOKEN},
    )
    assert "trimmed to its scheme and host" in response.text
    assert f'value="{EXAMPLE_BASE_URL}"' in response.text


def test_an_unusable_base_url_is_refused_on_screen_with_a_remedy(client: TestClient) -> None:
    response = client.post("/settings", data={"canvas_base_url": "ftp://x.example"})
    assert response.status_code == 200
    assert "not a URL scheme Sightline can use" in response.text
    assert "https://" in response.text


def test_forgetting_removes_the_stored_credential(client: TestClient) -> None:
    configure(client)
    response = client.post("/settings/forget")
    assert "has been deleted" in response.text
    assert "not stored" in response.text


def test_the_scope_table_is_on_the_screen_not_only_in_the_readme(client: TestClient) -> None:
    body = client.get("/settings").text
    assert "url:GET|/api/v1/courses/:id" in body
    assert "cannot be scoped" in body
    assert "Course Content - view" in body


# -- the connection test ----------------------------------------------------------------------


def test_a_connection_test_reports_host_user_and_count(
    client: TestClient, canvas: dict[str, Recording]
) -> None:
    configure(client)
    response = client.post("/settings/test", data={"course_reference": ""})
    body = response.text
    assert EXAMPLE_HOST in body
    assert "Canvas user 4021" in body
    assert "3 courses" in body
    assert "url:GET|/api/v1/courses" in body


def test_a_connection_test_with_a_course_reports_both_extra_checks(
    client: TestClient, canvas: dict[str, Recording]
) -> None:
    configure(client)
    body = client.post("/settings/test", data={"course_reference": "4410"}).text
    assert "resolved to course 4410" in body
    assert "Course content is readable" in body


def test_a_failing_connection_test_shows_what_to_change(
    client: TestClient, canvas: dict[str, Recording]
) -> None:
    """'Error' alone is not acceptable output; the message must name the next move."""
    canvas["recording"] = load("invalid-token")
    configure(client)
    body = client.post("/settings/test", data={"course_reference": ""}).text
    assert "rejected the token" in body
    assert "New Access Token" in body
    assert "skipped" in body


def test_testing_with_nothing_configured_says_what_to_configure(client: TestClient) -> None:
    body = client.post("/settings/test", data={"course_reference": ""}).text
    assert "No Canvas base URL is configured" in body
    assert "CANVAS_BASE_URL" in body


def test_the_json_endpoint_is_the_same_code_path(
    client: TestClient, canvas: dict[str, Recording]
) -> None:
    configure(client)
    payload = client.post("/api/canvas/test").json()
    assert payload["ok"] is True
    assert payload["host"] == EXAMPLE_HOST
    assert [check["name"] for check in payload["checks"]] == [
        "Authenticated user",
        "Courses visible",
    ]


def test_the_json_endpoint_reports_a_failure_as_a_failure(
    client: TestClient, canvas: dict[str, Recording]
) -> None:
    canvas["recording"] = load("not-permitted")
    configure(client)
    response = client.post("/api/canvas/test")
    assert response.status_code == 502
    assert response.json()["remedy"]


def test_the_status_page_still_shows_presence_without_values(client: TestClient) -> None:
    """The persistent surface keeps the old rule even though the settings screen relaxes it."""
    configure(client)
    body = client.get("/").text
    assert "CANVAS_BASE_URL" in body
    assert EXAMPLE_HOST not in body


# -- the course screen ------------------------------------------------------------------------


def test_the_course_screen_says_when_nothing_is_configured(client: TestClient) -> None:
    body = client.get("/courses").text
    assert "parsed but not looked up" in body


def test_pasted_references_resolve_and_report_every_outcome(
    client: TestClient, canvas: dict[str, Recording]
) -> None:
    configure(client)
    body = client.post(
        "/courses",
        data={"references": f"4410\n{EXAMPLE_BASE_URL}/courses/4410/pages/welcome\nnonsense"},
    ).text
    assert "1 resolved" in body
    assert "1 repeated" in body
    assert "1 not understood" in body
    assert "Sample Course 4410" in body


def test_a_csv_upload_resolves_the_same_course(
    client: TestClient, canvas: dict[str, Recording]
) -> None:
    configure(client)
    csv = b"course_url,term\nhttps://canvas.example.edu/courses/4410/assignments/1,Spring\n"
    body = client.post(
        "/courses",
        data={"references": ""},
        files={"upload": ("courses.csv", csv, "text/csv")},
    ).text
    # Jinja escapes the quotes around the column name, so compare on the unescaped text.
    assert "Read the column headed" in body.replace("&#39;", "'")
    assert "'course_url'" in body.replace("&#39;", "'")
    assert "Sample Course 4410" in body


def test_submitting_nothing_says_so_rather_than_rendering_an_empty_table(
    client: TestClient,
) -> None:
    body = client.post("/courses", data={"references": ""}).text
    assert "Nothing to resolve" in body


def test_a_course_visible_but_unreadable_is_reported_as_exactly_that(
    client: TestClient, canvas: dict[str, Recording]
) -> None:
    canvas["recording"] = load("content-blocked")
    configure(client)
    body = client.post("/courses", data={"references": "4431"}).text
    assert "Sample Course 4431" in body
    assert "Course Content - view" in body


def test_a_lookup_failure_is_a_row_not_a_missing_row(
    client: TestClient, canvas: dict[str, Recording]
) -> None:
    canvas["recording"] = load("not-found")
    configure(client)
    body = client.post("/courses", data={"references": "9999"}).text
    assert "failed" in body
    assert "cannot see" in body


def test_a_malformed_environment_base_url_is_a_row_not_a_500(
    settings: Settings, canvas: dict[str, Recording]
) -> None:
    """A stored base URL is normalized on the way in; the environment fallback is not."""
    broken = settings.model_copy(update={"canvas_base_url": "ftp://canvas.example.edu"})
    with TestClient(create_app(broken)) as broken_client:
        broken_client.post("/settings", data={"canvas_base_url": "", "canvas_token": FAKE_TOKEN})
        body = broken_client.post("/courses", data={"references": "4410"}).text
    assert "not attempted" in body
    assert "not a URL scheme Sightline can use" in body


def test_an_oversized_upload_is_refused_by_its_size(client: TestClient) -> None:
    oversized = b"course_id\n" + b"4410\n" * 500_000
    body = client.post(
        "/courses",
        data={"references": ""},
        files={"upload": ("huge.csv", oversized, "text/csv")},
    ).text
    assert "probably the wrong file" in body


# -- vocabulary -------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/settings", "/courses"])
def test_the_new_screens_never_evaluate_or_grade(client: TestClient, path: str) -> None:
    """A product requirement, not a nicety (SPEC §11): the vocabulary is review and refresh."""
    body = client.get(path).text.lower().replace("does not grade", "")
    for forbidden in (" grade", "grading", "evaluation", "pass/fail"):
        assert forbidden not in body, forbidden
