"""Acquisition and normalization against recorded Canvas responses.

The acceptance condition for this item is here: **a recorded fixture course normalizes
deterministically** — run it twice, get identical output — and **every normalized object carries a
resolvable deep link**. Both are asserted below over the whole document rather than over a sample,
because a determinism test that checks three fields passes while the fourth flaps.

The second fixture, ``course-partial``, is the one that matters most. It is a course read by a
token that may not see all of it: quizzes refused, the Files tab answering 404, one page body
refused. Reporting any of that as an empty collection is the silent under-detection SPEC §11 names,
so the tests here assert on the *examination record*, not only on what was found.
"""

from __future__ import annotations

import pytest

from canvas_recordings import EXAMPLE_BASE_URL, FAKE_TOKEN, Recording, load
from sightline.acquire import CourseReader, normalize
from sightline.canvas.client import CanvasClient
from sightline.model import ArtifactKind, CourseDocument, ExaminationState, Source


async def acquire(recording: Recording, course_id: str) -> CourseDocument:
    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=recording.transport()
    ) as client:
        reader = CourseReader(client)
        index = await reader.read_index(course_id)
        slugs = [str(page["url"]) for page in index.items("pages")]
        bodies = await reader.read_page_bodies(course_id, slugs)
    return normalize(index, base_url=EXAMPLE_BASE_URL, bodies=bodies)


@pytest.fixture
async def document() -> CourseDocument:
    return await acquire(load("course-acquisition"), "4411")


@pytest.fixture
async def partial() -> CourseDocument:
    return await acquire(load("course-partial"), "4412")


# -- the acceptance conditions ----------------------------------------------------------------


async def test_the_same_course_normalizes_to_identical_bytes_twice() -> None:
    first = await acquire(load("course-acquisition"), "4411")
    second = await acquire(load("course-acquisition"), "4411")
    assert first.to_json() == second.to_json()


async def test_every_normalized_object_carries_a_deep_link(document: CourseDocument) -> None:
    artifacts = list(document.artifacts())
    assert len(artifacts) > 10, "the fixture course is too small to be evidence of anything"
    for artifact in artifacts:
        assert artifact.canvas_path.startswith("/courses/4411"), artifact.id
        assert "/edit" not in artifact.canvas_path, artifact.id
        assert document.deep_link(artifact).startswith(f"{EXAMPLE_BASE_URL}/courses/4411")


async def test_the_document_holds_every_kind_the_item_asks_for(document: CourseDocument) -> None:
    counts = document.counts()
    for kind in (
        ArtifactKind.PAGE,
        ArtifactKind.ASSIGNMENT,
        ArtifactKind.QUIZ,
        ArtifactKind.DISCUSSION,
        ArtifactKind.ANNOUNCEMENT,
        ArtifactKind.MODULE,
        ArtifactKind.EMBED,
        ArtifactKind.FILE,
    ):
        assert counts.get(kind.value), f"nothing normalized for {kind.value}"


# -- structure --------------------------------------------------------------------------------


async def test_the_syllabus_becomes_an_artifact(document: CourseDocument) -> None:
    assert document.syllabus is not None
    assert document.syllabus.canvas_path == "/courses/4411/assignments/syllabus"
    assert "Synthetic syllabus" in (document.syllabus.body_html or "")


async def test_page_bodies_come_from_the_api(document: CourseDocument) -> None:
    """The listing endpoint returns titles only, so a body is a second request per page."""
    overview = document.by_id("page:synthetic-overview")
    assert overview is not None
    assert overview.body_source is Source.API
    assert "<h2>Synthetic overview</h2>" in (overview.body_html or "")


async def test_module_items_truncated_by_canvas_are_re_read(document: CourseDocument) -> None:
    """Canvas caps the items it includes with a module listing and gives no signal that it did.
    Trusting the include silently loses the eleventh item of every long module."""
    module = document.by_id("module:3101")
    assert module is not None
    assert len(module.items) == 3
    assert module.items[-1].item_type == "ExternalTool"


async def test_an_artifact_inherits_its_place_in_the_course(document: CourseDocument) -> None:
    assert document.by_id("page:synthetic-overview").module_path == ("Synthetic module one",)
    assert document.by_id("assignment:9101").module_path == ("Synthetic module one",)


async def test_a_sub_header_becomes_a_second_level_of_the_module_path(
    document: CourseDocument,
) -> None:
    """Canvas gives a sub-header no identity beyond its title, but it is how a designer groups a
    long module, and D47 reads structural consistency off exactly that."""
    assert document.by_id("quiz:8101").module_path == (
        "Synthetic module two",
        "Synthetic section",
    )


async def test_announcements_and_discussions_are_separated(document: CourseDocument) -> None:
    """Canvas models both as discussion topics; they answer different criteria."""
    assert [item.id for item in document.announcements] == ["announcement:6101"]
    assert [item.id for item in document.discussions] == ["discussion:7101", "discussion:7102"]
    assert document.discussions[1].graded is True
    assert document.discussions[0].graded is False


async def test_an_attached_rubric_is_recorded_on_the_assignment(document: CourseDocument) -> None:
    assert document.by_id("assignment:9101").has_rubric is True
    assert document.by_id("assignment:9101").rubric_title == "Synthetic reflection rubric"
    assert document.by_id("assignment:9102").has_rubric is False


async def test_navigation_and_gradebook_structure_are_normalized(
    document: CourseDocument,
) -> None:
    hidden = {tab.id for tab in document.navigation if tab.hidden}
    assert hidden == {"pages"}
    assert sum(group.group_weight or 0 for group in document.assignment_groups) == 90.0
    assert document.rubrics[0].criteria_count == 3


async def test_embeds_are_extracted_from_bodies(document: CourseDocument) -> None:
    assert [embed.container_id for embed in document.embeds] == ["page:synthetic-overview"]
    assert document.embeds[0].tag == "iframe"
    assert document.embeds[0].canvas_path == "/courses/4411/pages/synthetic-overview"


# -- what was examined ------------------------------------------------------------------------


async def test_every_collection_reports_what_it_examined(document: CourseDocument) -> None:
    """A detector that later says "nothing found" can check whether anything was readable."""
    names = {entry.collection for entry in document.examinations}
    assert {"pages", "assignments", "quizzes", "modules", "files", "embeds"} <= names
    for entry in document.examinations:
        assert entry.detail, entry.collection


async def test_a_refused_collection_is_not_reported_as_an_empty_one(
    partial: CourseDocument,
) -> None:
    quizzes = partial.examination("quizzes")
    assert quizzes is not None
    assert quizzes.state is ExaminationState.REFUSED
    assert not quizzes.usable
    assert quizzes.remedy
    assert partial.quizzes == ()


async def test_a_404_collection_says_canvas_does_not_distinguish_the_two_cases(
    partial: CourseDocument,
) -> None:
    files = partial.examination("files")
    assert files is not None
    assert files.state is ExaminationState.ABSENT
    assert "does not distinguish" in files.detail


async def test_an_unreadable_page_body_is_absent_rather_than_empty(
    partial: CourseDocument,
) -> None:
    """An empty body is a finding. Inventing one would be a fabricated fact."""
    readable = partial.by_id("page:synthetic-readable")
    refused = partial.by_id("page:synthetic-refused")
    assert readable.body_html == "<p>Synthetic readable body.</p>"
    assert refused.body_html is None
    bodies = partial.examination("page_bodies")
    assert bodies is not None
    assert "1 could not be read" in bodies.detail


async def test_an_empty_collection_is_reported_as_empty_not_refused(
    partial: CourseDocument,
) -> None:
    assignments = partial.examination("assignments")
    assert assignments is not None
    assert assignments.state is ExaminationState.EMPTY
    assert assignments.usable


async def test_an_empty_syllabus_still_produces_an_artifact(partial: CourseDocument) -> None:
    """An empty syllabus and an unrequested one are different facts, and D01 turns on it."""
    assert partial.syllabus is not None
    assert partial.syllabus.body_html == ""


# -- the recorded traffic ---------------------------------------------------------------------


async def test_reading_a_course_makes_only_the_requests_the_scopes_declare() -> None:
    """The transport refuses an unrecorded request, so this passing at all is the assertion. What
    is checked here is the shape: reading a course's structure is every bit a ``GET``.

    The one write Sightline makes is not here. It is the request that asks Canvas to package a
    copy of the course, it belongs to acquisition rather than to reading, and it is asserted in
    ``tests/test_acquire_service.py`` and ``tests/test_canvas_exports.py``.
    """
    recording = load("course-acquisition")
    await acquire(recording, "4411")
    assert recording.calls
    assert {request.method for request in recording.calls} == {"GET"}
    assert all("/api/v1/" in path for path in recording.paths_called)
