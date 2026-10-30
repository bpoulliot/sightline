"""A run's owner record, and recovery that takes over only what nobody is executing (item 43).

The defect this guards: recovery used to mark *every* ``running`` run interrupted, so starting or
stopping the app while ``python -m sightline.batch run`` was going took that run over, and the
app's next start executed it a second time, concurrently. Each test below sets up a run the way
another process leaves it — a real live child process, a reaped one, or a host name that is not
this one — and checks what this process does with it.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from run_owners import OTHER_HOST, STALE, dead_pid, live_process, owner_of, set_owner, this_host
from sightline.app import create_app
from sightline.batch.__main__ import main
from sightline.config import Settings, reset_settings_cache
from sightline.models import Run, RunStatus, utcnow
from sightline.ownership import (
    HEARTBEAT_INTERVAL,
    STALE_AFTER,
    RunHold,
    RunOwnedElsewhereError,
    RunOwner,
    holds,
    orphan_reason,
    owner_is_live,
    pid_alive,
    this_process,
)
from sightline.paths import DataPaths
from sightline.runner import BackgroundRunner
from sightline.store import RunStore, _same_owner, create_store_engine

PACK = "oscqr-3.1.3"
SAMPLE = "examples/sample-course"


@pytest.fixture
def store(paths: DataPaths) -> RunStore:
    opened = RunStore.open(paths)
    yield opened
    opened.dispose()


def _run(store: RunStore, *refs: str) -> str:
    return store.create_run(list(refs or ("101",)), rubric_pack=PACK)


def _status(store: RunStore, run_id: str) -> RunStatus:
    run = store.get_run(run_id)
    assert run is not None
    return RunStatus(run.status)


def _events(store: RunStore, run_id: str, name: str) -> list[object]:
    return [event for event in store.events_in_order(run_id) if event.event == name]


# -- the constants ---------------------------------------------------------------------------------


def test_the_stale_limit_tolerates_many_missed_beats() -> None:
    """One late beat — a locked database, a long synchronous parse — must not orphan a run."""
    assert STALE_AFTER >= HEARTBEAT_INTERVAL * 5
    assert timedelta(minutes=1) >= HEARTBEAT_INTERVAL


# -- what "gone" means -----------------------------------------------------------------------------


def _record(**fields: object) -> Run:
    run = Run(rubric_pack=PACK)
    run.id = "r" * 32
    for name, value in fields.items():
        setattr(run, name, value)
    return run


def test_pid_alive_tells_a_live_process_from_a_reaped_one() -> None:
    """The probe in both directions, so neither answer is the only one it can give."""
    assert pid_alive(this_process().pid)
    with live_process() as pid:
        assert pid_alive(pid)
    assert not pid_alive(dead_pid())
    assert not pid_alive(0)  # kill(0, 0) would signal this process group, not test a pid
    assert not pid_alive(-1)


def test_no_owner_is_orphaned() -> None:
    assert orphan_reason(_record()) == "no process was recorded as executing it"
    assert orphan_reason(_record(owner_host=OTHER_HOST, owner_pid=7)) is not None


def test_a_live_process_on_this_host_owns_its_run() -> None:
    with live_process() as pid:
        record = _record(owner_host=this_host(), owner_pid=pid, owner_heartbeat_at=utcnow())
        assert owner_is_live(record)


def test_a_dead_process_on_this_host_does_not() -> None:
    record = _record(owner_host=this_host(), owner_pid=dead_pid(), owner_heartbeat_at=utcnow())
    assert orphan_reason(record) == "the process executing it on this host has stopped"


def test_across_hosts_the_heartbeat_is_the_whole_test() -> None:
    """The trap: pid 7 in another container says nothing about pid 7 here.

    A reaped pid *on this host* is used as the other host's pid on purpose: if the check looked at
    the pid across hosts, this owner would wrongly read as gone.
    """
    pid = dead_pid()
    fresh = _record(owner_host=OTHER_HOST, owner_pid=pid, owner_heartbeat_at=utcnow())
    assert owner_is_live(fresh)
    stale = _record(owner_host=OTHER_HOST, owner_pid=pid, owner_heartbeat_at=utcnow() - STALE)
    assert "past the" in (orphan_reason(stale) or "")


def test_a_stale_heartbeat_orphans_even_a_live_pid() -> None:
    with live_process() as pid:
        record = _record(owner_host=this_host(), owner_pid=pid, owner_heartbeat_at=utcnow() - STALE)
        assert not owner_is_live(record)


def test_this_processs_pid_is_live_only_while_this_process_holds_the_run(store: RunStore) -> None:
    """A restarted container's app has the same small pid under the same host name as before.

    The record its previous incarnation left therefore names this process exactly, and must read
    as gone; only a run this process actually holds is live.
    """
    run_id = _run(store)
    me = this_process()
    set_owner(store, run_id, host=me.host, pid=me.pid)
    run = store.get_run(run_id)
    assert run is not None and not holds(run_id)
    assert "earlier process" in (orphan_reason(run) or "")

    set_owner(store, run_id, host=None, pid=None, heartbeat_age=None)
    with RunHold(store, run_id):
        run = store.get_run(run_id)
        assert run is not None and holds(run_id)
        assert owner_is_live(run)
    assert not holds(run_id)


# -- claim, heartbeat, release ---------------------------------------------------------------------


def test_a_claim_records_the_owner_and_release_clears_it(store: RunStore) -> None:
    run_id = _run(store)
    with RunHold(store, run_id):
        assert owner_of(store, run_id) == (this_host(), this_process().pid)
        run = store.get_run(run_id)
        assert run is not None and run.owner_heartbeat_at is not None
    assert owner_of(store, run_id) == (None, None)
    assert _status(store, run_id) is RunStatus.PENDING  # a hold never invents an outcome


def test_a_run_with_a_live_owner_cannot_be_claimed(store: RunStore) -> None:
    run_id = _run(store)
    with live_process() as pid:
        set_owner(store, run_id, host=this_host(), pid=pid)
        assert store.claim_run(run_id, this_process()) is False
        with pytest.raises(RunOwnedElsewhereError), RunHold(store, run_id):
            pass  # pragma: no cover - the claim is refused before the body
        assert owner_of(store, run_id) == (this_host(), pid)


def test_a_run_with_a_gone_owner_can_be_claimed(store: RunStore) -> None:
    run_id = _run(store)
    set_owner(store, run_id, host=OTHER_HOST, pid=7, heartbeat_age=STALE)
    assert store.claim_run(run_id, this_process()) is True
    assert owner_of(store, run_id) == (this_host(), this_process().pid)


def test_of_two_claims_on_one_snapshot_exactly_one_lands(store: RunStore) -> None:
    """The compare-and-set, fired: two processes that both read the run as orphaned.

    The second claimant is a different process on another host, so liveness alone would let it
    through; only the compare-and-set on the record it read refuses it.
    """
    run_id = _run(store)
    first = RunOwner(host=OTHER_HOST, pid=11)
    second = RunOwner(host=OTHER_HOST + "-2", pid=12)
    snapshot = store.get_run(run_id)
    assert snapshot is not None and snapshot.owner_host is None

    assert store.claim_run(run_id, first) is True

    # The second claimant's write, against the snapshot both read before either wrote. The same
    # write against a fresh read matches, so the zero below is the compare-and-set and not a
    # statement that can never match.
    def second_write(read: Run) -> int:
        with store.engine.begin() as connection:
            return connection.execute(
                update(Run)
                .where(Run.id == run_id, *_same_owner(read))
                .values(owner_host=second.host, owner_pid=second.pid)
            ).rowcount

    assert second_write(snapshot) == 0
    assert owner_of(store, run_id) == (first.host, first.pid)
    fresh = store.get_run(run_id)
    assert fresh is not None and second_write(fresh) == 1


def test_the_heartbeat_beats_on_its_own_thread(store: RunStore) -> None:
    """A stage that blocks the event loop cannot stop the beat: it is not on the event loop."""
    run_id = _run(store)
    with RunHold(store, run_id, interval=timedelta(milliseconds=20)):
        first = store.get_run(run_id)
        assert first is not None and first.owner_heartbeat_at is not None
        before_beat = first.owner_heartbeat_at
        updated_before = first.updated_at
        time.sleep(0.3)  # synchronous on purpose: nothing here yields to an event loop
        later = store.get_run(run_id)
        assert later is not None and later.owner_heartbeat_at is not None
        assert later.owner_heartbeat_at > before_beat
        # The run did not change, so the column that says it did is left alone.
        assert later.updated_at == updated_before
    assert owner_of(store, run_id) == (None, None)


def test_a_heartbeat_does_not_reclaim_a_run_taken_over(store: RunStore) -> None:
    run_id = _run(store)
    me = this_process()
    assert store.claim_run(run_id, me)
    set_owner(store, run_id, host=OTHER_HOST, pid=7, status=None)
    assert store.heartbeat_run(run_id, me) is False
    assert store.release_run(run_id, me) is False
    assert owner_of(store, run_id) == (OTHER_HOST, 7)


def test_an_owner_that_stops_mid_run_leaves_it_interrupted(store: RunStore) -> None:
    """A cancel or a crash the run did not record: interrupted on the way out, not left running."""
    run_id = _run(store)
    with pytest.raises(KeyboardInterrupt), RunHold(store, run_id):
        store.set_run_status(run_id, RunStatus.RUNNING)
        raise KeyboardInterrupt
    assert _status(store, run_id) is RunStatus.INTERRUPTED
    assert owner_of(store, run_id) == (None, None)
    assert len(_events(store, run_id, "run_interrupted")) == 1


# -- recovery --------------------------------------------------------------------------------------


def test_recovery_leaves_a_run_a_live_process_owns(store: RunStore) -> None:
    run_id = _run(store)
    with live_process() as pid:
        heartbeat = set_owner(store, run_id, host=this_host(), pid=pid)
        assert store.recover_interrupted() == []
        run = store.get_run(run_id)
        assert run is not None and RunStatus(run.status) is RunStatus.RUNNING
        assert owner_of(store, run_id) == (this_host(), pid)
        assert run.owner_heartbeat_at == heartbeat.replace(tzinfo=None)  # type: ignore[union-attr]
    assert _events(store, run_id, "run_interrupted") == []


def test_recovery_leaves_a_fresh_owner_on_another_host(store: RunStore) -> None:
    run_id = _run(store)
    set_owner(store, run_id, host=OTHER_HOST, pid=dead_pid())
    assert store.recover_interrupted() == []
    assert _status(store, run_id) is RunStatus.RUNNING


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("dead-pid-this-host", "on this host has stopped"),
        ("stale-other-host", "past the"),
        ("no-owner", "no process was recorded"),
    ],
)
def test_recovery_takes_over_an_orphaned_run_and_says_why(
    store: RunStore, case: str, expected: str
) -> None:
    run_id = _run(store)
    run = store.get_run(run_id)
    assert run is not None
    _orphan(store, run_id, case)
    store.set_course_status(run.courses[0].id, RunStatus.RUNNING, stage="acquire")

    assert store.recover_interrupted() == [run_id]

    recovered = store.get_run(run_id)
    assert recovered is not None and RunStatus(recovered.status) is RunStatus.INTERRUPTED
    assert RunStatus(recovered.courses[0].status) is RunStatus.INTERRUPTED
    assert owner_of(store, run_id) == (None, None)
    (event,) = _events(store, run_id, "run_interrupted")
    assert expected in event.data["reason"]  # type: ignore[attr-defined]


def _orphan(store: RunStore, run_id: str, case: str) -> None:
    if case == "dead-pid-this-host":
        set_owner(store, run_id, host=this_host(), pid=dead_pid())
    elif case == "stale-other-host":
        set_owner(store, run_id, host=OTHER_HOST, pid=7, heartbeat_age=STALE)
    else:
        set_owner(store, run_id, host=None, pid=None, heartbeat_age=None)


def test_a_data_root_from_before_owner_records_gains_the_columns(paths: DataPaths) -> None:
    """An older ``runs`` table has no owner columns; its ``running`` row reads as ownerless."""
    paths.ensure()
    engine = create_store_engine(paths.database_url())
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE runs (id VARCHAR(32) NOT NULL PRIMARY KEY, created_at DATETIME, "
            "updated_at DATETIME, started_at DATETIME, finished_at DATETIME, "
            "status VARCHAR(16), rubric_pack VARCHAR(64), note TEXT)"
        )
        connection.exec_driver_sql(
            "INSERT INTO runs (id, created_at, updated_at, status, rubric_pack) "
            "VALUES ('r1', '2026-10-01 00:00:00', '2026-10-01 00:00:00', 'running', 'p')"
        )
    engine.dispose()

    store = RunStore.open(paths)
    try:
        with store.engine.begin() as connection:
            columns = {
                str(row[1])
                for row in connection.exec_driver_sql("PRAGMA table_info(runs)").fetchall()
            }
        assert {"owner_host", "owner_pid", "owner_heartbeat_at"} <= columns
        assert store.apply_added_columns() == []
        assert store.recover_interrupted() == ["r1"]
    finally:
        store.dispose()


# -- the background runner: startup, resume, shutdown ----------------------------------------------


@pytest.mark.parametrize("case", ["dead-pid-this-host", "stale-other-host", "no-owner"])
async def test_an_orphaned_run_is_recovered_and_resumed_exactly_once(
    store: RunStore, case: str
) -> None:
    handled: list[str] = []

    async def handler(run_id: str) -> None:
        handled.append(run_id)
        assert holds(run_id)
        assert owner_of(store, run_id) == (this_host(), this_process().pid)

    run_id = _run(store)
    _orphan(store, run_id, case)
    runner = BackgroundRunner(store, handler)
    assert runner.recover() == [run_id]
    assert runner.resume_all() == [run_id]
    await asyncio.sleep(0.1)
    # A second recovery in the same process finds nothing: the run is not taken over twice.
    assert runner.recover() == []
    assert runner.resume_all() == []
    await runner.shutdown()

    assert handled == [run_id]
    assert _status(store, run_id) is RunStatus.SUCCEEDED
    assert owner_of(store, run_id) == (None, None)


async def test_a_run_a_live_process_owns_survives_recovery_resume_and_shutdown(
    store: RunStore,
) -> None:
    async def handler(_run_id: str) -> None:  # pragma: no cover - must never be called
        raise AssertionError("another process is executing this run")

    run_id = _run(store)
    with live_process() as pid:
        set_owner(store, run_id, host=this_host(), pid=pid)
        runner = BackgroundRunner(store, handler)
        assert runner.recover() == []
        assert runner.resume_all() == []
        # Even submitted directly, it is refused rather than executed.
        await runner.submit(run_id)
        await runner.shutdown()
        assert _status(store, run_id) is RunStatus.RUNNING
        assert owner_of(store, run_id) == (this_host(), pid)
    assert _events(store, run_id, "run_interrupted") == []


async def test_a_pending_run_another_process_has_claimed_is_not_resumed(store: RunStore) -> None:
    """The instant between a command-line run's claim and its first status write."""
    run_id = _run(store)
    set_owner(store, run_id, host=OTHER_HOST, pid=7, status=None)
    runner = BackgroundRunner(store)
    assert runner.resume_all() == []


async def test_shutdown_interrupts_only_what_this_process_owns(store: RunStore) -> None:
    started = asyncio.Event()

    async def handler(_run_id: str) -> None:
        started.set()
        await asyncio.sleep(30)

    mine = _run(store, "101")
    theirs = _run(store, "202")
    set_owner(store, theirs, host=OTHER_HOST, pid=7)
    runner = BackgroundRunner(store, handler)
    runner.submit(mine)
    await started.wait()
    await runner.shutdown()

    assert _status(store, mine) is RunStatus.INTERRUPTED
    assert owner_of(store, mine) == (None, None)
    assert _status(store, theirs) is RunStatus.RUNNING
    assert owner_of(store, theirs) == (OTHER_HOST, 7)


async def test_no_run_executes_twice_concurrently(store: RunStore) -> None:
    """Two runners reaching for one run at once — two apps, or an app and a resume — run it once."""
    executing = 0
    most = 0
    calls = 0
    release = asyncio.Event()

    async def handler(_run_id: str) -> None:
        nonlocal executing, most, calls
        calls += 1
        executing += 1
        most = max(most, executing)
        await release.wait()
        executing -= 1

    run_id = _run(store)
    first = BackgroundRunner(store, handler)
    second = BackgroundRunner(store, handler)
    tasks = [first.submit(run_id), second.submit(run_id)]
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.gather(*tasks)
    await first.shutdown()
    await second.shutdown()

    assert calls == 1 and most == 1
    assert _status(store, run_id) is RunStatus.SUCCEEDED


# -- the application -------------------------------------------------------------------------------


def test_a_run_a_live_process_owns_survives_app_startup_and_shutdown(settings: Settings) -> None:
    paths = settings.paths.ensure()
    store = RunStore.open(paths)
    run_id = _run(store)
    try:
        with live_process() as pid:
            set_owner(store, run_id, host=this_host(), pid=pid)
            application = create_app(settings)
            with TestClient(application):
                assert application.state.runner.active_run_ids == []
                assert _status(store, run_id) is RunStatus.RUNNING
            # After the app's shutdown, too.
            assert _status(store, run_id) is RunStatus.RUNNING
            assert owner_of(store, run_id) == (this_host(), pid)
        assert _events(store, run_id, "run_interrupted") == []
        assert _events(store, run_id, "batch_started") == []
    finally:
        store.dispose()


def test_the_app_recovers_an_orphaned_run_at_startup(settings: Settings) -> None:
    """A dead pid on this host: taken over, and resumed (once) by the app's own pipeline."""
    paths = settings.paths.ensure()
    store = RunStore.open(paths)
    run_id = _run(store)
    try:
        set_owner(store, run_id, host=this_host(), pid=dead_pid())
        with TestClient(create_app(settings)):
            deadline = time.monotonic() + 10
            while not _events(store, run_id, "batch_summary") and time.monotonic() < deadline:
                time.sleep(0.05)
        assert len(_events(store, run_id, "run_interrupted")) >= 1
        assert len(_events(store, run_id, "batch_started")) == 1
        assert owner_of(store, run_id) == (None, None)
    finally:
        store.dispose()


# -- the command line ------------------------------------------------------------------------------


@pytest.fixture
def cli_root(monkeypatch: pytest.MonkeyPatch, data_root: Path, repo_root: Path) -> Path:
    monkeypatch.setenv("SIGHTLINE_DATA_DIR", str(data_root))
    monkeypatch.chdir(repo_root)
    reset_settings_cache()
    yield data_root
    reset_settings_cache()


def test_batch_resume_refuses_a_run_a_live_process_owns(
    cli_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = RunStore.open(DataPaths.from_setting(cli_root))
    try:
        run_id = store.create_run([SAMPLE], rubric_pack="uccs-oscqr-rsi")
        with live_process() as pid:
            set_owner(store, run_id, host=this_host(), pid=pid)
            assert main(["resume", run_id]) == 2
            assert "being executed by another process" in capsys.readouterr().err
            assert _status(store, run_id) is RunStatus.RUNNING
            assert owner_of(store, run_id) == (this_host(), pid)
        assert _events(store, run_id, "batch_started") == []
        assert _events(store, run_id, "run_interrupted") == []
    finally:
        store.dispose()


@pytest.mark.parametrize("case", ["dead-pid-this-host", "stale-other-host"])
def test_batch_resume_takes_over_an_orphaned_run_once(
    cli_root: Path, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    store = RunStore.open(DataPaths.from_setting(cli_root))
    try:
        run_id = store.create_run([SAMPLE], rubric_pack="uccs-oscqr-rsi")
        _orphan(store, run_id, case)
        # No model host: the batch stops at extract, interrupted — but it did execute, once.
        assert main(["resume", run_id]) == 1
        capsys.readouterr()
        assert len(_events(store, run_id, "run_interrupted")) == 1
        assert len(_events(store, run_id, "batch_started")) == 1
        assert owner_of(store, run_id) == (None, None)
    finally:
        store.dispose()


def test_batch_run_records_its_owner_while_it_executes(
    cli_root: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seen from inside a stage: the command-line run is owned by this process while it works."""
    from sightline.batch import runner as batch_runner

    seen: list[tuple[str | None, int | None]] = []
    original = batch_runner.BatchRunner._course

    async def watching(self, pipeline, run, course):  # type: ignore[no-untyped-def]
        seen.append(owner_of(self.store, run.id))
        return await original(self, pipeline, run, course)

    monkeypatch.setattr(batch_runner.BatchRunner, "_course", watching)
    assert main(["run", "--bundle", SAMPLE, "--pack", "uccs-oscqr-rsi"]) == 1
    capsys.readouterr()
    assert seen == [(this_host(), this_process().pid)]

    store = RunStore.open(DataPaths.from_setting(cli_root))
    try:
        run = store.list_runs()[0]
        assert (run.owner_host, run.owner_pid) == (None, None)
    finally:
        store.dispose()
