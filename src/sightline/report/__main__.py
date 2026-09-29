"""``python -m sightline.report`` — export one reviewed course's PDF report and workbook.

    python -m sightline.report export 91ab…

The argument is a run-store course id, as ``python -m sightline.batch status RUN`` lists it. The
command writes ``report.pdf`` and ``review.xlsx`` for that course, **through the export gate and
only through it**: a course whose review nobody has opened is refused with the gate's own message,
and nothing is written (SPEC §09). Open the review first — ``python -m sightline.batch
open-review COURSE`` until the review screen (roadmap item 16) lands.

It lives here rather than in :mod:`sightline.batch` on purpose. The batch command runs courses
unattended and says it "does not export anything and cannot be made to"; this one is the human
action that follows a review, and keeping the two apart keeps that sentence true.

Nothing is re-run. The course is read back from the review file its run saved
(:mod:`sightline.report.loader`), and both artifacts carry one timestamp: when the review was
opened, never the clock.
"""

from __future__ import annotations

import argparse
import sys

from ..batch.gate import ExportGate, ReviewNotOpenedError
from ..config import Settings, get_settings
from ..logging import configure_logging
from ..store import RunStore
from ..workbook import WorkbookError, build_workbook, export_workbook, to_xlsx
from .loader import ReviewFileError, read_review
from .model import ReportError, build_report
from .pdf import export_report, to_pdf


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sightline.report",
        description="Export a reviewed course's PDF report and workbook, through the export gate.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser(
        "export", help="Write report.pdf and review.xlsx for one course whose review was opened."
    )
    export.add_argument("course_id", help="A run-store course id.")
    return parser


def export(course_id: str, settings: Settings) -> int:
    paths = settings.paths.ensure()
    store = RunStore.open(paths)
    try:
        gate = ExportGate(store, paths)
        try:
            opened = gate.require_open(course_id)
        except KeyError:
            print(f"No course {course_id!r} in the run store.", file=sys.stderr)
            return 2
        except ReviewNotOpenedError as exc:
            print(exc.message, file=sys.stderr)
            return 1
        try:
            review = read_review(paths, store, course_id)
            pdf = to_pdf(build_report(review, created=opened))
            workbook = to_xlsx(
                build_workbook(
                    review.projection,
                    review.pack,
                    base_url=review.base_url or "",
                    created=opened,
                    report=review.report,
                    extractions=review.extractions,
                    course_id=review.course_id,
                )
            )
        except (ReviewFileError, ReportError, WorkbookError) as exc:
            print(f"Nothing was exported: {exc}", file=sys.stderr)
            return 1
        # Both are built before either is written, so a failure leaves no half an export behind.
        written = [export_report(gate, course_id, pdf), export_workbook(gate, course_id, workbook)]
        for path in written:
            print(path.relative_to(paths.root))
        return 0
    finally:
        store.dispose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    match args.command:
        case "export":
            return export(args.course_id, settings)
    return 2  # pragma: no cover - argparse rejects anything else


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
