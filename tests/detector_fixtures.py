"""Builders for the courses and documents the detector suite runs against.

Every fixture here is **synthetic**. Nothing traces to a course or a person, every hostname is
under ``example.edu``, and the document bytes are assembled byte by byte rather than copied from a
file somebody exported — so there is no path by which a real attachment reaches a public
repository (SPEC §11).

The office files are built with :mod:`zipfile` and the PDFs with :mod:`zlib`, which keeps the
defective case honest: a fixture produced by the same library the detector reads with would prove
the two agree, not that the detector is right.
"""

from __future__ import annotations

import zlib
from collections.abc import Iterable, Mapping, Sequence
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from sightline.acquire.embeds import embeds_for
from sightline.model import (
    Artifact,
    ArtifactKind,
    Assignment,
    AssignmentGroup,
    CourseDocument,
    CourseFile,
    CourseInfo,
    CourseOutcome,
    Discussion,
    Examination,
    ExaminationState,
    Module,
    ModuleItem,
    NavigationTab,
    Page,
    Quiz,
    RubricSummary,
    Source,
    Syllabus,
    announcement_path,
    artifact_id,
    assignment_path,
    discussion_path,
    file_path,
    module_item_path,
    module_path,
    outcome_path,
    page_path,
    quiz_path,
    syllabus_path,
)

COURSE_ID = "4410"
BASE_URL = "https://canvas.example.edu"


def page(slug: str, body: str, *, title: str | None = None) -> Page:
    return Page(
        id=artifact_id_for_page(slug),
        title=title or slug.replace("-", " ").title(),
        canvas_path=page_path(COURSE_ID, slug),
        slug=slug,
        body_html=body,
        published=True,
    )


def artifact_id_for_page(slug: str) -> str:
    from sightline.model import ArtifactKind

    return artifact_id(ArtifactKind.PAGE, slug)


def course_file(
    canvas_id: str,
    filename: str,
    *,
    content_type: str | None = None,
    local_path: str | None = None,
) -> CourseFile:
    from sightline.model import ArtifactKind

    return CourseFile(
        id=artifact_id(ArtifactKind.FILE, canvas_id),
        title=filename,
        canvas_path=file_path(COURSE_ID, canvas_id),
        canvas_id=canvas_id,
        filename=filename,
        content_type=content_type,
        local_path=local_path,
        published=True,
    )


def examined(collection: str, count: int) -> Examination:
    return Examination(
        collection=collection,
        state=ExaminationState.OK if count else ExaminationState.EMPTY,
        examined=count,
        source=Source.BUNDLE,
        endpoint=f"fixture#{collection}",
        detail=f"The fixture declares {count} item(s).",
    )


def refused(collection: str) -> Examination:
    return Examination(
        collection=collection,
        state=ExaminationState.REFUSED,
        examined=0,
        source=Source.API,
        endpoint=f"GET /api/v1/courses/{COURSE_ID}/{collection}",
        detail="Canvas answered 403 for this collection.",
        remedy="Grant the token the scope this collection needs and run the review again.",
    )


#: Every collection any shipped detector declares. A fixture course records all of them so that a
#: zero count in one test is never explained by a record another test forgot to write.
FIXTURE_COLLECTIONS = (
    "announcements",
    "assignment_groups",
    "assignments",
    "discussions",
    "embeds",
    "files",
    "modules",
    "outcomes",
    "pages",
    "quizzes",
    "rubrics",
    "syllabus",
    "tabs",
)

#: The course start date every fixture course carries, so a fixture announcement can be posted
#: before or after it deliberately. D05 turns on exactly that comparison.
COURSE_START = "2026-01-12T00:00:00Z"


def course(
    pages: Sequence[Page] = (),
    *,
    files: Sequence[CourseFile] = (),
    examinations: Iterable[Examination] = (),
    syllabus: Syllabus | None = None,
    modules: Sequence[Module] = (),
    assignments: Sequence[Assignment] = (),
    quizzes: Sequence[Quiz] = (),
    discussions: Sequence[Discussion] = (),
    announcements: Sequence[Discussion] = (),
    navigation: Sequence[NavigationTab] = (),
    assignment_groups: Sequence[AssignmentGroup] = (),
    rubrics: Sequence[RubricSummary] = (),
    outcomes: Sequence[CourseOutcome] = (),
    start_at: str | None = COURSE_START,
) -> CourseDocument:
    """A normalized course carrying whatever the test needs, with embeds derived as acquisition
    derives them — from the bodies, through the same scanner.

    Every collection gets an examination record whether the test filled it or not, so that a
    detector's zero count is always explained by the course rather than by a record the fixture
    forgot. A test wanting the *other* case — a collection acquisition could not read — passes it
    in ``examinations``.
    """
    declared = {entry.collection: entry for entry in examinations}
    with_bodies: list[Artifact] = [
        *([syllabus] if syllabus is not None else []),
        *pages,
        *assignments,
        *quizzes,
        *discussions,
        *announcements,
    ]
    embeds = embeds_for(with_bodies)
    counts = {
        "announcements": len(announcements),
        "assignment_groups": len(assignment_groups),
        "assignments": len(assignments),
        "discussions": len(discussions),
        "embeds": len(embeds),
        "files": len(files),
        "modules": len(modules),
        "outcomes": len(outcomes),
        "pages": len(pages),
        "quizzes": len(quizzes),
        "rubrics": len(rubrics),
        "syllabus": 1 if syllabus is not None else 0,
        "tabs": len(navigation),
    }
    records = [
        declared.get(name, examined(name, counts.get(name, 0))) for name in FIXTURE_COLLECTIONS
    ]
    return CourseDocument(
        base_url=BASE_URL,
        course=CourseInfo(id=COURSE_ID, name="Synthetic Studies 101", start_at=start_at),
        syllabus=syllabus,
        modules=tuple(modules),
        pages=tuple(pages),
        assignments=tuple(assignments),
        quizzes=tuple(quizzes),
        discussions=tuple(discussions),
        announcements=tuple(announcements),
        files=tuple(files),
        embeds=embeds,
        outcomes=tuple(outcomes),
        navigation=tuple(navigation),
        assignment_groups=tuple(assignment_groups),
        rubrics=tuple(rubrics),
        examinations=tuple(records),
    )


# ---------------------------------------------------------------------------------------------
# Structure: the objects the D01-D20 cluster reads
# ---------------------------------------------------------------------------------------------


def syllabus(body: str = "<h2>Syllabus</h2><p>Synthetic.</p>") -> Syllabus:
    return Syllabus(
        id=artifact_id(ArtifactKind.SYLLABUS, COURSE_ID),
        title="Syllabus",
        canvas_path=syllabus_path(COURSE_ID),
        body_html=body,
        published=True,
    )


def module_item(
    canvas_id: str,
    title: str,
    *,
    module_id: str,
    item_type: str = "Page",
    position: int = 1,
    target_id: str | None = None,
    external_url: str | None = None,
) -> ModuleItem:
    return ModuleItem(
        id=artifact_id(ArtifactKind.MODULE_ITEM, canvas_id),
        title=title,
        canvas_path=module_item_path(COURSE_ID, canvas_id),
        canvas_id=canvas_id,
        module_id=module_id,
        position=position,
        item_type=item_type,
        target_id=target_id,
        external_url=external_url,
        published=True,
    )


def module(canvas_id: str, title: str, *items: ModuleItem, position: int = 1) -> Module:
    return Module(
        id=artifact_id(ArtifactKind.MODULE, canvas_id),
        title=title,
        canvas_path=module_path(COURSE_ID, canvas_id),
        canvas_id=canvas_id,
        position=position,
        items=tuple(items),
        published=True,
    )


def assignment(
    canvas_id: str,
    title: str = "Synthetic assignment",
    *,
    body: str | None = "<p>Do the thing.</p>",
    due_at: str | None = "2026-02-01T23:59:00Z",
    points: float | None = 10.0,
    grading_type: str = "points",
    submission_types: Sequence[str] = ("online_text_entry",),
    group_id: str | None = "4001",
    has_rubric: bool = False,
    rubric_title: str | None = None,
    omit_from_final_grade: bool = False,
) -> Assignment:
    return Assignment(
        id=artifact_id(ArtifactKind.ASSIGNMENT, canvas_id),
        title=title,
        canvas_path=assignment_path(COURSE_ID, canvas_id),
        canvas_id=canvas_id,
        body_html=body,
        due_at=due_at,
        points_possible=points,
        grading_type=grading_type,
        submission_types=tuple(submission_types),
        assignment_group_id=group_id,
        has_rubric=has_rubric,
        rubric_title=rubric_title,
        omit_from_final_grade=omit_from_final_grade,
        published=True,
    )


def quiz(
    canvas_id: str,
    title: str = "Synthetic quiz",
    *,
    body: str | None = "<p>Ten questions.</p>",
    quiz_type: str | None = "assignment",
    time_limit: int | None = None,
    due_at: str | None = "2026-02-01T23:59:00Z",
    points: float | None = 20.0,
) -> Quiz:
    return Quiz(
        id=artifact_id(ArtifactKind.QUIZ, canvas_id),
        title=title,
        canvas_path=quiz_path(COURSE_ID, canvas_id),
        canvas_id=canvas_id,
        body_html=body,
        quiz_type=quiz_type,
        time_limit=time_limit,
        due_at=due_at,
        points_possible=points,
        published=True,
    )


def discussion(
    canvas_id: str,
    title: str = "Synthetic discussion",
    *,
    body: str | None = "<p>Post a reply.</p>",
    graded: bool = False,
    due_at: str | None = None,
) -> Discussion:
    return Discussion(
        id=artifact_id(ArtifactKind.DISCUSSION, canvas_id),
        title=title,
        canvas_path=discussion_path(COURSE_ID, canvas_id),
        canvas_id=canvas_id,
        body_html=body,
        graded=graded,
        due_at=due_at,
        published=True,
    )


def announcement(
    canvas_id: str,
    title: str = "Synthetic announcement",
    *,
    body: str | None = "<p>Something happened.</p>",
    posted_at: str = "2026-01-13T08:00:00Z",
) -> Discussion:
    return Discussion(
        kind=ArtifactKind.ANNOUNCEMENT,
        id=artifact_id(ArtifactKind.ANNOUNCEMENT, canvas_id),
        title=title,
        canvas_path=announcement_path(COURSE_ID, canvas_id),
        canvas_id=canvas_id,
        body_html=body,
        posted_at=posted_at,
        published=True,
    )


def tab(
    identifier: str,
    label: str | None = None,
    *,
    position: int = 1,
    hidden: bool = False,
    type: str | None = None,
) -> NavigationTab:
    return NavigationTab(
        id=identifier,
        label=label or identifier.title(),
        position=position,
        hidden=hidden,
        type=type,
    )


def group(identifier: str, name: str, *, weight: float | None = None, position: int = 1):
    return AssignmentGroup(id=identifier, name=name, position=position, group_weight=weight)


def rubric(identifier: str, title: str, *, criteria: int = 3) -> RubricSummary:
    return RubricSummary(id=identifier, title=title, criteria_count=criteria)


def outcome(
    canvas_id: str,
    title: str,
    *,
    description: str = "Measured this way.",
    group: str = "Course Outcomes",
) -> CourseOutcome:
    return CourseOutcome(
        id=artifact_id(ArtifactKind.OUTCOME, canvas_id),
        title=title,
        canvas_path=outcome_path(COURSE_ID, canvas_id),
        canvas_id=canvas_id,
        group=group,
        description=description,
    )


def one_page(body: str, *, slug: str = "sample") -> CourseDocument:
    return course([page(slug, body)])


def empty_course() -> CourseDocument:
    """A course with nothing in it and no record of why. What every detector must refuse."""
    return CourseDocument(base_url=BASE_URL, course=CourseInfo(id=COURSE_ID, name="Empty"))


def codes(result: object) -> list[str]:
    return sorted(finding.code for finding in getattr(result, "findings", ()))


def located(result: object, artifact: Artifact | str) -> list[str]:
    wanted = artifact if isinstance(artifact, str) else artifact.id
    return sorted(
        finding.code
        for finding in getattr(result, "findings", ())
        if finding.location.artifact_id == wanted
    )


# ---------------------------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------------------------


def pdf(*, tagged: bool, text_layer: bool, language: bool = True, pages: int = 1) -> bytes:
    """A PDF assembled by hand, carrying exactly the structure asked for.

    Real enough for the probe to read: numbered objects, a Flate-compressed content stream, a
    catalog. Small enough that what it does and does not contain is visible in this function
    rather than inside a library — which is the point of not generating it with one.
    """
    catalog = ["/Type /Catalog", "/Pages 2 0 R"]
    if tagged:
        catalog += ["/StructTreeRoot 6 0 R", "/MarkInfo << /Marked true >>"]
    if language:
        catalog.append("/Lang (en-US)")

    if text_layer:
        content = b"BT /F1 12 Tf 72 720 Td (Synthetic Studies 101) Tj ET"
        resources = "<< /Font << /F1 5 0 R >> >>"
        extra = "5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    else:
        # A page of pixels: an image drawn to fill the page, no font resource and no text operator.
        content = b"q 612 0 0 792 0 0 cm /Im1 Do Q"
        resources = "<< /XObject << /Im1 5 0 R >> >>"
        extra = (
            "5 0 obj << /Type /XObject /Subtype /Image /Width 8 /Height 8 "
            "/ColorSpace /DeviceGray /BitsPerComponent 8 /Length 64 >> stream\n"
            + "\x00" * 64
            + "\nendstream endobj\n"
        )

    stream = zlib.compress(content)
    kids = " ".join(f"{3 + index * 10} 0 R" for index in range(pages))
    head = [
        "%PDF-1.7\n",
        f"1 0 obj << {' '.join(catalog)} >> endobj\n",
        f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {pages} >> endobj\n",
    ]
    for index in range(pages):
        head.append(
            f"{3 + index * 10} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources {resources} /Contents 4 0 R >> endobj\n"
        )
    head.append(f"4 0 obj << /Length {len(stream)} /Filter /FlateDecode >> stream\n")

    tail = ["\nendstream endobj\n", extra]
    if tagged:
        tail.append("6 0 obj << /Type /StructTreeRoot /K [] >> endobj\n")
    tail.append("trailer << /Size 7 /Root 1 0 R >>\n%%EOF\n")

    return "".join(head).encode("latin-1") + stream + "".join(tail).encode("latin-1")


def encrypted_pdf() -> bytes:
    return (
        b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n"
        b"trailer << /Root 1 0 R /Encrypt 9 0 R >>\n%%EOF\n"
    )


# ---------------------------------------------------------------------------------------------
# Office
# ---------------------------------------------------------------------------------------------

_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="xml" ContentType="application/xml"/></Types>'
)

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _zip(entries: Mapping[str, str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        for name, text in entries.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def docx(
    *,
    heading_styles: bool = True,
    table_header: bool = True,
    image_alt: bool = True,
    face: str = "Calibri",
    half_points: int = 24,
) -> bytes:
    """A Word document carrying exactly the structure asked for."""
    heading = (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        "<w:r><w:t>Module overview</w:t></w:r></w:p>"
        if heading_styles
        else '<w:p><w:r><w:rPr><w:b/><w:sz w:val="32"/></w:rPr>'
        "<w:t>Module overview</w:t></w:r></w:p>"
    )
    header_mark = "<w:trPr><w:tblHeader/></w:trPr>" if table_header else ""
    alt = ' descr="A chart of the analysis sequence"' if image_alt else ""
    document = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}" xmlns:wp="{_WP}"><w:body>'
        f"{heading}"
        f"<w:tbl><w:tr>{header_mark}<w:tc><w:p><w:r><w:t>Week</w:t></w:r></w:p></w:tc></w:tr>"
        f"<w:tr><w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        f'<w:p><w:r><w:drawing><wp:inline><wp:docPr id="1" name="Picture 1"{alt}/>'
        f"</wp:inline></w:drawing></w:r></w:p>"
        f"</w:body></w:document>"
    )
    styles = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:styles xmlns:w="{_W}"><w:docDefaults><w:rPrDefault><w:rPr>'
        f'<w:rFonts w:ascii="{face}"/><w:sz w:val="{half_points}"/>'
        f"</w:rPr></w:rPrDefault></w:docDefaults></w:styles>"
    )
    return _zip({"word/document.xml": document, "word/styles.xml": styles})


def pptx(slides: Sequence[Mapping[str, object]]) -> bytes:
    """A deck of slides, each described by ``title``, ``auto_advance`` and ``placeholders``."""
    entries: dict[str, str] = {
        "ppt/presentation.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:presentation xmlns:p="{_P}"/>'
        )
    }
    for position, slide in enumerate(slides, start=1):
        title = slide.get("title")
        placeholders = bool(slide.get("placeholders", True))
        shapes = ""
        if placeholders:
            text = (
                f"<a:p><a:r><a:t>{title}</a:t></a:r></a:p>"
                if title
                else "<a:p><a:r><a:t></a:t></a:r></a:p>"
            )
            shapes += (
                f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:nvPr>'
                f'<p:ph type="title"/></p:nvPr></p:nvSpPr>'
                f"<p:txBody>{text}</p:txBody></p:sp>"
            )
        else:
            shapes += (
                f'<p:sp><p:nvSpPr><p:cNvPr id="2" name="TextBox 1"/><p:nvPr/></p:nvSpPr>'
                f"<p:txBody><a:p><a:r><a:t>{title or 'Free text'}</a:t></a:r></a:p>"
                f"</p:txBody></p:sp>"
            )
        transition = '<p:transition advTm="5000"/>' if slide.get("auto_advance") else ""
        entries[f"ppt/slides/slide{position}.xml"] = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:sld xmlns:p="{_P}" xmlns:a="{_A}"><p:cSld><p:spTree>{shapes}</p:spTree>'
            f"</p:cSld>{transition}</p:sld>"
        )
    return _zip(entries)


__all__ = [
    "BASE_URL",
    "COURSE_ID",
    "COURSE_START",
    "FIXTURE_COLLECTIONS",
    "announcement",
    "assignment",
    "codes",
    "course",
    "course_file",
    "discussion",
    "docx",
    "empty_course",
    "encrypted_pdf",
    "examined",
    "group",
    "located",
    "module",
    "module_item",
    "one_page",
    "outcome",
    "page",
    "pdf",
    "pptx",
    "quiz",
    "refused",
    "rubric",
    "syllabus",
    "tab",
]
