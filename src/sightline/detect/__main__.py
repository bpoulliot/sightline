"""``python -m sightline.detect`` — run the detectors over a course and print what they examined.

    python -m sightline.detect --bundle examples/sample-course
    python -m sightline.detect --bundle examples/sample-course --export ./data/exports/.../files

Needs no Canvas token when given a bundle. It prints, per detector, the number of artifacts opened
alongside the number of observations recorded — which is the invariant this framework carries made
visible at a terminal rather than asserted only in a test. A detector that could not run is listed
under its own heading and is never quietly absent.

``--export`` points at an unpacked content export's ``files/`` directory, which is where document
bytes come from. Without it the detectors that read PDFs and Office files report what they could
not read instead of reporting nothing, and that is what the output says.

It prints observations, not verdicts: a code, a location and the value measured. Nothing here says
whether a course is good.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..acquire.bundle import BundleError, load_bundle
from ..model import CourseDocument
from .base import ResultState
from .runner import DetectionReport, run_detectors
from .sources import ExportFileSource


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sightline.detect",
        description="Run Sightline's detectors over a normalized course.",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        required=True,
        help="A course bundle directory, e.g. examples/sample-course — needs no token.",
    )
    parser.add_argument(
        "--export",
        type=Path,
        help="An unpacked content export's files/ directory, where document bytes live.",
    )
    parser.add_argument(
        "--detector",
        action="append",
        default=[],
        metavar="ID",
        help="Run only this detector. Repeatable.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the whole report to stdout as JSON."
    )
    return parser


def describe(report: DetectionReport, document: CourseDocument) -> str:
    lines = [report.statement(), ""]
    for result in report.results:
        if result.state is ResultState.NOT_EXAMINED:
            continue
        lines.append(f"  {result.detector}  {result.statement()[len(result.detector) + 2 :]}")
        for finding in result.findings:
            link = finding.location.deep_link(document.base_url)
            where = finding.location.within or finding.location.title
            lines.append(f"      {finding.severity.value:<9} {finding.code}  ({where})")
            lines.append(f"      {'':<9} {finding.observed}")
            lines.append(f"      {'':<9} {link}")
        for entry in result.undetermined:
            lines.append(f"      {'not read':<9} {entry.reason}")

    if report.not_examined:
        lines += [
            "",
            "  Detectors that could not run — their criteria are NOT covered by this run:",
        ]
        for result in report.not_examined:
            lines.append(f"    {result.detector}  {result.not_examined_because}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = load_bundle(args.bundle)
    except BundleError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    files = ExportFileSource(args.export) if args.export else None
    only = None
    if args.detector:
        from .base import registry

        known = registry()
        missing = [name for name in args.detector if name not in known]
        if missing:
            print(
                f"no such detector: {', '.join(missing)}. Registered: {', '.join(known)}",
                file=sys.stderr,
            )
            return 2
        only = [known[name]() for name in args.detector]

    report = run_detectors(document, files=files, only=only)
    if args.json:
        import json

        sys.stdout.write(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    else:
        print(describe(report, document))
    # A detector that could not run is a gap in the review, and a command that exits 0 over one is
    # a command a script will treat as a clean course.
    return 1 if report.not_examined else 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
