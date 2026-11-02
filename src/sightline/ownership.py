"""Who is executing a run, and whether they still are (roadmap item 43).

A run can be executed by the application's background runner or by ``python -m sightline.batch
run|resume``, and those can be different processes — different containers, even — sharing one
data volume and so one run store. Before this module the store could not tell whose a ``running``
run was, and restart recovery took over every one of them: starting or stopping the app while a
command-line run was going marked it interrupted, and the app's next start executed it a second
time, concurrently.

So a process that executes a run records itself as the run's **owner** — host name and process id
— and refreshes a **heartbeat** time while it works. Another process takes the run over only when
the owner is gone:

- no owner is recorded. A ``running`` row written before this module existed, or by a process
  killed between its status write and its claim, has none, and is orphaned as it always was;
- the heartbeat is older than :data:`STALE_AFTER`, whatever the host;
- the owner is on **this** host and its process id is not alive.

**A process id means something only when the host name matches.** The app container and a
command-line container share the data volume but not a process namespace, and the host name is the
container's; pid 7 in one is not pid 7 in the other. Across hosts the heartbeat is the whole test.

Within one host a process id is reused, and in a container it is reused by design: the app is the
same small pid every time the container restarts, under the same host name. A run whose recorded
owner has *this* process's id is therefore live only if this process actually holds it (see
:func:`holds`); otherwise it was the previous incarnation's, and that process is gone. Reuse by an
unrelated process with a different id is not detectable from a pid alone, and the heartbeat is what
ends it.

The heartbeat is a thread rather than an asyncio task on purpose. Stages await a model host for
minutes at a time and some of their work is synchronous CPU in the event loop; a beat that rode the
event loop would be exactly as late as the slowest stage. A thread beats on its own interval
whatever a stage is doing, and :data:`STALE_AFTER` is ten intervals, so a busy database or a
stalled loop has to miss ten beats in a row before anybody takes the run over.

Clocks: the heartbeat is compared against this process's clock. Containers on one machine share a
clock. Processes on two machines sharing a volume would need their clocks to agree to well inside
:data:`STALE_AFTER`, which an NTP-synchronised pair does.
"""

from __future__ import annotations

import os
import socket
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import TracebackType
from typing import TYPE_CHECKING, Protocol

from .logging import get_logger
from .models import as_utc, utcnow

if TYPE_CHECKING:  # pragma: no cover - typing only; the store imports this module
    from .store import RunStore

log = get_logger("sightline.ownership")

#: How often an owner refreshes its heartbeat while it executes a run.
HEARTBEAT_INTERVAL = timedelta(seconds=30)

#: How old a heartbeat may be before its owner is treated as gone. Ten intervals: one missed beat
#: — a write that met a locked database, a loop held by a long synchronous parse — is noise, and a
#: run is taken over only when its owner has been silent for five minutes. That is also how long a
#: command-line run that was killed outright on another host blocks Start before Start goes ahead.
STALE_AFTER = HEARTBEAT_INTERVAL * 10

#: Run ids this process is executing now. Module-level because ownership is a property of the
#: process, not of any one store or runner object in it.
_held: set[str] = set()
_held_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class RunOwner:
    """A process, as the owner record names it."""

    host: str
    pid: int


class OwnerRecord(Protocol):
    """The three owner columns, as :class:`sightline.models.Run` carries them."""

    id: str
    owner_host: str | None
    owner_pid: int | None
    owner_heartbeat_at: datetime | None


def this_process() -> RunOwner:
    """The owner this process records. Read each time: a fork has a new pid."""
    return RunOwner(host=socket.gethostname(), pid=os.getpid())


def pid_alive(pid: int) -> bool:
    """Whether a process with this id exists on this host.

    Signal 0 checks existence without delivering anything. A process owned by another user answers
    ``PermissionError``, which still means it exists. A pid that is not positive is never alive:
    ``kill(0, 0)`` would address this process's whole group.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def holds(run_id: str) -> bool:
    """Whether this process is executing ``run_id`` now."""
    with _held_lock:
        return run_id in _held


def orphan_reason(
    record: OwnerRecord, *, now: datetime | None = None, me: RunOwner | None = None
) -> str | None:
    """Why ``record``'s owner is gone, or ``None`` while it is live.

    The reason is the sentence recovery writes into the run log, so it says what was observed.
    """
    if record.owner_host is None or record.owner_pid is None or record.owner_heartbeat_at is None:
        return "no process was recorded as executing it"
    current = now or utcnow()
    heartbeat = as_utc(record.owner_heartbeat_at)
    assert heartbeat is not None  # checked above
    if current - heartbeat > STALE_AFTER:
        minutes = int((current - heartbeat).total_seconds() // 60)
        return (
            f"the process executing it last reported {minutes} minute(s) ago, past the "
            f"{int(STALE_AFTER.total_seconds() // 60)}-minute limit"
        )
    process = me or this_process()
    if record.owner_host != process.host:
        return None  # another host: the heartbeat is the whole test, and it is fresh
    if record.owner_pid == process.pid:
        if holds(record.id):
            return None
        return "it was recorded by an earlier process with this process's id, which has stopped"
    if not pid_alive(record.owner_pid):
        return "the process executing it on this host has stopped"
    return None


def owner_is_live(
    record: OwnerRecord, *, now: datetime | None = None, me: RunOwner | None = None
) -> bool:
    """Whether the process recorded as executing ``record`` is still doing so."""
    return orphan_reason(record, now=now, me=me) is None


class RunOwnedElsewhereError(RuntimeError):
    """The run has a live owner, so this process may not execute it."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(
            f"Run {run_id} is being executed by another process. It is left to that process; "
            "once that process stops, the run can be resumed."
        )


class _Heartbeat(threading.Thread):
    def __init__(self, store: RunStore, run_id: str, owner: RunOwner, interval: timedelta) -> None:
        super().__init__(name=f"sightline-heartbeat-{run_id[:8]}", daemon=True)
        self.store = store
        self.run_id = run_id
        self.owner = owner
        self.interval = interval
        self.stopping = threading.Event()
        self.beats = 0

    def run(self) -> None:
        while not self.stopping.wait(self.interval.total_seconds()):
            try:
                kept = self.store.heartbeat_run(self.run_id, self.owner)
            except Exception as exc:  # one missed beat is what STALE_AFTER is sized for
                log.warning(
                    "run_heartbeat_failed", run_id=self.run_id, error_type=type(exc).__name__
                )
                continue
            if not kept:
                # Another process took the run over: this one was silent past the limit. Stop
                # beating, so the heartbeat does not claim a run this process no longer owns.
                log.warning("run_ownership_lost", run_id=self.run_id)
                return
            self.beats += 1

    def stop(self) -> None:
        self.stopping.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=self.interval.total_seconds() + 10)


class RunHold:
    """This process's ownership of one run, for as long as it executes it.

    Entering claims the run — atomically, so of two processes reaching for the same run one wins —
    and starts the heartbeat; leaving stops the heartbeat and clears the owner record. A run still
    ``running`` when its owner leaves did not finish, and is marked interrupted on the way out
    rather than left for the next recovery to find.
    """

    def __init__(
        self, store: RunStore, run_id: str, *, interval: timedelta = HEARTBEAT_INTERVAL
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.interval = interval
        self.owner = this_process()
        self._heartbeat: _Heartbeat | None = None

    def __enter__(self) -> RunHold:
        if not self.store.claim_run(self.run_id, self.owner):
            raise RunOwnedElsewhereError(self.run_id)
        with _held_lock:
            _held.add(self.run_id)
        self._heartbeat = _Heartbeat(self.store, self.run_id, self.owner, self.interval)
        self._heartbeat.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._heartbeat is not None:
            self._heartbeat.stop()
            self._heartbeat = None
        with _held_lock:
            _held.discard(self.run_id)
        self.store.release_run(self.run_id, self.owner)


__all__ = [
    "HEARTBEAT_INTERVAL",
    "STALE_AFTER",
    "OwnerRecord",
    "RunHold",
    "RunOwnedElsewhereError",
    "RunOwner",
    "holds",
    "orphan_reason",
    "owner_is_live",
    "pid_alive",
    "this_process",
]
