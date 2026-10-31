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

**A queued run says how many runs are ahead of it** (roadmap item 46): *Queued, N ahead* on its
page and in the list, a count and never an estimate of when it will start. Its page carries a
*Cancel this run* button; the cancel answers only the starter or an admin — anybody else gets the
404 every other route gives them — and records who cancelled it on the run, not in its log. Only
a run still waiting can be cancelled; once it has started, it runs to its end.

**A run is its starter's** (roadmap item 45). In sign-in mode the list holds only the runs the
signed-in person started — every run, for an admin, with who started each — and a run page for
anybody else's run answers 404 with the same page as a run that does not exist.

Nothing here logs a course reference (item 17). The page shows each course's reference because
the person reading it is the person who typed it, the same decision the course screen makes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .access import LOCAL, Viewer, viewer, visible_run
from .batch import (
    CourseProgress,
    ExportGate,
    ReviewNotOpenedError,
    RunProgress,
    Stage,
    StageState,
    progress_of,
)
from .logging import get_logger
from .models import STATUS_LABELS, TERMINAL_STATUSES, Run, RunEvent, RunStatus, as_utc
from .new_run import is_queued, is_running, short_id
from .paths import DataPaths
from .review.identity import current_reviewer
from .review.screen import index_href
from .runner import BackgroundRunner
from .store import RunStore

log = get_logger("sightline.run_screens")

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
    "run_dequeued": "Run left the queue",
    "run_cancelled": "Run cancelled",
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
    #: Roadmap item 46: how many runs are ahead of a queued run; ``None`` for any other run.
    ahead: int | None = None
    #: Who cancelled it while it was queued, as this reader is told; ``None`` otherwise.
    cancelled_by: str | None = None

    @property
    def short_id(self) -> str:
        return short_id(self.progress.run_id)

    @property
    def live(self) -> bool:
        return is_running(self.progress.status)

    @property
    def queued(self) -> bool:
        return is_queued(self.progress.status)

    @property
    def status_label(self) -> str:
        return queue_label(self.progress.status_label, self.ahead)

    @property
    def finished(self) -> bool:
        """Over, one way or another. A run recorded before item 42 may carry no finish time."""
        return RunStatus(self.progress.status) in TERMINAL_STATUSES

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
        who = viewer(request)
        runs = store.list_runs(limit=RUN_LIST_LIMIT, starter_subject=who.runs_filter)
        positions = _positions(request, runs)
        rows = [
            {
                "run": run,
                "short_id": short_id(run.id),
                "status_label": queue_label(run.status_label, positions.get(run.id)),
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
            context={
                "nav": "runs",
                "rows": rows,
                "limit": RUN_LIST_LIMIT,
                # Sign-in mode (item 45): whose runs these are, and for an admin who started each.
                "signed_in": who.signed_in,
                "admin": who.admin,
            },
        )

    @router.get("/runs/{run_id}", include_in_schema=False)
    async def run_page(request: Request, run_id: str) -> Any:
        store = _store(request)
        who = viewer(request)
        visible = visible_run(request, store, run_id)
        progress = progress_of(store, run_id) if visible is not None else None
        if progress is None:
            return templates.TemplateResponse(
                request=request,
                name="run_missing.html",
                context={"nav": "runs", "run_id": run_id, "signed_in": who.signed_in},
                status_code=404,
            )
        ahead = _positions(request, [visible]).get(run_id) if visible is not None else None
        page = build_page(store, _paths(request), progress, ahead=ahead, reader=who)
        return templates.TemplateResponse(
            request=request,
            name="run.html",
            context={
                "nav": "runs",
                "page": page,
                "payload": payload_of(progress.to_dict(), ahead),
                "poll_ms": POLL_MS,
                "state_class": state_class,
                # Item 45: an admin reading somebody's run is told whose it is.
                "starter": _starter(visible) if who.signed_in and who.admin else None,
            },
        )

    @router.post("/runs/{run_id}/cancel", include_in_schema=False)
    async def cancel_run(request: Request, run_id: str) -> Any:
        """Cancel a queued run: its starter or an admin; anybody else is told it does not exist.

        Only a run still waiting. The cancel is a compare-and-set against the queue taking the
        run, so a run whose turn came a moment earlier is started, not cancelled, and the page
        says which.
        """
        store = _store(request)
        who = viewer(request)
        run = visible_run(request, store, run_id)
        if run is None:
            return templates.TemplateResponse(
                request=request,
                name="run_missing.html",
                context={"nav": "runs", "run_id": run_id, "signed_in": who.signed_in},
                status_code=404,
            )
        reviewer = current_reviewer(request)
        cancelled = is_queued(run.status) and store.cancel_queued(
            run_id,
            subject=who.subject,
            name=reviewer.name if reviewer is not None else who.name,
        )
        if not cancelled:
            current = store.get_run(run_id)
            label = current.status_label if current is not None else run.status_label
            return templates.TemplateResponse(
                request=request,
                name="review_message.html",
                context={
                    "nav": "runs",
                    "summary": (
                        f"Run {short_id(run_id)} was not cancelled: it is {label.lower()}, not "
                        "queued. Only a run still waiting in the queue can be cancelled."
                    ),
                    "remedy": "A run that has started goes on to its end. Its page shows how far "
                    "it has got.",
                    "back": f"Back to run {short_id(run_id)}",
                    "back_href": f"/runs/{run_id}",
                },
                status_code=409,
            )
        log.info("run_cancelled_from_screen", run_id=run_id)
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    return router


# ---------------------------------------------------------------------------------------------
# The queue (roadmap item 46)
# ---------------------------------------------------------------------------------------------


def queue_label(label: str, ahead: int | None) -> str:
    """*Queued, N ahead* for a queued run; any other status's label as it is."""
    return label if ahead is None else f"{label}, {ahead} ahead"


def payload_of(payload: dict[str, Any], ahead: int | None) -> dict[str, Any]:
    """A run's progress with its place in the queue, so a page that polls reloads when it moves."""
    if ahead is None:
        return payload
    return {**payload, "queue_ahead": ahead}


def queue_positions(request: Request) -> dict[str, int]:
    """Runs ahead of each queued run, by id, as this process's runner counts them."""
    runner: BackgroundRunner | None = getattr(request.app.state, "runner", None)
    return runner.queue_positions() if runner is not None else {}


def _positions(request: Request, runs: list[Run]) -> dict[str, int]:
    if not any(is_queued(run.status) for run in runs):
        return {}
    return queue_positions(request)


def cancelled_by(run: Run, reader: Viewer) -> str:
    """Who cancelled a queued run, as ``reader`` is told it.

    The record keeps the subject and name. The reader who did it reads *you*; an admin reads the
    name; a starter whose run an admin cancelled — nobody else may — reads *an admin*, as the
    starter's own name is not shown to other people either.
    """
    if reader.signed_in and run.cancelled_by_subject is not None:
        if run.cancelled_by_subject == reader.subject:
            return "you"
        if not reader.admin:
            return "an admin"
    return run.cancelled_by_name or run.cancelled_by_subject or "the local user"


# ---------------------------------------------------------------------------------------------
# Assembling a page
# ---------------------------------------------------------------------------------------------


def _starter(run: Run | None) -> str:
    """Who started a run, for an admin; a run with no starter recorded says so."""
    if run is None or run.starter_subject is None:
        return "nobody recorded — started on the command line or before runs recorded a starter"
    return run.starter_name or run.starter_subject


def build_page(
    store: RunStore,
    paths: DataPaths,
    progress: RunProgress,
    *,
    ahead: int | None = None,
    reader: Viewer | None = None,
) -> RunPage:
    gate = ExportGate(store, paths)
    events = store.list_events(progress.run_id, limit=LOG_LIMIT)
    ordinals = {course.course_id: course.ordinal for course in progress.courses}
    run = store.get_run(progress.run_id)
    was_cancelled_queued = run is not None and any(
        event.event == "run_cancelled" for event in store.events_in_order(progress.run_id)
    )
    return RunPage(
        progress=progress,
        courses=tuple(_card(gate, course) for course in progress.courses),
        failures=_run_failures(store, progress.run_id),
        log=tuple(_line(event, ordinals) for event in events),
        note=run.note if run is not None else None,
        ahead=ahead if is_queued(progress.status) else None,
        cancelled_by=(
            cancelled_by(run, reader or LOCAL) if run is not None and was_cancelled_queued else None
        ),
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
    "cancelled_by",
    "payload_of",
    "queue_label",
    "queue_positions",
    "CourseCard",
    "ExportState",
    "RunPage",
    "build_page",
    "build_run_router",
    "export_state",
    "state_class",
]
