"""The batch loop: one course failing does not take the batch with it, and a kill resumes.

Driven by a scripted pipeline rather than by Canvas and a GPU. What is under test here is the
*loop* — order, isolation, resume, the events every progress surface reads — and a test of that
which needed a model host is a test that does not run in CI. The real pipeline has its own module
(``tests/test_batch_pipeline.py``), which runs it against recorded payloads.
"""

from __future__ import annotations

import asyncio
import io
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

from sightline.batch import (
    BatchAborted,
    BatchRunner,
    BatchTooLargeError,
    CourseWork,
    Stage,
    StageError,
    StageOutcome,
    create_batch_run,
    pipeline_factory,
)
from sightline.batch.stages import ORDER
from sightline.logging import configure_logging
from sightline.models import RunStatus
from sightline.paths import DataPaths
from sightline.store import RunStore

PACK = "uccs-oscqr-3-1-3"


@pytest.fixture
def store(paths: DataPaths) -> Iterator[RunStore]:
    opened = RunStore.open(paths)
    yield opened
    opened.dispose()


@dataclass
class ScriptedPipeline:
    """Every stage succeeds, except where a test says otherwise.

    ``fail_at`` is keyed on the course reference so a test can name *which* course misbehaves,
    which is the whole subject of per-course isolation.
    """

    fail_at: dict[str, tuple[Stage, Exception]] = field(default_factory=dict)
    examined: dict[str, int] = field(default_factory=dict)
    #: Every (reference, stage) pair this pipeline was asked for, in order.
    seen: list[tuple[str, Stage]] = field(default_factory=list)

    async def run(self, stage: Stage, work: CourseWork) -> StageOutcome:
        self.seen.append((work.reference, stage))
        planned = self.fail_at.get(work.reference)
        if planned is not None and planned[0] is stage:
            raise planned[1]
        count = self.examined.get(work.reference, 4)
        return work.record(
            StageOutcome(
                stage=stage,
                examined=count,
                statement=f"{stage.value}: examined {count} thing(s).",
                detail={"stage_detail": stage.value},
            )
        )

    def review(self, work: CourseWork) -> Mapping[str, Any]:
        return {
            "course": {"reference": work.reference},
            "stages": [outcome.to_dict() for outcome in work.outcomes.values()],
        }


def _batch(store: RunStore, paths: DataPaths, pipeline: ScriptedPipeline) -> BatchRunner:
    return BatchRunner(store, paths, pipeline_factory(pipeline))


def _statuses(store: RunStore, run_id: str) -> list[RunStatus]:
    run = store.get_run(run_id)
    assert run is not None
    return [RunStatus(course.status) for course in sorted(run.courses, key=lambda c: c.ordinal)]


def _event_names(store: RunStore, run_id: str) -> list[str]:
    return [event.event for event in store.events_in_order(run_id)]


# ---------------------------------------------------------------------------------------------
# Per-course isolation
# ---------------------------------------------------------------------------------------------


async def test_the_middle_course_failing_leaves_the_other_two_reviewed(
    store: RunStore, paths: DataPaths
) -> None:
    """The acceptance case: three courses, the middle one fails, and the batch finishes."""
    run_id = create_batch_run(store, ["one", "two", "three"], rubric_pack=PACK)
    pipeline = ScriptedPipeline(
        fail_at={
            "two": (
                Stage.DETECT,
                StageError(
                    Stage.DETECT,
                    "The course export never arrived.",
                    remedy="Ask Canvas for a fresh one.",
                ),
            )
        }
    )

    summary = await _batch(store, paths, pipeline).execute(run_id)

    assert [outcome.ordinal for outcome in summary.reviewed] == [0, 2]
    assert [outcome.ordinal for outcome in summary.failed] == [1]
    assert _statuses(store, run_id) == [
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.SUCCEEDED,
    ]
    run = store.get_run(run_id)
    assert run is not None and RunStatus(run.status) is RunStatus.PARTIAL

    # The summary says plainly which course failed and why, in the lines a person reads.
    lines = summary.lines()
    assert lines[0] == "3 course(s) in this run; 2 reviewed; 1 failed."
    assert "Course 2: failed at the detect stage." in lines[2]
    assert "The course export never arrived." in lines[2]
    assert "Ask Canvas for a fresh one." in lines[2]

    # And the third course ran after the second failed, rather than the batch stopping there.
    assert ("three", Stage.SCORE) in pipeline.seen


async def test_an_unexpected_exception_is_contained_like_any_other_failure(
    store: RunStore, paths: DataPaths
) -> None:
    """A parser that raises something nobody anticipated costs one course, not the batch."""
    run_id = create_batch_run(store, ["one", "two"], rubric_pack=PACK)
    pipeline = ScriptedPipeline(fail_at={"one": (Stage.PROJECT, ValueError("odd markup"))})

    summary = await _batch(store, paths, pipeline).execute(run_id)

    assert [outcome.ordinal for outcome in summary.failed] == [0]
    assert [outcome.ordinal for outcome in summary.reviewed] == [1]
    failure = summary.failed[0].error or ""
    assert "unexpected ValueError" in failure
    assert "odd markup" in failure


async def test_a_stage_that_examined_nothing_fails_the_course(
    store: RunStore, paths: DataPaths
) -> None:
    """Zero examined is never a pass — the invariant item 06 established, at stage scale."""
    run_id = create_batch_run(store, ["one", "two"], rubric_pack=PACK)
    pipeline = ScriptedPipeline(examined={"two": 0})

    summary = await _batch(store, paths, pipeline).execute(run_id)

    assert [outcome.ordinal for outcome in summary.failed] == [1]
    assert "examined nothing" in (summary.failed[0].error or "")
    assert "not a clean course" in (summary.failed[0].error or "")


async def test_every_course_failing_is_a_failed_run_not_a_partial_one(
    store: RunStore, paths: DataPaths
) -> None:
    run_id = create_batch_run(store, ["one", "two"], rubric_pack=PACK)
    pipeline = ScriptedPipeline(
        fail_at={
            "one": (Stage.ACQUIRE, StageError(Stage.ACQUIRE, "gone")),
            "two": (Stage.ACQUIRE, StageError(Stage.ACQUIRE, "gone")),
        }
    )

    summary = await _batch(store, paths, pipeline).execute(run_id)

    run = store.get_run(run_id)
    assert run is not None and RunStatus(run.status) is RunStatus.FAILED
    assert RunStatus(summary.status) is RunStatus.FAILED


async def test_a_clean_batch_succeeds(store: RunStore, paths: DataPaths) -> None:
    run_id = create_batch_run(store, ["one", "two"], rubric_pack=PACK)
    summary = await _batch(store, paths, ScriptedPipeline()).execute(run_id)
    run = store.get_run(run_id)
    assert run is not None and RunStatus(run.status) is RunStatus.SUCCEEDED
    assert len(summary.reviewed) == 2 and not summary.failed


# ---------------------------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------------------------


async def test_a_killed_batch_resumes_without_redoing_or_skipping(
    store: RunStore, paths: DataPaths
) -> None:
    """Kill the process mid-batch, start again: it continues rather than restarting or skipping.

    The kill is a ``CancelledError`` raised inside a stage, which is literally what a shutdown
    does to the task a run executes in.
    """
    run_id = create_batch_run(store, ["one", "two", "three"], rubric_pack=PACK)
    killing = ScriptedPipeline(fail_at={"two": (Stage.EXTRACT, asyncio.CancelledError())})

    with pytest.raises(asyncio.CancelledError):
        await _batch(store, paths, killing).execute(run_id)

    # What a restart finds: course one finished, course two mid-flight, course three untouched.
    assert _statuses(store, run_id) == [RunStatus.SUCCEEDED, RunStatus.RUNNING, RunStatus.PENDING]
    store.recover_interrupted()
    assert _statuses(store, run_id) == [
        RunStatus.SUCCEEDED,
        RunStatus.INTERRUPTED,
        RunStatus.PENDING,
    ]

    resumed = ScriptedPipeline()
    summary = await _batch(store, paths, resumed).execute(run_id)

    # Course one is not run again, and is not silently absent either.
    assert [reference for reference, _stage in resumed.seen if reference == "one"] == []
    carried = [outcome for outcome in summary.courses if outcome.carried_over]
    assert [outcome.ordinal for outcome in carried] == [0]
    assert "reviewed by an earlier attempt" in carried[0].line()

    # Course two starts again from the first stage, and the log says so rather than implying it.
    assert [stage for reference, stage in resumed.seen if reference == "two"] == list(ORDER)
    restarted = [
        event for event in store.events_in_order(run_id) if event.event == "course_restarted"
    ]
    assert len(restarted) == 1
    assert restarted[0].data["stopped_in"] == Stage.EXTRACT.value

    run = store.get_run(run_id)
    assert run is not None and RunStatus(run.status) is RunStatus.SUCCEEDED
    assert _statuses(store, run_id) == [
        RunStatus.SUCCEEDED,
        RunStatus.SUCCEEDED,
        RunStatus.SUCCEEDED,
    ]


async def test_a_course_that_failed_stays_failed_on_a_resume(
    store: RunStore, paths: DataPaths
) -> None:
    """Resuming does not retry a course that failed: the reason is still the reason."""
    run_id = create_batch_run(store, ["one", "two"], rubric_pack=PACK)
    first = ScriptedPipeline(
        fail_at={
            "one": (Stage.ACQUIRE, StageError(Stage.ACQUIRE, "Canvas answered 404.")),
            "two": (Stage.SCORE, asyncio.CancelledError()),
        }
    )
    with pytest.raises(asyncio.CancelledError):
        await _batch(store, paths, first).execute(run_id)
    store.recover_interrupted()

    second = ScriptedPipeline()
    summary = await _batch(store, paths, second).execute(run_id)

    assert [reference for reference, _stage in second.seen if reference == "one"] == []
    failed = summary.failed[0]
    assert failed.carried_over is True
    assert "Canvas answered 404." in (failed.error or "")
    assert "failed in an earlier attempt" in failed.line()
    assert RunStatus(summary.status) is RunStatus.PARTIAL


# ---------------------------------------------------------------------------------------------
# The batch's shared dependency failing is not a course failing
# ---------------------------------------------------------------------------------------------


async def test_an_unreachable_model_host_stops_the_batch_and_names_what_did_not_run(
    store: RunStore, paths: DataPaths
) -> None:
    run_id = create_batch_run(store, ["one", "two", "three"], rubric_pack=PACK)
    pipeline = ScriptedPipeline(
        fail_at={
            "two": (
                Stage.EXTRACT,
                BatchAborted(
                    "The model host stopped answering.",
                    remedy="Check the endpoint on the Settings screen.",
                    stage=Stage.EXTRACT,
                ),
            )
        }
    )

    summary = await _batch(store, paths, pipeline).execute(run_id)

    assert summary.aborted is not None
    assert "The model host stopped answering." in summary.aborted
    assert [outcome.ordinal for outcome in summary.reviewed] == [0]
    assert [outcome.ordinal for outcome in summary.not_run] == [1, 2]
    assert "not run" in summary.statement()
    # Course three was never started: the batch did not spend a timeout on it.
    assert [reference for reference, _stage in pipeline.seen if reference == "three"] == []

    # The run is interrupted rather than failed — nothing is known to be wrong with the courses,
    # and fixing the host and starting again continues it.
    run = store.get_run(run_id)
    assert run is not None and RunStatus(run.status) is RunStatus.INTERRUPTED
    assert [resumable.id for resumable in store.resumable_runs()] == [run_id]

    resumed = ScriptedPipeline()
    summary = await _batch(store, paths, resumed).execute(run_id)
    # The summary describes the run and not this attempt of it, so the course an earlier attempt
    # reviewed is still counted as reviewed — and is marked as carried over rather than re-run.
    assert [outcome.ordinal for outcome in summary.reviewed] == [0, 1, 2]
    assert [outcome.ordinal for outcome in summary.courses if outcome.carried_over] == [0]
    assert [reference for reference, _stage in resumed.seen if reference == "one"] == []
    assert RunStatus(summary.status) is RunStatus.SUCCEEDED


async def test_a_batch_that_cannot_be_assembled_starts_no_course(
    store: RunStore, paths: DataPaths
) -> None:
    """No credentials, no such pack: every course keeps its status and the summary says so."""
    run_id = create_batch_run(store, ["one", "two"], rubric_pack=PACK)

    def refusing(_run: Any) -> Any:
        raise BatchAborted("No Canvas credentials.", remedy="Enter them on the Settings screen.")

    class Factory:
        def __call__(self, run: Any) -> Any:
            refusing(run)

    summary = await BatchRunner(store, paths, Factory()).execute(run_id)

    assert summary.aborted is not None and "No Canvas credentials." in summary.aborted
    assert len(summary.not_run) == 2
    assert _statuses(store, run_id) == [RunStatus.PENDING, RunStatus.PENDING]
    assert "batch_aborted" in _event_names(store, run_id)


# ---------------------------------------------------------------------------------------------
# The cap
# ---------------------------------------------------------------------------------------------


def test_the_cap_refuses_a_seventeenth_course(store: RunStore) -> None:
    with pytest.raises(BatchTooLargeError) as raised:
        create_batch_run(store, [str(index) for index in range(17)], rubric_pack=PACK)
    assert raised.value.asked == 17
    assert raised.value.cap == 16
    assert "17 courses were given and the cap for one run is 16." in str(raised.value)


def test_the_cap_is_configurable_downwards(store: RunStore) -> None:
    with pytest.raises(BatchTooLargeError):
        create_batch_run(store, ["one", "two", "three"], rubric_pack=PACK, cap=2)
    run_id = create_batch_run(store, ["one", "two"], rubric_pack=PACK, cap=2)
    assert store.get_run(run_id) is not None


def test_an_empty_batch_is_refused(store: RunStore) -> None:
    with pytest.raises(ValueError):
        create_batch_run(store, ["  ", ""], rubric_pack=PACK)


# ---------------------------------------------------------------------------------------------
# What the run leaves behind
# ---------------------------------------------------------------------------------------------


async def test_every_stage_is_reported_per_course(store: RunStore, paths: DataPaths) -> None:
    run_id = create_batch_run(store, ["one"], rubric_pack=PACK)
    await _batch(store, paths, ScriptedPipeline()).execute(run_id)

    events = store.events_in_order(run_id)
    started = [event.stage for event in events if event.event == "stage_started"]
    finished = [event.stage for event in events if event.event == "stage_finished"]
    assert started == [stage.value for stage in ORDER]
    assert finished == [stage.value for stage in ORDER]
    for event in events:
        if event.event == "stage_finished":
            assert event.data["examined"] == 4
            assert event.data["statement"].startswith(event.stage)


async def test_the_reviewable_result_is_written_under_the_run(
    store: RunStore, paths: DataPaths
) -> None:
    run_id = create_batch_run(store, ["one"], rubric_pack=PACK)
    summary = await _batch(store, paths, ScriptedPipeline()).execute(run_id)

    written = summary.reviewed[0].review_path
    assert written is not None
    assert written.startswith(f"runs/{run_id}/courses/")
    payload = json.loads((paths.root / written).read_text(encoding="utf-8"))
    assert payload["course"]["reference"] == "one"
    assert len(payload["stages"]) == len(ORDER)


async def test_a_course_reference_never_reaches_a_log_line(
    store: RunStore, paths: DataPaths
) -> None:
    """A course URL carries the Canvas host, and SPEC §11 treats that host as a secret."""
    stream = io.StringIO()
    configure_logging(level="DEBUG", fmt="json", stream=stream)
    try:
        reference = "https://canvas.example.edu/courses/4321"
        run_id = create_batch_run(store, [reference], rubric_pack=PACK)
        await _batch(store, paths, ScriptedPipeline()).execute(run_id)
    finally:
        configure_logging(level="INFO", fmt="json")

    captured = stream.getvalue()
    assert "batch_finished" in captured  # the batch really did log
    assert "canvas.example.edu" not in captured
    assert "courses/4321" not in captured
