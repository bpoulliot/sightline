"""The normalized document model: identity, deep links and determinism.

Three properties are load-bearing and each is tested here rather than assumed by the layers above.
Every artifact has a resolvable deep link into the Canvas UI and never into an editor. Ids are
built from a thing's own identity, so a finding recorded today still points at the same artifact
after somebody reorders a module. And serialization is deterministic, which is what the cache and
anyone diffing two runs depend on.
"""

from __future__ import annotations

import pytest

from sightline.model import (
    SCHEMA_VERSION,
    ArtifactKind,
    Assignment,
    AssignmentGroup,
    CourseDocument,
    CourseFile,
    CourseInfo,
    Discussion,
    Embed,
    Examination,
    ExaminationState,
    Module,
    ModuleItem,
    NavigationTab,
    Page,
    Quiz,
    RubricSummary,
    SchemaVersionMismatch,
    Source,
    Syllabus,
    artifact_from_dict,
    artifact_id,
    embed_id,
    file_search_path,
)

BASE = "https://canvas.example.edu"


def build_document() -> CourseDocument:
    """One of everything, so the round-trip covers every artifact class."""
    page = Page(
        id=artifact_id(ArtifactKind.PAGE, "start-here"),
        title="Start Here",
        canvas_path="/courses/4410/pages/start-here",
        slug="start-here",
        front_page=True,
        published=True,
        body_html='<p>Body with <iframe src="https://canvas.example.edu/x"></iframe></p>',
        body_source=Source.API,
        module_path=("Module 1",),
        updated_at="2026-01-10T12:00:00Z",
    )
    return CourseDocument(
        base_url=BASE,
        course=CourseInfo(id="4410", name="Sample Course 4410", course_code="SAMPLE-4410"),
        syllabus=Syllabus(
            id=artifact_id(ArtifactKind.SYLLABUS, "4410"),
            title="Syllabus",
            canvas_path="/courses/4410/assignments/syllabus",
            body_html="<p>Syllabus body.</p>",
            body_source=Source.API,
        ),
        modules=(
            Module(
                id=artifact_id(ArtifactKind.MODULE, "3001"),
                title="Module 1",
                canvas_path="/courses/4410/modules#module_3001",
                canvas_id="3001",
                position=1,
                items=(
                    ModuleItem(
                        id=artifact_id(ArtifactKind.MODULE_ITEM, "31001"),
                        title="Start Here",
                        canvas_path="/courses/4410/modules/items/31001",
                        canvas_id="31001",
                        module_id="module:3001",
                        item_type="Page",
                        target_id="page:start-here",
                    ),
                ),
            ),
        ),
        pages=(page,),
        assignments=(
            Assignment(
                id=artifact_id(ArtifactKind.ASSIGNMENT, "9001"),
                title="Reflection 1",
                canvas_path="/courses/4410/assignments/9001",
                canvas_id="9001",
                due_at="2026-01-23T23:59:00Z",
                points_possible=30.0,
                submission_types=("online_text_entry",),
                has_rubric=True,
            ),
        ),
        quizzes=(
            Quiz(
                id=artifact_id(ArtifactKind.QUIZ, "8001"),
                title="Quiz 1",
                canvas_path="/courses/4410/quizzes/8001",
                canvas_id="8001",
                time_limit=45,
            ),
        ),
        discussions=(
            Discussion(
                id=artifact_id(ArtifactKind.DISCUSSION, "7001"),
                title="Introduce yourself",
                canvas_path="/courses/4410/discussion_topics/7001",
                canvas_id="7001",
            ),
        ),
        announcements=(
            Discussion(
                kind=ArtifactKind.ANNOUNCEMENT,
                id=artifact_id(ArtifactKind.ANNOUNCEMENT, "6001"),
                title="Reminder",
                canvas_path="/courses/4410/discussion_topics/6001",
                canvas_id="6001",
            ),
        ),
        files=(
            CourseFile(
                id=artifact_id(ArtifactKind.FILE, "5001"),
                title="handout.pdf",
                canvas_path="/courses/4410/files/5001",
                canvas_id="5001",
                filename="handout.pdf",
                content_type="application/pdf",
                size=1024,
            ),
        ),
        embeds=(
            Embed(
                id=embed_id("page:start-here", 0),
                title="iframe embed",
                canvas_path=page.canvas_path,
                container_id=page.id,
                tag="iframe",
                src="https://canvas.example.edu/x",
            ),
        ),
        navigation=(NavigationTab(id="home", label="Home", position=1),),
        assignment_groups=(AssignmentGroup(id="4001", name="Group", group_weight=100.0),),
        rubrics=(RubricSummary(id="2001", title="Rubric", criteria_count=3),),
        examinations=(
            Examination(
                collection="pages",
                state=ExaminationState.OK,
                examined=1,
                source=Source.API,
                endpoint="GET /api/v1/courses/4410/pages",
                detail="Read 1 item(s).",
            ),
        ),
        provenance={"acquired_from": "canvas-api"},
    )


# -- deep links -----------------------------------------------------------------------------


def test_every_artifact_carries_a_resolvable_deep_link() -> None:
    document = build_document()
    artifacts = list(document.artifacts())
    assert artifacts, "the document under test holds no artifacts"
    for artifact in artifacts:
        assert artifact.canvas_path.startswith("/courses/4410"), artifact.id
        link = document.deep_link(artifact)
        assert link.startswith(f"{BASE}/courses/4410"), artifact.id


def test_no_deep_link_points_at_an_editor() -> None:
    """SPEC §08: item-level, not the editor. An edit link invites a change before the review is
    agreed, and on a published course it is the wrong place to be reading from."""
    for artifact in build_document().artifacts():
        assert "/edit" not in artifact.canvas_path, artifact.id


def test_a_search_link_is_marked_as_one() -> None:
    """A file known only from an export has no Canvas id, so its link locates it by search. That
    is weaker than an item link and nothing downstream may present it as the same thing."""
    path = file_search_path("4410", "a file with spaces.docx")
    assert path.startswith("/courses/4410/files?search_term=")
    assert " " not in path
    exact = CourseFile(
        id="file:1",
        title="a",
        canvas_path=path,
        filename="a",
        link_exact=False,
    )
    assert exact.link_exact is False


def test_no_artifact_stores_an_absolute_link() -> None:
    """The base URL is institution-identifying (SPEC §11), so it is configuration, held once on
    the document and joined on the way out. Every artifact stores a path.

    Course *content* can of course name the host — the author wrote it that way, and an embed's
    ``src`` is copied verbatim from the body it was found in. What must never happen is Sightline
    interpolating the host into a link of its own, because that is what would bake an institution
    into a cached document and into ``examples/sample-course/``.
    """
    document = build_document()
    for artifact in document.artifacts():
        assert artifact.canvas_path.startswith("/"), artifact.id
        assert "://" not in artifact.canvas_path, artifact.id
    assert document.base_url == BASE


# -- identity -------------------------------------------------------------------------------


def test_ids_are_namespaced_by_kind() -> None:
    assert artifact_id(ArtifactKind.PAGE, "start-here") == "page:start-here"
    assert artifact_id(ArtifactKind.ASSIGNMENT, "9001") == "assignment:9001"


def test_an_embed_id_names_its_container() -> None:
    """An embed has no Canvas identity, so document order within its container is all there is —
    and a finding against one always names the page as well."""
    assert embed_id("page:start-here", 2) == "embed:page:start-here:002"


def test_lookup_by_id_reaches_every_artifact() -> None:
    document = build_document()
    for artifact in document.artifacts():
        assert document.by_id(artifact.id) is artifact


def test_counts_are_per_kind_and_sorted() -> None:
    counts = build_document().counts()
    assert counts["page"] == 1
    assert counts["announcement"] == 1
    assert list(counts) == sorted(counts)


# -- serialization --------------------------------------------------------------------------


def test_serialization_round_trips() -> None:
    document = build_document()
    restored = CourseDocument.from_json(document.to_json())
    assert restored.to_json() == document.to_json()
    assert restored.announcements[0].kind is ArtifactKind.ANNOUNCEMENT
    assert restored.pages[0].front_page is True


def test_serialization_is_byte_identical_across_calls() -> None:
    """The cache and any diff between two runs both depend on this."""
    document = build_document()
    assert document.to_json() == document.to_json()
    assert CourseDocument.from_json(document.to_json()).to_json() == document.to_json()


def test_no_wall_clock_value_reaches_the_document() -> None:
    """A timestamp in the document would make two runs over an unchanged course differ."""
    encoded = build_document().to_json()
    assert "acquired_at" not in encoded
    assert "generated_at" not in encoded


def test_a_document_from_another_schema_version_is_refused_by_name() -> None:
    payload = build_document().to_dict()
    payload["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(SchemaVersionMismatch) as caught:
        CourseDocument.from_dict(payload)
    assert str(SCHEMA_VERSION) in str(caught.value)


@pytest.mark.parametrize(
    "kind",
    [
        ArtifactKind.PAGE,
        ArtifactKind.ASSIGNMENT,
        ArtifactKind.QUIZ,
        ArtifactKind.DISCUSSION,
        ArtifactKind.ANNOUNCEMENT,
        ArtifactKind.FILE,
        ArtifactKind.MODULE,
        ArtifactKind.MODULE_ITEM,
        ArtifactKind.EMBED,
        ArtifactKind.SYLLABUS,
    ],
)
def test_every_kind_rebuilds_from_its_own_serialized_form(kind: ArtifactKind) -> None:
    document = build_document()
    matching = [artifact for artifact in document.artifacts() if artifact.kind is kind]
    assert matching, f"the document under test holds no {kind.value}"
    for artifact in matching:
        rebuilt = artifact_from_dict(artifact.to_dict())
        assert rebuilt.to_dict() == artifact.to_dict()


def test_null_values_are_dropped_but_false_and_zero_survive() -> None:
    """A key absent and a key set to null mean the same thing here, and dropping them keeps a
    serialized sample course readable. ``False`` and ``0`` are facts and must not be dropped."""
    page = Page(id="page:x", title="X", canvas_path="/courses/1/pages/x", slug="x", published=False)
    payload = page.to_dict()
    assert payload["published"] is False
    assert payload["front_page"] is False
    assert "updated_at" not in payload
    assert "body_html" not in payload


# -- examinations ---------------------------------------------------------------------------


def test_an_examination_separates_empty_from_refused() -> None:
    """The distinction is the whole point: a course with no quizzes and a course whose quizzes
    could not be read look identical downstream unless acquisition says which (SPEC §11)."""
    empty = Examination(
        collection="quizzes",
        state=ExaminationState.EMPTY,
        examined=0,
        source=Source.API,
        endpoint="GET /q",
        detail="none",
    )
    refused = Examination(
        collection="quizzes",
        state=ExaminationState.REFUSED,
        examined=0,
        source=Source.API,
        endpoint="GET /q",
        detail="403",
        remedy="grant the permission",
    )
    assert empty.usable
    assert not refused.usable
    assert refused.remedy


def test_the_document_can_be_asked_what_was_examined() -> None:
    document = build_document()
    assert document.examination("pages") is not None
    assert document.examination("quizzes") is None
