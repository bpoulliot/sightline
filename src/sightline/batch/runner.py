"""The batch runner: up to sixteen courses, one after another, resumable, and nothing exported.

SPEC §04 sizes this deliberately. One FastAPI process, SQLite for run state, asyncio for the
background work; no Celery, no Redis, no worker container. At one to sixteen courses the work is
I/O-bound against one local model host, so there is nothing to distribute and a queue broker would
add a second thing to operate in exchange for nothing. Typical batches are one or two courses and
the largest expected is eight to fifteen.

WHAT THIS MODULE GUARANTEES, AND WHAT IT LEAVES TO THE PIPELINE
----------------------------------------------------------------
It guarantees the *loop*: the order, per-course isolation, the resume, the events every surface
reads progress from, and the file a reviewer eventually opens. The work itself is a
:class:`~sightline.batch.pipeline.CoursePipeline`. That split is what lets the guarantees be
proved against a fixture pipeline rather than against Canvas and a GPU.

PER-COURSE ISOLATION, AND THE ONE THING THAT IS NOT A COURSE FAILING
---------------------------------------------------------------------
A course that fails fails alone: its exception is caught, its row records the stage and the
reason, and the next course starts. The summary names it. What does *not* isolate is
:class:`~sightline.batch.stages.BatchAborted` — an unreachable model host, a missing credential.
Every remaining course would fail the same way, one timeout at a time, so the batch stops, the run
is left **interrupted** rather than failed (the courses are fine; the run was stopped), and the
summary says which courses never ran and why.

RESUME IS PER COURSE, AND THAT IS A CHOICE
--------------------------------------------
Restarting picks up at the first course that is not finished. A course that was mid-flight when
the process died starts again at its first stage rather than resuming inside itself, and the run
log says so in as many words. Within one course the stages hand typed objects to each other;
persisting and rehydrating each intermediate would be a second serialization contract with no
reader, and the expensive stage — extract — is the one a kill lands in most often, where there is
nothing complete to resume from anyway. A finished course is never redone and is never silently
skipped either: it is reported as carried over from the earlier attempt.

NOTHING HERE EXPORTS
--------------------
There is no call into :mod:`sightline.batch.gate` in this module and there is not supposed to be.
A completed run leaves a reviewable result per course under the data root and an empty
``reports/``; a person opens the review, and only then can anything be written for delivery
(SPEC §09).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from ..config import DEFAULT_BATCH_CAP
from ..logging import get_logger
from ..models import Run, RunCourse, RunStatus
from ..paths import DataPaths
from ..store import RunStore
from .pipeline import CoursePipeline, CourseWork
from .stages import ORDER, BatchAborted, NothingExamined, Stage, StageError, StageOutcome

log = get_logger("sightline.batch.runner")

#: Filename of the artifact a reviewer opens, inside the course's directory under ``runs/``.
REVIEW_FILENAME = "review.json"

#: The rubric a run is reviewed against when nobody chose another (SPEC §09: rubric selection is
#: per run, defaulting to OSCQR 3.1.3). One home, read by the command line and the New run screen.
DEFAULT_RUBRIC_PACK = "uccs-oscqr-3-1-3"

#: Builds the pipeline a run executes, and closes whatever it opened. An async context manager
#: rather than a plain callable because the real one holds an HTTP client per run.
PipelineFactory = Callable[[Run], AbstractAsyncContextManager[CoursePipeline]]


class BatchTooLargeError(ValueError):
    """More courses were asked for than the cap allows."""

    def __init__(self, asked: int, cap: int) -> None:
        self.asked = asked
        self.cap = cap
        self.summary = f"{asked} courses were given and the cap for one run is {cap}."
        self.remedy = (
            "Split them across runs. The cap is deliberate: courses are reviewed one after "
            "another against a single model host, so a larger batch is a longer wait rather than "
            "more throughput."
        )
        super().__init__(f"{self.summary} {self.remedy}")


def create_batch_run(
    store: RunStore,
    references: Sequence[str],
    *,
    rubric_pack: str,
    bindings_digest: str | None = None,
    cap: int = DEFAULT_BATCH_CAP,
    note: str | None = None,
) -> str:
    """Record a batch, refusing one that is over the cap. Returns the run id.

    The cap is checked *here*, at the one point a run comes into existence, rather than in the
    loop that executes it: a run created over the cap and truncated at execution time is a batch
    where courses somebody listed are silently not reviewed.

    ``bindings_digest`` is the accepted bindings the run uses, from
    :func:`sightline.rubric.accepted.resolve_pack` at the moment it is started; ``None`` is the
    shipped pack's own.
    """
    cleaned = [reference.strip() for reference in references if reference.strip()]
    if not cleaned:
        raise ValueError("a run needs at least one course reference")
    if len(cleaned) > cap:
        raise BatchTooLargeError(len(cleaned), cap)
    return store.create_run(
        cleaned, rubric_pack=rubric_pack, bindings_digest=bindings_digest, note=note
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class CourseOutcome:
    """What became of one course in one execution of the batch."""

    ordinal: int
    course_id: str
    status: RunStatus
    stages: tuple[StageOutcome, ...] = ()
    #: The stage that failed, when one did. Not derivable from ``stages``, which holds the ones
    #: that *finished* — reading the failure off the last of those names the stage before it.
    failed_stage: Stage | None = None
    error: str | None = None
    #: True when this execution did not run the course because an earlier one finished it.
    carried_over: bool = False
    #: Where the reviewable result was written, relative to the data root.
    review_path: str | None = None

    @property
    def reviewed(self) -> bool:
        return RunStatus(self.status) is RunStatus.SUCCEEDED

    def line(self) -> str:
        """One plain line for the run summary. States what happened, never how bad it was."""
        head = f"Course {self.ordinal + 1}"
        if self.carried_over and self.reviewed:
            return f"{head}: reviewed by an earlier attempt of this run; not run again."
        if self.carried_over:
            return f"{head}: failed in an earlier attempt of this run; not run again. {self.error}"
        if self.reviewed:
            last = self.stages[-1].statement if self.stages else ""
            return f"{head}: reviewed. {last}"
        if RunStatus(self.status) is RunStatus.PENDING:
            return f"{head}: not run — the batch stopped before it started."
        if RunStatus(self.status) is RunStatus.INTERRUPTED:
            return f"{head}: stopped part-way; it will start again from the beginning."
        stage = (self.failed_stage or ORDER[0]).value
        return f"{head}: failed at the {stage} stage. {self.error}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "course_id": self.course_id,
            "status": RunStatus(self.status).value,
            "failed_stage": self.failed_stage.value if self.failed_stage else None,
            "carried_over": self.carried_over,
            "review_path": self.review_path,
            "error": self.error,
            "line": self.line(),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class BatchSummary:
    """What the whole run did, in the register of SPEC §08: counts and plain statements."""

    run_id: str
    status: RunStatus
    courses: tuple[CourseOutcome, ...]
    #: Why the batch stopped early, when it did. Null on a run that reached the last course.
    aborted: str | None = None

    @property
    def reviewed(self) -> tuple[CourseOutcome, ...]:
        return tuple(course for course in self.courses if course.reviewed)

    @property
    def failed(self) -> tuple[CourseOutcome, ...]:
        return tuple(
            course for course in self.courses if RunStatus(course.status) is RunStatus.FAILED
        )

    @property
    def not_run(self) -> tuple[CourseOutcome, ...]:
        return tuple(
            course
            for course in self.courses
            if RunStatus(course.status) in {RunStatus.PENDING, RunStatus.INTERRUPTED}
        )

    def statement(self) -> str:
        parts = [f"{len(self.courses)} course(s) in this run", f"{len(self.reviewed)} reviewed"]
        if self.failed:
            parts.append(f"{len(self.failed)} failed")
        if self.not_run:
            parts.append(f"{len(self.not_run)} not run")
        head = "; ".join(parts) + "."
        return f"{head} The batch stopped early: {self.aborted}" if self.aborted else head

    def lines(self) -> tuple[str, ...]:
        """The summary a person reads: the headline, then one line per course, failures included."""
        return (self.statement(),) + tuple(course.line() for course in self.courses)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": RunStatus(self.status).value,
            "statement": self.statement(),
            "aborted": self.aborted,
            "courses": [course.to_dict() for course in self.courses],
        }


@asynccontextmanager
async def fixed_pipeline(pipeline: CoursePipeline) -> AsyncIterator[CoursePipeline]:
    """A factory over a pipeline that is already built and owns nothing to close."""
    yield pipeline


def pipeline_factory(pipeline: CoursePipeline) -> PipelineFactory:
    """Adapt one ready-made pipeline into a :data:`PipelineFactory`."""
    return lambda _run: fixed_pipeline(pipeline)


class BatchRunner:
    """Executes one run: every course in order, each isolated, all of it resumable."""

    def __init__(self, store: RunStore, paths: DataPaths, factory: PipelineFactory) -> None:
        self.store = store
        self.paths = paths
        self.factory = factory

    # -- the handler the background runner takes ----------------------------------------------

    async def handle(self, run_id: str) -> None:
        """:data:`sightline.runner.RunHandler` shape: run the batch, discard the summary.

        The summary is not lost by being discarded — every line of it is in the run store, which
        is the only copy any screen or restart can read anyway.
        """
        await self.execute(run_id)

    # -- execution ----------------------------------------------------------------------------

    async def execute(self, run_id: str) -> BatchSummary:
        run = self.store.get_run(run_id)
        if run is None:
            raise KeyError(f"no run {run_id!r} in the run store")
        if RunStatus(run.status) is not RunStatus.RUNNING:
            self.store.set_run_status(run_id, RunStatus.RUNNING)

        courses = sorted(run.courses, key=lambda course: course.ordinal)
        resumed = [
            course for course in courses if RunStatus(course.status) is not RunStatus.PENDING
        ]
        self.store.append_event(
            run_id,
            "batch_started",
            courses=len(courses),
            rubric_pack=run.rubric_pack,
            resuming=len(resumed),
        )
        log.info("batch_started", run_id=run_id, courses=len(courses), resuming=len(resumed))

        outcomes: list[CourseOutcome] = []
        aborted: str | None = None
        try:
            async with self.factory(run) as pipeline:
                for course in courses:
                    if aborted is not None:
                        outcomes.append(
                            CourseOutcome(
                                ordinal=course.ordinal,
                                course_id=course.id,
                                status=RunStatus(course.status),
                            )
                        )
                        continue
                    try:
                        outcomes.append(await self._course(pipeline, run, course))
                    except BatchAborted as stop:
                        aborted = stop.message
                        outcomes.append(self._stopped(run, course, stop))
        except BatchAborted as stop:
            # Raised while the pipeline was being built: no credentials, no such rubric pack. No
            # course was touched, so each keeps the status it had and the summary says none ran.
            aborted = stop.message
            self.store.append_event(
                run_id,
                "batch_aborted",
                level="error",
                summary=stop.summary,
                remedy=stop.remedy,
                detail="The batch could not be assembled, so no course was started.",
            )
            log.error("batch_aborted_before_start", run_id=run_id)
            outcomes = [
                CourseOutcome(
                    ordinal=course.ordinal,
                    course_id=course.id,
                    status=RunStatus(course.status),
                )
                for course in courses
            ]

        summary = BatchSummary(
            run_id=run_id,
            status=self._final_status(outcomes, aborted=aborted),
            courses=tuple(outcomes),
            aborted=aborted,
        )
        payload = {key: value for key, value in summary.to_dict().items() if key != "run_id"}
        self.store.append_event(
            run_id,
            "batch_summary",
            level="error" if (summary.failed or summary.aborted) else "info",
            **payload,
        )
        self.store.set_run_status(run_id, summary.status)
        log.info(
            "batch_finished",
            run_id=run_id,
            status=RunStatus(summary.status).value,
            reviewed=len(summary.reviewed),
            failed=len(summary.failed),
            not_run=len(summary.not_run),
        )
        return summary

    # -- one course ---------------------------------------------------------------------------

    async def _course(self, pipeline: CoursePipeline, run: Run, course: RunCourse) -> CourseOutcome:
        carried = self._carried_over(run, course)
        if carried is not None:
            return carried

        status = RunStatus(course.status)
        if status in {RunStatus.RUNNING, RunStatus.INTERRUPTED}:
            # Never a silent skip, and never a resume from the middle: say which stage the earlier
            # attempt stopped in and that this one starts again from the first.
            self.store.append_event(
                run.id,
                "course_restarted",
                course_id=course.id,
                level="warning",
                ordinal=course.ordinal,
                stopped_in=course.stage,
                detail=(
                    f"An earlier attempt stopped during the {course.stage or 'acquire'} stage. "
                    "This course starts again at the first stage; nothing is carried over from "
                    "the attempt that was cut short."
                ),
            )

        work = CourseWork(reference=course.course_ref, course_id=course.id, ordinal=course.ordinal)
        self.store.set_course_status(course.id, RunStatus.RUNNING, stage=ORDER[0].value)
        self.store.append_event(
            run.id, "course_started", course_id=course.id, ordinal=course.ordinal
        )

        stages: list[StageOutcome] = []
        for stage in ORDER:
            self.store.set_course_stage(course.id, stage.value)
            self.store.append_event(run.id, "stage_started", course_id=course.id, stage=stage.value)
            try:
                outcome = await pipeline.run(stage, work)
                if outcome.examined <= 0:
                    # Checked here as well as inside the pipeline, so the invariant belongs to
                    # the loop rather than to each stage's memory of it. Item 06 established it
                    # for one detector: zero examined is never a pass.
                    raise NothingExamined(stage, detail=outcome.statement)
            except BatchAborted:
                raise
            except StageError as exc:
                return self._failed(run, course, stages, stage, exc.summary, exc.remedy)
            except Exception as exc:  # a course's fault, whatever it is; the batch goes on
                log.error(
                    "stage_crashed",
                    run_id=run.id,
                    course_id=course.id,
                    stage=stage.value,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                return self._failed(
                    run,
                    course,
                    stages,
                    stage,
                    f"The {stage.value} stage stopped with an unexpected "
                    f"{type(exc).__name__}: {exc}",
                    "This is a fault in Sightline rather than in the course. The run log carries "
                    "the stage and the error type.",
                )
            stages.append(outcome)
            self.store.append_event(
                run.id,
                "stage_finished",
                course_id=course.id,
                stage=stage.value,
                examined=outcome.examined,
                statement=outcome.statement,
                **{key: value for key, value in outcome.detail.items() if key != "statement"},
            )

        review_path = self._save_review(run, course, pipeline.review(work))
        self.store.set_course_status(course.id, RunStatus.SUCCEEDED, stage=ORDER[-1].value)
        self.store.append_event(
            run.id,
            "course_reviewed",
            course_id=course.id,
            ordinal=course.ordinal,
            review=review_path,
            statement=stages[-1].statement if stages else "",
        )
        return CourseOutcome(
            ordinal=course.ordinal,
            course_id=course.id,
            status=RunStatus.SUCCEEDED,
            stages=tuple(stages),
            review_path=review_path,
        )

    # -- the three ways a course ends without being reviewed now -------------------------------

    def _carried_over(self, run: Run, course: RunCourse) -> CourseOutcome | None:
        """A course an earlier attempt already finished. Reported, never silently skipped."""
        status = RunStatus(course.status)
        if status not in {RunStatus.SUCCEEDED, RunStatus.FAILED}:
            return None
        self.store.append_event(
            run.id,
            "course_carried_over",
            course_id=course.id,
            level="info" if status is RunStatus.SUCCEEDED else "error",
            ordinal=course.ordinal,
            status=status.value,
            detail=(
                "An earlier attempt of this run already finished this course, so it was not run "
                "again. A course that failed stays failed until the run is started afresh — "
                "retrying it here would hide a reason somebody has to read."
            ),
        )
        return CourseOutcome(
            ordinal=course.ordinal,
            course_id=course.id,
            status=status,
            error=course.error,
            carried_over=True,
        )

    def _failed(
        self,
        run: Run,
        course: RunCourse,
        stages: list[StageOutcome],
        stage: Stage,
        summary: str,
        remedy: str | None,
    ) -> CourseOutcome:
        message = " ".join(part for part in (summary, remedy) if part)
        self.store.append_event(
            run.id,
            "stage_failed",
            course_id=course.id,
            stage=stage.value,
            level="error",
            summary=summary,
            remedy=remedy,
        )
        self.store.set_course_status(course.id, RunStatus.FAILED, stage=stage.value, error=message)
        log.warning("course_failed", run_id=run.id, course_id=course.id, stage=stage.value)
        return CourseOutcome(
            ordinal=course.ordinal,
            course_id=course.id,
            status=RunStatus.FAILED,
            stages=tuple(stages),
            failed_stage=stage,
            error=message,
        )

    def _stopped(self, run: Run, course: RunCourse, stop: BatchAborted) -> CourseOutcome:
        """The course that was in flight when the batch aborted. Not its fault, so not failed."""
        stage = (stop.stage or ORDER[0]).value
        self.store.set_course_status(course.id, RunStatus.INTERRUPTED, stage=stage)
        self.store.append_event(
            run.id,
            "batch_aborted",
            course_id=course.id,
            stage=stage,
            level="error",
            summary=stop.summary,
            remedy=stop.remedy,
            detail=(
                "Every remaining course would have failed the same way, so the batch stopped "
                "here rather than spending a timeout on each. Nothing is known to be wrong with "
                "the courses that did not run."
            ),
        )
        log.error("batch_aborted", run_id=run.id, course_id=course.id, stage=stage)
        return CourseOutcome(
            ordinal=course.ordinal,
            course_id=course.id,
            status=RunStatus.INTERRUPTED,
            error=stop.message,
        )

    # -- results -------------------------------------------------------------------------------

    def _save_review(self, run: Run, course: RunCourse, review: Any) -> str:
        """Write the reviewable result under the data root and return its relative path.

        JSON, and under ``runs/`` rather than ``reports/``: this is what the review screen reads,
        not something delivered to anybody. The distinction is the export gate's whole subject.
        """
        payload = json.dumps(review, indent=2, sort_keys=True, default=str)
        parts = ("runs", run.id, "courses", course.id, REVIEW_FILENAME)
        written = self.paths.save_text(*parts, text=payload)
        return str(written.relative_to(self.paths.root))

    def _final_status(self, outcomes: Sequence[CourseOutcome], *, aborted: str | None) -> RunStatus:
        """Succeeded, partial, failed — or interrupted, which is what an abort leaves behind.

        An aborted batch is resumable and its courses are not known to be bad, so it lands in
        ``interrupted`` rather than ``failed``: fixing what the message names and starting the
        process again continues it.
        """
        if aborted is not None:
            return RunStatus.INTERRUPTED
        reviewed = [outcome for outcome in outcomes if outcome.reviewed]
        failed = [outcome for outcome in outcomes if RunStatus(outcome.status) is RunStatus.FAILED]
        if failed and reviewed:
            return RunStatus.PARTIAL
        if failed:
            return RunStatus.FAILED
        return RunStatus.SUCCEEDED


__all__ = [
    "DEFAULT_RUBRIC_PACK",
    "REVIEW_FILENAME",
    "BatchRunner",
    "BatchSummary",
    "BatchTooLargeError",
    "CourseOutcome",
    "PipelineFactory",
    "create_batch_run",
    "fixed_pipeline",
    "pipeline_factory",
]
