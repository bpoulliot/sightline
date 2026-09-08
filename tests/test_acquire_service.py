"""Orchestration: what runs, what is skipped when nothing changed, and the one write not made.

Two things are asserted here that nothing else can see. The **cheap phase always runs and the
expensive phase does not**, which is what makes the cache trustworthy rather than merely fast — the
signature is computed from the course as it is now, not as it was when the entry was written. And
**Sightline never asks Canvas to build a content export**, because that is a ``POST``; it reads one
the course already has and says what to do when there is none.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import httpx
import pytest

from canvas_recordings import EXAMPLE_BASE_URL, FAKE_TOKEN, load
from sightline.acquire import CourseAcquisition
from sightline.acquire.service import REMEDY_NO_EXPORT
from sightline.canvas.client import CanvasClient
from sightline.model import ExaminationState, Source
from sightline.paths import DataPaths

EXPORT_URL = f"{EXAMPLE_BASE_URL}/files/9001/download?download_frd=1&verifier=synthetic"

MANIFEST = (
    '<?xml version="1.0" encoding="UTF-8"?><manifest><organizations><organization>'
    "<item><item><title>Synthetic module one</title>"
    '<item identifierref="r1"><title>Synthetic overview</title></item></item></item>'
    "</organization></organizations><resources>"
    '<resource identifier="r1" href="wiki_content/synthetic-exported.html"/>'
    "</resources></manifest>"
)


def build_archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("imsmanifest.xml", MANIFEST)
        bundle.writestr("wiki_content/synthetic-exported.html", "<p>From the export.</p>")
        bundle.writestr("web_resources/synthetic-handout.pdf", "%PDF-1.4 synthetic")
    return buffer.getvalue()


def json_response(payload: Any) -> httpx.Response:
    return httpx.Response(
        200,
        content=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )


def export_transport(*, listed: bool = True) -> httpx.MockTransport:
    """A minimal instance that holds one course and, optionally, one finished export.

    Hand-built rather than recorded: the archive is bytes, and a committed fixture is JSON.
    """
    archive = build_archive()
    exports = (
        [
            {
                "id": 71,
                "workflow_state": "exported",
                "export_type": "common_cartridge",
                "created_at": "2026-02-01T09:00:00Z",
                "attachment": {"url": EXPORT_URL},
            }
        ]
        if listed
        else []
    )
    bodies: dict[str, Any] = {
        "/api/v1/courses/4413": {
            "id": 4413,
            "name": "Sample Course 4413",
            "workflow_state": "available",
            "syllabus_body": "<p>Synthetic syllabus.</p>",
        },
        "/api/v1/courses/4413/tabs": [],
        "/api/v1/courses/4413/assignment_groups": [],
        "/api/v1/courses/4413/rubrics": [],
        "/api/v1/courses/4413/modules": [],
        "/api/v1/courses/4413/pages": [
            {"page_id": 1, "url": "synthetic-exported", "title": "Synthetic exported"}
        ],
        "/api/v1/courses/4413/assignments": [],
        "/api/v1/courses/4413/quizzes": [],
        "/api/v1/courses/4413/discussion_topics": [],
        "/api/v1/courses/4413/files": [],
        "/api/v1/courses/4413/content_exports": exports,
    }

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/files/9001/download":
            return httpx.Response(200, content=archive)
        if path == "/api/v1/courses/4413/pages/synthetic-exported":
            # The API refuses this body, so the export is the only source for it.
            return httpx.Response(
                403,
                content=b'{"status":"unauthorized"}',
                headers={"Content-Type": "application/json"},
            )
        if path in bodies:
            return json_response(bodies[path])
        raise AssertionError(f"unexpected request: {request.method} {path}")

    return httpx.MockTransport(handle)


# -- the cache decision -------------------------------------------------------------------------


async def test_a_second_run_over_an_unchanged_course_is_a_cache_hit(paths: DataPaths) -> None:
    recording = load("course-acquisition")
    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=recording.transport()
    ) as client:
        acquisition = CourseAcquisition(client, paths)
        first = await acquisition.acquire("4411", include_export=False)
        calls_after_first = len(recording.calls)
        second = await acquisition.acquire("4411", include_export=False)

    assert first.from_cache is False
    assert second.from_cache is True
    assert second.document.to_json() == first.document.to_json()
    assert second.signature == first.signature
    # The index phase runs again — that is what makes the key trustworthy — but the page bodies
    # are not re-fetched.
    made = len(recording.calls) - calls_after_first
    assert 0 < made < calls_after_first


async def test_the_cache_can_be_bypassed(paths: DataPaths) -> None:
    recording = load("course-acquisition")
    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=recording.transport()
    ) as client:
        acquisition = CourseAcquisition(client, paths)
        await acquisition.acquire("4411", include_export=False)
        again = await acquisition.acquire("4411", include_export=False, use_cache=False)
    assert again.from_cache is False


async def test_the_summary_states_counts_rather_than_a_verdict(paths: DataPaths) -> None:
    recording = load("course-acquisition")
    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=recording.transport()
    ) as client:
        result = await CourseAcquisition(client, paths).acquire("4411", include_export=False)
    summary = result.summary()
    assert "artifact(s) acquired" in summary
    assert "page" in summary
    for verdict in ("good", "poor", "score", "grade"):
        assert verdict not in summary.lower()


async def test_a_url_resolves_to_the_same_document_as_a_bare_id(paths: DataPaths) -> None:
    recording = load("course-acquisition")
    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=recording.transport()
    ) as client:
        acquisition = CourseAcquisition(client, paths)
        by_id = await acquisition.acquire("4411", include_export=False)
        by_url = await acquisition.acquire(
            f"{EXAMPLE_BASE_URL}/courses/4411/modules", include_export=False
        )
    assert by_url.signature == by_id.signature


# -- content exports ----------------------------------------------------------------------------


async def test_an_existing_export_fills_in_a_body_the_api_refused(paths: DataPaths) -> None:
    async with CanvasClient(EXAMPLE_BASE_URL, FAKE_TOKEN, transport=export_transport()) as client:
        result = await CourseAcquisition(client, paths).acquire("4413")

    page = result.document.by_id("page:synthetic-exported")
    assert page.body_html == "<p>From the export.</p>"
    assert page.body_source is Source.EXPORT
    note = result.document.examination("content_export")
    assert note is not None and note.state is ExaminationState.OK


async def test_a_file_only_the_export_knows_about_is_linked_by_search(paths: DataPaths) -> None:
    """No Canvas file id exists for it, so the link locates it by search and says so rather than
    pretending to be an item link."""
    async with CanvasClient(EXAMPLE_BASE_URL, FAKE_TOKEN, transport=export_transport()) as client:
        result = await CourseAcquisition(client, paths).acquire("4413")

    orphans = [item for item in result.document.files if not item.link_exact]
    assert [item.filename for item in orphans] == ["synthetic-handout.pdf"]
    assert orphans[0].canvas_path.startswith("/courses/4413/files?search_term=")
    assert orphans[0].local_path == "web_resources/synthetic-handout.pdf"


async def test_no_export_is_reported_with_what_to_do_about_it(paths: DataPaths) -> None:
    """Sightline never creates one. The remedy is the whole of the answer to 'why not'."""
    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=export_transport(listed=False)
    ) as client:
        result = await CourseAcquisition(client, paths).acquire("4413")

    note = result.document.examination("content_export")
    assert note is not None
    assert note.state is ExaminationState.ABSENT
    assert note.remedy == REMEDY_NO_EXPORT
    assert "never creates one" in note.remedy


async def test_acquisition_makes_no_write_request_to_canvas(paths: DataPaths) -> None:
    methods: list[str] = []

    inner = export_transport()

    def record(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return inner.handler(request)

    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=httpx.MockTransport(record)
    ) as client:
        await CourseAcquisition(client, paths).acquire("4413")

    assert methods
    assert set(methods) == {"GET"}


async def test_a_refused_export_listing_is_skipped_with_a_remedy(paths: DataPaths) -> None:
    recording = load("course-partial")
    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=recording.transport()
    ) as client:
        result = await CourseAcquisition(client, paths).acquire("4412")

    note = result.document.examination("content_export")
    assert note is not None
    assert note.state is ExaminationState.SKIPPED
    assert note.remedy


async def test_an_archive_outside_the_data_root_is_refused(paths: DataPaths, tmp_path: Any) -> None:
    """Course exports carry instructor-authored content and stay under the data root."""
    outside = tmp_path / "elsewhere.imscc"
    outside.write_bytes(build_archive())
    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=export_transport(listed=False)
    ) as client:
        result = await CourseAcquisition(client, paths).acquire("4413", archive=outside)

    note = result.document.examination("content_export")
    assert note is not None
    assert note.state is ExaminationState.SKIPPED
    assert "data root" in (note.remedy or "")


async def test_everything_written_stays_under_the_data_root(paths: DataPaths) -> None:
    """Item 01's containment test must still pass after this item, and so must this."""
    async with CanvasClient(EXAMPLE_BASE_URL, FAKE_TOKEN, transport=export_transport()) as client:
        await CourseAcquisition(client, paths).acquire("4413")

    written = [path for path in paths.root.rglob("*") if path.is_file()]
    assert written
    for path in written:
        assert paths.contains(path), path
    assert not list(paths.root.rglob("*.partial"))


@pytest.mark.parametrize("reference", ["not a course", "https://other.example.edu/courses/4411"])
async def test_an_unusable_reference_fails_before_any_request(
    paths: DataPaths, reference: str
) -> None:
    from sightline.canvas.errors import CanvasError

    recording = load("course-acquisition")
    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=recording.transport()
    ) as client:
        with pytest.raises(CanvasError):
            await CourseAcquisition(client, paths).acquire(reference)
    assert recording.calls == []
