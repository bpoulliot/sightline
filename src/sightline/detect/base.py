"""The detector interface, and the invariant it exists to carry.

**The invariant.** Every detector reports what it *examined* alongside what it found, and a
detector that examined nothing is an error rather than a pass. SPEC §11 names the failure this
prevents: the dangerous bug is not a red test, it is a detector that quietly finds nothing because
it broke, handed to a designer as a course with nothing wrong. Same class as a test suite that
collects nothing and exits green.

It is enforced structurally, not by care. A detector never touches the :class:`CourseDocument`; the
only way it can reach an artifact is through the :class:`Survey` it is handed, and the survey counts
every artifact it gives out. :meth:`Detector.run` is final — a subclass that tries to override it
fails at class-definition time — so the count is taken on a path no detector can step around.
:mod:`sightline.detect.integrity` is the guard over it, and ``tests/test_detector_integrity.py``
fires that guard deliberately, because a guard that has never failed is indistinguishable from a
guard that cannot fail.

**Zero examined is two different facts, and only one of them is an error.** A detector handed a
document with no artifacts at all has been broken or starved, and raises :class:`NothingExamined`.
A detector over a course that genuinely has no files is a different thing, and acquisition already
recorded which it was: every collection carries an :class:`~sightline.model.Examination` saying
``ok`` / ``empty`` / ``refused`` / ``absent`` / ``skipped``. So a zero count backed by a *usable*
examination returns a result in the :attr:`ResultState.NOT_EXAMINED` state — explicit, carrying the
reason and the remedy, and never clean. Neither path can produce a pass.

**Nothing here is a verdict.** A :class:`Finding` is a computed observation with a location, a
verbatim quote of what was written, and the number of artifacts the detector opened to find it. A
:class:`Undetermined` is the explicit statement that something could not be read, which is the
honest answer often enough that it needs a first-class shape rather than a silence.

**A detector never names a rubric criterion.** Detectors emit facts about course artifacts; a
rubric pack decides how those facts are reported and rated (SPEC §04, and the item 04 decision
recorded in ``docs/ROADMAP.md``). The criteria column in SPEC §05 is documentation of the 3.1.3
pack, not a property of anything here. What a detector *does* declare is a ``capability_tag`` from
the closed vocabulary in :mod:`sightline.capabilities` — a course-quality concept, not a criterion
and not a rubric number — and a ``capability_description`` of its share of that concept. Packs bind
rows to tags, so one detector serves every rubric that cares about the same concept and none of
them has to be crosswalked to another.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

from ..capabilities import require as require_capability
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
    Embed,
    Examination,
    ExaminationState,
    Module,
    NavigationTab,
    Page,
    Quiz,
    RubricSummary,
    Syllabus,
)
from .html_scan import Scan, scan
from .links import LinkCheck, LinkResolver, NoLinkResolver
from .sources import FileSource


class Tier(enum.StrEnum):
    """How a finding was reached, which governs how confidently it may be worded (SPEC §02)."""

    #: Computed by a parser. No model. Stated declaratively.
    A = "A"
    #: Located and quoted. Worded as evidence, never as a conclusion.
    B = "B"
    #: Pedagogical judgement. Context assembled, question drafted, never pre-rated.
    C = "C"
    #: Not determinable from course artifacts.
    D = "D"


class Severity(enum.StrEnum):
    """What the observation means for a learner — never what it means about a course or a person.

    The vocabulary is deliberate. "Critical" and "major" are grades, and this tool does not grade
    (SPEC §11). These three name an effect a reviewer can check against the course themselves.
    """

    #: A learner using assistive technology cannot reach the content at all.
    BARRIER = "barrier"
    #: The content is reachable but the route to it is degraded.
    FRICTION = "friction"
    #: A convention worth correcting that raises no direct barrier.
    ADVISORY = "advisory"


class ResultState(enum.StrEnum):
    """Whether the detector actually ran over anything."""

    #: It opened at least one artifact. The only state in which "no findings" means anything.
    EXAMINED = "examined"
    #: There was nothing to open, and acquisition says why. Never a pass.
    NOT_EXAMINED = "not_examined"


@dataclass(frozen=True, kw_only=True, slots=True)
class Location:
    """Where a finding is, precisely enough to be falsified in seconds.

    The Canvas path is a *path*, joined to the base URL on the way out, for the reason the document
    model keeps it that way: the institution's hostname is a secret (SPEC §11) and a finding is
    written to disk.
    """

    artifact_id: str
    artifact_kind: ArtifactKind
    title: str
    canvas_path: str
    #: False when the path locates the artifact by search rather than by id.
    link_exact: bool = True
    #: Where inside the artifact, in a reviewer's words: ``"image 3"``, ``"table 1"``.
    within: str | None = None
    #: 1-based source line of the markup, where the artifact has markup.
    line: int | None = None

    def deep_link(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}{self.canvas_path}"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "artifact_kind": self.artifact_kind.value,
            "title": self.title,
            "canvas_path": self.canvas_path,
            "link_exact": self.link_exact,
        }
        if self.within is not None:
            payload["within"] = self.within
        if self.line is not None:
            payload["line"] = self.line
        return payload


@dataclass(frozen=True, kw_only=True, slots=True)
class Finding:
    """One computed observation about one place in the course.

    ``observed`` is the value as measured — ``alt=""``, ``1.9:1``, ``9pt``. ``evidence`` is the
    source text exactly as it was written, so a reviewer can search the page for it. Neither is a
    sentence about the course, and there is no field here in which one could be written.

    ``examined`` travels *with the finding*, not only with the result, because a finding is read on
    its own in a workbook row and "3 of 47 pages" is part of what makes it checkable. The framework
    stamps it; a detector never sets it.
    """

    detector: str
    #: What kind of observation this is, e.g. ``alt_missing``. Stable; downstream keys on it.
    code: str
    severity: Severity
    tier: Tier
    location: Location
    observed: str
    evidence: str | None = None
    #: Artifacts the detector opened for the whole run. Stamped by :meth:`Detector.run`.
    examined: int = 0
    #: Anything else the detector measured, carried verbatim into the report.
    context: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "detector": self.detector,
            "code": self.code,
            "severity": self.severity.value,
            "tier": self.tier.value,
            "location": self.location.to_dict(),
            "observed": self.observed,
            "examined": self.examined,
        }
        if self.evidence is not None:
            payload["evidence"] = self.evidence
        if self.context:
            payload["context"] = dict(sorted(self.context.items()))
        return payload


@dataclass(frozen=True, kw_only=True, slots=True)
class Undetermined:
    """Something the detector opened and could not read.

    Distinct from a finding and distinct from silence, and that is the entire point. A PDF whose
    bytes were never downloaded is not an accessible PDF and is not an inaccessible one; reporting
    it as either is a fabrication, and reporting it as nothing is the silent under-detection SPEC
    §11 is about.
    """

    detector: str
    location: Location
    reason: str
    remedy: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "detector": self.detector,
            "location": self.location.to_dict(),
            "reason": self.reason,
        }
        if self.remedy is not None:
            payload["remedy"] = self.remedy
        return payload


class NothingExamined(RuntimeError):
    """A detector examined zero artifacts and nothing said it should have.

    Raised by the framework, never by a detector. The runner turns it into a reported non-result;
    what it must never turn into is a course that looks clean.
    """

    def __init__(self, detector: str, *, collections: Sequence[str], detail: str) -> None:
        self.detector = detector
        self.collections = tuple(collections)
        self.detail = detail
        super().__init__(
            f"{detector} examined 0 artifacts and no examination record explains it. "
            f"It reads {', '.join(self.collections) or 'no declared collection'}. {detail}"
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class DetectorResult:
    """What one detector did, in full: what it opened, what it found, what it could not read."""

    detector: str
    title: str
    tier: Tier
    #: The capability tag the detector declared. Travels with the result so that projecting onto a
    #: rubric is a pure function of (pack, report) and needs no lookup back into the registry.
    capability_tag: str
    state: ResultState
    #: Artifacts opened. Zero is only ever paired with ``NOT_EXAMINED``.
    examined: int
    #: The specific things inside them — images, links, tables. Zero is an ordinary fact.
    subjects: int
    findings: tuple[Finding, ...] = ()
    undetermined: tuple[Undetermined, ...] = ()
    #: Collections the detector needed that acquisition could not read.
    unreadable: tuple[Examination, ...] = ()
    #: Why nothing was examined, when nothing was. ``None`` whenever the state is ``EXAMINED``.
    not_examined_because: str | None = None
    #: The exception type, when the detector raised something other than
    #: :class:`NothingExamined` and the runner reported it rather than let it end the review. Set
    #: only by :func:`~sightline.detect.runner.run_detectors`, always with ``NOT_EXAMINED``: a
    #: crashed detector is a detector that could not run, told apart so a summary never files it
    #: with one whose course merely held nothing to read.
    crashed: str | None = None

    @property
    def clean(self) -> bool:
        """True only when the detector actually ran and found nothing it could not account for.

        A ``NOT_EXAMINED`` result is never clean, and neither is one still carrying something it
        could not read. Anything downstream deciding a criterion is satisfied asks this and not
        ``not result.findings``.
        """
        return (
            self.state is ResultState.EXAMINED
            and not self.findings
            and not self.undetermined
            and self.examined > 0
        )

    def statement(self) -> str:
        """One declarative line of counts — the register SPEC §08 requires of a tier A result."""
        if self.state is ResultState.NOT_EXAMINED:
            because = self.not_examined_because or ""
            return f"{self.detector}: nothing was examined. {because}".strip()
        parts = [f"{self.detector}: examined {self.examined} artifact(s)"]
        if self.subjects:
            parts.append(f"holding {self.subjects} item(s) of interest")
        parts.append(f"found {len(self.findings)} observation(s)")
        if self.undetermined:
            parts.append(f"{len(self.undetermined)} could not be read")
        return ", ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "detector": self.detector,
            "title": self.title,
            "tier": self.tier.value,
            "capability_tag": self.capability_tag,
            "state": self.state.value,
            "examined": self.examined,
            "subjects": self.subjects,
            "clean": self.clean,
            "findings": [finding.to_dict() for finding in self.findings],
            "undetermined": [entry.to_dict() for entry in self.undetermined],
            "unreadable": [entry.to_dict() for entry in self.unreadable],
        }
        if self.not_examined_because is not None:
            payload["not_examined_because"] = self.not_examined_because
        if self.crashed is not None:
            payload["crashed"] = self.crashed
        return payload


@dataclass(frozen=True, slots=True)
class Body:
    """An artifact that carries markup. Its parse is :meth:`Survey.parse`, asked for by body."""

    artifact: Artifact
    html: str


#: What :meth:`Parses._of` hands out for an artifact that carries no markup.
_NO_MARKUP = scan("")


class Parses:
    """One parse per body per detector run, shared by every detector in that run.

    Every HTML detector reads the same tree of the same body, and until roadmap item 30 each of
    them built its own: twelve body-reading detectors, twelve parses of every body, and the parse
    was nine tenths of the detector stage's wall time. :func:`~sightline.detect.run_detectors`
    makes one of these per run and hands it to every :class:`Survey`, so a body is parsed the first
    time a detector asks for it and read from here every time after.

    **What is shared is the parse, never the count.** Each detector still gets its own
    :class:`Survey`, which is what counts the artifacts it examined; a detector that asks for no
    body is counted for none, however many bodies other detectors had parsed before it. And nothing
    here is reachable except through :meth:`Survey.parse`, which answers only for a body that same
    survey handed out — so a detector cannot use the shared parses to read a body it never asked
    for, and the survey stays the only route to an artifact.

    **Its lifetime is the run.** It is an object the runner holds, not module state: when the run
    ends the parses go with it, so instructor-authored course text does not sit in a process-wide
    cache between runs or between courses.

    Keyed on the artifact id *and* the markup, so the same id carrying different markup can never
    be handed the wrong tree. A :class:`~sightline.detect.html_scan.Scan` is immutable, which is
    what makes one safe to hand to several detectors.

    An artifact with no markup is not parsed at all: it is handed one shared empty scan, so the
    number of parses a run makes is the number of bodies that carry HTML, and a detector may read
    ``survey.parse(artifact).text`` for an assignment with no description without special-casing it.
    """

    __slots__ = ("_scans",)

    def __init__(self) -> None:
        self._scans: dict[tuple[str, str], Scan] = {}

    def __len__(self) -> int:
        return len(self._scans)

    def _of(self, artifact_id: str, html: str) -> Scan:
        if not html:
            return _NO_MARKUP
        key = (artifact_id, html)
        page = self._scans.get(key)
        if page is None:
            page = self._scans[key] = scan(html)
        return page


class Survey:
    """The only way a detector reaches the course, and therefore the only place counting happens.

    Every accessor increments :attr:`examined` as it yields. A detector cannot obtain an artifact
    without being counted for it, which is what makes the invariant structural rather than a rule
    somebody has to remember at the bottom of a new detector.
    """

    def __init__(
        self,
        document: CourseDocument,
        detector: Detector,
        *,
        files: FileSource | None = None,
        links: LinkResolver | None = None,
        parses: Parses | None = None,
    ) -> None:
        self._document = document
        self._detector = detector
        self._files = files
        self._links = links or NoLinkResolver()
        # None is a detector run on its own, which still parses each body once within the run.
        self._parses = parses if parses is not None else Parses()
        self._examined: set[str] = set()
        self._subjects = 0
        self._undetermined: list[Undetermined] = []
        self._files_by_key: dict[str, CourseFile] | None = None

    # -- what was examined -------------------------------------------------------------------

    @property
    def examined(self) -> int:
        """Distinct artifacts handed out. Distinct, so a second pass is not a second count."""
        return len(self._examined)

    @property
    def subjects(self) -> int:
        return self._subjects

    def counted(self, count: int = 1) -> None:
        """Record ``count`` more subjects — images, links, table cells — inside what was opened.

        Zero subjects over a non-zero number of artifacts is an ordinary, reportable fact: a course
        with no images is not a broken image detector. Only zero *artifacts* is an error.
        """
        self._subjects += count

    # -- artifacts ---------------------------------------------------------------------------

    def bodies(self) -> Iterator[Body]:
        """Every artifact carrying HTML, in the document's stable order."""
        for artifact in self._document.bodies():
            self._examined.add(artifact.id)
            yield Body(artifact=artifact, html=artifact.body_html or "")

    def parse(self, subject: Body | Artifact) -> Scan:
        """The one parse of a body this survey handed out, shared across the run (:class:`Parses`).

        Takes a :class:`Body` from :meth:`bodies` or an artifact from any other accessor — a
        syllabus, a page — and parses the markup it carries. Refuses anything this survey did not
        hand out: every artifact reached through an accessor was counted on the way, so this cannot
        become a route to markup nobody counted.
        """
        artifact = subject.artifact if isinstance(subject, Body) else subject
        if artifact.id not in self._examined:
            raise LookupError(
                f"{self._detector.id} asked for the parse of {artifact.id}, which its survey never "
                "handed out. Reach an artifact through a Survey accessor, so that it is counted."
            )
        return self._parses._of(artifact.id, artifact.body_html or "")

    def embeds(self) -> Iterator[Embed]:
        for embed in self._document.embeds:
            self._examined.add(embed.id)
            yield embed

    def files(self, *extensions: str) -> Iterator[CourseFile]:
        """Course files, optionally narrowed to a set of extensions.

        Narrowing still counts every file considered, not only the matches. A course of forty
        files none of which is a PDF has been examined by the PDF detector; reporting that as zero
        examined would turn an honest "no documents here" into the error state.

        An extension may be written with or without its leading dot. The model stores ``.pdf``,
        and a caller passing ``"pdf"`` used to match nothing at all and report it as a clean
        course with no PDFs in it — a wrong answer with no error, which is the failure this whole
        interface exists to prevent. Both spellings are normalized here rather than in each
        caller, because the caller that gets it wrong is by definition the one not looking.
        """
        wanted = {f".{extension.lstrip('.').lower()}" for extension in extensions}
        for artifact in self._document.files:
            self._examined.add(artifact.id)
            if not wanted or artifact.extension in wanted:
                yield artifact

    def container_of(self, embed: Embed) -> Artifact | None:
        """The artifact an embed was found inside.

        Not counted: an embed was already counted when it was handed out, and a finding about it
        should name the page a reviewer opens rather than an element with no screen of its own.
        """
        return self._document.by_id(embed.container_id)

    def read(self, file: CourseFile) -> bytes | None:
        """The file's bytes, or ``None`` when they were never acquired."""
        if self._files is None:
            return None
        return self._files.read(file)

    # -- course structure --------------------------------------------------------------------
    #
    # The API-sourced detectors of item 07 read structure rather than markup, and each accessor
    # counts exactly as the body and file accessors above do. Two of these deliberately do not
    # count, and both say why on themselves: `course_location` builds a location and hands out no
    # artifact, and `population` answers "does the course have any of these" without opening one.

    def syllabus(self) -> Syllabus | None:
        """The course syllabus, counted when there is one."""
        entry = self._document.syllabus
        if entry is not None:
            self._examined.add(entry.id)
        return entry

    def modules(self) -> Iterator[Module]:
        """Every module, in the course's own order. Items travel inside the module."""
        for module in self._document.modules:
            self._examined.add(module.id)
            yield module

    def pages(self) -> Iterator[Page]:
        for page in self._document.pages:
            self._examined.add(page.id)
            yield page

    def assignments(self) -> Iterator[Assignment]:
        for assignment in self._document.assignments:
            self._examined.add(assignment.id)
            yield assignment

    def quizzes(self) -> Iterator[Quiz]:
        for quiz in self._document.quizzes:
            self._examined.add(quiz.id)
            yield quiz

    def discussions(self) -> Iterator[Discussion]:
        for topic in self._document.discussions:
            self._examined.add(topic.id)
            yield topic

    def announcements(self) -> Iterator[Discussion]:
        for topic in self._document.announcements:
            self._examined.add(topic.id)
            yield topic

    def outcomes(self) -> Iterator[CourseOutcome]:
        for outcome in self._document.outcomes:
            self._examined.add(outcome.id)
            yield outcome

    def navigation(self) -> Iterator[NavigationTab]:
        """The course navigation menu, in menu order.

        A tab is not an :class:`~sightline.model.Artifact` and has no id of that shape, so it is
        counted under a prefixed key. It is still something examined: a detector that read the
        menu and found nothing wrong looked at fifteen entries, and that number belongs in the
        result.
        """
        for tab in self._document.navigation:
            self._examined.add(f"tab:{tab.id}")
            yield tab

    def assignment_groups(self) -> Iterator[AssignmentGroup]:
        for group in self._document.assignment_groups:
            self._examined.add(f"assignment_group:{group.id}")
            yield group

    def rubrics(self) -> Iterator[RubricSummary]:
        for rubric in self._document.rubrics:
            self._examined.add(f"rubric:{rubric.id}")
            yield rubric

    def population(self, collection: str) -> int:
        """How many things the course holds in ``collection``, **without examining any of them**.

        A census, not an examination. D06 asks whether the Quizzes tab has any quizzes behind it;
        it never opens a quiz, and counting one as examined would let a detector inflate its own
        count by asking questions about collections it never reads.

        The consequence is deliberate: a detector whose only call was this one would examine zero
        artifacts and be refused by :class:`NothingExamined`, exactly as a broken detector is.
        """
        if collection == "syllabus":
            return 1 if self._document.syllabus is not None else 0
        field = {"tabs": "navigation"}.get(collection, collection)
        value = getattr(self._document, field, ())
        return len(value) if isinstance(value, tuple) else 0

    def course(self) -> CourseInfo:
        """The course's own metadata — name, code, term dates. Not an artifact, not counted."""
        return self._document.course

    # -- links ---------------------------------------------------------------------------------

    def resolve(self, url: str) -> LinkCheck:
        """Ask the run's link resolver about one URL.

        Resolution is off by default and the answer then says so, which is a different fact from
        a link that was tried and did not answer. See :mod:`sightline.detect.links`.
        """
        return self._links.check(url)

    @property
    def link_resolution_enabled(self) -> bool:
        return not isinstance(self._links, NoLinkResolver)

    # -- building what a detector emits ------------------------------------------------------

    def course_location(self, *, within: str | None = None, path: str | None = None) -> Location:
        """A location on the course as a whole, for a finding about the course as a whole.

        Some facts belong to no artifact: the course has no syllabus, its navigation menu lists
        tools nothing uses, its group weights total 90. A finding still has to point somewhere a
        reviewer can open, so it points at the course.

        **This hands out no artifact and therefore counts nothing.** A detector that built one of
        these and examined nothing else has examined nothing, and the framework refuses it — which
        is what stops a course-level detector from quietly becoming exempt from the invariant.

        ``path`` narrows the link to a course-scoped screen — the navigation settings, the
        assignment groups — and is checked against the course path so that a detector cannot send
        a reviewer somewhere else.
        """
        artifact = self._document.course_artifact
        canvas_path = artifact.canvas_path
        if path is not None:
            if not path.startswith(canvas_path):
                raise ValueError(
                    f"{path!r} is not inside {canvas_path!r}; a course-level finding may only "
                    "link into the course it is about."
                )
            canvas_path = path
        return Location(
            artifact_id=artifact.id,
            artifact_kind=artifact.kind,
            title=artifact.title,
            canvas_path=canvas_path,
            within=within,
        )

    def location(
        self,
        artifact: Artifact,
        *,
        within: str | None = None,
        line: int | None = None,
    ) -> Location:
        return Location(
            artifact_id=artifact.id,
            artifact_kind=artifact.kind,
            title=artifact.title,
            canvas_path=artifact.canvas_path,
            link_exact=artifact.link_exact,
            within=within,
            line=line,
        )

    def finding(
        self,
        *,
        code: str,
        severity: Severity,
        location: Location,
        observed: str,
        evidence: str | None = None,
        tier: Tier | None = None,
        **context: Any,
    ) -> Finding:
        return Finding(
            detector=self._detector.id,
            code=code,
            severity=severity,
            tier=tier or self._detector.tier,
            location=location,
            observed=observed,
            evidence=evidence,
            context=context,
        )

    def undetermined(self, location: Location, reason: str, remedy: str | None = None) -> None:
        """Record that something was opened and could not be read.

        Never a finding, and never a silence.
        """
        self._undetermined.append(
            Undetermined(
                detector=self._detector.id, location=location, reason=reason, remedy=remedy
            )
        )

    @property
    def undetermined_records(self) -> tuple[Undetermined, ...]:
        return tuple(self._undetermined)

    # -- acquisition's own record ------------------------------------------------------------

    def examinations(self) -> tuple[Examination, ...]:
        """The examination records for the collections this detector declared it reads."""
        found = []
        for name in self._detector.collections:
            entry = self._document.examination(name)
            if entry is not None:
                found.append(entry)
        return tuple(found)

    def unreadable(self) -> tuple[Examination, ...]:
        return tuple(entry for entry in self.examinations() if not entry.usable)

    def find_file(self, reference: str) -> CourseFile | None:
        """The course file a ``src`` or ``href`` points at, when one can be identified.

        Matching is on the Canvas file id in the URL, then on the filename, because a course body
        references a file three different ways and a detector that only understood one of them
        would under-report on the other two.
        """
        return self._file_index().get(_file_key(reference))

    def _file_index(self) -> Mapping[str, CourseFile]:
        if self._files_by_key is None:
            index: dict[str, CourseFile] = {}
            for artifact in self._document.files:
                if artifact.canvas_id:
                    index.setdefault(f"id:{artifact.canvas_id}", artifact)
                index.setdefault(f"name:{artifact.filename.lower()}", artifact)
            self._files_by_key = index
        return self._files_by_key


class Detector:
    """Base class for every detector. Subclasses implement :meth:`examine` and nothing else.

    A subclass declares six things: a stable ``id``, a plain-language ``title`` saying what it
    finds, the determinacy ``tier`` it reports at, the acquisition ``collections`` it reads, and a
    ``capability_tag`` with a ``capability_description``. The collections are not documentation —
    they are how a zero count is told apart from a broken detector, and how a refused collection
    reaches the result.

    **The capability tag is the only thing connecting a detector to a rubric, and it connects in
    one direction only.** The tag names a course-quality concept from the closed vocabulary in
    :mod:`sightline.capabilities` — ``alt_text``, ``due_date_coverage``. A *pack* binds its own
    rows to tags; nothing here knows or may know which rows those are. A tag outside the vocabulary
    raises at class-definition time, because we write the detectors and a typo in one is our bug.
    Several detectors may share a tag, and a row bound to it receives from all of them.
    """

    #: ``D21``. Stable across refactors; downstream keys on it.
    id: ClassVar[str] = ""
    #: What this detector finds, in a sentence a non-programmer can check. Never names a criterion.
    title: ClassVar[str] = ""
    tier: ClassVar[Tier] = Tier.A
    #: Acquisition collection names, as recorded in :class:`~sightline.model.Examination`.
    collections: ClassVar[tuple[str, ...]] = ()
    #: The course-quality concept this detector produces facts about, from the vocabulary in
    #: :mod:`sightline.capabilities`. Never a criterion, never a rubric number.
    capability_tag: ClassVar[str] = ""
    #: What this detector contributes to that concept, in a reviewer's words. Distinct from
    #: ``title``: the title describes this parser, and this describes its share of the concept —
    #: which is what a person authoring a pack reads when deciding whether to bind the tag.
    capability_description: ClassVar[str] = ""

    def __init_subclass__(cls, *, abstract: bool = False, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if abstract:
            # An intermediate base — a shared HTML walker, say. Declared explicitly rather than
            # inferred from a missing id, so a detector that simply forgot one still fails here.
            return
        if "run" in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} overrides run(). It is final: the examined count is taken there, "
                "and a detector that takes its own count is a detector that can forget to. "
                "Implement examine(survey) instead."
            )
        for attribute in (
            "id",
            "title",
            "collections",
            "capability_tag",
            "capability_description",
        ):
            if not getattr(cls, attribute, None):
                raise TypeError(f"{cls.__name__} must declare {attribute}.")
        # Hard failure, not a warning. See the class docstring and sightline.capabilities.
        require_capability(cls.capability_tag, declared_by=f"{cls.__module__}.{cls.__qualname__}")
        register(cls)

    # -- the one method a detector writes ----------------------------------------------------

    def examine(self, survey: Survey) -> Iterable[Finding]:
        """Walk the course through ``survey`` and yield findings. Raise nothing; find nothing."""
        raise NotImplementedError

    # -- final --------------------------------------------------------------------------------

    def run(
        self,
        document: CourseDocument,
        *,
        files: FileSource | None = None,
        links: LinkResolver | None = None,
        parses: Parses | None = None,
    ) -> DetectorResult:
        """Run this detector over one course. Not overridable — see :meth:`__init_subclass__`.

        ``parses`` is the run's shared :class:`Parses`, handed in by the runner; omitted, this run
        parses for itself.
        """
        survey = Survey(document, self, files=files, links=links, parses=parses)
        findings = tuple(self.examine(survey))
        examined = survey.examined
        unreadable = survey.unreadable()

        if examined == 0:
            explained = self._explained_empty(survey)
            if explained is None:
                raise NothingExamined(
                    self.id,
                    collections=self.collections,
                    detail=(
                        "The document holds no artifact this detector reads and no examination "
                        "record saying the course has none. Treating that as a pass would report "
                        "a course clean on something never looked at."
                    ),
                )
            return DetectorResult(
                detector=self.id,
                title=self.title,
                tier=self.tier,
                capability_tag=self.capability_tag,
                state=ResultState.NOT_EXAMINED,
                examined=0,
                subjects=0,
                findings=(),
                undetermined=survey.undetermined_records,
                unreadable=unreadable,
                not_examined_because=explained,
            )

        stamped = tuple(replace(finding, examined=examined) for finding in findings)
        return DetectorResult(
            detector=self.id,
            title=self.title,
            tier=self.tier,
            capability_tag=self.capability_tag,
            state=ResultState.EXAMINED,
            examined=examined,
            subjects=survey.subjects,
            findings=stamped,
            undetermined=survey.undetermined_records,
            unreadable=unreadable,
        )

    def _explained_empty(self, survey: Survey) -> str | None:
        """The sentence explaining a zero count, or ``None`` when nothing explains it.

        Three cases, and the third is the one worth being careful about.

        A collection acquisition could not read — refused, absent, skipped — explains a zero count
        and is reported as the reason, which is what lets a reviewer see a permissions problem
        instead of a clean course.

        Every declared collection recorded ``empty`` is acquisition saying the course genuinely has
        none of these. Also an explanation, also not a pass.

        A collection recorded ``ok`` — acquisition read things — paired with a detector that
        examined none of them is *nothing being explained*. The course has the artifacts and the
        detector did not open them, which is the broken detector this whole interface exists to
        catch. It raises.
        """
        records = survey.examinations()
        if not records:
            return None
        unusable = [entry for entry in records if not entry.usable]
        if unusable:
            entry = unusable[0]
            return (
                f"The {entry.collection} collection was not readable ({entry.state.value}): "
                f"{entry.detail}"
            )
        if any(entry.state is not ExaminationState.EMPTY for entry in records):
            return None
        names = ", ".join(entry.collection for entry in records)
        return f"The course holds nothing in {names}, so there was nothing to examine."


# ---------------------------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------------------------
#
# Populated by ``__init_subclass__``, so importing a detector module registers it and there is no
# second list to forget to update. The coverage guard in `integrity.py` walks this.

_REGISTRY: dict[str, type[Detector]] = {}


def register(cls: type[Detector]) -> type[Detector]:
    existing = _REGISTRY.get(cls.id)
    if existing is not None and existing is not cls and existing.__qualname__ != cls.__qualname__:
        raise TypeError(
            f"two detectors claim the id {cls.id}: {existing.__module__}.{existing.__qualname__} "
            f"and {cls.__module__}.{cls.__qualname__}. Ids are what findings are keyed on."
        )
    _REGISTRY[cls.id] = cls
    return cls


@contextmanager
def registry_snapshot() -> Iterator[None]:
    """Put the registry back the way it was on the way out.

    Defining a detector registers it, which is what makes the registry impossible to forget to
    update — and also what makes a throwaway detector defined inside a test leak into every later
    caller of :func:`detectors`. A coverage guard that reports a detector nobody ships is a guard
    nobody trusts, so anything defining one for a moment wraps it in this.
    """
    saved = dict(_REGISTRY)
    try:
        yield
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)


def registry() -> Mapping[str, type[Detector]]:
    """Every registered detector class, keyed by id, in id order."""
    return dict(sorted(_REGISTRY.items()))


def detectors() -> tuple[Detector, ...]:
    """One instance of every registered detector, in id order."""
    return tuple(cls() for cls in registry().values())


def _file_key(reference: str) -> str:
    """The index key a ``src`` or ``href`` resolves to."""
    trimmed = (reference or "").split("?", 1)[0].split("#", 1)[0].rstrip("/")
    segments = [segment for segment in trimmed.split("/") if segment]
    if "files" in segments:
        position = segments.index("files")
        if position + 1 < len(segments):
            candidate = segments[position + 1]
            if candidate.isdigit():
                return f"id:{candidate}"
    return f"name:{segments[-1].lower()}" if segments else "name:"


__all__ = [
    "Body",
    "Detector",
    "DetectorResult",
    "Finding",
    "Location",
    "NothingExamined",
    "Parses",
    "ResultState",
    "Severity",
    "Survey",
    "Tier",
    "Undetermined",
    "detectors",
    "register",
    "registry",
    "registry_snapshot",
]
