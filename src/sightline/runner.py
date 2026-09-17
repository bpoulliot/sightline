"""Background work: one asyncio task per run, in the same process.

There is no Celery, no Redis and no worker container (SPEC §04). At one to sixteen courses the
work is I/O-bound against a local model server and there is nothing to distribute. What replaces a
broker is the run store: a run's state is written to SQLite as it changes, so a restart resumes
rather than loses.

This module is the seam. It owns task lifecycle and the restart semantics; the *pipeline* that a
run executes arrives with later roadmap items and is injected as ``handler``. Until then a
:class:`BackgroundRunner` with no handler refuses to start work and says so, rather than
appearing to run and doing nothing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .logging import get_logger
from .models import RunStatus
from .store import RunStore

log = get_logger("sightline.runner")

#: Signature every pipeline implementation must satisfy.
RunHandler = Callable[[str], Awaitable[None]]


class RunnerNotConfiguredError(RuntimeError):
    """No pipeline handler is registered, so a run cannot be started."""


class BackgroundRunner:
    """Tracks the asyncio tasks executing runs, and reconciles state across restarts."""

    def __init__(self, store: RunStore, handler: RunHandler | None = None) -> None:
        self.store = store
        self.handler = handler
        self._tasks: dict[str, asyncio.Task[None]] = {}
        #: The task that works through what a restart found. One, not one per run: courses are
        #: reviewed one after another against a single model host (SPEC §04), and two runs
        #: resuming at once would be two batches competing for it.
        self._resume: asyncio.Task[None] | None = None

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
        host.
        """
        run_ids = [run.id for run in self.store.resumable_runs()]
        if not run_ids:
            return []
        self._resume = asyncio.create_task(self._resume_serially(run_ids), name="sightline-resume")
        log.info("runs_resuming", run_ids=run_ids, count=len(run_ids))
        return run_ids

    async def _resume_serially(self, run_ids: list[str]) -> None:
        for run_id in run_ids:
            try:
                await self.submit(run_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # already recorded by _execute; the next run still gets its turn
                log.warning("resumed_run_failed", run_id=run_id)

    async def shutdown(self) -> None:
        """Cancel in-flight tasks and leave the store in a resumable state."""
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
        # Anything still marked running belongs to a process that no longer exists.
        self.store.recover_interrupted()

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
        self._tasks[run_id] = task
        return task

    async def _execute(self, run_id: str) -> None:
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
