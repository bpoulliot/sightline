"""Installing a model, and the progress of it.

An install moves gigabytes and takes minutes, which is longer than an HTTP request should live.
So the pull runs as an asyncio task in this same process — there is no broker and no worker
container, and this is the same decision the run store already records for background work
(SPEC §04) — and this module is the state that task writes into.

**Progress is stored, not streamed to the page.** The settings screen is server-rendered and works
with JavaScript switched off (SPEC §09), so a reload of the page has to be able to show where an
install has got to. Reading state that a task keeps updating does that; a stream to an open
connection does not, and would also make a browser tab the thing an install depends on staying
alive.

**A finished pull stays in the list.** A success that disappears the moment it completes is
indistinguishable from one that never started, and a failure that disappears is worse — the whole
point of the roadmap item's logging requirement is that the earlier local-AI pipeline on this
network failed *silently*.

Nothing here removes a model. ``/api/delete`` is deliberately not wired up (SPEC §06).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..logging import get_logger
from .client import OllamaClient
from .errors import ModelHostError

log = get_logger("sightline.llm.pulls")


@dataclass
class PullState:
    """What an install is doing, or what it did.

    Mutated by the task that owns it and read by whatever renders the screen. Every field is a
    fact from the host's own stream — a status line it sent, a byte count it reported — except
    ``error``, which is the mapped failure with its remedy.
    """

    model: str
    status: str = "queued"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    completed: int | None = None
    total: int | None = None
    #: Layer digests the host has named, so a screen can say how many parts it has touched
    #: rather than appearing to restart whenever the per-layer bar goes back to zero.
    digests_seen: set[str] = field(default_factory=set)
    error: dict[str, Any] | None = None

    @property
    def running(self) -> bool:
        return self.finished_at is None

    @property
    def succeeded(self) -> bool:
        return self.finished_at is not None and self.error is None

    @property
    def failed(self) -> bool:
        return self.error is not None

    @property
    def percent(self) -> float | None:
        """Per-layer percentage, or ``None`` when the host has not sent numbers yet.

        ``None`` rather than zero, deliberately: a bar reading 0% while a manifest is being
        fetched is telling the reader something false about a request that is going fine.
        """
        if self.total and self.completed is not None and self.total > 0:
            return min(100.0, round(self.completed * 100 / self.total, 1))
        return None

    @property
    def detail(self) -> str:
        """One line for the screen, naming what the host is doing and how far in."""
        if self.failed and self.error:
            return str(self.error.get("summary") or "The install failed.")
        percent = self.percent
        if percent is None:
            return self.status
        return f"{self.status} — {percent:.0f}%"

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "status": self.status,
            "detail": self.detail,
            "percent": self.percent,
            "completed": self.completed,
            "total": self.total,
            "layers_seen": len(self.digests_seen),
            "running": self.running,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
        }


class PullRegistry:
    """Every install this process has started, running or finished."""

    def __init__(self) -> None:
        self._states: dict[str, PullState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def states(self) -> tuple[PullState, ...]:
        """Newest first, so the thing somebody just started is at the top."""
        return tuple(sorted(self._states.values(), key=lambda s: s.started_at, reverse=True))

    def get(self, model: str) -> PullState | None:
        return self._states.get(model)

    @property
    def running(self) -> tuple[PullState, ...]:
        return tuple(state for state in self.states() if state.running)

    def start(self, model: str, factory: Callable[[], OllamaClient]) -> PullState:
        """Begin an install, or return the one already running for this model.

        Starting the same model twice is not an error and does not start a second download — the
        host would coalesce them anyway, and reporting "already installing" as a failure would be
        an error message about a button that did what the person wanted.
        """
        existing = self._states.get(model)
        if existing is not None and existing.running:
            return existing
        state = PullState(model=model)
        self._states[model] = state
        self._tasks[model] = asyncio.create_task(self._run(state, factory))
        log.info("model_pull_started", model=model)
        return state

    async def _run(self, state: PullState, factory: Callable[[], OllamaClient]) -> None:
        client = factory()
        try:
            finished = False
            async for progress in client.pull(state.model):
                state.status = progress.status or state.status
                state.digests_seen.update([progress.digest] if progress.digest else [])
                if progress.total is not None:
                    state.total = progress.total
                    state.completed = progress.completed
                finished = progress.done
            if not finished:
                # The stream ended without the host saying "success". Marking it succeeded here
                # because nothing raised would be exactly the silent wrongness this layer is for:
                # a half-downloaded model reported as installed fails much later, somewhere else.
                state.error = {
                    "summary": (
                        f"The install stopped after {state.status!r} without the host reporting "
                        "success."
                    ),
                    "remedy": (
                        "Start it again; Ollama keeps the layers it had already fetched. If it "
                        "stops in the same place each time, the Ollama server's own log on that "
                        "machine says why."
                    ),
                    "kind": "Incomplete",
                }
            log.info("model_pull_finished", model=state.model, status=state.status, ok=finished)
        except ModelHostError as exc:
            state.error = exc.as_dict()
            state.status = "failed"
            log.error("model_pull_failed", model=state.model, error=exc.summary)
        except asyncio.CancelledError:
            state.error = {
                "summary": "The install was stopped when Sightline shut down.",
                "remedy": "Start it again; Ollama keeps the layers it had already fetched.",
                "kind": "Cancelled",
            }
            state.status = "cancelled"
            log.info("model_pull_cancelled", model=state.model)
            raise
        except Exception as exc:  # pragma: no cover - surfaced, never swallowed
            state.error = {
                "summary": f"The install failed: {type(exc).__name__}.",
                "remedy": "The Ollama server's own log on that machine has the rest.",
                "kind": type(exc).__name__,
            }
            state.status = "failed"
            log.error("model_pull_failed", model=state.model, error=type(exc).__name__)
        finally:
            state.finished_at = datetime.now(UTC)
            await client.aclose()
            self._tasks.pop(state.model, None)

    async def shutdown(self) -> None:
        """Cancel every running install and wait for it, so nothing outlives the process."""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        # return_exceptions, because shutdown must not be the thing that raises: a failed install
        # has already recorded its failure on its own state.
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()


__all__ = ["PullRegistry", "PullState"]
