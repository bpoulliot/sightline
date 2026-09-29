"""Roadmap item 30's instrument: what parsing course bodies costs the detector stage, at size.

    .venv/bin/python scripts/measure_parse_cost.py                 # or ./scripts/dev.sh python ...
    .venv/bin/python scripts/measure_parse_cost.py --copies 20 --repeats 5

The committed sample course is fifteen short bodies, too small to decide anything from. This builds
a synthetic course from it — every body-carrying artifact repeated ``--copies`` times with distinct
ids — in two shapes: the sample's own bodies (a few hundred bytes each), and every body replaced by
all fifteen sample bodies concatenated (about 8.6 KB, the length of a real content page). Both are
built from ``examples/sample-course`` and nothing else, so the course stays synthetic.

Each shape is run two ways over the same code. **shared** is :func:`run_detectors` as shipped: one
:class:`~sightline.detect.base.Parses` for the whole run. **unshared** runs every detector through
its own :func:`run_detectors` call, so each gets a fresh ``Parses`` — which is exactly what every
detector did before item 30, when each parsed every body for itself.

It reports the number of :func:`~sightline.detect.html_scan.scan` calls, the wall time of the
whole detector run, and the share of it spent inside ``scan()``. Until item 31 it also timed the
structure cluster's regex-based ``plain_text()``; item 31 moved those detectors onto the shared
parse and retired the helper, so ``scan()`` is now the only way a detector reads a body's text.

**The counter self-tests in both directions before it reports.** One body-reading detector run
alone (D21) must count exactly one parse per body, and a detector that reads no bodies (D11) must
count zero parses. Either failing refuses the report: a counter
wrapped around the wrong name reads zero and looks like a free parse, and one wrapped twice reads
double and looks like a problem worth fixing.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import sightline.detect.base as base
import sightline.detect.mobile as mobile
import sightline.extract.passages as passages
from sightline.acquire import load_bundle
from sightline.detect import Detector, detectors, html_scan, run_detectors, structure
from sightline.model import CourseDocument

REPO = Path(__file__).resolve().parent.parent
BODY_FIELDS = ("pages", "assignments", "quizzes", "discussions", "announcements")

#: Every module that binds ``scan`` by name. A call through a module missing here is not
#: counted — which is what the self-test exists to catch.
SCAN_SITES = (html_scan, base, structure, mobile, passages)


@dataclass
class Tally:
    calls: int = 0
    seconds: float = 0.0


@contextmanager
def counted(
    sites: tuple[object, ...], name: str, original: Callable, tally: Tally
) -> Iterator[None]:
    """Wrap ``name`` wherever ``sites`` bound it, so every call is timed and counted."""

    def wrapper(*args: object, **kwargs: object) -> object:
        tally.calls += 1
        start = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            tally.seconds += time.perf_counter() - start

    saved = [(site, getattr(site, name)) for site in sites if hasattr(site, name)]
    for site, _ in saved:
        setattr(site, name, wrapper)
    try:
        yield
    finally:
        for site, value in saved:
            setattr(site, name, value)


def measure(
    document: CourseDocument, *, shared: bool = True, only: tuple[Detector, ...] | None = None
) -> dict[str, float]:
    scans = Tally()
    selected = only if only is not None else detectors()
    with counted(SCAN_SITES, "scan", html_scan.scan, scans):
        start = time.perf_counter()
        if shared:
            run_detectors(document, only=selected)
        else:
            for detector in selected:
                run_detectors(document, only=(detector,))
        wall = time.perf_counter() - start
    return {
        "wall": wall,
        "scan_calls": scans.calls,
        "scan_seconds": scans.seconds,
    }


def synthetic(document: CourseDocument, copies: int, body: str | None) -> CourseDocument:
    """Every body-carrying artifact ``copies`` times, with distinct ids; ``body`` replaces each
    non-empty body when given. The syllabus and every other collection are kept once."""
    changes = {}
    for name in BODY_FIELDS:
        changes[name] = tuple(
            replace(
                artifact,
                id=f"{artifact.id}-copy{copy}",
                body_html=body if body is not None and artifact.body_html else artifact.body_html,
            )
            for copy in range(copies)
            for artifact in getattr(document, name)
        )
    return replace(document, **changes)


def by_id(detector_id: str) -> tuple[Detector, ...]:
    found = tuple(detector for detector in detectors() if detector.id == detector_id)
    if not found:
        sys.exit(f"SELF-TEST FAILED: no detector {detector_id} is registered")
    return found


def self_test(document: CourseDocument) -> None:
    """Refuse to report unless the counter sees what it must, in both directions."""
    bodies = sum(1 for _ in document.bodies())
    for shared in (True, False):
        alone = measure(document, shared=shared, only=by_id("D21"))
        if alone["scan_calls"] != bodies:
            sys.exit(
                f"SELF-TEST FAILED: D21 alone over {bodies} bodies must parse {bodies} times; "
                f"the counter saw {alone['scan_calls']} (shared={shared})"
            )
        none = measure(document, shared=shared, only=by_id("D11"))
        if none["scan_calls"] != 0:
            sys.exit(f"SELF-TEST FAILED: D11 reads no bodies and was counted parsing: {none}")
    print(f"self-test passed: D21 alone parses {bodies} times over {bodies} bodies; D11 parses 0")


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--copies", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    sample = load_bundle(REPO / "examples" / "sample-course")
    self_test(sample)

    long_body = "\n".join(artifact.body_html or "" for artifact in sample.bodies())
    shapes = {
        "sample course as committed": sample,
        f"sample bodies x{args.copies}": synthetic(sample, args.copies, None),
        f"{len(long_body)}-byte bodies x{args.copies}": synthetic(sample, args.copies, long_body),
    }
    for label, document in shapes.items():
        bodies = sum(1 for _ in document.bodies())
        size = sum(len(artifact.body_html or "") for artifact in document.bodies())
        print(f"\n{label}: {bodies} bodies, {size} bytes of HTML")
        for shared in (False, True):
            runs = [measure(document, shared=shared) for _ in range(args.repeats)]
            wall = statistics.median(run["wall"] for run in runs)
            scan_s = statistics.median(run["scan_seconds"] for run in runs)
            calls = runs[0]["scan_calls"]
            print(
                f"  {'shared  ' if shared else 'unshared'}  scan() {calls:5d} calls "
                f"({calls / max(bodies, 1):4.1f}/body)  wall {wall * 1000:7.0f} ms  "
                f"in scan() {scan_s * 1000:7.0f} ms ({scan_s / wall:3.0%})  "
                f"[median of {args.repeats}]"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
