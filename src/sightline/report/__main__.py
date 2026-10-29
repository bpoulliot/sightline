"""``python -m sightline.report`` — export one reviewed course's PDF report and workbook.

    python -m sightline.report export 91ab…

The argument is a run-store course id, as ``python -m sightline.batch status RUN`` lists it. The
command writes ``report.pdf`` and ``review.xlsx`` for that course, **through the export gate and
only through it**: a course whose review nobody has opened is refused with the gate's own message,
and nothing is written (SPEC §09). Open the review first: on the review screen (roadmap item 16),
or with ``python -m sightline.batch open-review COURSE``. Any rating a reviewer recorded there is
carried beside the proposal (:mod:`sightline.report.export`).

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

from ..batch.gate import ReviewNotOpenedError
from ..config import Settings, get_settings
from ..logging import configure_logging
from ..store import RunStore
from ..workbook import WorkbookError
from .export import export_course
from .loader import ReviewFileError
from .model import ReportError


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
        if store.get_course(course_id) is None:
            print(f"No course {course_id!r} in the run store.", file=sys.stderr)
            return 2
        try:
            # Both are built before either is written, so a failure leaves no half an export.
            written = export_course(store, paths, course_id)
        except ReviewNotOpenedError as exc:
            print(exc.message, file=sys.stderr)
            return 1
        except (ReviewFileError, ReportError, WorkbookError) as exc:
            print(f"Nothing was exported: {exc}", file=sys.stderr)
            return 1
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
