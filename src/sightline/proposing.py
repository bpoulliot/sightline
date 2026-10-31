"""Proposing bindings in the background, from the screen (roadmap item 22, part two).

The *Propose bindings* action on a rubric's page runs
:func:`sightline.rubric.proposals.propose` as an asyncio task in the app process, with the review
model chosen in Settings, through item 09's client at concurrency 1. This module holds those tasks
— one at a time — and what each has done so far, the way :mod:`sightline.llm.pulls` holds model
installs.

**Progress is rows asked and rows that could not be asked, never a percentage.** The rows to ask
are counted before the first call (every row but Tier D), so the page can say how many there are;
how long each takes depends on the model, which is why no bar or estimate is drawn from them.

**One thing at a time against the model host.** Proposing is refused while a review run is
executing or runs are queued (roadmap item 46), and a run is refused while proposals are being
made (:mod:`sightline.new_run`); each refusal names the other, and the run queue waits for a
proposer. Proposals are only kept once every row has been asked: a task that stops
part-way — the host went away, the app shut down — keeps nothing and says why, and proposing again
starts over.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .llm.client import OllamaClient
from .llm.errors import ModelHostError
from .logging import get_logger
from .paths import DataPaths
from .rubric.accepted import BindingStore, asked_rows
from .rubric.pack import RubricPack
from .rubric.proposals import RowOutcome, propose

log = get_logger("sightline.proposing")


@dataclass(kw_only=True)
class ProposingState:
    """One proposing task: which pack, against which model, and how far it has got."""

    pack_id: str
    pack_name: str
    model: str
    #: The digest of the bindings in force when it started; ``None`` is the shipped bindings.
    over: str | None
    to_ask: int
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))
    asked: int = 0
    could_not: int = 0
    finished_at: dt.datetime | None = None
    #: The proposal set it kept, once it finished.
    set_id: str | None = None
    #: Why it stopped without keeping anything: ``summary`` and ``remedy``.
    error: dict[str, Any] | None = None

    @property
    def running(self) -> bool:
        return self.finished_at is None

    def counted(self, outcome: RowOutcome) -> None:
        if outcome.not_asked is not None:
            return
        self.asked += 1
        if outcome.failure is not None:
            self.could_not += 1

    def to_dict(self) -> dict[str, Any]:
        """What the page's optional script compares to know when to re-read the page."""
        return {
            "pack": self.pack_id,
            "running": self.running,
            "asked": self.asked,
            "could_not": self.could_not,
            "set": self.set_id,
            "error": None if self.error is None else self.error.get("summary"),
        }


class ProposingRegistry:
    """The proposing task this process is running, and the last one per pack."""

    def __init__(self) -> None:
        self._states: dict[str, ProposingState] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def get(self, pack_id: str) -> ProposingState | None:
        return self._states.get(pack_id)

    @property
    def running(self) -> ProposingState | None:
        for state in self._states.values():
            if state.running:
                return state
        return None

    def start(
        self,
        pack: RubricPack,
        *,
        over: str | None,
        model: str,
        client_factory: Callable[[], OllamaClient],
        paths: DataPaths,
    ) -> ProposingState:
        """Begin proposing over ``pack`` (the pack as runs use it now). The caller has checked that
        nothing else is using the model host; this refuses a second proposing task regardless."""
        busy = self.running
        if busy is not None:
            raise RuntimeError(f"bindings are already being proposed for {busy.pack_id}")
        state = ProposingState(
            pack_id=pack.id,
            pack_name=pack.name,
            model=model,
            over=over,
            to_ask=asked_rows(pack),
        )
        self._states[pack.id] = state
        self._tasks[pack.id] = asyncio.create_task(
            self._run(state, pack, client_factory, paths), name=f"sightline-propose-{pack.id}"
        )
        log.info("binding_proposals_started", pack=pack.id, model=model, rows=state.to_ask)
        return state

    async def _run(
        self,
        state: ProposingState,
        pack: RubricPack,
        client_factory: Callable[[], OllamaClient],
        paths: DataPaths,
    ) -> None:
        client = client_factory()
        try:
            proposals = await propose(pack, client, state.model, progress=state.counted)
            state.set_id = BindingStore(paths).save_proposals(proposals)
        except ModelHostError as exc:
            state.error = {"summary": exc.summary, "remedy": exc.remedy}
            log.warning("binding_proposals_stopped", pack=pack.id, error=exc.summary)
        except asyncio.CancelledError:
            state.error = {
                "summary": "Proposing stopped when Sightline shut down, and nothing was kept.",
                "remedy": "Propose again; it starts from the first row.",
            }
            raise
        except Exception as exc:  # surfaced on the page, never swallowed
            state.error = {
                "summary": f"Proposing stopped on an unexpected error ({type(exc).__name__}).",
                "remedy": "Sightline's log has the rest. Propose again once it is fixed.",
            }
            log.exception("binding_proposals_crashed", pack=pack.id)
        finally:
            state.finished_at = dt.datetime.now(dt.UTC)
            await client.aclose()

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # already recorded on its state; do not mask shutdown
                log.warning("binding_proposals_shutdown_error", error=str(exc))
        self._tasks.clear()


__all__ = ["ProposingRegistry", "ProposingState"]
