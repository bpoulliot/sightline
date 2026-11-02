"""Exporting one reviewed course: the PDF and the workbook, through the gate (roadmap items 14, 16).

One function, called by both ways a person exports — ``python -m sightline.report export`` and the
review screen's export control — so the two cannot disagree about what an export holds. It reads
the course back from its review file (nothing is re-run), takes the reviewer's latest rating per
row from the override record (operator decision Q2, 2026-10-29), builds both artifacts, and only
then writes them, so a failure leaves no half an export behind. Both carry one timestamp: when the
review was opened, never the clock.
"""

from __future__ import annotations

from pathlib import Path

from ..batch.gate import ExportGate
from ..paths import DataPaths
from ..rubric.overrides import reviewer_ratings
from ..store import RunStore
from ..workbook import build_workbook, export_workbook, to_xlsx
from .loader import read_review
from .model import build_report
from .pdf import export_report, to_pdf


def export_course(store: RunStore, paths: DataPaths, course_id: str) -> list[Path]:
    """Write ``report.pdf`` and ``review.xlsx`` for one course and return where they went.

    Raises :class:`~sightline.batch.gate.ReviewNotOpenedError` for a course nobody opened the
    review of (before reading anything), ``KeyError`` for a course the store does not know, and
    :class:`~sightline.report.loader.ReviewFileError`,
    :class:`~sightline.report.model.ReportError` or :class:`~sightline.workbook.WorkbookError`
    when the artifacts cannot be built as promised.
    """
    gate = ExportGate(store, paths)
    opened = gate.require_open(course_id)
    review = read_review(paths, store, course_id)
    overrides = reviewer_ratings(store, course_id)
    pdf = to_pdf(build_report(review, created=opened, overrides=overrides))
    workbook = to_xlsx(
        build_workbook(
            review.projection,
            review.pack,
            base_url=review.base_url or "",
            created=opened,
            report=review.report,
            extractions=review.extractions,
            course_id=review.course_id,
            overrides=overrides,
        )
    )
    return [export_report(gate, course_id, pdf), export_workbook(gate, course_id, workbook)]


__all__ = ["export_course"]
