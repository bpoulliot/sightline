"""The run store, and the restart that must not lose it."""

from __future__ import annotations

from pathlib import Path

import pytest

from sightline.models import TERMINAL_STATUSES, RunStatus
from sightline.paths import DataPaths
from sightline.store import RunStore, create_store_engine


@pytest.fixture
def store(paths: DataPaths) -> RunStore:
    opened = RunStore.open(paths)
    yield opened
    opened.dispose()


def test_the_database_lives_inside_the_data_root(paths: DataPaths, store: RunStore) -> None:
    assert paths.database.is_file()
    assert paths.contains(paths.database)


def test_create_run_records_courses_in_order(store: RunStore) -> None:
    run_id = store.create_run(["101", "202", "303"], rubric_pack="oscqr-3.1.3")
    run = store.get_run(run_id)
    assert run is not None
    assert run.status == RunStatus.PENDING
    assert run.rubric_pack == "oscqr-3.1.3"
    assert [course.course_ref for course in run.courses] == ["101", "202", "303"]
    assert [course.ordinal for course in run.courses] == [0, 1, 2]


def test_a_run_needs_at_least_one_course(store: RunStore) -> None:
    with pytest.raises(ValueError):
        store.create_run([], rubric_pack="oscqr-3.1.3")


def test_status_transitions_are_recorded(store: RunStore) -> None:
    run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    store.set_run_status(run_id, RunStatus.RUNNING)
    running = store.get_run(run_id)
    assert running is not None and running.started_at is not None
    store.set_run_status(run_id, RunStatus.SUCCEEDED)
    done = store.get_run(run_id)
    assert done is not None and done.finished_at is not None
    assert done.is_terminal is True
    events = [event.event for event in store.list_events(run_id)]
    assert events.count("run_status") == 2


@pytest.mark.parametrize("status", sorted(TERMINAL_STATUSES, key=lambda s: s.value))
def test_every_terminal_status_records_a_finish_time(store: RunStore, status: RunStatus) -> None:
    run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    store.set_run_status(run_id, RunStatus.RUNNING)
    store.set_run_status(run_id, status)
    run = store.get_run(run_id)
    assert run is not None and run.is_terminal is True
    assert run.finished_at is not None


@pytest.mark.parametrize("status", [RunStatus.RUNNING, RunStatus.INTERRUPTED])
def test_a_run_that_is_not_over_records_no_finish_time(store: RunStore, status: RunStatus) -> None:
    # Interrupted is resumable, not terminal: the run is not over until a later pass ends it.
    run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    store.set_run_status(run_id, RunStatus.RUNNING)
    store.set_run_status(run_id, status)
    run = store.get_run(run_id)
    assert run is not None and run.is_terminal is False
    assert run.finished_at is None


def test_course_failure_is_visible_not_silent(store: RunStore) -> None:
    run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    run = store.get_run(run_id)
    assert run is not None
    course_id = run.courses[0].id
    store.set_course_status(course_id, RunStatus.FAILED, stage="acquire", error="404 from export")
    events = store.list_events(run_id)
    failures = [event for event in events if event.level == "error"]
    assert len(failures) == 1
    assert failures[0].data["error"] == "404 from export"


def test_event_payloads_are_scrubbed(store: RunStore) -> None:
    from sightline.config import Settings

    Settings(_env_file=None, canvas_base_url="https://canvas.example.edu")
    run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    store.append_event(run_id, "fetch", url="https://canvas.example.edu/api/v1/courses/101")
    stored = [event for event in store.list_events(run_id) if event.event == "fetch"]
    assert "canvas.example.edu" not in str(stored[0].data)


def test_course_error_text_is_scrubbed(store: RunStore) -> None:
    from sightline.config import Settings

    Settings(_env_file=None, canvas_base_url="https://canvas.example.edu")
    run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    run = store.get_run(run_id)
    assert run is not None
    store.set_course_status(
        run.courses[0].id,
        RunStatus.FAILED,
        error="GET https://canvas.example.edu/api/v1/courses/101 returned 401",
    )
    refreshed = store.get_run(run_id)
    assert refreshed is not None
    assert "canvas.example.edu" not in (refreshed.courses[0].error or "")


def test_state_survives_a_restart(paths: DataPaths, data_root: Path) -> None:
    """A new process opening the same file sees the same runs — this is what replaces a broker."""
    first = RunStore.open(paths)
    run_id = first.create_run(["101", "202"], rubric_pack="oscqr-3.1.3")
    first.set_run_status(run_id, RunStatus.RUNNING)
    first.dispose()

    second = RunStore.open(DataPaths.from_setting(data_root))
    try:
        run = second.get_run(run_id)
        assert run is not None
        assert run.status == RunStatus.RUNNING
        assert len(run.courses) == 2
    finally:
        second.dispose()


def test_recover_interrupted_turns_running_into_resumable(
    paths: DataPaths, data_root: Path
) -> None:
    first = RunStore.open(paths)
    run_id = first.create_run(["101"], rubric_pack="oscqr-3.1.3")
    first.set_run_status(run_id, RunStatus.RUNNING)
    run = first.get_run(run_id)
    assert run is not None
    first.set_course_status(run.courses[0].id, RunStatus.RUNNING, stage="acquire")
    first.dispose()  # the process goes away mid-run

    second = RunStore.open(DataPaths.from_setting(data_root))
    try:
        touched = second.recover_interrupted()
        assert touched == [run_id]
        recovered = second.get_run(run_id)
        assert recovered is not None
        assert recovered.status == RunStatus.INTERRUPTED
        assert recovered.courses[0].status == RunStatus.INTERRUPTED
        assert [r.id for r in second.resumable_runs()] == [run_id]
        warnings = [e for e in second.list_events(run_id) if e.event == "run_interrupted"]
        assert len(warnings) == 1
    finally:
        second.dispose()


def test_recover_interrupted_leaves_finished_runs_alone(store: RunStore) -> None:
    done = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
    store.set_run_status(done, RunStatus.SUCCEEDED)
    assert store.recover_interrupted() == []
    run = store.get_run(done)
    assert run is not None and run.status == RunStatus.SUCCEEDED


def test_counts_by_status(store: RunStore) -> None:
    first = store.create_run(["1"], rubric_pack="oscqr-3.1.3")
    store.create_run(["2"], rubric_pack="oscqr-3.1.3")
    store.set_run_status(first, RunStatus.SUCCEEDED)
    assert store.counts_by_status() == {"succeeded": 1, "pending": 1}


def test_unknown_ids_raise(store: RunStore) -> None:
    with pytest.raises(KeyError):
        store.set_run_status("nope", RunStatus.RUNNING)
    with pytest.raises(KeyError):
        store.set_course_status("nope", RunStatus.RUNNING)
    assert store.get_run("nope") is None


def test_a_data_root_from_before_the_export_gate_gains_its_column(paths: DataPaths) -> None:
    """The whole migration mechanism, fired: ``create_all`` never alters a table it did not make.

    A developer who has been running Sightline has a ``run_courses`` table with no
    ``review_opened_at`` column, and the symptom of doing nothing about it is ``no such column``
    on the first query — at runtime, in front of them. So the store adds what
    :data:`sightline.store.ADDED_COLUMNS` names, and this test builds the older schema by hand and
    checks both that the column arrives and that the row that was already there survives.
    """
    paths.ensure()
    engine = create_store_engine(paths.database_url())
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE run_courses ("
            "id VARCHAR(32) NOT NULL PRIMARY KEY, run_id VARCHAR(32) NOT NULL, "
            "ordinal INTEGER, course_ref VARCHAR(512), status VARCHAR(16), "
            "stage VARCHAR(32), error TEXT, updated_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO run_courses (id, run_id, ordinal, course_ref, status) "
            "VALUES ('c1', 'r1', 0, '101', 'succeeded')"
        )
    engine.dispose()

    store = RunStore.open(paths)
    try:
        with store.engine.begin() as connection:
            columns = {
                str(row[1])
                for row in connection.exec_driver_sql("PRAGMA table_info(run_courses)").fetchall()
            }
            kept = connection.exec_driver_sql(
                "SELECT course_ref FROM run_courses WHERE id = 'c1'"
            ).scalar_one()
        assert "review_opened_at" in columns
        assert kept == "101"
        # Idempotent: a second open finds nothing left to add.
        assert store.apply_added_columns() == []
    finally:
        store.dispose()
