"""The effort model: findings become hours, hours become a band, and a band near its edge says so.

**Read this before using a number out of here.** The per-defect costs are an **uncalibrated
placeholder** and every surface says so. Nothing in either source workbook prices a defect.
`CONFIG`'s `timeToFix` range (`CONFIG!$H$15:$K$19`) was checked and it is not a cost table: it maps
*rating → hours*, the inverse of what Sightline computes, and its `VALUE` column is an inverted
scale that exists to be averaged, which is precisely the aggregate quality score SPEC §11 forbids.
So :attr:`EffortModel.calibrated` is false, and roadmap item 12b fits the costs against completed
human reviews. Tuning a number here to make an output look right is 12b's work done badly.

WHERE EACH PART LIVES
---------------------
**Costs are global configuration, in one file:** ``costs.yaml`` beside this module, overridable
without a code change by pointing ``SIGHTLINE_EFFORT_COSTS`` at a file of the same shape
(:func:`load_effort_model`). What it costs to write one alt attribute is a property of the work,
not of the rubric being used to look at it.

**Bands are pack data** (:class:`sightline.rubric.pack.RatingScale`), because they are printed on
the instrument: ``Instructional Designer`` row 3 names the ratings and row 4 prints the ranges under
them — `T4` "1/2 hour or less", `U4` "1/2-2 hours", `V4` "2+ hours" — and `S4`, under Sufficiently
Present, is empty. Nothing here holds a threshold.

**One roll-up rule, globally: sum, then band.** A row fed by several detectors sums their effort
and bands the total. Not worst-of, not a per-row policy — 12b calibrates one model, not sixty-eight.
The banding itself happens in :mod:`sightline.rubric.project`, after the tiers have been checked.

THE COST TABLE IS KEYED ON THE DETECTOR
---------------------------------------
Item 04 keyed a throwaway table on :class:`~sightline.detect.base.Severity`, to avoid inventing a
parameter per finding code. Item 12a keys it on the **detector id**, for three reasons:

* Severity is a statement about the *learner* (a barrier, friction, advisory); cost is a statement
  about the *work*. A missing alt attribute and an uncaptioned video are both barriers and differ in
  effort by an order of magnitude. What a detector checks fixes the kind of work far better.
* The detector registry is enumerable before a course is read, so a gap in the table can be reported
  on the pack (:mod:`sightline.rubric.coverage`). Finding codes are not declared anywhere and could
  only be discovered by running.
* It is thirty-seven parameters, not ninety, and the shared default below pools anything unpriced.

**An unpriced detector is not free.** Its findings cost :attr:`EffortModel.default_hours`, a
documented non-zero value; the row it feeds carries a note naming it, and the coverage report lists
it. A cost of zero is refused at load time — a zero-cost finding vanishes from the arithmetic, which
is SPEC §11's silent under-detection wearing a price tag.

THE NEAR-BOUNDARY MARKER
------------------------
Alongside a band, the model emits a :class:`NearBoundary` when the row's rolled-up hours fall within
a configured margin of a threshold: the lowest one (Sufficiently Present / Minor Revision, at zero
hours) or one between two bands (0.5 h, 2 h on the reference instrument).

**The Sufficiently Present / Minor margin is wider by default, and that is the point.** Across five
hand-completed reviews, independent human passes over the same course agreed 72–91% of the time;
every disagreement was exactly one band, and 29 of 34 were Sufficiently Present versus Minor
Revision. The boundary qualified humans are least consistent about is the one with the *smallest*
computed effort — where arithmetic is cleanest and a naive implementation most confident.

**It is a computed distance, not a confidence score.** It carries the threshold, the two ratings
either side, the distance in hours and the margin that was applied — every one of them checkable
against the row's own findings. There is no percentage and no probability; SPEC §01 holds that
verbalized self-confidence is worse than useless. It is computed once, here, and persisted on the
row, so every surface that renders a band sees the same marker (items 13, 14, 16; item 24 is the one
that shows it to a reader). This module builds no UI for it.

A row reading **Sufficiently Present carries no marker**. It has no effort figure to measure a
distance from — it means "no qualifying defects found", not "zero hours" — and marking every clean
row would mark four rows in five and tell a reviewer nothing. The disagreement from that side is a
human seeing what no detector looked for, which the tiers and the evidence layer answer, not
arithmetic.

WHAT THIS MODULE NEVER EMITS
----------------------------
No course-level score, index, percentage or total. Not a sum of hours across rows (that is an
aggregate score with a clock face on it), not an average of ratings, and not in a debug log either.
``tests/test_rubric_effort.py`` reads this module's code tokens for the arithmetic that would
produce one.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from ..detect.base import Finding

#: The shipped table's filename, beside this module.
COSTS_FILENAME = "costs.yaml"

#: What any surface showing a band or an hour figure must say while the costs are uncalibrated.
PLACEHOLDER_LABEL = (
    "Estimated from an uncalibrated cost table (roadmap item 12b fits it). The band is a starting "
    "point for the reviewer, not a measurement."
)

#: What the same surfaces say once item 12b has fitted the table. Still an estimate.
CALIBRATED_LABEL = (
    "Estimated from a cost table fitted to completed reviews. The band is an estimate for the "
    "reviewer to confirm, not a measurement of this course."
)

#: The fallback for an unpriced detector when a table does not set one. Never zero.
DEFAULT_HOURS = 0.1

#: The internal name of the rating on the clean side of the lowest threshold.
_BELOW_LOWEST = "sufficiently_present"

_TOP_LEVEL = frozenset({"calibrated", "default_hours", "near_boundary", "costs"})
_MARGIN_KEYS = frozenset({"lowest", "between_bands"})


class EffortConfigError(ValueError):
    """A cost table is malformed. Raised at load time, never at scoring time."""

    def __init__(self, message: str, *, origin: str) -> None:
        self.origin = origin
        super().__init__(f"{origin}: {message}")


@dataclass(frozen=True, kw_only=True, slots=True)
class Margins:
    """How near a threshold a row must be to carry a near-boundary marker. Hours, not a share."""

    #: The Sufficiently Present / first-band boundary. Wider than the rest by default.
    lowest: float = 0.25
    #: Every boundary between two bands.
    between_bands: float = 0.1

    def to_dict(self) -> dict[str, float]:
        return {"lowest": self.lowest, "between_bands": self.between_bands}


@dataclass(frozen=True, kw_only=True, slots=True)
class NearBoundary:
    """A rolled-up effort that sits within a margin of a band threshold. A distance, not a doubt.

    Every field is checkable: ``threshold_hours`` is on the pack, ``hours`` is the sum of the row's
    own findings, ``distance_hours`` is the difference, and ``margin_hours`` is the configured
    margin it fell inside.
    """

    #: The ratings either side of the threshold, lower-effort side first.
    below: str
    above: str
    threshold_hours: float
    hours: float
    distance_hours: float
    margin_hours: float

    @property
    def lowest(self) -> bool:
        """Whether this is the Sufficiently Present / first-band boundary."""
        return self.below == _BELOW_LOWEST

    def statement(self, labels: Mapping[str, str] | None = None) -> str:
        """One declarative line. Hours and labels only — no percentage, no likelihood."""
        names = labels or {}
        below = names.get(self.below, self.below)
        above = names.get(self.above, self.above)
        return (
            f"{self.hours:g} h is {self.distance_hours:g} h from the {below} / {above} boundary at "
            f"{self.threshold_hours:g} h (margin {self.margin_hours:g} h)."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "below": self.below,
            "above": self.above,
            "threshold_hours": self.threshold_hours,
            "hours": self.hours,
            "distance_hours": self.distance_hours,
            "margin_hours": self.margin_hours,
            "lowest": self.lowest,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class EffortModel:
    """Turns findings into hours, and hours near a threshold into a marker. Global to a run.

    ``calibrated`` is false for the shipped table and is the flag every rendering surface reads to
    decide how a band is worded. ``note`` is what those surfaces print beside an hour figure.
    """

    #: Hours per finding, keyed on the detector id that reported it.
    costs: Mapping[str, float] = field(default_factory=dict)
    #: Hours per finding from a detector with no entry in :attr:`costs`. Never zero.
    default_hours: float = DEFAULT_HOURS
    margins: Margins = field(default_factory=Margins)
    calibrated: bool = False
    #: Where the table came from: the shipped file, plus an override file if one was applied.
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        origin = ", ".join(self.sources) or "EffortModel"
        _check_hours(self.default_hours, what="default_hours", origin=origin)
        for key, hours in self.costs.items():
            _check_hours(hours, what=f"costs.{key}", origin=origin)
        for name, margin in self.margins.to_dict().items():
            if not isinstance(margin, int | float) or not math.isfinite(margin) or margin < 0:
                raise EffortConfigError(
                    f"near_boundary.{name} must be a non-negative number of hours, not {margin!r}",
                    origin=origin,
                )

    @property
    def note(self) -> str:
        return CALIBRATED_LABEL if self.calibrated else PLACEHOLDER_LABEL

    def priced(self, detector: str) -> bool:
        """Whether the table carries an entry for this detector."""
        return detector in self.costs

    def unpriced(self, detectors: Iterable[str]) -> tuple[str, ...]:
        """The detectors among these that fall back to :attr:`default_hours`, sorted."""
        return tuple(sorted({detector for detector in detectors if not self.priced(detector)}))

    def hours_for(self, detector: str) -> float:
        """Hours per finding from this detector. The default when unpriced — never zero."""
        return self.costs.get(detector, self.default_hours)

    def cost_of(self, finding: Finding) -> float:
        """Hours for one finding, looked up on the detector that reported it."""
        return self.hours_for(finding.detector)

    def total(self, findings: Iterable[Finding]) -> float:
        """Sum one row's findings. The pack's bands are applied to this total, never worst-of.

        Rounded to four places so that summing twenty findings of 0.1 hours does not put a total on
        the wrong side of a band boundary through binary floating point alone.
        """
        return round(sum(self.cost_of(finding) for finding in findings), 4)

    def near_boundary(
        self, hours: float, ceilings: Iterable[tuple[str, float | None]]
    ) -> NearBoundary | None:
        """The threshold ``hours`` sits within a margin of, if any. Nearest wins; a tie goes low.

        ``ceilings`` are the pack's bands in ascending order as ``(rating, up_to_hours)``, the last
        one open-ended. Only a row that was *banded* is asked: a Sufficiently Present row has no
        effort figure to measure from (see the module docstring).
        """
        bands = list(ceilings)
        if not bands:
            return None
        # Threshold 0 sits between "no qualifying defects" and the first band; each finite ceiling
        # sits between its band and the next one up.
        thresholds: list[tuple[str, str, float, float]] = [
            (_BELOW_LOWEST, bands[0][0], 0.0, self.margins.lowest)
        ]
        for (rating, ceiling), (above, _next) in zip(bands, bands[1:], strict=False):
            if ceiling is not None:
                thresholds.append((rating, above, float(ceiling), self.margins.between_bands))

        best: NearBoundary | None = None
        for below, above, threshold, margin in thresholds:
            distance = round(abs(hours - threshold), 4)
            if distance > margin:
                continue
            if best is None or distance < best.distance_hours:
                best = NearBoundary(
                    below=below,
                    above=above,
                    threshold_hours=threshold,
                    hours=hours,
                    distance_hours=distance,
                    margin_hours=margin,
                )
        return best

    def to_dict(self) -> dict[str, Any]:
        return {
            "calibrated": self.calibrated,
            "note": self.note,
            "default_hours": self.default_hours,
            "margins": self.margins.to_dict(),
            "costs": dict(sorted(self.costs.items())),
            "sources": list(self.sources),
        }


# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------


def parse_effort_table(payload: Any, *, origin: str) -> dict[str, Any]:
    """Validate one table's shape and return its fields. Unknown keys are refused, not ignored.

    Refused because a misspelled ``default_hour`` silently falling back to the shipped value is the
    kind of override that looks applied and is not.
    """
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise EffortConfigError("the table must be a mapping", origin=origin)
    unknown = sorted(set(payload) - _TOP_LEVEL)
    if unknown:
        raise EffortConfigError(
            f"unknown key(s) {unknown}; expected some of {sorted(_TOP_LEVEL)}", origin=origin
        )
    fields: dict[str, Any] = {}
    if "calibrated" in payload:
        if not isinstance(payload["calibrated"], bool):
            raise EffortConfigError("calibrated must be true or false", origin=origin)
        fields["calibrated"] = payload["calibrated"]
    if "default_hours" in payload:
        fields["default_hours"] = _check_hours(
            payload["default_hours"], what="default_hours", origin=origin
        )
    if "near_boundary" in payload:
        margins = payload["near_boundary"]
        if not isinstance(margins, Mapping) or set(margins) - _MARGIN_KEYS:
            raise EffortConfigError(
                f"near_boundary takes only {sorted(_MARGIN_KEYS)}", origin=origin
            )
        fields["near_boundary"] = dict(margins)
    if "costs" in payload:
        costs = payload["costs"]
        if not isinstance(costs, Mapping):
            raise EffortConfigError("costs must map a detector id to hours", origin=origin)
        fields["costs"] = {
            str(key): _check_hours(value, what=f"costs.{key}", origin=origin)
            for key, value in costs.items()
        }
    return fields


def load_effort_model(override: Path | None = None) -> EffortModel:
    """The shipped table, with an override file applied on top if one is named.

    An override replaces the shipped ``costs`` entry by entry and leaves anything it does not name
    alone, so a deployment that disagrees about one detector writes one line.
    """
    shipped = resources.files(__package__).joinpath(COSTS_FILENAME)
    base = parse_effort_table(
        yaml.safe_load(shipped.read_text(encoding="utf-8")), origin=COSTS_FILENAME
    )
    sources = [COSTS_FILENAME]
    if override is not None:
        origin = str(override)
        try:
            text = Path(override).read_text(encoding="utf-8")
        except OSError as exc:
            raise EffortConfigError(f"cannot be read ({exc.strerror})", origin=origin) from exc
        try:
            layered = parse_effort_table(yaml.safe_load(text), origin=origin)
        except yaml.YAMLError as exc:
            raise EffortConfigError(f"is not valid YAML ({exc})", origin=origin) from exc
        costs = dict(base.get("costs", {}))
        costs.update(layered.pop("costs", {}))
        margins = dict(base.get("near_boundary", {}))
        margins.update(layered.pop("near_boundary", {}))
        base.update(layered)
        base["costs"] = costs
        base["near_boundary"] = margins
        sources.append(origin)
    return _model(base, sources=tuple(sources))


def default_effort_model() -> EffortModel:
    """The shipped table with no override. What the suite and a bare ``project()`` use."""
    return load_effort_model(None)


def _model(fields: Mapping[str, Any], *, sources: tuple[str, ...]) -> EffortModel:
    margins = Margins(**fields.get("near_boundary", {}))
    return EffortModel(
        costs=dict(sorted(fields.get("costs", {}).items())),
        default_hours=fields.get("default_hours", DEFAULT_HOURS),
        margins=margins,
        calibrated=fields.get("calibrated", False),
        sources=sources,
    )


def _check_hours(value: Any, *, what: str, origin: str) -> float:
    """A per-finding cost must be a finite, strictly positive number of hours."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise EffortConfigError(f"{what} must be a number of hours, not {value!r}", origin=origin)
    if value <= 0:
        raise EffortConfigError(
            f"{what} is {value!r}; a finding may not cost nothing, or it disappears from the "
            "arithmetic. Use the smallest value you mean.",
            origin=origin,
        )
    return float(value)


__all__ = [
    "CALIBRATED_LABEL",
    "COSTS_FILENAME",
    "DEFAULT_HOURS",
    "PLACEHOLDER_LABEL",
    "EffortConfigError",
    "EffortModel",
    "Margins",
    "NearBoundary",
    "default_effort_model",
    "load_effort_model",
    "parse_effort_table",
]
