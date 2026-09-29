"""The normalized document model — the one shape every detector reads.

Acquisition has two sources with different strengths (SPEC §04): the Canvas API knows the
*structure* of a course, and a content export knows the *bytes* of its documents. Detectors must
not care which answered. So both are normalized into the types here, and a finding cites an
:class:`Artifact` rather than a Canvas payload.

Three properties of this model are load-bearing.

**Every artifact carries a deep link into the Canvas UI.** Every later finding cites one, so the
link is not decoration (SPEC §08). It is stored as a *path* — ``/courses/4410/pages/start-here`` —
and joined to the configured base URL on the way out. That is what lets a normalized document,
including the shipped ``examples/sample-course/``, contain no institution hostname at all while
still producing a resolvable link. Where only a search link is possible, ``link_exact`` says so
rather than the link quietly meaning something weaker than it looks.

**Serialization is deterministic.** ``to_dict`` emits sorted collections and no wall-clock value,
so normalizing the same course twice produces byte-identical output. The cache depends on that,
and so does anyone diffing two runs.

**Nothing here is a judgement.** These are counts, ids, titles and bytes. The determinacy tiers,
the findings and the ratings all live above this layer.
"""

from __future__ import annotations

import enum
import json
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

#: Bumped when the shape below changes incompatibly. It is part of the cache key, so a bump
#: invalidates every cached document rather than deserializing an old one into a new class.
SCHEMA_VERSION = 1

#: The provenance key naming where a course's document bytes live: the unpacked content export's
#: ``files/`` directory, as a POSIX path relative to the data root. Every
#: :attr:`CourseFile.local_path` in the same document is relative to that directory.
EXPORT_FILES_KEY = "content_export_files"


class ArtifactKind(enum.StrEnum):
    """Every kind of thing a detector can be handed."""

    COURSE = "course"
    MODULE = "module"
    MODULE_ITEM = "module_item"
    PAGE = "page"
    ASSIGNMENT = "assignment"
    QUIZ = "quiz"
    DISCUSSION = "discussion"
    ANNOUNCEMENT = "announcement"
    FILE = "file"
    EMBED = "embed"
    SYLLABUS = "syllabus"
    OUTCOME = "outcome"


class Source(enum.StrEnum):
    """Where a value came from. Recorded because the two sources disagree in known ways."""

    API = "api"
    EXPORT = "export"
    BUNDLE = "bundle"


class ExaminationState(enum.StrEnum):
    """What happened when a collection was asked for.

    ``EMPTY`` and ``REFUSED`` are different facts and the distinction is the whole point: a course
    with no quizzes and a course whose quizzes could not be read look identical downstream unless
    acquisition says which it was. Silent under-detection is the failure this project is most
    afraid of (SPEC §11).
    """

    OK = "ok"
    EMPTY = "empty"
    REFUSED = "refused"
    ABSENT = "absent"
    SKIPPED = "skipped"


@dataclass(frozen=True, kw_only=True, slots=True)
class Examination:
    """One collection that acquisition tried to read, and what came back.

    Carried into the document so a detector can refuse to report "nothing found" over a collection
    that was never readable.
    """

    collection: str
    state: ExaminationState
    examined: int
    source: Source
    endpoint: str
    detail: str
    remedy: str | None = None

    @property
    def usable(self) -> bool:
        return self.state in {ExaminationState.OK, ExaminationState.EMPTY}

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "collection": self.collection,
                "state": self.state.value,
                "examined": self.examined,
                "source": self.source.value,
                "endpoint": self.endpoint,
                "detail": self.detail,
                "remedy": self.remedy,
            }
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Examination:
        return cls(
            collection=str(payload["collection"]),
            state=ExaminationState(payload["state"]),
            examined=int(payload["examined"]),
            source=Source(payload["source"]),
            endpoint=str(payload["endpoint"]),
            detail=str(payload["detail"]),
            remedy=_opt_str(payload.get("remedy")),
        )


# ---------------------------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class Artifact:
    """The four things every normalized object has, plus the body where it has one.

    ``id`` is stable across runs and across sources: it is built from the thing's own Canvas
    identity, never from its position in a list, so a finding recorded today still points at the
    same artifact after a module is reordered.
    """

    kind: ArtifactKind
    id: str
    title: str
    #: Canvas UI path, always beginning with ``/``. Item-level, never the editor (SPEC §08).
    canvas_path: str
    #: False when the path locates the artifact by search rather than by id — a file known only
    #: from an export has no Canvas file id to link to.
    link_exact: bool = True
    published: bool | None = None
    body_html: str | None = None
    body_source: Source | None = None
    #: Module titles from the course's own structure, outermost first. Empty when the artifact
    #: sits in no module, which is itself a fact D47 cares about.
    module_path: tuple[str, ...] = ()
    updated_at: str | None = None

    def deep_link(self, base_url: str) -> str:
        """Absolute link into the Canvas UI."""
        return f"{base_url.rstrip('/')}{self.canvas_path}"

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "kind": self.kind.value,
                "id": self.id,
                "title": self.title,
                "canvas_path": self.canvas_path,
                "link_exact": self.link_exact,
                "published": self.published,
                "body_html": self.body_html,
                "body_source": self.body_source.value if self.body_source else None,
                "module_path": list(self.module_path),
                "updated_at": self.updated_at,
                **self._extra_dict(),
            }
        )

    def _extra_dict(self) -> dict[str, Any]:
        return {}

    @staticmethod
    def _base_kwargs(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": str(payload["id"]),
            "title": str(payload["title"]),
            "canvas_path": str(payload["canvas_path"]),
            "link_exact": bool(payload.get("link_exact", True)),
            "published": _opt_bool(payload.get("published")),
            "body_html": _opt_str(payload.get("body_html")),
            "body_source": Source(payload["body_source"]) if payload.get("body_source") else None,
            "module_path": tuple(payload.get("module_path") or ()),
            "updated_at": _opt_str(payload.get("updated_at")),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class Page(Artifact):
    kind: ArtifactKind = ArtifactKind.PAGE
    slug: str
    front_page: bool = False

    def _extra_dict(self) -> dict[str, Any]:
        return {"slug": self.slug, "front_page": self.front_page}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Page:
        return cls(
            **Artifact._base_kwargs(payload),
            slug=str(payload["slug"]),
            front_page=bool(payload.get("front_page", False)),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class Assignment(Artifact):
    kind: ArtifactKind = ArtifactKind.ASSIGNMENT
    canvas_id: str
    due_at: str | None = None
    unlock_at: str | None = None
    lock_at: str | None = None
    points_possible: float | None = None
    grading_type: str | None = None
    submission_types: tuple[str, ...] = ()
    assignment_group_id: str | None = None
    #: D08 asks whether a graded assignment has a rubric attached, so this is recorded even when
    #: the rubric itself is not fetched.
    has_rubric: bool = False
    rubric_title: str | None = None
    omit_from_final_grade: bool = False

    @property
    def graded(self) -> bool:
        return not self.omit_from_final_grade and self.grading_type != "not_graded"

    def _extra_dict(self) -> dict[str, Any]:
        return {
            "canvas_id": self.canvas_id,
            "due_at": self.due_at,
            "unlock_at": self.unlock_at,
            "lock_at": self.lock_at,
            "points_possible": self.points_possible,
            "grading_type": self.grading_type,
            "submission_types": list(self.submission_types),
            "assignment_group_id": self.assignment_group_id,
            "has_rubric": self.has_rubric,
            "rubric_title": self.rubric_title,
            "omit_from_final_grade": self.omit_from_final_grade,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Assignment:
        return cls(
            **Artifact._base_kwargs(payload),
            canvas_id=str(payload["canvas_id"]),
            due_at=_opt_str(payload.get("due_at")),
            unlock_at=_opt_str(payload.get("unlock_at")),
            lock_at=_opt_str(payload.get("lock_at")),
            points_possible=_opt_float(payload.get("points_possible")),
            grading_type=_opt_str(payload.get("grading_type")),
            submission_types=tuple(payload.get("submission_types") or ()),
            assignment_group_id=_opt_str(payload.get("assignment_group_id")),
            has_rubric=bool(payload.get("has_rubric", False)),
            rubric_title=_opt_str(payload.get("rubric_title")),
            omit_from_final_grade=bool(payload.get("omit_from_final_grade", False)),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class Quiz(Artifact):
    kind: ArtifactKind = ArtifactKind.QUIZ
    canvas_id: str
    quiz_type: str | None = None
    #: Minutes, or ``None`` for untimed. D15 reads this and asks whether learners were told.
    time_limit: int | None = None
    due_at: str | None = None
    points_possible: float | None = None
    allowed_attempts: int | None = None
    question_count: int | None = None

    def _extra_dict(self) -> dict[str, Any]:
        return {
            "canvas_id": self.canvas_id,
            "quiz_type": self.quiz_type,
            "time_limit": self.time_limit,
            "due_at": self.due_at,
            "points_possible": self.points_possible,
            "allowed_attempts": self.allowed_attempts,
            "question_count": self.question_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Quiz:
        return cls(
            **Artifact._base_kwargs(payload),
            canvas_id=str(payload["canvas_id"]),
            quiz_type=_opt_str(payload.get("quiz_type")),
            time_limit=_opt_int(payload.get("time_limit")),
            due_at=_opt_str(payload.get("due_at")),
            points_possible=_opt_float(payload.get("points_possible")),
            allowed_attempts=_opt_int(payload.get("allowed_attempts")),
            question_count=_opt_int(payload.get("question_count")),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class Discussion(Artifact):
    """A discussion topic. Canvas models announcements as discussion topics too, and the kind
    field is what separates them — they answer different criteria (D05 versus D17)."""

    kind: ArtifactKind = ArtifactKind.DISCUSSION
    canvas_id: str
    graded: bool = False
    #: A graded topic's due date. Canvas keeps it on the *assignment* the topic is attached to,
    #: never on the topic itself, so a detector reading ``due_at`` off a discussion would read
    #: ``None`` on every graded discussion ever posted and report each one as undated.
    due_at: str | None = None
    posted_at: str | None = None
    delayed_post_at: str | None = None
    require_initial_post: bool = False
    discussion_type: str | None = None
    reply_count: int | None = None

    def _extra_dict(self) -> dict[str, Any]:
        return {
            "canvas_id": self.canvas_id,
            "graded": self.graded,
            "due_at": self.due_at,
            "posted_at": self.posted_at,
            "delayed_post_at": self.delayed_post_at,
            "require_initial_post": self.require_initial_post,
            "discussion_type": self.discussion_type,
            "reply_count": self.reply_count,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Discussion:
        return cls(
            **Artifact._base_kwargs(payload),
            kind=ArtifactKind(payload.get("kind", ArtifactKind.DISCUSSION)),
            canvas_id=str(payload["canvas_id"]),
            graded=bool(payload.get("graded", False)),
            due_at=_opt_str(payload.get("due_at")),
            posted_at=_opt_str(payload.get("posted_at")),
            delayed_post_at=_opt_str(payload.get("delayed_post_at")),
            require_initial_post=bool(payload.get("require_initial_post", False)),
            discussion_type=_opt_str(payload.get("discussion_type")),
            reply_count=_opt_int(payload.get("reply_count")),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class CourseFile(Artifact):
    """A file in the course.

    ``local_path`` is where the file sits inside the unpacked content export — the archive's own
    href, such as ``web_resources/week2-reading.docx`` — and is therefore relative to that
    export's ``files/`` directory, **not** to the data root. The document names that directory
    once, in :attr:`CourseDocument.export_files`; the two together locate the bytes. ``None``
    means the export did not carry this file, or there was no export. The model never carries an
    absolute path, because a normalized document is copied between machines and an absolute path
    is a machine's, not a course's."""

    kind: ArtifactKind = ArtifactKind.FILE
    canvas_id: str | None = None
    filename: str
    content_type: str | None = None
    size: int | None = None
    folder_path: str | None = None
    local_path: str | None = None
    sha256: str | None = None

    @property
    def extension(self) -> str:
        _, _, suffix = self.filename.rpartition(".")
        return f".{suffix.lower()}" if suffix and suffix != self.filename else ""

    def _extra_dict(self) -> dict[str, Any]:
        return {
            "canvas_id": self.canvas_id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size": self.size,
            "folder_path": self.folder_path,
            "local_path": self.local_path,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CourseFile:
        return cls(
            **Artifact._base_kwargs(payload),
            canvas_id=_opt_str(payload.get("canvas_id")),
            filename=str(payload["filename"]),
            content_type=_opt_str(payload.get("content_type")),
            size=_opt_int(payload.get("size")),
            folder_path=_opt_str(payload.get("folder_path")),
            local_path=_opt_str(payload.get("local_path")),
            sha256=_opt_str(payload.get("sha256")),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class ModuleItem(Artifact):
    kind: ArtifactKind = ArtifactKind.MODULE_ITEM
    canvas_id: str
    module_id: str
    position: int = 0
    indent: int = 0
    item_type: str | None = None
    #: The artifact this item points at, when it points at one in this document.
    target_id: str | None = None
    external_url: str | None = None
    completion_requirement: str | None = None

    def _extra_dict(self) -> dict[str, Any]:
        return {
            "canvas_id": self.canvas_id,
            "module_id": self.module_id,
            "position": self.position,
            "indent": self.indent,
            "item_type": self.item_type,
            "target_id": self.target_id,
            "external_url": self.external_url,
            "completion_requirement": self.completion_requirement,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ModuleItem:
        return cls(
            **Artifact._base_kwargs(payload),
            canvas_id=str(payload["canvas_id"]),
            module_id=str(payload["module_id"]),
            position=int(payload.get("position", 0)),
            indent=int(payload.get("indent", 0)),
            item_type=_opt_str(payload.get("item_type")),
            target_id=_opt_str(payload.get("target_id")),
            external_url=_opt_str(payload.get("external_url")),
            completion_requirement=_opt_str(payload.get("completion_requirement")),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class Module(Artifact):
    kind: ArtifactKind = ArtifactKind.MODULE
    canvas_id: str
    position: int = 0
    unlock_at: str | None = None
    prerequisite_module_ids: tuple[str, ...] = ()
    items: tuple[ModuleItem, ...] = ()

    def _extra_dict(self) -> dict[str, Any]:
        return {
            "canvas_id": self.canvas_id,
            "position": self.position,
            "unlock_at": self.unlock_at,
            "prerequisite_module_ids": list(self.prerequisite_module_ids),
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Module:
        return cls(
            **Artifact._base_kwargs(payload),
            canvas_id=str(payload["canvas_id"]),
            position=int(payload.get("position", 0)),
            unlock_at=_opt_str(payload.get("unlock_at")),
            prerequisite_module_ids=tuple(payload.get("prerequisite_module_ids") or ()),
            items=tuple(ModuleItem.from_dict(item) for item in payload.get("items") or ()),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class Embed(Artifact):
    """Something embedded inside another artifact's body.

    An embed has no Canvas id of its own, so its deep link is its container's — the reviewer is
    sent to the page that holds it, which is where the fix is made. ``container_id`` is what makes
    a finding say *which* page.
    """

    kind: ArtifactKind = ArtifactKind.EMBED
    container_id: str
    tag: str
    src: str | None = None
    provider: str | None = None
    width: str | None = None
    height: str | None = None
    #: A ``<track kind="captions">`` child. Absence is not proof there are no captions — the
    #: provider may carry them — which is why D27 is worded as what was examined.
    has_caption_track: bool = False
    #: A sibling anchor pointing at the same source. D39 asks for exactly this.
    accompanied_by_link: bool = False

    def _extra_dict(self) -> dict[str, Any]:
        return {
            "container_id": self.container_id,
            "tag": self.tag,
            "src": self.src,
            "provider": self.provider,
            "width": self.width,
            "height": self.height,
            "has_caption_track": self.has_caption_track,
            "accompanied_by_link": self.accompanied_by_link,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Embed:
        return cls(
            **Artifact._base_kwargs(payload),
            container_id=str(payload["container_id"]),
            tag=str(payload["tag"]),
            src=_opt_str(payload.get("src")),
            provider=_opt_str(payload.get("provider")),
            width=_opt_str(payload.get("width")),
            height=_opt_str(payload.get("height")),
            has_caption_track=bool(payload.get("has_caption_track", False)),
            accompanied_by_link=bool(payload.get("accompanied_by_link", False)),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class Syllabus(Artifact):
    """The course syllabus body. A first-class artifact because eight criteria cite it."""

    kind: ArtifactKind = ArtifactKind.SYLLABUS

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Syllabus:
        return cls(**Artifact._base_kwargs(payload))


@dataclass(frozen=True, kw_only=True, slots=True)
class CourseOutcome(Artifact):
    """One learning outcome recorded in the course, and the outcome group it sits in.

    Canvas keeps outcomes in a tree of groups rather than against modules, so ``group`` is the
    only structural placement there is to read. A detector wanting to say something about where an
    outcome sits has this and nothing else, and it says so rather than inferring a module.

    ``description`` is instructor-authored text and travels verbatim, like any other body.
    """

    kind: ArtifactKind = ArtifactKind.OUTCOME
    canvas_id: str
    #: Title of the outcome group this outcome is linked into. Empty for the course's root group.
    group: str = ""
    #: The outcome's own description, as plain text. May be empty, which is the fact D11 reads.
    description: str = ""
    #: Canvas's "friendly name" for the outcome, where one is set. Often empty.
    display_name: str = ""

    def _extra_dict(self) -> dict[str, Any]:
        return {
            "canvas_id": self.canvas_id,
            "group": self.group,
            "description": self.description,
            "display_name": self.display_name,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CourseOutcome:
        return cls(
            **Artifact._base_kwargs(payload),
            canvas_id=str(payload["canvas_id"]),
            group=str(payload.get("group") or ""),
            description=str(payload.get("description") or ""),
            display_name=str(payload.get("display_name") or ""),
        )


# ---------------------------------------------------------------------------------------------
# Course-level structure that is not itself an artifact
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class NavigationTab:
    """One entry in the course navigation menu. D06 counts the ones nobody uses."""

    id: str
    label: str
    position: int
    hidden: bool = False
    visibility: str | None = None
    type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "id": self.id,
                "label": self.label,
                "position": self.position,
                "hidden": self.hidden,
                "visibility": self.visibility,
                "type": self.type,
            }
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> NavigationTab:
        return cls(
            id=str(payload["id"]),
            label=str(payload["label"]),
            position=int(payload.get("position", 0)),
            hidden=bool(payload.get("hidden", False)),
            visibility=_opt_str(payload.get("visibility")),
            type=_opt_str(payload.get("type")),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class AssignmentGroup:
    """A gradebook group and its weight. D07 checks the weights total 100."""

    id: str
    name: str
    position: int = 0
    group_weight: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "id": self.id,
                "name": self.name,
                "position": self.position,
                "group_weight": self.group_weight,
            }
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AssignmentGroup:
        return cls(
            id=str(payload["id"]),
            name=str(payload["name"]),
            position=int(payload.get("position", 0)),
            group_weight=_opt_float(payload.get("group_weight")),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class RubricSummary:
    """A rubric defined in the course. Counts only — the criteria text is not needed here."""

    id: str
    title: str
    criteria_count: int = 0
    points_possible: float | None = None
    free_form_comments: bool = False

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "id": self.id,
                "title": self.title,
                "criteria_count": self.criteria_count,
                "points_possible": self.points_possible,
                "free_form_comments": self.free_form_comments,
            }
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RubricSummary:
        return cls(
            id=str(payload["id"]),
            title=str(payload["title"]),
            criteria_count=int(payload.get("criteria_count", 0)),
            points_possible=_opt_float(payload.get("points_possible")),
            free_form_comments=bool(payload.get("free_form_comments", False)),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class CourseInfo:
    """The course itself. ``name`` and ``course_code`` are the only free text here."""

    id: str
    name: str
    course_code: str | None = None
    workflow_state: str | None = None
    start_at: str | None = None
    end_at: str | None = None
    default_view: str | None = None
    is_public: bool | None = None
    time_zone: str | None = None

    @property
    def canvas_path(self) -> str:
        return f"/courses/{self.id}"

    def to_dict(self) -> dict[str, Any]:
        return _compact(
            {
                "id": self.id,
                "name": self.name,
                "course_code": self.course_code,
                "workflow_state": self.workflow_state,
                "start_at": self.start_at,
                "end_at": self.end_at,
                "default_view": self.default_view,
                "is_public": self.is_public,
                "time_zone": self.time_zone,
            }
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CourseInfo:
        return cls(
            id=str(payload["id"]),
            name=str(payload["name"]),
            course_code=_opt_str(payload.get("course_code")),
            workflow_state=_opt_str(payload.get("workflow_state")),
            start_at=_opt_str(payload.get("start_at")),
            end_at=_opt_str(payload.get("end_at")),
            default_view=_opt_str(payload.get("default_view")),
            is_public=_opt_bool(payload.get("is_public")),
            time_zone=_opt_str(payload.get("time_zone")),
        )


# ---------------------------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class CourseDocument:
    """One normalized course. This is what detectors are handed and nothing else.

    ``base_url`` is the Canvas instance the paths belong to. It is institution-identifying and
    therefore a secret (SPEC §11), which is exactly why it lives here once instead of being
    interpolated into several hundred artifact URLs.
    """

    schema_version: int = SCHEMA_VERSION
    base_url: str
    course: CourseInfo
    syllabus: Syllabus | None = None
    modules: tuple[Module, ...] = ()
    pages: tuple[Page, ...] = ()
    assignments: tuple[Assignment, ...] = ()
    quizzes: tuple[Quiz, ...] = ()
    discussions: tuple[Discussion, ...] = ()
    announcements: tuple[Discussion, ...] = ()
    files: tuple[CourseFile, ...] = ()
    embeds: tuple[Embed, ...] = ()
    outcomes: tuple[CourseOutcome, ...] = ()
    navigation: tuple[NavigationTab, ...] = ()
    assignment_groups: tuple[AssignmentGroup, ...] = ()
    rubrics: tuple[RubricSummary, ...] = ()
    examinations: tuple[Examination, ...] = ()
    #: Free-form provenance the acquisition layer fills in: which sources answered, what was
    #: capped. Never a wall-clock value, because the document has to serialize deterministically.
    provenance: Mapping[str, Any] = field(default_factory=dict)

    # -- traversal ---------------------------------------------------------------------------

    def artifacts(self) -> Iterator[Artifact]:
        """Every artifact in the document, in a stable order.

        Module items are yielded with their module: a detector iterating this sees the whole
        course exactly once.
        """
        if self.syllabus is not None:
            yield self.syllabus
        for module in self.modules:
            yield module
            yield from module.items
        yield from self.pages
        yield from self.assignments
        yield from self.quizzes
        yield from self.discussions
        yield from self.announcements
        yield from self.files
        yield from self.embeds
        yield from self.outcomes

    @property
    def course_artifact(self) -> Artifact:
        """The course itself, as something a finding can be located on.

        Several structure detectors answer a question about the course as a whole — it has no
        syllabus, its navigation lists tools nothing uses, its group weights do not total 100 —
        and a finding has to point somewhere a reviewer can open. This is that somewhere.

        **It is deliberately not yielded by :meth:`artifacts`**, which is the course's *contents*.
        A container that listed itself among its contents would make every count off by one, and
        the detector integrity guard's empty document — a course holding zero artifacts, which
        every detector must refuse to report on — would hold one. So :meth:`by_id` resolves it and
        :meth:`artifacts` does not yield it, and ``tests/test_model.py`` asserts both halves.
        """
        return Artifact(
            kind=ArtifactKind.COURSE,
            id=artifact_id(ArtifactKind.COURSE, self.course.id),
            title=self.course.name,
            canvas_path=self.course.canvas_path,
        )

    def by_id(self, artifact_id: str) -> Artifact | None:
        return self._index().get(artifact_id)

    def _index(self) -> dict[str, Artifact]:
        index = {artifact.id: artifact for artifact in self.artifacts()}
        course = self.course_artifact
        index[course.id] = course
        return index

    def of_kind(self, kind: ArtifactKind) -> tuple[Artifact, ...]:
        return tuple(artifact for artifact in self.artifacts() if artifact.kind is kind)

    def bodies(self) -> Iterator[Artifact]:
        """Artifacts that carry HTML. What the content detectors of item 06 walk."""
        return (artifact for artifact in self.artifacts() if artifact.body_html)

    def deep_link(self, artifact: Artifact) -> str:
        return artifact.deep_link(self.base_url)

    def examination(self, collection: str) -> Examination | None:
        for entry in self.examinations:
            if entry.collection == collection:
                return entry
        return None

    @property
    def export_files(self) -> str | None:
        """The unpacked export's ``files/`` directory, relative to the data root, or ``None``.

        Recorded by acquisition when an export was unpacked. It is what a
        :attr:`CourseFile.local_path` is relative to; see
        :func:`sightline.detect.sources.export_file_source`.
        """
        value = self.provenance.get(EXPORT_FILES_KEY)
        return str(value) if value else None

    def counts(self) -> dict[str, int]:
        """Artifacts per kind. The declarative summary of what acquisition found."""
        tally: dict[str, int] = {}
        for artifact in self.artifacts():
            tally[artifact.kind.value] = tally.get(artifact.kind.value, 0) + 1
        return dict(sorted(tally.items()))

    # -- serialization -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """A plain dict, deterministically ordered and free of wall-clock values."""
        return {
            "schema_version": self.schema_version,
            "base_url": self.base_url,
            "course": self.course.to_dict(),
            "syllabus": self.syllabus.to_dict() if self.syllabus else None,
            "modules": [module.to_dict() for module in self.modules],
            "pages": [page.to_dict() for page in self.pages],
            "assignments": [item.to_dict() for item in self.assignments],
            "quizzes": [quiz.to_dict() for quiz in self.quizzes],
            "discussions": [item.to_dict() for item in self.discussions],
            "announcements": [item.to_dict() for item in self.announcements],
            "files": [item.to_dict() for item in self.files],
            "embeds": [item.to_dict() for item in self.embeds],
            "outcomes": [item.to_dict() for item in self.outcomes],
            "navigation": [tab.to_dict() for tab in self.navigation],
            "assignment_groups": [group.to_dict() for group in self.assignment_groups],
            "rubrics": [rubric.to_dict() for rubric in self.rubrics],
            "examinations": [entry.to_dict() for entry in self.examinations],
            "provenance": dict(sorted(self.provenance.items())),
        }

    def to_json(self) -> str:
        """Canonical JSON. Two runs over an unchanged course produce identical bytes."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> CourseDocument:
        version = int(payload.get("schema_version", 0))
        if version != SCHEMA_VERSION:
            raise SchemaVersionMismatch(
                f"document is schema version {version}; this build reads {SCHEMA_VERSION}"
            )
        syllabus = payload.get("syllabus")
        return cls(
            schema_version=version,
            base_url=str(payload["base_url"]),
            course=CourseInfo.from_dict(payload["course"]),
            syllabus=Syllabus.from_dict(syllabus) if syllabus else None,
            modules=tuple(Module.from_dict(item) for item in payload.get("modules") or ()),
            pages=tuple(Page.from_dict(item) for item in payload.get("pages") or ()),
            assignments=tuple(
                Assignment.from_dict(item) for item in payload.get("assignments") or ()
            ),
            quizzes=tuple(Quiz.from_dict(item) for item in payload.get("quizzes") or ()),
            discussions=tuple(
                Discussion.from_dict(item) for item in payload.get("discussions") or ()
            ),
            announcements=tuple(
                Discussion.from_dict(item) for item in payload.get("announcements") or ()
            ),
            files=tuple(CourseFile.from_dict(item) for item in payload.get("files") or ()),
            embeds=tuple(Embed.from_dict(item) for item in payload.get("embeds") or ()),
            outcomes=tuple(CourseOutcome.from_dict(item) for item in payload.get("outcomes") or ()),
            navigation=tuple(
                NavigationTab.from_dict(item) for item in payload.get("navigation") or ()
            ),
            assignment_groups=tuple(
                AssignmentGroup.from_dict(item) for item in payload.get("assignment_groups") or ()
            ),
            rubrics=tuple(RubricSummary.from_dict(item) for item in payload.get("rubrics") or ()),
            examinations=tuple(
                Examination.from_dict(item) for item in payload.get("examinations") or ()
            ),
            provenance=dict(payload.get("provenance") or {}),
        )

    @classmethod
    def from_json(cls, text: str) -> CourseDocument:
        return cls.from_dict(json.loads(text))


class SchemaVersionMismatch(ValueError):
    """A serialized document was written by a build with a different model."""


# ---------------------------------------------------------------------------------------------
# Canvas UI paths
# ---------------------------------------------------------------------------------------------
#
# Item-level and never the editor (SPEC §08). A reviewer following a finding should land on what a
# learner sees, not on an edit form: an editor link invites a change before the review is agreed,
# and on a published course it is the wrong place to be reading from.


def course_path(course_id: str) -> str:
    return f"/courses/{course_id}"


def syllabus_path(course_id: str) -> str:
    return f"/courses/{course_id}/assignments/syllabus"


def page_path(course_id: str, slug: str) -> str:
    return f"/courses/{course_id}/pages/{slug}"


def assignment_path(course_id: str, assignment_id: str) -> str:
    return f"/courses/{course_id}/assignments/{assignment_id}"


def quiz_path(course_id: str, quiz_id: str) -> str:
    return f"/courses/{course_id}/quizzes/{quiz_id}"


def discussion_path(course_id: str, topic_id: str) -> str:
    return f"/courses/{course_id}/discussion_topics/{topic_id}"


def announcement_path(course_id: str, topic_id: str) -> str:
    """Canvas stores announcements as discussion topics and links them the same way."""
    return discussion_path(course_id, topic_id)


def module_path(course_id: str, module_id: str) -> str:
    """The modules page, anchored at the module. Canvas has no standalone module screen."""
    return f"/courses/{course_id}/modules#module_{module_id}"


def module_item_path(course_id: str, item_id: str) -> str:
    return f"/courses/{course_id}/modules/items/{item_id}"


def file_path(course_id: str, file_id: str) -> str:
    return f"/courses/{course_id}/files/{file_id}"


def outcome_path(course_id: str, outcome_id: str) -> str:
    """The course outcomes screen, anchored at the outcome. Canvas has no per-outcome page."""
    return f"/courses/{course_id}/outcomes#outcome_{outcome_id}"


def file_search_path(course_id: str, filename: str) -> str:
    """For a file known only from an export, which carries no Canvas file id.

    A search link is weaker than an item link and is marked ``link_exact=False`` so that nothing
    downstream presents it as if it were the same thing.
    """
    from urllib.parse import quote

    return f"/courses/{course_id}/files?search_term={quote(filename)}"


# ---------------------------------------------------------------------------------------------
# Stable ids
# ---------------------------------------------------------------------------------------------


def artifact_id(kind: ArtifactKind, key: str) -> str:
    """``kind:key``. Built from the thing's own identity, never from its position in a list."""
    return f"{kind.value}:{key}"


def embed_id(container_id: str, ordinal: int) -> str:
    """Embeds have no Canvas id, so document order within their container is the key.

    That is positional, and deliberately so: it is the only identity an embed has. It is stable
    for an unedited page, and a finding against an embed always names its container as well.
    """
    return f"{ArtifactKind.EMBED.value}:{container_id}:{ordinal:03d}"


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def sort_artifacts(items: Iterable[Artifact]) -> tuple[Artifact, ...]:
    """Stable ordering by id. Determinism starts here."""
    return tuple(sorted(items, key=lambda artifact: artifact.id))


def _compact(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop ``None`` values, keeping ``False``, ``0`` and empty collections.

    A key present with ``null`` and a key absent mean the same thing to every reader here, and
    dropping them keeps a serialized sample course readable by a person.
    """
    return {key: value for key, value in payload.items() if value is not None}


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _opt_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _opt_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _opt_float(value: Any) -> float | None:
    return None if value is None else float(value)


ARTIFACT_CLASSES: Mapping[ArtifactKind, Any] = {
    ArtifactKind.PAGE: Page,
    ArtifactKind.ASSIGNMENT: Assignment,
    ArtifactKind.QUIZ: Quiz,
    ArtifactKind.DISCUSSION: Discussion,
    ArtifactKind.ANNOUNCEMENT: Discussion,
    ArtifactKind.FILE: CourseFile,
    ArtifactKind.MODULE: Module,
    ArtifactKind.MODULE_ITEM: ModuleItem,
    ArtifactKind.EMBED: Embed,
    ArtifactKind.SYLLABUS: Syllabus,
    ArtifactKind.OUTCOME: CourseOutcome,
}


def artifact_from_dict(payload: Mapping[str, Any]) -> Artifact:
    """Rebuild an artifact from its serialized form, dispatching on ``kind``."""
    kind = ArtifactKind(payload["kind"])
    return ARTIFACT_CLASSES[kind].from_dict(payload)


__all__ = [
    "ARTIFACT_CLASSES",
    "EXPORT_FILES_KEY",
    "SCHEMA_VERSION",
    "Artifact",
    "ArtifactKind",
    "Assignment",
    "AssignmentGroup",
    "CourseDocument",
    "CourseFile",
    "CourseInfo",
    "CourseOutcome",
    "Discussion",
    "Embed",
    "Examination",
    "ExaminationState",
    "Module",
    "ModuleItem",
    "NavigationTab",
    "Page",
    "Quiz",
    "RubricSummary",
    "SchemaVersionMismatch",
    "Source",
    "Syllabus",
    "announcement_path",
    "artifact_from_dict",
    "artifact_id",
    "assignment_path",
    "course_path",
    "discussion_path",
    "embed_id",
    "file_path",
    "file_search_path",
    "module_item_path",
    "module_path",
    "outcome_path",
    "page_path",
    "quiz_path",
    "sort_artifacts",
    "syllabus_path",
]
