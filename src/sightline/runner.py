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

    async def shutdown(self) -> None:
        """Cancel in-flight tasks and leave the store in a resumable state."""
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
            self.store.set_run_status(run_id, RunStatus.SUCCEEDED)
