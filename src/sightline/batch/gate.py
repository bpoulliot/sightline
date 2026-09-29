"""The export gate: nothing is written for a course until a person has opened its review.

SPEC §09 states the product rule in one line — *nothing exports until a human has opened the
review; no unattended report generation* — and a rule stated in a document is a rule the next item
can miss. So it is a gate with a key rather than a convention: the only way to a path under
``reports/`` is :meth:`ExportGate.destination`, and it refuses for a course whose
``review_opened_at`` is null.

**Why the guard is structural as well as behavioural.** ``tests/test_export_gate.py`` scans the
package's syntax tree and fails the build if any module other than :mod:`sightline.paths` and this
one names ``reports`` as a path — the same shape as the data-root guard item 01 built, and for the
same reason. The XLSX writer (roadmap item 13) and the PDF report (item 14) both write through
:meth:`ExportGate.save`, called by ``python -m sightline.report export``; the guard is what catches
a writer reaching for ``paths.resolve("reports", …)`` directly.

**What "a human opened the review" means here.** A timestamp set by an action a person took: the
review screen (roadmap item 16) when it lands, and ``python -m sightline.batch open-review`` until
then. The batch runner cannot set it — it has no method that does — and that is the whole design.
A completed batch therefore leaves reviewable results in the run store and on disk, and leaves the
``reports/`` directory empty.

**It is not access control.** Sightline is one process on a designer's machine with no login
(:mod:`sightline.views` explains why), and anyone who can call this can call
:meth:`ExportGate.open_review` first. The gate stops a *program* exporting unattended — a batch
that ran overnight, a later item's convenience helper, a script somebody writes. That is the
failure the rule is about: a report delivered to a faculty member that no instructional designer
ever read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..logging import get_logger
from ..models import RunCourse, as_utc
from ..paths import DataPaths
from ..store import RunStore

log = get_logger("sightline.batch.gate")

#: The reports directory, spelled here and in :mod:`sightline.paths` and nowhere else.
REPORTS = "reports"


class ReviewNotOpenedError(RuntimeError):
    """An export was asked for on a course nobody has opened the review of."""

    def __init__(self, summary: str, *, remedy: str | None = None) -> None:
        super().__init__(summary)
        self.summary = summary
        self.remedy = remedy

    @property
    def message(self) -> str:
        return " ".join(part for part in (self.summary, self.remedy) if part)


@dataclass(frozen=True)
class ExportGate:
    """Holds the rule. Every writer of an exported artifact asks it for its destination."""

    store: RunStore
    paths: DataPaths

    # -- the human action ---------------------------------------------------------------------

    def open_review(self, course_id: str) -> datetime:
        """Record that a person opened this course's review, and return when.

        Idempotent, and the timestamp does not move on a second opening — see
        :meth:`sightline.store.RunStore.mark_review_opened`.
        """
        opened = self.store.mark_review_opened(course_id)
        log.info("review_opened", course_id=course_id)
        return opened

    # -- the rule -----------------------------------------------------------------------------

    def opened_at(self, course_id: str) -> datetime | None:
        return as_utc(self._course(course_id).review_opened_at)

    def is_open(self, course_id: str) -> bool:
        return self.opened_at(course_id) is not None

    def require_open(self, course_id: str) -> datetime:
        """The timestamp, or raise. Every export path goes through here."""
        course = self._course(course_id)
        opened = as_utc(course.review_opened_at)
        if opened is None:
            raise ReviewNotOpenedError(
                f"Nobody has opened the review of course {course.ordinal + 1} in this run, so "
                "there is nothing to export yet.",
                remedy=(
                    "Open the review, confirm or change what the run proposed, and export from "
                    "there. Sightline does not generate a report nobody has read."
                ),
            )
        return opened

    def destination(self, course_id: str, filename: str) -> Path:
        """Where an exported artifact for this course may be written.

        Goes through :meth:`sightline.paths.DataPaths.resolve`, so the containment guarantee
        applies to a filename a later item builds from a course name.
        """
        self.require_open(course_id)
        return self.paths.resolve(REPORTS, course_id, filename)

    def save(self, course_id: str, filename: str, data: bytes) -> Path:
        """Write an exported artifact for this course, atomically, and return where it went.

        The writers render to bytes and hand them here (roadmap item 13): a library call such as
        ``Workbook.save(path)`` is not a write the structural guard in ``tests/test_paths.py`` can
        see, so no writer is given a path to pass one.
        """
        self.require_open(course_id)
        return self.paths.save_bytes(REPORTS, course_id, filename, data=data)

    # -- internals ----------------------------------------------------------------------------

    def _course(self, course_id: str) -> RunCourse:
        course = self.store.get_course(course_id)
        if course is None:
            raise KeyError(f"no course {course_id!r} in the run store")
        return course


__all__ = ["REPORTS", "ExportGate", "ReviewNotOpenedError"]
