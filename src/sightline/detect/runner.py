"""Running every detector over one course, and refusing to hide one that could not run.

The framework raises :class:`~sightline.detect.base.NothingExamined` at the detector boundary,
which is where the invariant belongs: it fires whether the detector was called by the pipeline, by
a test or by item 21's guard. This module is the one place that catches it, and what it turns it
into is a *reported* non-result — a detector named, in the ``NOT_EXAMINED`` state, carrying the
reason. It never turns into a detector quietly missing from the list, and it never turns into a
pass, because :attr:`DetectorResult.clean` is false for that state by construction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..logging import get_logger
from ..model import CourseDocument
from .base import (
    Detector,
    DetectorResult,
    Finding,
    NothingExamined,
    ResultState,
    Tier,
    Undetermined,
    detectors,
)
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
        """Detectors that could not run. Never empty because something was hidden."""
        return tuple(result for result in self.results if result.state is ResultState.NOT_EXAMINED)

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
        if self.not_examined:
            names = ", ".join(result.detector for result in self.not_examined)
            parts.append(f"{len(self.not_examined)} could not run ({names})")
        return "; ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement(),
            "results": [result.to_dict() for result in self.results],
        }


def run_detectors(
    document: CourseDocument,
    *,
    files: FileSource | None = None,
    only: Sequence[Detector] | None = None,
) -> DetectionReport:
    """Run every registered detector, or the ones given, over one normalized course."""
    selected = tuple(only) if only is not None else detectors()
    results: list[DetectorResult] = []
    for detector in selected:
        try:
            results.append(detector.run(document, files=files))
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
    return DetectionReport(results=tuple(sorted(results, key=lambda item: item.detector)))


def tier_counts(report: DetectionReport) -> dict[str, int]:
    """Findings per determinacy tier. Tier A and tier B must never be presented together
    (SPEC §02), so the count that separates them is computed here rather than in a template."""
    tally = {tier.value: 0 for tier in Tier}
    for finding in report.findings:
        tally[finding.tier.value] += 1
    return tally


__all__ = ["DetectionReport", "run_detectors", "tier_counts"]
