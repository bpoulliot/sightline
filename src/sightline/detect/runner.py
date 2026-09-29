"""Running every detector over one course, and refusing to hide one that could not run.

The framework raises :class:`~sightline.detect.base.NothingExamined` at the detector boundary,
which is where the invariant belongs: it fires whether the detector was called by the pipeline, by
a test or by the integrity guard. This module is the one place that catches it, and what it turns
it into is a *reported* non-result — a detector named, in the ``NOT_EXAMINED`` state, carrying the
reason. It never turns into a detector quietly missing from the list, and it never turns into a
pass, because :attr:`DetectorResult.clean` is false for that state by construction.

**A detector that crashes is reported the same way, and by the same layer.** Anything else a
detector raises — a ``ValueError`` out of a parser handed markup nobody anticipated — used to
propagate out of :func:`run_detectors` and end the review, so every detector went missing from the
list at once. The runner now reports it as a detector that could not run: ``NOT_EXAMINED``, never
clean, with :attr:`DetectorResult.crashed` naming the exception type so the summary tells it apart
from a detector whose course simply held nothing (item 21's word for this outcome, reused). The
other detectors still run and still report, and the CLI still exits non-zero.

Only this layer contains it. :meth:`Detector.run` called directly — as every detector test and the
integrity guard call it — still raises, because a test that got a quiet ``NOT_EXAMINED`` back
instead of a traceback is a test that stopped testing.

**What reaches the report is not what reaches the log.** An exception's message is free text and
is often built from what was being parsed, which is course content and can carry the institution's
hostname (SPEC §11). The reason written into the report — and from there onto rubric rows and into
files on disk — therefore carries the exception *type* and the *code location* that raised it,
never the message. The message and the traceback go to the log, scrubbed here rather than relying
on the logging configuration, because the detect CLI runs without one.
"""

from __future__ import annotations

import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePath
from types import TracebackType
from typing import Any

from ..logging import get_logger
from ..model import CourseDocument
from ..redaction import scrub
from .base import (
    Detector,
    DetectorResult,
    Finding,
    NothingExamined,
    Parses,
    ResultState,
    Tier,
    Undetermined,
    detectors,
)
from .links import LinkResolver
from .sources import FileSource

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class DetectionReport:
    """Every detector's result over one course, plus the counts that make it checkable."""

    results: tuple[DetectorResult, ...]

    @property
    def findings(self) -> tuple[Finding, ...]:
        return tuple(finding for result in self.results for finding in result.findings)

    @property
    def undetermined(self) -> tuple[Undetermined, ...]:
        return tuple(entry for result in self.results for entry in result.undetermined)

    @property
    def not_examined(self) -> tuple[DetectorResult, ...]:
        """Detectors that could not run, crashed ones included. Never empty because something was
        hidden."""
        return tuple(result for result in self.results if result.state is ResultState.NOT_EXAMINED)

    @property
    def crashed(self) -> tuple[DetectorResult, ...]:
        """The detectors that could not run because they raised. A subset of
        :attr:`not_examined`."""
        return tuple(result for result in self.not_examined if result.crashed is not None)

    def by_detector(self, detector_id: str) -> DetectorResult | None:
        for result in self.results:
            if result.detector == detector_id:
                return result
        return None

    def statement(self) -> str:
        """The declarative summary a reviewer reads before anything else."""
        ran = len(self.results) - len(self.not_examined)
        parts = [
            f"{ran} of {len(self.results)} detector(s) examined the course",
            f"{len(self.findings)} observation(s) recorded",
        ]
        if self.undetermined:
            parts.append(f"{len(self.undetermined)} item(s) could not be read")
        # A crash is named on its own, ahead of the rest: filed among detectors whose course held
        # nothing to read, a broken detector reads as one more ordinary gap.
        if self.crashed:
            names = ", ".join(result.detector for result in self.crashed)
            parts.append(f"{len(self.crashed)} crashed and could not run ({names})")
        quiet = [result for result in self.not_examined if result.crashed is None]
        if quiet:
            names = ", ".join(result.detector for result in quiet)
            parts.append(f"{len(quiet)} could not run ({names})")
        return "; ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement(),
            "results": [result.to_dict() for result in self.results],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DetectionReport:
        """The inverse of :meth:`to_dict`. The statement is recomputed from the results."""
        return cls(results=tuple(DetectorResult.from_dict(entry) for entry in payload["results"]))


def run_detectors(
    document: CourseDocument,
    *,
    files: FileSource | None = None,
    links: LinkResolver | None = None,
    only: Sequence[Detector] | None = None,
) -> DetectionReport:
    """Run every registered detector, or the ones given, over one normalized course.

    ``links`` is the resolver the one link-following detector asks. Omitted means
    :class:`~sightline.detect.links.NoLinkResolver`: no detector reaches the network unless a
    caller hands one in, which is why the suite and CI need no network and a batch run does not
    silently become a crawl.

    Every detector gets its own survey, and therefore its own examined count, but they share one
    :class:`~sightline.detect.base.Parses`: each body is parsed once for the whole run rather than
    once per detector that reads it. It is made here and dropped when this returns.
    """
    selected = tuple(only) if only is not None else detectors()
    parses = Parses()
    results: list[DetectorResult] = []
    for detector in selected:
        try:
            results.append(detector.run(document, files=files, links=links, parses=parses))
        except NothingExamined as exc:
            log.warning(
                "detector_examined_nothing",
                detector=detector.id,
                collections=list(exc.collections),
            )
            results.append(
                DetectorResult(
                    detector=detector.id,
                    title=detector.title,
                    tier=detector.tier,
                    capability_tag=detector.capability_tag,
                    state=ResultState.NOT_EXAMINED,
                    examined=0,
                    subjects=0,
                    not_examined_because=(
                        "This detector examined nothing and nothing in the course explains why. "
                        f"It reads {', '.join(exc.collections)}. Its criteria were not checked, "
                        "and no part of this review covers them."
                    ),
                )
            )
        except Exception as exc:  # noqa: BLE001 - reported below as a detector that could not run
            results.append(_crashed(detector, exc))
    return DetectionReport(results=tuple(sorted(results, key=lambda item: item.detector)))


def _crashed(detector: Detector, exc: Exception) -> DetectorResult:
    """A detector that raised, as a named non-result. The message stays in the log."""
    kind = type(exc).__name__
    where = _raised_at(exc.__traceback__)
    log.error(
        "detector_crashed",
        detector=detector.id,
        error_type=kind,
        raised_at=where,
        error=scrub(str(exc)),
        traceback=scrub("".join(traceback.format_exception(exc))),
    )
    return DetectorResult(
        detector=detector.id,
        title=detector.title,
        tier=detector.tier,
        capability_tag=detector.capability_tag,
        state=ResultState.NOT_EXAMINED,
        examined=0,
        subjects=0,
        crashed=kind,
        not_examined_because=(
            f"This detector crashed: it raised {kind} at {where}. It reads "
            f"{', '.join(detector.collections)}. Its criteria were not checked, and no part of "
            "this review covers them. The exception's message is in the run log and not here, "
            "because it can quote course content."
        ),
    )


def _raised_at(tb: TracebackType | None) -> str:
    """Where the exception was raised, as code locations only — never a source line or a value.

    The innermost frame, and the innermost frame in Sightline's own code when that is a different
    one: a crash inside a parsing library is debugged from the detector line that called it.
    Paths are cut to their package-relative tail, so the host's directory layout stays out of the
    report.
    """
    frames = traceback.extract_tb(tb)
    if not frames:
        return "an unknown location"
    innermost = frames[-1]
    ours = next(
        (frame for frame in reversed(frames) if _frame(frame).startswith("sightline/")),
        None,
    )
    where = _frame(innermost)
    if ours is not None and ours is not innermost:
        where += f", called from {_frame(ours)}"
    return where


def _frame(frame: traceback.FrameSummary) -> str:
    parts = PurePath(frame.filename).parts
    for anchor in ("site-packages", "tests", "sightline"):
        if anchor in parts:
            start = len(parts) - 1 - parts[::-1].index(anchor)
            tail = parts[start + 1 :] if anchor == "site-packages" else parts[start:]
            return f"{'/'.join(tail)}:{frame.lineno} ({frame.name})"
    return f"{'/'.join(parts[-2:]) or '?'}:{frame.lineno} ({frame.name})"


def tier_counts(report: DetectionReport) -> dict[str, int]:
    """Findings per determinacy tier. Tier A and tier B must never be presented together
    (SPEC §02), so the count that separates them is computed here rather than in a template."""
    tally = {tier.value: 0 for tier in Tier}
    for finding in report.findings:
        tally[finding.tier.value] += 1
    return tally


__all__ = ["DetectionReport", "run_detectors", "tier_counts"]
