"""Owner records for tests: another process, live or gone, on this host or another (item 43).

The processes are real. A live owner on this host is a child that sleeps until the test ends; a
gone one is a child that has exited and been reaped, so its pid answers "no such process". Faking
:func:`sightline.ownership.pid_alive` instead would test the fake.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import update

from sightline.models import Run, RunStatus, utcnow
from sightline.ownership import STALE_AFTER, this_process
from sightline.store import RunStore

#: A host name no test machine has: the command-line container beside the app.
OTHER_HOST = "sightline-cli-elsewhere"

#: A heartbeat this old is past the stale limit.
STALE = STALE_AFTER + timedelta(seconds=30)


@contextmanager
def live_process() -> Iterator[int]:
    """A process on this host that is alive for the duration. Yields its pid."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        yield child.pid
    finally:
        child.kill()
        child.wait()


def dead_pid() -> int:
    """The pid of a process on this host that has exited and been reaped."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def set_owner(
    store: RunStore,
    run_id: str,
    *,
    host: str | None,
    pid: int | None,
    heartbeat_age: timedelta | None = timedelta(0),
    status: RunStatus | None = RunStatus.RUNNING,
) -> datetime | None:
    """Write an owner record (and by default a ``running`` status) as another process would.

    Returns the heartbeat written, so a test can assert it was not touched.
    """
    heartbeat = None if heartbeat_age is None else utcnow() - heartbeat_age
    values: dict[str, object] = {
        "owner_host": host,
        "owner_pid": pid,
        "owner_heartbeat_at": heartbeat,
    }
    if status is not None:
        values["status"] = status
    with store.engine.begin() as connection:
        connection.execute(update(Run).where(Run.id == run_id).values(**values))
    return heartbeat


def this_host() -> str:
    return this_process().host


def owner_of(store: RunStore, run_id: str) -> tuple[str | None, int | None]:
    run = store.get_run(run_id)
    assert run is not None
    return run.owner_host, run.owner_pid
