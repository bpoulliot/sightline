"""Turning Canvas payloads and export bytes into one document model.

The API and a content export disagree about almost everything except the facts, so this module
picks a winner per field rather than per source: **structure from the API, document bytes from the
export**, and a page body from whichever answered. Every artifact records which source gave it a
body, so a later surprise is traceable instead of mysterious.

Ordering is chosen for determinism first and for a reader second. Collections that carry a
meaningful order in Canvas — modules, module items, navigation tabs — keep it, with the Canvas id
as a tiebreak so two items at the same position cannot swap between runs. Everything else is
sorted by its own identity. Running twice over an unchanged course produces identical bytes, and
``tests/test_acquire_normalize.py`` is what holds that true.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence
from html import unescape
from typing import Any

from ..model import (
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
    file_search_path,
    module_item_path,
    module_path,
    outcome_path,
    page_path,
    quiz_path,
    syllabus_path,
)
from .archive import UnpackedExport
from .canvas_api import CourseBodies, CourseIndex
from .embeds import embeds_for

#: Canvas module-item types that point at something this document also holds.
_ITEM_TARGETS: Mapping[str, ArtifactKind] = {
    "Page": ArtifactKind.PAGE,
    "Assignment": ArtifactKind.ASSIGNMENT,
    "Quiz": ArtifactKind.QUIZ,
    "Discussion": ArtifactKind.DISCUSSION,
    "File": ArtifactKind.FILE,
}


def normalize(
    index: CourseIndex,
    *,
    base_url: str,
    bodies: CourseBodies | None = None,
    export: UnpackedExport | None = None,
    provenance: Mapping[str, Any] | None = None,
) -> CourseDocument:
    """Build the normalized document for one course."""
    course = _course_info(index.course)
    course_id = course.id
    page_bodies = dict(bodies.page_bodies) if bodies else {}

    modules, module_paths = _modules(index.items("modules"), course_id)
    pages = _pages(index.items("pages"), course_id, page_bodies, export, module_paths)
    assignments = _assignments(index.items("assignments"), course_id, module_paths)
    quizzes = _quizzes(index.items("quizzes"), course_id, module_paths)
    discussions = _discussions(
        index.items("discussions"), course_id, module_paths, announcement=False
    )
    announcements = _discussions(
        index.items("announcements"), course_id, module_paths, announcement=True
    )
    files = _files(index.items("files"), course_id, export, module_paths)
    syllabus = _syllabus(index.course, course_id)
    outcomes = _outcomes(index.items("outcomes"), course_id)

    with_bodies: list[Artifact] = [
        *([syllabus] if syllabus else []),
        *pages,
        *assignments,
        *quizzes,
        *discussions,
        *announcements,
    ]
    embeds = tuple(sorted(embeds_for(with_bodies), key=lambda item: item.id))

    examinations = list(index.examinations())
    examinations.append(_embed_examination(with_bodies, embeds))
    examinations.append(_syllabus_examination(index.course, syllabus))
    if bodies is not None and bodies.examination is not None:
        examinations.append(bodies.examination)
    if export is not None:
        examinations.append(_export_examination(export))

    return CourseDocument(
        base_url=base_url.rstrip("/"),
        course=course,
        syllabus=syllabus,
        modules=modules,
        pages=pages,
        assignments=assignments,
        quizzes=quizzes,
        discussions=discussions,
        announcements=announcements,
        files=files,
        embeds=embeds,
        outcomes=outcomes,
        navigation=_navigation(index.items("tabs")),
        assignment_groups=_assignment_groups(index.items("assignment_groups")),
        rubrics=_rubrics(index.items("rubrics")),
        examinations=tuple(sorted(examinations, key=lambda entry: entry.collection)),
        provenance=dict(provenance or {}),
    )


# ---------------------------------------------------------------------------------------------
# Course level
# ---------------------------------------------------------------------------------------------


def _course_info(payload: Mapping[str, Any]) -> CourseInfo:
    return CourseInfo(
        id=str(payload.get("id", "")),
        name=str(payload.get("name") or f"course {payload.get('id', '')}"),
        course_code=_text(payload.get("course_code")),
        workflow_state=_text(payload.get("workflow_state")),
        start_at=_text(payload.get("start_at")),
        end_at=_text(payload.get("end_at")),
        default_view=_text(payload.get("default_view")),
        is_public=_flag(payload.get("is_public")),
        time_zone=_text(payload.get("time_zone")),
    )


def _syllabus(payload: Mapping[str, Any], course_id: str) -> Syllabus | None:
    """The syllabus, or ``None`` when Canvas did not return the field at all.

    An *empty* syllabus and an *unrequested* one are different facts and D01 turns on the
    difference, so an empty string still produces an artifact with an empty body.
    """
    if "syllabus_body" not in payload:
        return None
    body = payload.get("syllabus_body")
    return Syllabus(
        id=artifact_id(ArtifactKind.SYLLABUS, course_id),
        title="Syllabus",
        canvas_path=syllabus_path(course_id),
        body_html=body if isinstance(body, str) else "",
        body_source=Source.API,
    )


def _syllabus_examination(payload: Mapping[str, Any], syllabus: Syllabus | None) -> Examination:
    """Acquisition's own record of the syllabus, which is not a collection endpoint.

    The syllabus arrives on the course payload under ``include[]=syllabus_body`` rather than from
    a listing of its own, so nothing else would record it — and a detector reading the syllabus
    would then have a zero count that no examination explains, which the detector framework is
    required to treat as a broken detector rather than as a course without a syllabus. Recording
    it here is what keeps those two apart.
    """
    if syllabus is None:
        return Examination(
            collection="syllabus",
            state=ExaminationState.SKIPPED,
            examined=0,
            source=Source.API,
            endpoint="GET /api/v1/courses/{id}?include[]=syllabus_body",
            detail="Canvas did not return the syllabus_body field for this course.",
            remedy=(
                "Grant the token permission to read course content and run the review again; "
                "until then nothing about the syllabus has been examined."
            ),
        )
    present = bool((syllabus.body_html or "").strip())
    return Examination(
        collection="syllabus",
        state=ExaminationState.OK if present else ExaminationState.EMPTY,
        examined=1 if present else 0,
        source=Source.API,
        endpoint="GET /api/v1/courses/{id}?include[]=syllabus_body",
        detail=(
            "The course carries a syllabus body."
            if present
            else "Canvas returned the syllabus field and it is empty."
        ),
    )


def _outcomes(items: Sequence[Any], course_id: str) -> tuple[CourseOutcome, ...]:
    """Outcomes, from the course's outcome *group links*.

    One endpoint answers both halves of the question: each link carries the outcome and the group
    it is filed under, so a second call per group is not needed. Canvas gives the outcome under
    ``outcome`` and the group under ``outcome_group``; a link missing either is skipped rather
    than guessed at.
    """
    built: list[CourseOutcome] = []
    seen: set[str] = set()
    for payload in items:
        if not isinstance(payload, Mapping):
            continue
        outcome = payload.get("outcome")
        if not isinstance(outcome, Mapping):
            continue
        canvas_id = str(outcome.get("id", ""))
        if not canvas_id or canvas_id in seen:
            continue
        seen.add(canvas_id)
        group = payload.get("outcome_group")
        group_title = str(group.get("title") or "") if isinstance(group, Mapping) else ""
        built.append(
            CourseOutcome(
                id=artifact_id(ArtifactKind.OUTCOME, canvas_id),
                title=str(outcome.get("title") or f"outcome {canvas_id}"),
                canvas_path=outcome_path(course_id, canvas_id),
                canvas_id=canvas_id,
                group=group_title,
                description=_plain(outcome.get("description")),
                display_name=_plain(outcome.get("display_name")),
            )
        )
    return tuple(sorted(built, key=lambda item: _natural(item.canvas_id)))


#: Markup in a Canvas outcome description. Stripped here rather than in a detector so that one
#: field of the document is not HTML while every other plain-text field is text.
_TAG = re.compile(r"<[^>]*>")


def _plain(value: Any) -> str:
    """An outcome description as text. Canvas returns it as HTML even when it is one sentence.

    A local strip rather than the HTML scanner in :mod:`sightline.detect.html_scan`: acquisition
    must not import the detector package, because the dependency runs the other way and a cycle
    here would be paid for by every later import.
    """
    text = _text(value) or ""
    if not text:
        return ""
    return " ".join(unescape(_TAG.sub(" ", text)).split())


def _navigation(items: Sequence[Any]) -> tuple[NavigationTab, ...]:
    tabs = [
        NavigationTab(
            id=str(item.get("id", "")),
            label=str(item.get("label") or item.get("id") or ""),
            position=int(item.get("position") or 0),
            hidden=bool(item.get("hidden", False)),
            visibility=_text(item.get("visibility")),
            type=_text(item.get("type")),
        )
        for item in items
        if isinstance(item, Mapping)
    ]
    return tuple(sorted(tabs, key=lambda tab: (tab.position, tab.id)))


def _assignment_groups(items: Sequence[Any]) -> tuple[AssignmentGroup, ...]:
    groups = [
        AssignmentGroup(
            id=str(item.get("id", "")),
            name=str(item.get("name") or f"group {item.get('id', '')}"),
            position=int(item.get("position") or 0),
            group_weight=_number(item.get("group_weight")),
        )
        for item in items
        if isinstance(item, Mapping)
    ]
    return tuple(sorted(groups, key=lambda group: (group.position, _natural(group.id))))


def _rubrics(items: Sequence[Any]) -> tuple[RubricSummary, ...]:
    rubrics = [
        RubricSummary(
            id=str(item.get("id", "")),
            title=str(item.get("title") or f"rubric {item.get('id', '')}"),
            criteria_count=len(item.get("data") or ()),
            points_possible=_number(item.get("points_possible")),
            free_form_comments=bool(item.get("free_form_criterion_comments", False)),
        )
        for item in items
        if isinstance(item, Mapping)
    ]
    return tuple(sorted(rubrics, key=lambda rubric: _natural(rubric.id)))


# ---------------------------------------------------------------------------------------------
# Modules, and the module path every other artifact inherits
# ---------------------------------------------------------------------------------------------


def _modules(
    items: Sequence[Any], course_id: str
) -> tuple[tuple[Module, ...], dict[str, tuple[str, ...]]]:
    """Modules with their items, and a map from artifact id to its place in the course.

    A sub-header inside a module is treated as a second level of the path. Canvas gives it no
    identity beyond its title and position, but it is how designers actually group a long module,
    and D47 reads structural consistency off exactly that.
    """
    built: list[Module] = []
    paths: dict[str, tuple[str, ...]] = {}

    for payload in items:
        if not isinstance(payload, Mapping):
            continue
        module_canvas_id = str(payload.get("id", ""))
        module_title = str(payload.get("name") or f"module {module_canvas_id}")
        module_artifact_id = artifact_id(ArtifactKind.MODULE, module_canvas_id)
        paths[module_artifact_id] = (module_title,)

        raw_items = [item for item in payload.get("items") or () if isinstance(item, Mapping)]
        raw_items.sort(key=lambda item: (int(item.get("position") or 0), _natural(item.get("id"))))

        module_items: list[ModuleItem] = []
        subheading: str | None = None
        for raw in raw_items:
            item_type = _text(raw.get("type"))
            if item_type == "SubHeader":
                subheading = _text(raw.get("title"))
            here = (module_title, subheading) if subheading else (module_title,)
            target = _target_id(raw, item_type)
            item_canvas_id = str(raw.get("id", ""))
            built_item = ModuleItem(
                id=artifact_id(ArtifactKind.MODULE_ITEM, item_canvas_id),
                title=str(raw.get("title") or f"item {item_canvas_id}"),
                canvas_path=module_item_path(course_id, item_canvas_id),
                published=_flag(raw.get("published")),
                module_path=here,
                canvas_id=item_canvas_id,
                module_id=module_artifact_id,
                position=int(raw.get("position") or 0),
                indent=int(raw.get("indent") or 0),
                item_type=item_type,
                target_id=target,
                external_url=_text(raw.get("external_url")),
                completion_requirement=_completion(raw.get("completion_requirement")),
            )
            module_items.append(built_item)
            paths[built_item.id] = here
            if target:
                paths.setdefault(target, here)

        built.append(
            Module(
                id=module_artifact_id,
                title=module_title,
                canvas_path=module_path(course_id, module_canvas_id),
                published=_flag(payload.get("published")),
                module_path=(module_title,),
                canvas_id=module_canvas_id,
                position=int(payload.get("position") or 0),
                unlock_at=_text(payload.get("unlock_at")),
                prerequisite_module_ids=tuple(
                    str(value) for value in payload.get("prerequisite_module_ids") or ()
                ),
                items=tuple(module_items),
            )
        )

    built.sort(key=lambda module: (module.position, _natural(module.canvas_id)))
    return tuple(built), paths


def _target_id(raw: Mapping[str, Any], item_type: str | None) -> str | None:
    kind = _ITEM_TARGETS.get(item_type or "")
    if kind is None:
        return None
    if kind is ArtifactKind.PAGE:
        slug = _text(raw.get("page_url"))
        return artifact_id(kind, slug) if slug else None
    content_id = raw.get("content_id")
    return artifact_id(kind, str(content_id)) if content_id is not None else None


def _completion(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return _text(value.get("type"))


# ---------------------------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------------------------


def _pages(
    items: Sequence[Any],
    course_id: str,
    page_bodies: Mapping[str, str],
    export: UnpackedExport | None,
    paths: Mapping[str, tuple[str, ...]],
) -> tuple[Page, ...]:
    built: list[Page] = []
    for payload in items:
        if not isinstance(payload, Mapping):
            continue
        slug = _text(payload.get("url")) or _text(payload.get("page_id")) or ""
        if not slug:
            continue
        identifier = artifact_id(ArtifactKind.PAGE, slug)
        body, source = _body_for(slug, page_bodies, export)
        built.append(
            Page(
                id=identifier,
                title=str(payload.get("title") or slug),
                canvas_path=page_path(course_id, slug),
                published=_flag(payload.get("published")),
                body_html=body,
                body_source=source,
                module_path=paths.get(identifier, ()),
                updated_at=_text(payload.get("updated_at")),
                slug=slug,
                front_page=bool(payload.get("front_page", False)),
            )
        )
    return tuple(sorted(built, key=lambda page: page.slug))


def _body_for(
    slug: str, page_bodies: Mapping[str, str], export: UnpackedExport | None
) -> tuple[str | None, Source | None]:
    """The API's body wins; the export fills in for a page the API would not hand over."""
    if slug in page_bodies:
        return page_bodies[slug], Source.API
    if export is not None:
        body = export.page_body(slug)
        if body is not None:
            return body, Source.EXPORT
    return None, None


def _assignments(
    items: Sequence[Any], course_id: str, paths: Mapping[str, tuple[str, ...]]
) -> tuple[Assignment, ...]:
    built: list[Assignment] = []
    for payload in items:
        if not isinstance(payload, Mapping):
            continue
        canvas_id = str(payload.get("id", ""))
        identifier = artifact_id(ArtifactKind.ASSIGNMENT, canvas_id)
        rubric = payload.get("rubric")
        settings = payload.get("rubric_settings")
        built.append(
            Assignment(
                id=identifier,
                title=str(payload.get("name") or f"assignment {canvas_id}"),
                canvas_path=assignment_path(course_id, canvas_id),
                published=_flag(payload.get("published")),
                body_html=_text(payload.get("description")),
                body_source=Source.API if payload.get("description") is not None else None,
                module_path=paths.get(identifier, ()),
                updated_at=_text(payload.get("updated_at")),
                canvas_id=canvas_id,
                due_at=_text(payload.get("due_at")),
                unlock_at=_text(payload.get("unlock_at")),
                lock_at=_text(payload.get("lock_at")),
                points_possible=_number(payload.get("points_possible")),
                grading_type=_text(payload.get("grading_type")),
                submission_types=tuple(
                    str(value) for value in payload.get("submission_types") or ()
                ),
                assignment_group_id=_text(payload.get("assignment_group_id")),
                has_rubric=bool(rubric),
                rubric_title=(
                    _text(settings.get("title")) if isinstance(settings, Mapping) else None
                ),
                omit_from_final_grade=bool(payload.get("omit_from_final_grade", False)),
            )
        )
    return tuple(sorted(built, key=lambda item: _natural(item.canvas_id)))


def _quizzes(
    items: Sequence[Any], course_id: str, paths: Mapping[str, tuple[str, ...]]
) -> tuple[Quiz, ...]:
    built: list[Quiz] = []
    for payload in items:
        if not isinstance(payload, Mapping):
            continue
        canvas_id = str(payload.get("id", ""))
        identifier = artifact_id(ArtifactKind.QUIZ, canvas_id)
        built.append(
            Quiz(
                id=identifier,
                title=str(payload.get("title") or f"quiz {canvas_id}"),
                canvas_path=quiz_path(course_id, canvas_id),
                published=_flag(payload.get("published")),
                body_html=_text(payload.get("description")),
                body_source=Source.API if payload.get("description") is not None else None,
                module_path=paths.get(identifier, ()),
                updated_at=_text(payload.get("updated_at")),
                canvas_id=canvas_id,
                quiz_type=_text(payload.get("quiz_type")),
                time_limit=_integer(payload.get("time_limit")),
                due_at=_text(payload.get("due_at")),
                points_possible=_number(payload.get("points_possible")),
                allowed_attempts=_integer(payload.get("allowed_attempts")),
                question_count=_integer(payload.get("question_count")),
            )
        )
    return tuple(sorted(built, key=lambda item: _natural(item.canvas_id)))


def _discussions(
    items: Sequence[Any],
    course_id: str,
    paths: Mapping[str, tuple[str, ...]],
    *,
    announcement: bool,
) -> tuple[Discussion, ...]:
    kind = ArtifactKind.ANNOUNCEMENT if announcement else ArtifactKind.DISCUSSION
    built: list[Discussion] = []
    for payload in items:
        if not isinstance(payload, Mapping):
            continue
        # Canvas answers ``only_announcements=false`` with announcements included on some
        # versions, so the flag on the topic decides rather than the query that fetched it.
        is_announcement = bool(payload.get("is_announcement", False))
        if is_announcement != announcement:
            continue
        canvas_id = str(payload.get("id", ""))
        identifier = artifact_id(kind, canvas_id)
        path = (
            announcement_path(course_id, canvas_id)
            if announcement
            else discussion_path(course_id, canvas_id)
        )
        built.append(
            Discussion(
                kind=kind,
                id=identifier,
                title=str(payload.get("title") or f"topic {canvas_id}"),
                canvas_path=path,
                published=_flag(payload.get("published")),
                body_html=_text(payload.get("message")),
                body_source=Source.API if payload.get("message") is not None else None,
                module_path=paths.get(identifier, ()),
                updated_at=_text(payload.get("updated_at")),
                canvas_id=canvas_id,
                graded=payload.get("assignment_id") is not None,
                due_at=_topic_due_at(payload),
                posted_at=_text(payload.get("posted_at")),
                delayed_post_at=_text(payload.get("delayed_post_at")),
                require_initial_post=bool(payload.get("require_initial_post", False)),
                discussion_type=_text(payload.get("discussion_type")),
                reply_count=_integer(payload.get("discussion_subentry_count")),
            )
        )
    return tuple(sorted(built, key=lambda item: _natural(item.canvas_id)))


def _topic_due_at(payload: Mapping[str, Any]) -> str | None:
    """A graded topic's due date, which Canvas keeps on the attached assignment.

    Reading ``due_at`` off the topic itself returns ``None`` for every graded discussion there
    has ever been, and a due-date detector fed that would report each one as undated.
    """
    assignment = payload.get("assignment")
    if isinstance(assignment, Mapping):
        return _text(assignment.get("due_at"))
    return _text(payload.get("due_at"))


def _files(
    items: Sequence[Any],
    course_id: str,
    export: UnpackedExport | None,
    paths: Mapping[str, tuple[str, ...]],
) -> tuple[CourseFile, ...]:
    """Files the API listed, with export bytes attached, plus files only the export knows about.

    Both halves matter. A course with the Files tab hidden lists nothing over the API and every
    document still exists; a course whose export skipped a media type lists files with no bytes.
    Neither is an error and the document says which is which through ``local_path``.
    """
    documents = dict(export.documents()) if export is not None else {}
    by_name: dict[str, str] = {}
    for href, path in documents.items():
        by_name.setdefault(path.name, href)

    built: list[CourseFile] = []
    claimed: set[str] = set()
    for payload in items:
        if not isinstance(payload, Mapping):
            continue
        canvas_id = str(payload.get("id", ""))
        identifier = artifact_id(ArtifactKind.FILE, canvas_id)
        filename = str(
            payload.get("display_name") or payload.get("filename") or f"file {canvas_id}"
        )
        href = by_name.get(filename) or by_name.get(str(payload.get("filename") or ""))
        if href:
            claimed.add(href)
        built.append(
            CourseFile(
                id=identifier,
                title=filename,
                canvas_path=file_path(course_id, canvas_id),
                published=_flag(payload.get("published")),
                module_path=paths.get(identifier, ()),
                updated_at=_text(payload.get("updated_at")),
                canvas_id=canvas_id,
                filename=filename,
                content_type=_text(payload.get("content-type") or payload.get("content_type")),
                size=_integer(payload.get("size")),
                folder_path=_text(payload.get("folder_path")),
                local_path=href,
                sha256=None,
            )
        )

    for href in sorted(documents):
        if href in claimed:
            continue
        filename = posixpath.basename(href)
        built.append(
            CourseFile(
                id=artifact_id(ArtifactKind.FILE, f"export:{href}"),
                title=filename,
                # No Canvas file id exists for a file the API never listed, so the link locates it
                # by search and says so rather than pretending to be an item link.
                canvas_path=file_search_path(course_id, filename),
                link_exact=False,
                module_path=export.module_path_for(href) if export else (),
                filename=filename,
                content_type=None,
                size=None,
                folder_path=posixpath.dirname(href) or None,
                local_path=href,
            )
        )

    return tuple(sorted(built, key=lambda item: (item.filename.lower(), item.id)))


def _embed_examination(with_bodies: Sequence[Artifact], embeds: Sequence[Any]) -> Examination:
    """What the embed scan looked at, alongside what it found.

    Embeds are derived, not fetched, so there is no endpoint that could refuse them — which makes
    it easy to forget that "no embeds" and "no bodies to scan" are different answers. A detector
    reading this can tell them apart.
    """
    scanned = sum(1 for artifact in with_bodies if artifact.body_html)
    return Examination(
        collection="embeds",
        state=ExaminationState.OK if embeds else ExaminationState.EMPTY,
        examined=scanned,
        source=Source.API,
        endpoint="(derived from artifact bodies)",
        detail=(
            f"Scanned {scanned} of {len(with_bodies)} artifact(s) that can carry a body and found "
            f"{len(embeds)} embed(s)."
        ),
    )


def _export_examination(export: UnpackedExport) -> Examination:
    documents = list(export.documents())
    slugs = export.page_slugs()
    return Examination(
        collection="content_export",
        state=ExaminationState.OK if documents or slugs else ExaminationState.EMPTY,
        examined=len(documents) + len(slugs),
        source=Source.EXPORT,
        endpoint=str(export.root.name),
        detail=(
            f"Unpacked export holds {len(slugs)} page bod(ies) and {len(documents)} document(s). "
            "Media and other types a detector cannot read were left inside the archive."
        ),
    )


# ---------------------------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------------------------


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text


def _flag(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _natural(value: Any) -> tuple[int, int, str]:
    """Sort key that puts numeric Canvas ids in numeric order without breaking on SIS ids."""
    text = "" if value is None else str(value)
    if text.isdigit():
        return (0, int(text), "")
    return (1, 0, text)


__all__ = ["normalize"]
