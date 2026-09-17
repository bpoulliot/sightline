"""Per-defect remediation costs — A DELIBERATE, LABELLED PLACEHOLDER.

**Read this before using a number out of here.** Nothing in either source workbook prices a defect.
`CONFIG`'s `timeToFix` range was checked and it is not the table an earlier revision of SPEC §07
claimed: it maps *rating → hours*, the inverse of what Sightline computes, and its `VALUE` column
is an inverted scale that exists to be averaged, which is precisely the aggregate quality score
SPEC §11 forbids. So the costs below are **uncalibrated**, they are flagged as such by
:attr:`EffortModel.calibrated`, and roadmap item 12b fits them against completed human reviews.
Roadmap item 12a owns the shipped effort model; this module exists so item 04's arithmetic can be
demonstrated end to end without pre-empting it, and it is written to be thrown away.

**The bands are a different matter and they are not here.** ≤ ½ hour / ½–2 hours / 2 hours+ are
printed on the instrument, so they are pack data (:class:`sightline.rubric.pack.RatingScale`).
Costs are global: one effort model, calibrated once, because sixty-eight per-row cost tables will
not converge against a handful of reviews.

**One global rule for rolling up: sum, then band.** A criterion fed by several detectors sums their
effort and bands the total. Not worst-of, and not a per-row policy.

**Keyed on severity, not on finding code, and that is on purpose.** A table keyed on every
detector's every code would be sixty-odd free parameters invented here and inherited by the item
that has to fit them. Severity is the coarsest honest key the finding model already carries, and
:meth:`EffortModel.cost_of` is the single seam item 12a replaces.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ..detect.base import Finding, Severity

#: PLACEHOLDER. Hours per finding, by what the finding means for a learner. Round numbers on
#: purpose: a table of plausible-looking decimals reads as though somebody measured it.
PLACEHOLDER_COSTS: Mapping[Severity, float] = {
    Severity.BARRIER: 0.25,
    Severity.FRICTION: 0.1,
    Severity.ADVISORY: 0.05,
}

#: What any surface showing a band must say while the costs are uncalibrated.
PLACEHOLDER_LABEL = (
    "Estimated from an uncalibrated cost table (roadmap item 12b fits it). The band is a starting "
    "point for the reviewer, not a measurement."
)


@dataclass(frozen=True, kw_only=True, slots=True)
class EffortModel:
    """Turns findings into hours. One instance per run, global to the run.

    ``calibrated`` is false for the shipped placeholder and is the flag every rendering surface
    reads to decide whether a band is stated or offered.
    """

    costs: Mapping[Severity, float] = field(default_factory=lambda: dict(PLACEHOLDER_COSTS))
    calibrated: bool = False
    note: str = PLACEHOLDER_LABEL

    def cost_of(self, finding: Finding) -> float:
        """Hours for one finding. The seam roadmap item 12a replaces."""
        return self.costs.get(finding.severity, 0.0)

    def total(self, findings: Iterable[Finding]) -> float:
        """Sum, then hand the total to the pack's bands. Never worst-of.

        Rounded to the nearest minute so that summing twenty findings of 0.1 hours does not put a
        total on the wrong side of a band boundary through binary floating point alone.
        """
        return round(sum(self.cost_of(finding) for finding in findings), 4)


def placeholder_effort_model() -> EffortModel:
    """The shipped, uncalibrated model. Named so a call site reads as what it is."""
    return EffortModel()


__all__ = [
    "PLACEHOLDER_COSTS",
    "PLACEHOLDER_LABEL",
    "EffortModel",
    "placeholder_effort_model",
]
