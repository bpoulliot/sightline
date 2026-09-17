"""Background work is an asyncio task, and its state outlives the task."""

from __future__ import annotations

import asyncio

import pytest

from sightline.models import RunStatus
from sightline.paths import DataPaths
from sightline.runner import BackgroundRunner, RunnerNotConfiguredError
from sightline.store import RunStore


@pytest.fixture
def store(paths: DataPaths) -> RunStore:
    opened = RunStore.open(paths)
    yield opened
    opened.dispose()


async def test_a_runner_without_a_handler_refuses_rather_than_pretending(
    store: RunStore,
) -> None:
    """An unconfigured runner must not look like a runner that ran and found nothing."""
    runner = BackgroundRunner(store)
    run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    with pytest.raises(RunnerNotConfiguredError):
        runner.submit(run_id)


async def test_a_completed_run_is_marked_succeeded(store: RunStore) -> None:
    seen: list[str] = []

    async def handler(run_id: str) -> None:
        seen.append(run_id)

    runner = BackgroundRunner(store, handler)
    run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    await runner.submit(run_id)
    run = store.get_run(run_id)
    assert seen == [run_id]
    assert run is not None and run.status == RunStatus.SUCCEEDED


async def test_a_failing_run_is_recorded_and_reraised(store: RunStore) -> None:
    async def handler(_run_id: str) -> None:
        raise RuntimeError("the export never arrived")

    runner = BackgroundRunner(store, handler)
    run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    with pytest.raises(RuntimeError):
        await runner.submit(run_id)
    run = store.get_run(run_id)
    assert run is not None and run.status == RunStatus.FAILED
    failures = [event for event in store.list_events(run_id) if event.event == "run_failed"]
    assert len(failures) == 1
    assert "the export never arrived" in failures[0].data["error"]


async def test_shutdown_leaves_an_in_flight_run_resumable(store: RunStore) -> None:
    started = asyncio.Event()

    async def handler(_run_id: str) -> None:
        started.set()
        await asyncio.sleep(30)

    runner = BackgroundRunner(store, handler)
    run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    runner.submit(run_id)
    await started.wait()
    assert runner.active_run_ids == [run_id]

    await runner.shutdown()

    run = store.get_run(run_id)
    assert run is not None and run.status == RunStatus.INTERRUPTED
    assert [r.id for r in store.resumable_runs()] == [run_id]
    assert runner.active_run_ids == []


async def test_submitting_twice_returns_the_same_task(store: RunStore) -> None:
    async def handler(_run_id: str) -> None:
        await asyncio.sleep(5)

    runner = BackgroundRunner(store, handler)
    run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    first = runner.submit(run_id)
    second = runner.submit(run_id)
    assert first is second
    await runner.shutdown()


async def test_recover_reports_what_it_found(store: RunStore) -> None:
    run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    store.set_run_status(run_id, RunStatus.RUNNING)
    runner = BackgroundRunner(store)
    assert runner.recover() == [run_id]


async def test_a_handler_that_recorded_a_more_precise_outcome_keeps_it(store: RunStore) -> None:
    """A batch that reviewed two courses of three is ``partial``, and must not become succeeded.

    The background runner sets ``succeeded`` for a handler that simply returned. A handler that
    already recorded what happened has observed more than "it did not raise", so only a run still
    marked running is completed here.
    """

    async def handler(run_id: str) -> None:
        store.set_run_status(run_id, RunStatus.PARTIAL)

    runner = BackgroundRunner(store, handler)
    run_id = store.create_run(["101", "202"], rubric_pack="oscqr-3.1.3")
    await runner.submit(run_id)

    run = store.get_run(run_id)
    assert run is not None and RunStatus(run.status) is RunStatus.PARTIAL


async def test_resume_all_works_through_what_a_restart_found_one_at_a_time(
    store: RunStore,
) -> None:
    order: list[str] = []
    running = asyncio.Event()

    async def handler(run_id: str) -> None:
        order.append(run_id)
        running.set()
        await asyncio.sleep(0)

    first = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    second = store.create_run(["202"], rubric_pack="oscqr-3.1.3")
    store.set_run_status(first, RunStatus.RUNNING)

    runner = BackgroundRunner(store, handler)
    runner.recover()
    assert runner.resume_all() == [first, second]
    await running.wait()
    await asyncio.sleep(0.05)
    await runner.shutdown()

    assert order == [first, second]
    for run_id in (first, second):
        run = store.get_run(run_id)
        assert run is not None and RunStatus(run.status) is RunStatus.SUCCEEDED


async def test_a_run_with_nothing_to_resume_starts_nothing(store: RunStore) -> None:
    async def handler(_run_id: str) -> None:  # pragma: no cover - must never be called
        raise AssertionError("nothing was resumable")

    runner = BackgroundRunner(store, handler)
    assert runner.resume_all() == []
    await runner.shutdown()


async def test_shutdown_stops_the_resume_before_it_reaches_the_next_run(store: RunStore) -> None:
    started = asyncio.Event()
    handled: list[str] = []

    async def handler(run_id: str) -> None:
        handled.append(run_id)
        started.set()
        await asyncio.sleep(30)

    first = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    second = store.create_run(["202"], rubric_pack="oscqr-3.1.3")
    runner = BackgroundRunner(store, handler)
    assert runner.resume_all() == [first, second]
    await started.wait()

    await runner.shutdown()

    assert handled == [first]
    run = store.get_run(first)
    assert run is not None and RunStatus(run.status) is RunStatus.INTERRUPTED
    run = store.get_run(second)
    assert run is not None and RunStatus(run.status) is RunStatus.PENDING
