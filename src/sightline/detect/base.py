"""The detector interface, and the invariant it exists to carry.

**The invariant.** Every detector reports what it *examined* alongside what it found, and a
detector that examined nothing is an error rather than a pass. SPEC §11 names the failure this
prevents: the dangerous bug is not a red test, it is a detector that quietly finds nothing because
it broke, handed to a designer as a course with nothing wrong. Same class as a test suite that
collects nothing and exits green.

It is enforced structurally, not by care. A detector never touches the :class:`CourseDocument`; the
only way it can reach an artifact is through the :class:`Survey` it is handed, and the survey counts
every artifact it gives out. :meth:`Detector.run` is final — a subclass that tries to override it
fails at class-definition time — so the count is taken on a path no detector can step around. Item
21 builds the guard that fires this deliberately; this module is what makes that possible.

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
pack, not a property of anything here.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar

from ..model import (
    Artifact,
    ArtifactKind,
    CourseDocument,
    CourseFile,
    Embed,
    Examination,
    ExaminationState,
)
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
        return payload


@dataclass(frozen=True, slots=True)
class Body:
    """An artifact that carries markup, paired with the one parse of it."""

    artifact: Artifact
    html: str


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
    ) -> None:
        self._document = document
        self._detector = detector
        self._files = files
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

    def embeds(self) -> Iterator[Embed]:
        for embed in self._document.embeds:
            self._examined.add(embed.id)
            yield embed

    def files(self, *extensions: str) -> Iterator[CourseFile]:
        """Course files, optionally narrowed to a set of extensions.

        Narrowing still counts every file considered, not only the matches. A course of forty
        files none of which is a PDF has been examined by the PDF detector; reporting that as zero
        examined would turn an honest "no documents here" into the error state.
        """
        wanted = {extension.lower() for extension in extensions}
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

    # -- building what a detector emits ------------------------------------------------------

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

    A subclass declares four things: a stable ``id``, a plain-language ``title`` saying what it
    finds, the determinacy ``tier`` it reports at, and the acquisition ``collections`` it reads.
    The collections are not documentation — they are how a zero count is told apart from a broken
    detector, and how a refused collection reaches the result.
    """

    #: ``D21``. Stable across refactors; downstream keys on it.
    id: ClassVar[str] = ""
    #: What this detector finds, in a sentence a non-programmer can check. Never names a criterion.
    title: ClassVar[str] = ""
    tier: ClassVar[Tier] = Tier.A
    #: Acquisition collection names, as recorded in :class:`~sightline.model.Examination`.
    collections: ClassVar[tuple[str, ...]] = ()

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
        for attribute in ("id", "title", "collections"):
            if not getattr(cls, attribute, None):
                raise TypeError(f"{cls.__name__} must declare {attribute}.")
        register(cls)

    # -- the one method a detector writes ----------------------------------------------------

    def examine(self, survey: Survey) -> Iterable[Finding]:
        """Walk the course through ``survey`` and yield findings. Raise nothing; find nothing."""
        raise NotImplementedError

    # -- final --------------------------------------------------------------------------------

    def run(self, document: CourseDocument, *, files: FileSource | None = None) -> DetectorResult:
        """Run this detector over one course. Not overridable — see :meth:`__init_subclass__`."""
        survey = Survey(document, self, files=files)
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
# second list to forget to update. Item 21's coverage guard walks this.

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
