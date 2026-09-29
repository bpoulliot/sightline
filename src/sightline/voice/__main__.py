"""A plain-text rendering of a course bundle, so the voice layer can be read on its own.

    python -m sightline.voice --bundle examples/sample-course --pack uccs-oscqr-3-1-3

**A demonstration, not an output format.** The workbook (item 13, :mod:`sightline.workbook`)
calls :func:`sightline.voice.render_review` and lays the sentences out its own way, as the PDF and
the review screen (items 14 and 16) are to. This prints them one per line so the register can be
read and diffed.

It makes no model call. Located evidence is not gathered, and every row that would have been asked
about says so — which is also how the demo shows the fourth slot of the closing narrative doing its
job. It writes nothing to disk.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from ..acquire.bundle import BundleError, load_bundle
from ..detect.runner import run_detectors
from ..rubric.shipped import shipped_packs
from .render import RenderedReview, render_review


def plain_text(review: RenderedReview) -> str:
    """The review as indented plain text. Deterministic: the same review prints the same bytes."""
    lines = [f"{review.pack_name} {review.pack_version}", ""]
    section = None
    for row in review.rows:
        if row.section != section:
            section = row.section
            lines.extend([f"== {section} ==", ""])
        priority = row.priority or "-"
        lines.append(f"{row.code}  [Tier {row.tier.value}] [{priority}] {row.label}")
        for sentence in row.sentences:
            indent = "      - " if sentence.role in ("finding", "quote", "check") else "    "
            lines.append(f"{indent}{sentence.text}")
            if sentence.link:
                suffix = "" if sentence.link_exact else " (by search)"
                lines.append(f"{' ' * len(indent)}  {sentence.link}{suffix}")
        lines.append("")
    lines.extend(["== Closing narrative ==", ""])
    for slot in review.narrative.slots:
        lines.append(slot.heading.text)
        lines.extend(f"    {sentence.text}" for sentence in slot.sentences)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    packs = shipped_packs()
    parser = argparse.ArgumentParser(
        prog="python -m sightline.voice",
        description="Print a course bundle's review as plain text. No model call; writes nothing.",
    )
    parser.add_argument("--bundle", required=True, help="a course bundle directory")
    parser.add_argument(
        "--pack", required=True, choices=sorted(packs), help="the rubric to review against"
    )
    parser.add_argument(
        "--links",
        action="store_true",
        help="print a Canvas link under each finding, built from the bundle's base URL",
    )
    args = parser.parse_args(argv)
    try:
        document = load_bundle(args.bundle)
    except BundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    report = run_detectors(document)
    from ..rubric.project import project

    projection = project(packs[args.pack], report)
    review = render_review(
        projection,
        report=report,
        base_url=document.base_url if args.links else None,
        course_id=document.course.id,
    )
    sys.stdout.write(plain_text(review))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
