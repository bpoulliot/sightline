"""The run-progress screens (roadmap item 36): the recent runs, and a page per run.

A run page is :func:`sightline.batch.progress_of` rendered: per course and per stage, what each
stage examined and the statement it made, never a percentage (SPEC §09). A count of courses
reviewed is a count; a completion bar would be a guess about how long the stages still to come
will take, and those vary by an order of magnitude with the model and the course.

**Failures are on the page with their remedies, never only in a log.** A failed stage shows its
summary and what to change; a failure that belongs to the run rather than to a course — the batch
could not be assembled, the task raised — comes from the run log and is shown above the courses.
An **interrupted** run says so and names ``python -m sightline.batch resume <id>``. There is no
resume button: the application already continues what it was running when it restarts, and a
button would be a second way to start a batch that the one-run-at-a-time check does not see.

**The live log is the page re-read.** The page is rendered from the store on every request, as
the model-install progress on the settings screen is. While a run is going, an optional script
polls ``GET /api/runs/{run_id}`` and reloads the page when the answer changes; with scripting off,
reloading does the same thing.

**Beside every course, the way to its review and the state of its export.** A course with a result
links to the review screen (item 16), whose first page records that a person opened it. Until
somebody has, the export control is shown closed, with the export gate's own reason
(:meth:`sightline.batch.gate.ExportGate.require_open`) — the gate's words, not a paraphrase of
them. This page never opens a review and never exports.

Nothing here logs a course reference (item 17). The page shows each course's reference because
the person reading it is the person who typed it, the same decision the course screen makes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from .batch import (
    CourseProgress,
    ExportGate,
    ReviewNotOpenedError,
    RunProgress,
    Stage,
    StageState,
    progress_of,
)
from .models import STATUS_LABELS, RunEvent, RunStatus, as_utc
from .new_run import is_running, short_id
from .paths import DataPaths
from .review.screen import index_href
from .store import RunStore

#: How many runs the list shows. The store holds every run; this is a screen, not an archive.
RUN_LIST_LIMIT = 25

#: How many log lines a run page shows, newest first.
LOG_LIMIT = 60

#: How often the optional script asks whether anything changed, in milliseconds.
POLL_MS = 5000

#: What each run-log event is called on the page. An event this table does not name is shown by
#: its own name rather than dropped: a log that hides what it does not recognise is not a log.
EVENT_LABELS: dict[str, str] = {
    "run_created": "Run recorded",
    "run_status": "Run status",
    "run_interrupted": "Run interrupted",
    "run_failed": "Run failed",
    "batch_started": "Batch started",
    "batch_aborted": "Batch stopped",
    "batch_summary": "Batch finished",
    "course_started": "Course started",
    "course_status": "Course status",
    "course_restarted": "Course restarted",
    "course_carried_over": "Carried over",
    "course_reviewed": "Course reviewed",
    "stage_started": "Stage started",
    "stage_finished": "Stage finished",
    "stage_failed": "Stage failed",
    "review_opened": "Review opened",
}

#: Run-log events that are a failure of the run itself rather than of one course.
RUN_FAILURES = frozenset({"batch_aborted", "run_failed"})


@dataclass(frozen=True, kw_only=True, slots=True)
class ExportState:
    """Whether a course's export is open, and the words that say why not."""

    open: bool
    summary: str | None = None
    remedy: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class CourseCard:
    progress: CourseProgress
    review_href: str | None
    export: ExportState

    @property
    def number(self) -> int:
        return self.progress.ordinal + 1

    @property
    def stage_title(self) -> str | None:
        """The stage a running course is in, by its title."""
        return _stage_title(self.progress.stage)

    @property
    def has_failed_stage(self) -> bool:
        return any(stage.state is StageState.FAILED for stage in self.progress.stages)


@dataclass(frozen=True, kw_only=True, slots=True)
class Failure:
    """A failure of the run, as the log recorded it."""

    summary: str
    remedy: str | None
    at: datetime | None


@dataclass(frozen=True, kw_only=True, slots=True)
class LogLine:
    at: datetime | None
    level: str
    event: str
    course: int | None
    stage: str | None
    detail: str | None


@dataclass(frozen=True, kw_only=True, slots=True)
class RunPage:
    progress: RunProgress
    courses: tuple[CourseCard, ...]
    failures: tuple[Failure, ...]
    log: tuple[LogLine, ...]
    note: str | None

    @property
    def short_id(self) -> str:
        return short_id(self.progress.run_id)

    @property
    def live(self) -> bool:
        return is_running(self.progress.status)

    @property
    def interrupted(self) -> bool:
        return RunStatus(self.progress.status) is RunStatus.INTERRUPTED

    @property
    def resume_command(self) -> str:
        return f"python -m sightline.batch resume {self.progress.run_id}"


def build_run_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/runs", include_in_schema=False)
    async def run_list(request: Request) -> Any:
        store = _store(request)
        runs = store.list_runs(limit=RUN_LIST_LIMIT)
        rows = [
            {
                "run": run,
                "short_id": short_id(run.id),
                "reviewed": sum(
                    1 for course in run.courses if RunStatus(course.status) is RunStatus.SUCCEEDED
                ),
                "failed": sum(
                    1 for course in run.courses if RunStatus(course.status) is RunStatus.FAILED
                ),
                "state_class": state_class(run.status),
            }
            for run in runs
        ]
        return templates.TemplateResponse(
            request=request,
            name="runs.html",
            context={"nav": "runs", "rows": rows, "limit": RUN_LIST_LIMIT},
        )

    @router.get("/runs/{run_id}", include_in_schema=False)
    async def run_page(request: Request, run_id: str) -> Any:
        store = _store(request)
        progress = progress_of(store, run_id)
        if progress is None:
            return templates.TemplateResponse(
                request=request,
                name="run_missing.html",
                context={"nav": "runs", "run_id": run_id},
                status_code=404,
            )
        page = build_page(store, _paths(request), progress)
        return templates.TemplateResponse(
            request=request,
            name="run.html",
            context={
                "nav": "runs",
                "page": page,
                "payload": progress.to_dict(),
                "poll_ms": POLL_MS,
                "state_class": state_class,
            },
        )

    return router


# ---------------------------------------------------------------------------------------------
# Assembling a page
# ---------------------------------------------------------------------------------------------


def build_page(store: RunStore, paths: DataPaths, progress: RunProgress) -> RunPage:
    gate = ExportGate(store, paths)
    events = store.list_events(progress.run_id, limit=LOG_LIMIT)
    ordinals = {course.course_id: course.ordinal for course in progress.courses}
    run = store.get_run(progress.run_id)
    return RunPage(
        progress=progress,
        courses=tuple(_card(gate, course) for course in progress.courses),
        failures=_run_failures(store, progress.run_id),
        log=tuple(_line(event, ordinals) for event in events),
        note=run.note if run is not None else None,
    )


def _card(gate: ExportGate, course: CourseProgress) -> CourseCard:
    return CourseCard(
        progress=course,
        review_href=index_href(course.course_id) if course.reviewable else None,
        export=export_state(gate, course),
    )


def export_state(gate: ExportGate, course: CourseProgress) -> ExportState:
    """Open once a person has opened the review; otherwise closed, in the gate's own words.

    The gate's remedy is to open the review. A course with no result has no review to open, so it
    is given the remedy that is true for it instead, and keeps the gate's summary.
    """
    try:
        gate.require_open(course.course_id)
    except ReviewNotOpenedError as exc:
        if course.reviewable:
            return ExportState(open=False, summary=exc.summary, remedy=exc.remedy)
        return ExportState(open=False, summary=exc.summary, remedy=_no_result(course))
    return ExportState(open=True)


def _no_result(course: CourseProgress) -> str:
    status = RunStatus(course.status)
    if status is RunStatus.FAILED:
        return (
            "This course has no review to open: it failed before one was written. The failed "
            "stage above says what to change."
        )
    if status is RunStatus.INTERRUPTED:
        return (
            "This course has no review to open yet: the run stopped before it was finished. It "
            "starts again from its first stage when the run continues."
        )
    return "This course has no review to open yet: it has not been reviewed."


def _run_failures(store: RunStore, run_id: str) -> tuple[Failure, ...]:
    """Failures the log records against the run itself, oldest first."""
    failures: list[Failure] = []
    for event in store.events_in_order(run_id):
        if event.course_id or event.event not in RUN_FAILURES:
            continue
        data = event.data or {}
        summary = data.get("summary") or data.get("error") or EVENT_LABELS[event.event]
        failures.append(
            Failure(summary=str(summary), remedy=data.get("remedy"), at=as_utc(event.ts))
        )
    return tuple(failures)


def _line(event: RunEvent, ordinals: dict[str, int]) -> LogLine:
    return LogLine(
        at=as_utc(event.ts),
        level=event.level,
        event=EVENT_LABELS.get(event.event, event.event),
        course=ordinals[event.course_id] + 1 if event.course_id in ordinals else None,
        stage=_stage_title(event.stage),
        detail=_detail(event),
    )


def _stage_title(value: str | None) -> str | None:
    """A stage by its title. The stored name of the last one is not a word this screen uses."""
    if not value:
        return None
    try:
        return Stage(value).title
    except ValueError:  # a stage name from an older build; shown as stored, not guessed at
        return value


def _detail(event: RunEvent) -> str | None:
    """The one line of an event's data a person needs: its statement, failure or new status."""
    data: dict[str, Any] = event.data or {}
    if event.event in {"run_status", "course_status"} and data.get("status"):
        label = STATUS_LABELS.get(RunStatus(data["status"]), data["status"])
        return " ".join(part for part in (label + ".", data.get("error")) if part)
    for key in ("statement", "summary", "error", "reason"):
        if data.get(key):
            text = str(data[key])
            if key == "summary" and data.get("remedy"):
                text = f"{text} {data['remedy']}"
            return text
    if event.event == "run_created":
        return f"{data.get('courses')} course(s) against {data.get('rubric_pack')}."
    return None


def state_class(status: str | RunStatus) -> str:
    """The chip a status is shown in. Colour follows what somebody has to do, not a quality."""
    value = RunStatus(status)
    if value in {RunStatus.SUCCEEDED}:
        return "state-on"
    if value in {RunStatus.PARTIAL, RunStatus.FAILED}:
        return "state-bad"
    if value in {RunStatus.INTERRUPTED, RunStatus.CANCELLED}:
        return "state-off"
    return ""


def _store(request: Request) -> RunStore:
    store: RunStore | None = getattr(request.app.state, "store", None)
    if store is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the run store is not initialised")
    return store


def _paths(request: Request) -> DataPaths:
    paths: DataPaths | None = getattr(request.app.state, "paths", None)
    if paths is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the data paths are not initialised")
    return paths


__all__ = [
    "EVENT_LABELS",
    "CourseCard",
    "ExportState",
    "RunPage",
    "build_page",
    "build_run_router",
    "export_state",
    "state_class",
]
