"""Reading a run back: where every course is, which stage it is in, and what failed.

SPEC §09 asks for progress *per course and per stage*, with failures visible and never silent.
The run store already holds it — statuses on ``run_courses``, an append-only log in
``run_events`` — so this module is a read model over the two and adds no state of its own. That
matters for the restart story: progress after a kill is whatever the database says, because there
is nowhere else it could have been.

Stage state is replayed from the log **oldest first**. ``stage_started`` moves a stage to running,
``stage_finished`` to done with the count it examined, ``stage_failed`` to failed with what it
said. A course restarted after an interruption replays over its earlier attempt, which is the
right reading: the earlier attempt's stages did happen, and the run log still carries them.

**A course reference is not written to a log line anywhere in this package**, because a Canvas
course URL carries the host and SPEC §11 treats that host as a secret. It is carried here, on a
surface a designer is reading back what they themselves typed — the same decision the courses
screen already makes — and :meth:`sightline.store.RunStore.append_event` scrubs it out of the
stored events.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from ..models import STATUS_LABELS, RunStatus, as_utc
from ..store import RunStore
from .stages import ORDER, Stage


class StageState(StrEnum):
    """Where one stage of one course stands."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, kw_only=True, slots=True)
class StageProgress:
    stage: Stage
    state: StageState
    #: What the stage examined, in its own unit. ``None`` until it has finished.
    examined: int | None = None
    statement: str | None = None

    def line(self) -> str:
        head = f"{self.stage.value:<8} {self.state.value}"
        if self.statement:
            return f"{head}  {self.statement}"
        return head

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "state": self.state.value,
            "examined": self.examined,
            "statement": self.statement,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class CourseProgress:
    ordinal: int
    course_id: str
    reference: str
    status: RunStatus
    stage: str | None
    error: str | None
    stages: tuple[StageProgress, ...]
    review_opened_at: datetime | None = None

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(RunStatus(self.status), RunStatus(self.status).value)

    @property
    def reviewable(self) -> bool:
        """Whether there is a result for a person to open. Not whether anything may be exported."""
        return RunStatus(self.status) is RunStatus.SUCCEEDED

    @property
    def review_opened(self) -> bool:
        return self.review_opened_at is not None

    def line(self) -> str:
        head = f"Course {self.ordinal + 1} — {self.reference}: {self.status_label.lower()}"
        if self.error:
            return f"{head}. {self.error}"
        return f"{head}."

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "course_id": self.course_id,
            "reference": self.reference,
            "status": RunStatus(self.status).value,
            "status_label": self.status_label,
            "stage": self.stage,
            "error": self.error,
            "reviewable": self.reviewable,
            "review_opened": self.review_opened,
            "review_opened_at": (
                self.review_opened_at.isoformat() if self.review_opened_at else None
            ),
            "stages": [stage.to_dict() for stage in self.stages],
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class RunProgress:
    run_id: str
    status: RunStatus
    rubric_pack: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    courses: tuple[CourseProgress, ...]

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(RunStatus(self.status), RunStatus(self.status).value)

    def statement(self) -> str:
        reviewed = sum(1 for course in self.courses if course.reviewable)
        failed = sum(1 for course in self.courses if RunStatus(course.status) is RunStatus.FAILED)
        parts = [
            f"{len(self.courses)} course(s)",
            f"{reviewed} reviewed",
        ]
        if failed:
            parts.append(f"{failed} failed")
        opened = sum(1 for course in self.courses if course.review_opened)
        parts.append(f"{opened} review(s) opened")
        return f"{self.status_label}: " + ", ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": RunStatus(self.status).value,
            "status_label": self.status_label,
            "statement": self.statement(),
            "rubric_pack": self.rubric_pack,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "courses": [course.to_dict() for course in self.courses],
        }


def progress_of(store: RunStore, run_id: str) -> RunProgress | None:
    """Assemble one run's progress, or ``None`` when there is no such run."""
    run = store.get_run(run_id)
    if run is None:
        return None
    replayed = _replay(store, run_id)
    courses = tuple(
        CourseProgress(
            ordinal=course.ordinal,
            course_id=course.id,
            reference=course.course_ref,
            status=RunStatus(course.status),
            stage=course.stage,
            error=course.error,
            review_opened_at=as_utc(course.review_opened_at),
            stages=_stages_of(replayed.get(course.id, {})),
        )
        for course in sorted(run.courses, key=lambda course: course.ordinal)
    )
    return RunProgress(
        run_id=run.id,
        status=RunStatus(run.status),
        rubric_pack=run.rubric_pack,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        courses=courses,
    )


def _replay(store: RunStore, run_id: str) -> dict[str, dict[str, StageProgress]]:
    """Fold the run log into per-course, per-stage state. Later events win."""
    folded: dict[str, dict[str, StageProgress]] = {}
    for event in store.events_in_order(run_id):
        if not event.course_id or not event.stage:
            continue
        try:
            stage = Stage(event.stage)
        except ValueError:  # a stage name from an older build; carried, not guessed at
            continue
        data: Mapping[str, Any] = event.data or {}
        if event.event == "stage_started":
            entry = StageProgress(stage=stage, state=StageState.RUNNING)
        elif event.event == "stage_finished":
            entry = StageProgress(
                stage=stage,
                state=StageState.DONE,
                examined=data.get("examined"),
                statement=data.get("statement"),
            )
        elif event.event in {"stage_failed", "batch_aborted"}:
            entry = StageProgress(
                stage=stage,
                state=StageState.FAILED,
                statement=" ".join(
                    part for part in (data.get("summary"), data.get("remedy")) if part
                ),
            )
        else:
            continue
        folded.setdefault(event.course_id, {})[stage.value] = entry
    return folded


def _stages_of(seen: Mapping[str, StageProgress]) -> tuple[StageProgress, ...]:
    """Every stage in pipeline order, including the ones that have not started.

    A stage nobody has reached is reported as pending rather than left out. A progress view that
    lists only what has happened cannot be read as "three of five", which is the question somebody
    watching a sixteen-course batch is actually asking.
    """
    return tuple(
        seen.get(stage.value, StageProgress(stage=stage, state=StageState.PENDING))
        for stage in ORDER
    )


__all__ = [
    "CourseProgress",
    "RunProgress",
    "StageProgress",
    "StageState",
    "progress_of",
]
