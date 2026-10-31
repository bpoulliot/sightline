"""Background work: one asyncio task per run, in the same process.

There is no Celery, no Redis and no worker container (SPEC §04). At one to sixteen courses the
work is I/O-bound against a local model server and there is nothing to distribute. What replaces a
broker is the run store: a run's state is written to SQLite as it changes, so a restart resumes
rather than loses.

This module is the seam. It owns task lifecycle and the restart semantics; the *pipeline* that a
run executes is injected as ``handler`` (the application wires the batch runner in, item 17). A
:class:`BackgroundRunner` with no handler refuses to start work and says so, rather than
appearing to run and doing nothing.

A run is executed under a :class:`sightline.ownership.RunHold`: this process records itself as
the run's owner before marking it running, beats while it works, and clears the record when it
stops. Restart recovery and shutdown touch only runs whose owner is gone or is this process, so a
command-line run in another process or container is never taken over while it is going (item 43).

**One run at a time, and the rest wait in a queue** (roadmap item 46). A run started while another
is active is recorded ``queued``, and one task here — the queue — starts the oldest of them when
nothing is ahead of it: no task of this process's, no resumed run still to come, no ``running``
run another process owns while that owner is live, and nothing else holding the model host (the
bindings proposer, which the application names through :attr:`BackgroundRunner.held_by`). The
queue is the store's ``queued`` rows in creation order, so a restart keeps it and its order; the
queue starts again after the runs a restart resumes. It is woken when one of this process's runs
ends, and it looks again every :data:`QUEUE_POLL_SECONDS` for what it cannot be told about — a
command-line run in another process finishing.

There is no priority and no reordering, and nothing here estimates when a queued run will start:
the runs ahead of it take as long as their courses and the model host take.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from enum import Enum

from .logging import get_logger
from .models import RunStatus
from .ownership import RunHold, RunOwnedElsewhereError, owner_is_live, this_process
from .store import RunStore

log = get_logger("sightline.runner")

#: Signature every pipeline implementation must satisfy.
RunHandler = Callable[[str], Awaitable[None]]

#: How often a non-empty queue looks again without being woken. What it cannot be woken by is a
#: run in another process finishing, so this is at most how long the next run waits after a
#: command-line run ends.
QUEUE_POLL_SECONDS = 5.0


class _Turn(Enum):
    """What one look at the queue found."""

    STARTED = "started"
    WAITING = "waiting"
    EMPTY = "empty"


class RunnerNotConfiguredError(RuntimeError):
    """No pipeline handler is registered, so a run cannot be started."""


class BackgroundRunner:
    """Tracks the asyncio tasks executing runs, and reconciles state across restarts."""

    def __init__(
        self,
        store: RunStore,
        handler: RunHandler | None = None,
        *,
        poll_seconds: float = QUEUE_POLL_SECONDS,
    ) -> None:
        self.store = store
        self.handler = handler
        self.poll_seconds = poll_seconds
        #: Whether something other than a run is using the model host now. The application points
        #: it at the bindings proposer (roadmap item 22); a queued run waits while it says yes.
        self.held_by: Callable[[], bool] | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        #: The task that works through what a restart found. One, not one per run: courses are
        #: reviewed one after another against a single model host (SPEC §04), and two runs
        #: resuming at once would be two batches competing for it.
        self._resume: asyncio.Task[None] | None = None
        #: The runs that task has still to start, in order. Each is removed only as it is
        #: submitted, so at every await it is either here or in ``_tasks``: the queue never sees a
        #: gap between two resumed runs.
        self._resume_waiting: list[str] = []
        #: The queue's task (roadmap item 46), and what wakes it.
        self._queue: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()

    # -- lifecycle ---------------------------------------------------------------------------

    def recover(self) -> list[str]:
        """Reconcile the store with reality at startup. Returns interrupted run ids."""
        interrupted = self.store.recover_interrupted()
        log.info(
            "runner_recovered",
            interrupted=len(interrupted),
            resumable=len(self.store.resumable_runs()),
        )
        return interrupted

    def resume_all(self) -> list[str]:
        """Start every run the store says can pick up again, one after another.

        Called once at startup, after :meth:`recover`. A process that comes back up and leaves a
        half-finished batch sitting there has persisted its state for nothing (SPEC §04). They go
        through one task in creation order rather than one task each, because they share a model
        host. A run another live process has claimed — a command-line run between its claim and
        its first status write — is that process's, and is not resumed here.
        """
        run_ids = [run.id for run in self.store.resumable_runs() if not owner_is_live(run)]
        if not run_ids:
            return []
        self._resume_waiting = list(run_ids)
        self._resume = asyncio.create_task(self._resume_serially(), name="sightline-resume")
        self._resume.add_done_callback(self._ended)
        log.info("runs_resuming", run_ids=run_ids, count=len(run_ids))
        return run_ids

    async def _resume_serially(self) -> None:
        while self._resume_waiting:
            run_id = self._resume_waiting.pop(0)
            try:
                await self.submit(run_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # already recorded by _execute; the next run still gets its turn
                log.warning("resumed_run_failed", run_id=run_id)

    async def shutdown(self) -> None:
        """Cancel in-flight tasks and leave the store in a resumable state.

        Queued runs stay queued: the queue is the store's, and the next start takes it up again.
        """
        if self._queue is not None:
            # First, so that nothing is taken from the queue while the runs ahead are cancelled.
            self._queue.cancel()
            try:
                await self._queue
            except asyncio.CancelledError:
                pass
            self._queue = None
        self._resume_waiting = []
        if self._resume is not None:
            # First, so that cancelling the run it is awaiting does not start the next one.
            self._resume.cancel()
            try:
                await self._resume
            except asyncio.CancelledError:
                log.info("resume_cancelled_on_shutdown")
            except Exception as exc:  # already logged by _execute; do not mask shutdown
                log.warning("resume_failed_during_shutdown", error=str(exc))
            self._resume = None
        tasks = list(self._tasks.items())
        for _run_id, task in tasks:
            task.cancel()
        for run_id, task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                log.info("run_cancelled_on_shutdown", run_id=run_id)
            except Exception as exc:  # already logged by _execute; do not mask shutdown
                log.warning("run_failed_during_shutdown", run_id=run_id, error=str(exc))
        self._tasks.clear()
        # Anything this process still holds as running is about to have no process. Only this
        # process's own: a run another process owns is left to it (roadmap item 43).
        self.store.interrupt_owned(this_process())

    # -- submission --------------------------------------------------------------------------

    @property
    def active_run_ids(self) -> list[str]:
        return [run_id for run_id, task in self._tasks.items() if not task.done()]

    def submit(self, run_id: str) -> asyncio.Task[None]:
        """Start (or resume) a run as a background task."""
        if self.handler is None:
            raise RunnerNotConfiguredError(
                "no pipeline handler is registered; a run cannot be started"
            )
        existing = self._tasks.get(run_id)
        if existing is not None and not existing.done():
            return existing
        task = asyncio.create_task(self._execute(run_id), name=f"sightline-run-{run_id}")
        task.add_done_callback(self._ended)
        self._tasks[run_id] = task
        return task

    # -- the queue (roadmap item 46) ---------------------------------------------------------

    def ahead_of_queue(self) -> list[str]:
        """The runs every queued run waits for, by id: executing now, or resumed and still to come.

        This process's tasks, the runs a restart has still to resume, and ``running`` runs another
        process owns while that owner is live (item 43) — a command-line run counts as the active
        run. A ``running`` row whose owner is gone is nobody's work and holds nothing up.
        """
        ahead: list[str] = list(self.active_run_ids)
        for run in self.store.running_runs():
            if run.id not in ahead and owner_is_live(run):
                ahead.append(run.id)
        ahead.extend(run_id for run_id in self._resume_waiting if run_id not in ahead)
        return ahead

    def must_queue(self) -> bool:
        """Whether a run started now waits: something is ahead of the queue, or the queue is not
        empty — a run started now never overtakes one started before it."""
        return bool(self.ahead_of_queue()) or bool(self.store.queued_runs())

    def queue_positions(self) -> dict[str, int]:
        """How many runs are ahead of each queued run, by run id. Counts, never a time."""
        ahead = len(self.ahead_of_queue())
        return {run.id: ahead + index for index, run in enumerate(self.store.queued_runs())}

    def start_queue(self) -> None:
        """Start the queue's task if it is not going, and have it look now.

        Called at startup, after :meth:`resume_all`, so a queue a restart found carries on behind
        the runs it resumed; and whenever a run is queued.
        """
        self._wake.set()
        if self._queue is None or self._queue.done():
            self._queue = asyncio.create_task(self._drain(), name="sightline-queue")

    def _ended(self, _task: asyncio.Task[None]) -> None:
        self._wake.set()

    async def _drain(self) -> None:
        while True:
            self._wake.clear()
            turn = self._next_turn()
            if turn is _Turn.STARTED:
                continue
            if turn is _Turn.EMPTY:
                await self._wake.wait()
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), self.poll_seconds)
            except TimeoutError:
                pass

    def _next_turn(self) -> _Turn:
        """Start the oldest queued run if nothing is ahead of it."""
        try:
            queued = self.store.queued_runs()
            if not queued:
                return _Turn.EMPTY
            if self.handler is None or self.ahead_of_queue():
                return _Turn.WAITING
            if self.held_by is not None and self.held_by():
                return _Turn.WAITING
            run_id = queued[0].id
            # A compare-and-set: a cancel that landed first leaves the run cancelled, and the
            # queue looks again at once.
            if self.store.take_queued(run_id):
                self.submit(run_id)
                log.info("run_started_from_queue", run_id=run_id)
            return _Turn.STARTED
        except Exception as exc:  # a failed read is retried on the next look, never fatal
            log.warning("queue_look_failed", error_type=type(exc).__name__)
            return _Turn.WAITING

    async def _execute(self, run_id: str) -> None:
        assert self.handler is not None  # guarded by submit()
        # The claim comes before the status write, so there is no moment at which the run is
        # ``running`` with no owner for another process's recovery to take over.
        try:
            with RunHold(self.store, run_id):
                await self._execute_held(run_id)
        except RunOwnedElsewhereError:
            # Another live process is executing it. Running it here too is the double execution
            # roadmap item 43 exists to prevent, so this task ends having done nothing.
            log.warning("run_owned_elsewhere", run_id=run_id)

    async def _execute_held(self, run_id: str) -> None:
        assert self.handler is not None  # guarded by submit()
        self.store.set_run_status(run_id, RunStatus.RUNNING)
        try:
            await self.handler(run_id)
        except asyncio.CancelledError:
            self.store.set_run_status(run_id, RunStatus.INTERRUPTED)
            raise
        except Exception as exc:
            self.store.append_event(
                run_id, "run_failed", level="error", error_type=type(exc).__name__, error=str(exc)
            )
            self.store.set_run_status(run_id, RunStatus.FAILED)
            log.error("run_failed", run_id=run_id, error_type=type(exc).__name__, error=str(exc))
            raise
        else:
            # The handler may have recorded a more precise outcome than "it did not raise" — a
            # batch that reviewed two courses of three is ``partial``, and one the model host
            # stopped is ``interrupted`` and resumable. Only a run still marked running is
            # completed here. Overwriting either would be this class asserting something it did
            # not observe.
            run = self.store.get_run(run_id)
            if run is None or RunStatus(run.status) is RunStatus.RUNNING:
                self.store.set_run_status(run_id, RunStatus.SUCCEEDED)
