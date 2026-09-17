"""Asking Canvas to package a copy of a course, waiting for it, and reading it back.

**Sightline does not modify course content; it asks Canvas to package a copy of the course it is
reviewing.** Nothing a learner or an instructor sees changes. What appears is an export artifact in
the course's own export list, the same one that appears when a teacher uses Settings -> Export
Course Content. This is the only request in the package that is not a ``GET``, and
``tests/test_canvas_scopes.py`` fails the build if a second one arrives.

Why it is here at all: the detectors that read a document's *own bytes* — whether a PDF is tagged,
whether a Word file uses real heading styles, whether a document can be downloaded — have nothing
to read without an export, and the Canvas API does not hand those bytes over. Item 03 used an
export the course already had, which meant every course whose teacher had not exported it by hand
was reviewed with those checks dead. Asking a designer to export sixteen courses before reviewing
them is the friction that decides whether a tool gets used.

**A fresh export, always.** Nothing here reuses an existing one. Canvas does not bump a course's
``updated_at`` when a page inside it is edited, so there is no cheap way to know whether an export
made last week matches the course as it is now, and a review of last week's course reported as
today's is exactly the silent wrongness this project exists to avoid. The opt-out path — reading an
export the course already has — lives in :mod:`sightline.acquire.service` and is what a deployment
that has switched the request off falls back to; that is a person choosing older bytes knowingly,
which is a different thing from this code choosing them.

**Three ways this ends badly and each is reported rather than absorbed.** Canvas can refuse the
request outright, because exporting needs a permission that reading does not. It can accept the
request and then report the export ``failed``, which is not an empty export — an empty document
normalized out of a failed export is a course every downstream detector reports as clean. And it
can sit in a state this code has never heard of, which is polled with a ceiling and then reported
by name rather than waited on forever.

**The wait is slow and is made visible.** An export is asynchronous: the ``POST`` comes back
``created`` or ``exporting``, and a large course becomes ``exported`` minutes later. Every poll
calls ``on_progress`` and logs, so a run that is waiting says so. A run that appears frozen for six
minutes is the same failure as a silent one wearing a different hat (SPEC §09).

**The download link is not logged.** Canvas hands back an attachment URL carrying a ``verifier``
query parameter, which is a bearer credential in a query string, and the host that ultimately
serves the bytes is a storage back end that nothing has registered with
:mod:`sightline.redaction`. So nothing here logs a URL or a host: what is logged is the export id,
its state, the attempt number and the elapsed time. :class:`ContentExport` keeps its ``repr`` free
of the URL too, because a ``repr`` is what ends up in a traceback and in pytest failure output.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..logging import get_logger
from .client import API_ROOT, CanvasClient
from .errors import (
    CanvasExportFailedError,
    CanvasExportNotPermittedError,
    CanvasExportStateError,
    CanvasExportTimeoutError,
    CanvasPermissionError,
    CanvasRateLimitedError,
    CanvasResponseError,
)

log = get_logger("sightline.canvas.exports")

#: The export Canvas builds. A ``qti`` export is assessments only and a ``zip`` export is the Files
#: tab with no manifest; a common cartridge is the one that carries the course.
EXPORT_TYPE = "common_cartridge"

#: States Canvas reports while it is still working. Anything outside these three sets is unknown
#: and is reported by name rather than waited on.
PENDING_STATES = frozenset({"created", "exporting"})
FINISHED_STATE = "exported"
FAILED_STATE = "failed"

#: Poll timing. The first check is soon, because a small course finishes in seconds; the interval
#: then backs off, because a large one takes minutes and polling it every two seconds spends the
#: token's rate limit on nothing.
FIRST_DELAY_SECONDS = 2.0
BACKOFF = 1.6
MAX_DELAY_SECONDS = 20.0

#: The ceiling. Reaching it is a reported failure with a remedy, never a longer wait: a poll with
#: no ceiling is a run that hangs, and a run that hangs is indistinguishable from one that died.
CEILING_SECONDS = 900.0

Sleeper = Callable[[float], Awaitable[Any]]
Clock = Callable[[], float]

#: Hands the finished export's bytes somewhere. Supplied by the caller rather than implemented
#: here: :mod:`sightline.paths` is the only module in the package permitted to write a file, and
#: :func:`sightline.acquire.archive.download_archive` is the one path that streams through it with
#: the download URL validated first. A second implementation of that here would be a second thing
#: to get wrong.
Downloader = Callable[["ContentExport"], Awaitable[Path]]


@dataclass(frozen=True)
class PollPacing:
    """How long to wait, and what to wait with.

    The clock and the sleep are parameters because a ceiling proved by actually waiting for it is
    a ceiling nobody keeps in a test suite — and a ceiling with no test is a ceiling that quietly
    stops working. One object rather than three arguments, because they are only ever varied
    together.
    """

    ceiling_seconds: float = CEILING_SECONDS
    sleep: Sleeper = asyncio.sleep
    clock: Clock = time.monotonic


DEFAULT_PACING = PollPacing()


@dataclass(frozen=True)
class ContentExport:
    """One Canvas content export, as the API reports it.

    ``attachment_url`` is a signed, expiring link that carries a credential in its query string. It
    is deliberately kept out of ``__repr__``: a ``repr`` reaches tracebacks and test output, both
    of which are places a secret escapes from.
    """

    id: str
    workflow_state: str
    export_type: str | None = None
    created_at: str | None = None
    progress: int | None = None
    attachment_url: str | None = None

    def __repr__(self) -> str:
        return (
            f"<ContentExport id={self.id!r} workflow_state={self.workflow_state!r} "
            f"attachment={'present' if self.attachment_url else 'absent'}>"
        )

    @property
    def finished(self) -> bool:
        return self.workflow_state == FINISHED_STATE

    @property
    def pending(self) -> bool:
        return self.workflow_state in PENDING_STATES

    @classmethod
    def from_payload(cls, payload: Any, *, endpoint: str) -> ContentExport:
        """Read Canvas's ``content_export`` object, or say what was missing from it."""
        if not isinstance(payload, dict) or "id" not in payload:
            raise CanvasResponseError(
                "Canvas did not return a content export object.",
                detail=f"{endpoint} returned no object with an id in it.",
                endpoint=endpoint,
            )
        state = payload.get("workflow_state")
        if not state:
            raise CanvasResponseError(
                "Canvas returned a content export with no workflow state.",
                detail=(
                    f"{endpoint} returned export {payload['id']} without a 'workflow_state', so "
                    "there is nothing to wait on."
                ),
                endpoint=endpoint,
            )
        attachment = payload.get("attachment")
        url = attachment.get("url") if isinstance(attachment, dict) else None
        return cls(
            id=str(payload["id"]),
            workflow_state=str(state),
            export_type=_optional_str(payload.get("export_type")),
            created_at=_optional_str(payload.get("created_at")),
            progress=payload.get("progress") if isinstance(payload.get("progress"), int) else None,
            attachment_url=str(url) if url else None,
        )


@dataclass(frozen=True)
class ExportProgress:
    """One observation of a running export, for whatever is showing the wait.

    Carries facts and no verdict: which attempt, what Canvas said, how long it has been, and how
    long until the next check. The run-progress screen renders these; until it exists the CLI
    prints them and :func:`wait_for_export` logs them, so the wait is never invisible.
    """

    export_id: str
    state: str
    attempt: int
    elapsed_seconds: float
    next_check_in_seconds: float | None
    percent: int | None = None

    @property
    def waiting(self) -> bool:
        return self.next_check_in_seconds is not None

    @property
    def message(self) -> str:
        head = (
            f"Canvas reports export {self.export_id} as '{self.state}' after "
            f"{self.elapsed_seconds:.0f}s"
        )
        if self.percent is not None:
            head += f", {self.percent}% built"
        if self.next_check_in_seconds is None:
            return f"{head}."
        return f"{head}. Checking again in {self.next_check_in_seconds:.0f}s."


@dataclass(frozen=True)
class FreshExport:
    """A finished export and what it took to get it."""

    export: ContentExport
    archive: Path
    attempts: int
    elapsed_seconds: float

    def detail(self) -> str:
        """One line of counts, in the declarative register of SPEC §08 tier A."""
        return (
            f"Asked Canvas to build a fresh export of this course and read it. Canvas finished it "
            f"after {self.elapsed_seconds:.0f}s over {self.attempts} check(s); it appears in the "
            f"course's export list as export {self.export.id}."
        )


ProgressCallback = Callable[[ExportProgress], None]


# ---------------------------------------------------------------------------------------------
# The three steps
# ---------------------------------------------------------------------------------------------


async def request_export(
    client: CanvasClient, course_id: str, *, export_type: str = EXPORT_TYPE
) -> ContentExport:
    """``POST /api/v1/courses/:id/content_exports`` — ask Canvas to package the course.

    No ``skip_notifications``. Canvas notifies the token's own user that an export was made, and
    suppressing that would be hiding the one signal the account owner gets about a request made on
    their behalf. The write is meant to be visible.
    """
    path = f"{API_ROOT}/courses/{course_id}/content_exports"
    endpoint = f"POST {path}"
    try:
        payload = await client.post_json(path, export_type=export_type)
    except CanvasRateLimitedError:
        # Throttling is a 403 with a body that says so, and it is not a permission problem. Its
        # own remedy is already right; re-labelling it as "not permitted" would send somebody to
        # change a role over a rate limit.
        raise
    except CanvasPermissionError as exc:
        raise CanvasExportNotPermittedError(
            f"{client.host} refused to build an export of course {course_id}.",
            detail=exc.detail,
            status_code=exc.status_code,
            endpoint=endpoint,
        ) from exc
    export = ContentExport.from_payload(payload, endpoint=endpoint)
    log.info(
        "content_export_requested",
        course_id=course_id,
        export_id=export.id,
        export_type=export.export_type,
        state=export.workflow_state,
    )
    return export


async def read_export(client: CanvasClient, course_id: str, export_id: str) -> ContentExport:
    """``GET /api/v1/courses/:id/content_exports/:export_id`` — one poll.

    Canvas also publishes a ``progress_url`` on the export. It is not followed: it is a second
    endpoint needing a second scope to learn the same thing the export object already reports.
    """
    path = f"{API_ROOT}/courses/{course_id}/content_exports/{export_id}"
    payload = await client.get_json(path)
    return ContentExport.from_payload(payload, endpoint=f"GET {path}")


async def wait_for_export(
    client: CanvasClient,
    course_id: str,
    export: ContentExport,
    *,
    on_progress: ProgressCallback | None = None,
    pacing: PollPacing = DEFAULT_PACING,
) -> ContentExport:
    """Poll ``export`` until Canvas finishes it, or fail with something to do about it.

    Terminates on every path. ``exported`` returns; ``failed`` raises; a state outside the pending
    set raises *naming the state*, because polling a state nobody has heard of is how a run waits
    forever; and the ceiling raises rather than extending. There is no branch here that loops
    without either sleeping against the ceiling or leaving the function.
    """
    started = pacing.clock()
    attempts = 1
    current = export
    delay = FIRST_DELAY_SECONDS

    while True:
        elapsed = pacing.clock() - started

        if current.finished:
            _report(on_progress, current, attempts, elapsed, None)
            log.info(
                "content_export_ready",
                course_id=course_id,
                export_id=current.id,
                attempts=attempts,
                elapsed_seconds=round(elapsed, 1),
                attachment=bool(current.attachment_url),
            )
            return current

        if current.workflow_state == FAILED_STATE:
            _report(on_progress, current, attempts, elapsed, None)
            log.warning(
                "content_export_failed",
                course_id=course_id,
                export_id=current.id,
                attempts=attempts,
                elapsed_seconds=round(elapsed, 1),
            )
            raise CanvasExportFailedError(
                f"Canvas reported the export of course {course_id} as failed.",
                detail=(
                    f"Export {current.id} reached workflow_state 'failed' after "
                    f"{elapsed:.0f}s and {attempts} check(s). A failed export is not an empty "
                    "one: no document bytes were examined."
                ),
                endpoint=f"GET {API_ROOT}/courses/{course_id}/content_exports/{current.id}",
            )

        if not current.pending:
            _report(on_progress, current, attempts, elapsed, None)
            log.warning(
                "content_export_unknown_state",
                course_id=course_id,
                export_id=current.id,
                state=current.workflow_state,
                attempts=attempts,
            )
            raise CanvasExportStateError(
                (
                    f"Canvas reported the export of course {course_id} in a state Sightline does "
                    f"not recognise: '{current.workflow_state}'."
                ),
                detail=(
                    f"Export {current.id} is neither "
                    f"{' nor '.join(sorted(PENDING_STATES))} nor '{FINISHED_STATE}' nor "
                    f"'{FAILED_STATE}', so Sightline stopped rather than waiting on it."
                ),
                endpoint=f"GET {API_ROOT}/courses/{course_id}/content_exports/{current.id}",
            )

        if elapsed + delay > pacing.ceiling_seconds:
            _report(on_progress, current, attempts, elapsed, None)
            log.warning(
                "content_export_timed_out",
                course_id=course_id,
                export_id=current.id,
                state=current.workflow_state,
                attempts=attempts,
                elapsed_seconds=round(elapsed, 1),
                ceiling_seconds=pacing.ceiling_seconds,
            )
            raise CanvasExportTimeoutError(
                f"Canvas had not finished the export of course {course_id} in time.",
                detail=(
                    f"Export {current.id} was still '{current.workflow_state}' after "
                    f"{elapsed:.0f}s and {attempts} check(s); Sightline waits "
                    f"{pacing.ceiling_seconds:.0f}s."
                ),
                endpoint=f"GET {API_ROOT}/courses/{course_id}/content_exports/{current.id}",
            )

        _report(on_progress, current, attempts, elapsed, delay)
        log.info(
            "content_export_waiting",
            course_id=course_id,
            export_id=current.id,
            state=current.workflow_state,
            attempt=attempts,
            elapsed_seconds=round(elapsed, 1),
            next_check_in_seconds=round(delay, 1),
        )
        await pacing.sleep(delay)
        delay = min(delay * BACKOFF, MAX_DELAY_SECONDS)
        current = await read_export(client, course_id, current.id)
        attempts += 1


async def build_fresh_export(
    client: CanvasClient,
    course_id: str,
    *,
    download: Downloader,
    on_progress: ProgressCallback | None = None,
    export_type: str = EXPORT_TYPE,
    pacing: PollPacing = DEFAULT_PACING,
) -> FreshExport:
    """Request, wait, download. The whole flow, in the order it happens.

    ``download`` receives the finished export and returns the path the archive landed on. It is
    injected because where bytes land is :mod:`sightline.paths`'s decision, not this module's.
    """
    started = pacing.clock()
    requested = await request_export(client, course_id, export_type=export_type)

    # The poll reports progress exactly once per check, so counting the observations *is* the
    # number of checks. Counting it here rather than returning it from the poll keeps that
    # function's contract to one thing — the finished export — and keeps the figure a count of
    # something that happened rather than an estimate of it.
    checks = 0

    def observe(progress: ExportProgress) -> None:
        nonlocal checks
        checks = progress.attempt
        if on_progress is not None:
            on_progress(progress)

    finished = await wait_for_export(
        client,
        course_id,
        requested,
        on_progress=observe,
        pacing=pacing,
    )
    if not finished.attachment_url:
        raise CanvasResponseError(
            f"Canvas finished the export of course {course_id} but attached no file to it.",
            detail=(
                f"Export {finished.id} reported workflow_state '{FINISHED_STATE}' with no "
                "attachment, so there is nothing to download."
            ),
            endpoint=f"GET {API_ROOT}/courses/{course_id}/content_exports/{finished.id}",
            remedy=(
                "Check the course's export list in Canvas under Settings -> Export Course "
                "Content. If the export is listed as complete there, download it by hand and "
                "report this: Canvas finished an export without publishing its file."
            ),
        )
    archive = await download(finished)
    return FreshExport(
        export=finished,
        archive=archive,
        attempts=checks,
        elapsed_seconds=pacing.clock() - started,
    )


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def _report(
    on_progress: ProgressCallback | None,
    export: ContentExport,
    attempt: int,
    elapsed: float,
    next_delay: float | None,
) -> None:
    if on_progress is None:
        return
    on_progress(
        ExportProgress(
            export_id=export.id,
            state=export.workflow_state,
            attempt=attempt,
            elapsed_seconds=elapsed,
            next_check_in_seconds=next_delay,
            percent=export.progress,
        )
    )


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "BACKOFF",
    "CEILING_SECONDS",
    "EXPORT_TYPE",
    "FAILED_STATE",
    "FINISHED_STATE",
    "FIRST_DELAY_SECONDS",
    "MAX_DELAY_SECONDS",
    "PENDING_STATES",
    "DEFAULT_PACING",
    "ContentExport",
    "ExportProgress",
    "FreshExport",
    "PollPacing",
    "ProgressCallback",
    "build_fresh_export",
    "read_export",
    "request_export",
    "wait_for_export",
]
