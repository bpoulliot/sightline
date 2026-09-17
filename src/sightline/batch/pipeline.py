"""What one course goes through, stage by stage, and the reviewable artifact it leaves behind.

The batch runner owns *ordering, isolation and persistence*; this module owns *the work*. They are
separate because the runner's guarantees — one course failing does not abort the batch, a killed
process resumes, a stage that examined nothing fails loudly — are properties of the loop, and a
test that proves them should not need Canvas, a model host, or sixty-eight rubric rows to do it.
So the loop drives a :class:`CoursePipeline`, and :class:`ReviewPipeline` is the real one.

WHERE THE COURSE COMES FROM IS A SEAM, DELIBERATELY
---------------------------------------------------
:class:`CanvasSource` acquires from Canvas the way SPEC §04 describes. :class:`BundleSource` reads
a directory on disk — `examples/sample-course` is one — and exists because the whole pipeline
should be runnable by somebody who has not yet typed a Canvas token, and because a fixture batch
in the suite must not need a Canvas instance. It is a development path and says so; a bundle is a
normalized course somebody already acquired, not a second acquisition implementation.

WHAT THE SCORE STAGE IS AND IS NOT
-----------------------------------
It rolls up: how many rows carry each rating, how many hours the bands add to, which rows lead the
action plan. It computes no aggregate quality number and there is no field here for one — SPEC §11
forbids it explicitly, and the reference instrument's own inverted ``VALUE`` column is the
cautionary example. Every number the rollup carries is a count of rows or a sum of hours, and the
uncalibrated-costs note travels with it so no surface can render a band as a measurement.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..acquire.bundle import BundleError, load_bundle
from ..acquire.service import CourseAcquisition
from ..canvas.errors import CanvasError
from ..detect.links import LinkResolver
from ..detect.runner import DetectionReport, run_detectors
from ..detect.sources import FileSource
from ..extract.base import Corpus
from ..extract.runner import (
    ExtractionReport,
    RowExtraction,
    RowExtractor,
    answer_requests,
    run_extractors,
)
from ..llm.client import ModelCard, OllamaClient
from ..llm.errors import ModelHostError, ModelHostNotConfiguredError
from ..logging import get_logger
from ..model import CourseDocument
from ..rubric.pack import RubricPack
from ..rubric.project import Projection, project
from ..rubric.routing import RoutingManifest, manifest
from ..rubric.states import Coverage, Rating
from .stages import BatchAborted, NothingExamined, Stage, StageError, StageOutcome

log = get_logger("sightline.batch")


# ---------------------------------------------------------------------------------------------
# The carrier
# ---------------------------------------------------------------------------------------------


@dataclass
class CourseWork:
    """One course's work in progress. Mutable, single-threaded, and never shared between courses.

    ``reference`` is what the designer typed — a Canvas course id, a course URL, or a bundle
    directory. **It never goes into a log line.** A course URL carries the Canvas host, which
    SPEC §11 treats as a secret because it identifies the institution; the run store scrubs event
    payloads on the way in, and log calls here key on ``course_id`` and ``ordinal`` instead.
    """

    reference: str
    course_id: str
    ordinal: int
    document: CourseDocument | None = None
    detection: DetectionReport | None = None
    projection: Projection | None = None
    routing: RoutingManifest | None = None
    topics: ExtractionReport | None = None
    rows: tuple[RowExtraction, ...] = ()
    rollup: Rollup | None = None
    outcomes: dict[Stage, StageOutcome] = field(default_factory=dict)

    def record(self, outcome: StageOutcome) -> StageOutcome:
        self.outcomes[outcome.stage] = outcome
        return outcome


@dataclass(frozen=True, kw_only=True, slots=True)
class Rollup:
    """The score stage's product: counts of rows and a sum of hours. Never a grade.

    ``hours`` is the total of the per-row rolled-up effort the projection computed, and
    ``calibrated`` says whether the cost table behind it has been fitted to anything (it has not —
    roadmap item 12b). Any surface rendering an hour figure renders ``note`` beside it.
    """

    rows: int
    by_rating: Mapping[str, int]
    by_coverage: Mapping[str, int]
    hours: float
    rows_with_hours: int
    calibrated: bool
    note: str
    #: Row codes in action-plan order: priority crossed with band, instrument order as tiebreak.
    action_plan: tuple[str, ...]
    #: Codes of rows a model was asked about, so the reviewer knows which carry located evidence.
    evidenced: tuple[str, ...] = ()

    def statement(self) -> str:
        ordered = ", ".join(
            f"{count} {rating}" for rating, count in sorted(self.by_rating.items()) if count
        )
        return (
            f"{self.rows} row(s) rolled up: {ordered or 'none rated'}; "
            f"{self.hours:g} h across {self.rows_with_hours} row(s) that carry an estimate. "
            f"{self.note}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement(),
            "rows": self.rows,
            "by_rating": dict(self.by_rating),
            "by_coverage": dict(self.by_coverage),
            "hours": self.hours,
            "rows_with_hours": self.rows_with_hours,
            "calibrated": self.calibrated,
            "note": self.note,
            "action_plan": list(self.action_plan),
            "evidenced": list(self.evidenced),
        }


# ---------------------------------------------------------------------------------------------
# Where a course comes from
# ---------------------------------------------------------------------------------------------


class DocumentSource(Protocol):
    """Turns a reference into a normalized course, and says where it came from."""

    async def acquire(self, reference: str) -> tuple[CourseDocument, str]: ...


@dataclass(frozen=True)
class CanvasSource:
    """Acquisition as SPEC §04 describes it: Canvas API plus a content export."""

    acquisition: CourseAcquisition

    async def acquire(self, reference: str) -> tuple[CourseDocument, str]:
        try:
            result = await self.acquisition.acquire(reference)
        except CanvasError as exc:
            raise StageError(
                Stage.ACQUIRE,
                f"Canvas did not hand over this course. {getattr(exc, 'summary', exc)}",
                remedy=getattr(exc, "remedy", None),
            ) from exc
        return result.document, result.summary()


@dataclass(frozen=True)
class BundleSource:
    """A course bundle on disk. The development path — see the module docstring.

    The reference is the bundle directory. Nothing is written, so this reads outside the data root
    on purpose: a bundle is an input a person points at, like the ``--bundle`` flag every other
    command in this package already takes.
    """

    async def acquire(self, reference: str) -> tuple[CourseDocument, str]:
        try:
            document = load_bundle(Path(reference))
        except BundleError as exc:
            raise StageError(
                Stage.ACQUIRE,
                f"That course bundle could not be read. {exc}",
                remedy=(
                    "Point at a directory holding course.json and a bodies/ directory — "
                    "examples/sample-course is one."
                ),
            ) from exc
        counts = document.counts()
        total = sum(counts.values())
        listed = ", ".join(f"{value} {name}" for name, value in sorted(counts.items()))
        return document, f"{total} artifact(s) read from a course bundle: {listed or 'none'}."


# ---------------------------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------------------------


class CoursePipeline(Protocol):
    """What the batch runner drives: five stages, then the artifact a reviewer opens."""

    async def run(self, stage: Stage, work: CourseWork) -> StageOutcome: ...

    def review(self, work: CourseWork) -> Mapping[str, Any]:
        """Everything the stages produced, ready to be written out and read by a person."""
        ...


class ReviewPipeline:
    """The real pipeline: acquire, detect, project, extract, score.

    Everything it needs is injected. There is no configuration read inside a stage, because a
    stage that reads a setting is a stage whose behaviour a test cannot pin.
    """

    def __init__(
        self,
        *,
        source: DocumentSource,
        pack: RubricPack,
        client: OllamaClient | None = None,
        model: str | None = None,
        files: FileSource | None = None,
        links: LinkResolver | None = None,
    ) -> None:
        self.source = source
        self.pack = pack
        self.client = client
        self.model = model
        self.files = files
        self.links = links
        self._card: ModelCard | None = None

    async def run(self, stage: Stage, work: CourseWork) -> StageOutcome:
        match stage:
            case Stage.ACQUIRE:
                return work.record(await self._acquire(work))
            case Stage.DETECT:
                return work.record(self._detect(work))
            case Stage.PROJECT:
                return work.record(self._project(work))
            case Stage.EXTRACT:
                return work.record(await self._extract(work))
            case Stage.SCORE:
                return work.record(self._score(work))
        raise StageError(stage, f"no implementation for stage {stage.value}")  # pragma: no cover

    # -- 1. acquire ---------------------------------------------------------------------------

    async def _acquire(self, work: CourseWork) -> StageOutcome:
        document, statement = await self.source.acquire(work.reference)
        work.document = document
        examined = sum(document.counts().values())
        if examined == 0:
            raise NothingExamined(
                Stage.ACQUIRE,
                detail="The course normalized to no pages, assignments, files or modules at all.",
                remedy=(
                    "Check that the reference points at the course you meant and that the token "
                    "can read it. A review of an empty course would report every criterion as "
                    "unexamined."
                ),
            )
        return StageOutcome(
            stage=Stage.ACQUIRE,
            examined=examined,
            statement=statement,
            detail={"counts": dict(document.counts()), "course": document.course.name},
        )

    # -- 2. detect ----------------------------------------------------------------------------

    def _detect(self, work: CourseWork) -> StageOutcome:
        document = _require(work.document, Stage.DETECT, "the course was never acquired")
        report = run_detectors(document, files=self.files, links=self.links)
        work.detection = report
        examined = sum(result.examined for result in report.results)
        if examined == 0:
            raise NothingExamined(
                Stage.DETECT,
                detail=report.statement(),
                remedy=(
                    "Every detector reported that it opened nothing. The course was acquired, so "
                    "this is a fault in the review rather than an empty course — the run log "
                    "carries each detector's own reason."
                ),
            )
        return StageOutcome(
            stage=Stage.DETECT,
            examined=examined,
            statement=report.statement(),
            detail={
                "findings": len(report.findings),
                "not_examined": [result.detector for result in report.not_examined],
                "undetermined": len(report.undetermined),
            },
        )

    # -- 3. project ---------------------------------------------------------------------------

    def _project(self, work: CourseWork) -> StageOutcome:
        document = _require(work.document, Stage.PROJECT, "the course was never acquired")
        report = _require(work.detection, Stage.PROJECT, "nothing was detected")
        projection = project(self.pack, report)
        work.projection = projection
        routing = manifest(projection.rows)
        work.routing = routing
        if not projection.rows:
            raise NothingExamined(
                Stage.PROJECT,
                detail=f"{self.pack.name} rendered no rows.",
                remedy=(
                    "Every section of the chosen rubric is switched off, or the pack holds no "
                    "criteria. Choose another rubric or re-enable a section."
                ),
            )
        calls = estimated_calls(document, routing.requests)
        return StageOutcome(
            stage=Stage.PROJECT,
            examined=len(projection.rows),
            statement=(
                f"{projection.statement()} {routing.statement()} "
                f"Those rows cost about {calls} model call(s) on this course."
            ),
            detail={
                "pack": projection.pack_id,
                "routed": len(routing.requests),
                "estimated_calls": calls,
                "warnings": [warning.line() for warning in projection.warnings],
            },
        )

    # -- 4. extract ---------------------------------------------------------------------------

    async def _extract(self, work: CourseWork) -> StageOutcome:
        document = _require(work.document, Stage.EXTRACT, "the course was never acquired")
        routing = _require(work.routing, Stage.EXTRACT, "nothing was routed")
        card = await self._model_card()

        try:
            topics = await run_extractors(document, self.client, card)
            rows = await answer_requests(routing.requests, document, self.client, card)
        except ModelHostError as exc:
            # Every remaining call would fail the same way, one timeout at a time. Item 10 made
            # this split inside one course; here it ends the batch and names what is left.
            raise BatchAborted(
                f"The model host stopped answering. {exc.summary}",
                remedy=getattr(exc, "remedy", None),
                stage=Stage.EXTRACT,
            ) from exc

        work.topics = topics
        work.rows = rows
        examined = sum(result.examined for result in topics.results) + sum(
            row.result.examined for row in rows
        )
        if examined == 0:
            raise NothingExamined(
                Stage.EXTRACT,
                detail=topics.statement(),
                remedy=(
                    "No extractor found an artifact of the kind it reads. The rows routed for "
                    "located evidence carry no quotes, and none of them may be read as satisfied."
                ),
            )
        quotes = len(topics.evidence) + sum(len(row.result.evidence) for row in rows)
        discarded = len(topics.discarded) + sum(len(row.result.discarded) for row in rows)
        failures = len(topics.failures) + sum(len(row.result.failures) for row in rows)
        return StageOutcome(
            stage=Stage.EXTRACT,
            examined=examined,
            statement=(
                f"{topics.statement()} {len(rows)} routed row(s) answered; "
                f"{quotes} verified quote(s) in total."
            ),
            detail={
                "model": topics.model,
                "quotes": quotes,
                "discarded": discarded,
                "failures": failures,
                "rows_answered": len(rows),
            },
        )

    async def _model_card(self) -> ModelCard:
        if self.client is None or not self.model:
            raise BatchAborted(
                "No model host and model are configured, so no row could be asked about.",
                remedy=(
                    "Set the model host and pick a model on the Settings screen, then start the "
                    "run again. Sightline does not review a course without them: the rows that "
                    "carry located evidence would silently carry none."
                ),
                stage=Stage.EXTRACT,
            )
        if self._card is None:
            try:
                self._card = await self.client.card(self.model)
            except ModelHostNotConfiguredError as exc:
                raise BatchAborted(exc.summary, remedy=exc.remedy, stage=Stage.EXTRACT) from exc
            except ModelHostError as exc:
                raise BatchAborted(
                    f"The chosen model could not be read from the host. {exc.summary}",
                    remedy=getattr(exc, "remedy", None),
                    stage=Stage.EXTRACT,
                ) from exc
        return self._card

    # -- 5. score -----------------------------------------------------------------------------

    def _score(self, work: CourseWork) -> StageOutcome:
        projection = _require(work.projection, Stage.SCORE, "nothing was projected")
        by_rating = {rating.value: len(projection.by_rating(rating)) for rating in Rating}
        by_coverage = {
            coverage.value: len(projection.by_coverage(coverage)) for coverage in Coverage
        }
        with_hours = [row for row in projection.rows if row.hours]
        rollup = Rollup(
            rows=len(projection.rows),
            by_rating=by_rating,
            by_coverage=by_coverage,
            hours=round(sum(row.hours or 0.0 for row in with_hours), 2),
            rows_with_hours=len(with_hours),
            calibrated=projection.effort.calibrated,
            note=projection.effort.note,
            action_plan=tuple(row.code for row in projection.action_plan_order()),
            evidenced=tuple(row.criterion for row in work.rows),
        )
        work.rollup = rollup
        return StageOutcome(
            stage=Stage.SCORE,
            examined=rollup.rows,
            statement=rollup.statement(),
            detail={"hours": rollup.hours, "calibrated": rollup.calibrated},
        )

    # -- what a reviewer opens ----------------------------------------------------------------

    def review(self, work: CourseWork) -> dict[str, Any]:
        """The reviewable artifact: everything the five stages produced, nothing they concluded.

        Projected rows and located evidence sit **side by side, keyed on the criterion code**,
        rather than the evidence being folded into the row. A row's rating is arithmetic over
        detector findings (item 04); a quote is what a model located. Merging them into one field
        would make the two indistinguishable at the point a reviewer decides whether to check the
        quote, which is the whole of SPEC §02.
        """
        document = work.document
        projection = work.projection
        return {
            "course": {
                "reference": work.reference,
                "id": document.course.id if document else None,
                "name": document.course.name if document else None,
                "base_url": document.base_url if document else None,
            },
            "pack": {
                "id": projection.pack_id if projection else self.pack.id,
                "name": projection.pack_name if projection else self.pack.name,
                "version": projection.pack_version if projection else self.pack.version,
            },
            "stages": [outcome.to_dict() for outcome in work.outcomes.values()],
            "detection": work.detection.to_dict() if work.detection else None,
            "projection": projection.to_dict() if projection else None,
            "routing": work.routing.to_dict() if work.routing else None,
            "extraction": {
                "topics": work.topics.to_dict() if work.topics else None,
                "rows": [row.to_dict() for row in work.rows],
            },
            "rollup": work.rollup.to_dict() if work.rollup else None,
        }


def is_bundle_reference(reference: str) -> bool:
    """Whether a course reference names a bundle directory rather than a Canvas course.

    Asked by anything that has to rebuild a pipeline for a run somebody started earlier — the
    resume path, mainly. A directory that exists on disk is a bundle; a Canvas id or URL is never
    one. Testing the filesystem rather than the shape of the string, because a Canvas course URL
    and a relative path both contain a slash and a heuristic over that would send half the resumes
    to the wrong source.
    """
    return bool(reference) and Path(reference).is_dir()


def estimated_calls(document: CourseDocument, requests: Sequence[Any]) -> int:
    """How many model calls the routed rows will cost, counted before any are made.

    One call per passage of the course per routed row (item 04 states the price plainly). A number
    somebody can read before starting a sixteen-course batch is worth the one pass over the
    corpus it costs to compute.
    """
    if not requests:
        return 0
    probe = RowExtractor(requests[0])
    passages = len(list(Corpus(document, probe).passages()))
    return passages * len(requests)


def _require(value: Any, stage: Stage, because: str) -> Any:
    if value is None:
        raise StageError(
            stage,
            f"The {stage.value} stage had nothing to work with: {because}.",
            remedy=(
                "This is a fault in the run rather than in the course. The run log carries the "
                "order the stages ran in."
            ),
        )
    return value


__all__ = [
    "BundleSource",
    "CanvasSource",
    "CoursePipeline",
    "CourseWork",
    "DocumentSource",
    "ReviewPipeline",
    "Rollup",
    "estimated_calls",
    "is_bundle_reference",
]
