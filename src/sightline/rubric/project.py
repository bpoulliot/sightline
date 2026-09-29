"""Projecting detector findings onto rubric rows, and keeping three states apart while doing it.

**Sightline reviews the course; the rubric is only the lens.** Detectors emitted facts. This module
decides which rows of the chosen instrument those facts are *reported* against, and what rating — if
any — the arithmetic supports. Resolution goes through capability tags in both directions, so
nothing here knows a detector id it was not handed and nothing here knows a criterion a pack did not
declare.

**It is many-to-many, in both directions, and both directions are load-bearing.** One detector fans
*out*: the syllabus check reaches four getting-started rows of the reference instrument at once.
Several detectors fan *in*: that instrument's GSOV 5 receives from both the schedule check and the
due-date check, and its ACD 11 from both the assignment check and the due-date check. A design that
handles one direction passes a naive test and is wrong.

**When a row is fed by several detectors their effort SUMS and the total is banded once.** One
global rule, every row. Not worst-of, and not a per-row policy: roadmap item 12b has to calibrate
*one* effort model against a handful of completed reviews, and sixty-eight free parameters will not
converge.

THREE STATES, AND CONFLATING THEM IS THE FAILURE THIS MODULE EXISTS TO PREVENT
-----------------------------------------------------------------------------

:attr:`Coverage.NOT_YET_IMPLEMENTED`
    The bound tag is in the vocabulary and no detector implements it yet. **Transient** — a
    development-phase artifact that disappears as the detector inventory lands.

:attr:`Coverage.NO_DETERMINISTIC_COVERAGE`
    The row carries no binding at all. **Permanent for that pack, and entirely normal.** It routes
    to the extraction layer as Tier B/C evidence. A freshly imported third-party instrument is
    *all* of these and that is a success case, not a degraded one: binding is an optimization, not
    a prerequisite. Extraction-only rows break no rule — they still carry verified quotes with
    locations, and the tier system already keeps them linguistically and visually distinct from
    Tier A arithmetic.

:attr:`Coverage.DETERMINISTIC`
    At least one implemented detector was bound, and it ran. Only here can a row read
    **Sufficiently Present**, and only when every contributing detector actually examined something
    and found nothing it could not account for — **and only on a Tier A row every one of whose
    bindings was examined.** A binding says a capability is *relevant* to a row; only Tier A says
    the bindings *settle* it, and they settle it together (roadmap item 28).

If an absent detector or an unbound row could produce "Sufficiently Present", Sightline would
report a course clean on criteria it never examined. That is the silent under-detection of SPEC §11
and it is the single worst thing this project can do. So the rating for anything that is not
``DETERMINISTIC`` and clean is ``No Response`` — the instrument's own default cell state — with the
reason stated on the row.

**The tiers override the arithmetic, not the other way round** (SPEC §02/§07). A Tier C row stays
at ``No Response`` *however many findings its bindings produced*; the findings are carried on the
row as context for the question a human answers. A Tier D row defaults to ``Not Applicable`` with a
note. Neither is a special case bolted on at the end — they are checked before the bands are.
A Tier B row is *proposed* a band when its detectors found something, and stays at ``No Response``
when they found nothing: its bindings bear on the criterion without answering all of it, so a clean
run of them is not a clean row. Only Tier A — "rating pre-selected" — reaches Sufficiently Present.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..capabilities import Vocabulary, vocabulary
from ..detect.base import Detector, DetectorResult, Finding, ResultState, Tier, detectors
from ..detect.runner import DetectionReport
from .effort import EffortModel, Margins, NearBoundary, default_effort_model
from .pack import Criterion, RatingScale, RubricPack
from .routing import routes_to_extraction
from .states import Coverage, Rating, coverage_of

# ---------------------------------------------------------------------------------------------
# What implements what
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class TagIndex:
    """Which capability tags the build implements, and which detectors implement each.

    Built from the detector registry, or from an explicit list in a test. Nothing in here maps a
    tag to a *criterion*: that direction belongs to the pack and to nothing else.
    """

    #: Tag -> detector ids declaring it, in id order. Several detectors may share a tag.
    detectors_by_tag: Mapping[str, tuple[str, ...]]
    #: Detector id -> the tag it declared.
    tag_by_detector: Mapping[str, str]
    vocabulary: Vocabulary

    @classmethod
    def build(
        cls,
        declared: Sequence[Detector] | None = None,
        *,
        words: Vocabulary | None = None,
    ) -> TagIndex:
        instances = tuple(declared) if declared is not None else detectors()
        by_tag: dict[str, list[str]] = {}
        by_detector: dict[str, str] = {}
        for detector in instances:
            by_detector[detector.id] = detector.capability_tag
            by_tag.setdefault(detector.capability_tag, []).append(detector.id)
        return cls(
            detectors_by_tag={tag: tuple(sorted(ids)) for tag, ids in sorted(by_tag.items())},
            tag_by_detector=dict(sorted(by_detector.items())),
            vocabulary=words or vocabulary(),
        )

    @property
    def implemented(self) -> frozenset[str]:
        return frozenset(self.detectors_by_tag)

    def implements(self, tag: str) -> bool:
        return tag in self.detectors_by_tag

    def recognises(self, tag: str) -> bool:
        return tag in self.vocabulary

    def statement(self) -> str:
        pending = len(self.vocabulary) - len(self.implemented)
        return (
            f"{len(self.tag_by_detector)} detector(s) implement {len(self.implemented)} of "
            f"{len(self.vocabulary)} capability tag(s); {pending} tag(s) have no detector yet."
        )


# ---------------------------------------------------------------------------------------------
# Warnings — reported, never raised
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class BindingWarning:
    """A pack bound a tag this build cannot use. Reported; the pack still loads and still runs.

    Asymmetry on purpose (see :mod:`sightline.capabilities`): a *detector* naming an unknown tag is
    a hard failure because we write the detectors, and a *pack* doing the same is this, because
    packs are third-party and an uploaded rubric must never be able to hard-fail Sightline.
    """

    criterion: str
    tag: str
    reason: str
    suggestions: tuple[str, ...] = ()

    def line(self) -> str:
        hint = f" Nearest known tag(s): {', '.join(self.suggestions)}." if self.suggestions else ""
        return f"{self.criterion}: {self.tag!r} — {self.reason}{hint}"

    def to_dict(self) -> dict[str, Any]:
        payload = {"criterion": self.criterion, "tag": self.tag, "reason": self.reason}
        if self.suggestions:
            payload["suggestions"] = list(self.suggestions)  # type: ignore[assignment]
        return payload


# ---------------------------------------------------------------------------------------------
# One projected row
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class Contribution:
    """One detector's share of one row: what it found, what that costs, what it examined."""

    detector: str
    capability_tag: str
    findings: tuple[Finding, ...]
    hours: float
    #: Whether the detector actually opened something. False is never clean.
    examined: int
    state: ResultState
    #: Things it opened and could not read. A row carrying one of these is not clean either.
    undetermined: int = 0
    #: The cost table has no entry for this detector, so its findings were priced at the table's
    #: default. Never free — see :mod:`sightline.rubric.effort`.
    unpriced: bool = False

    @property
    def clean(self) -> bool:
        return (
            self.state is ResultState.EXAMINED
            and self.examined > 0
            and not self.findings
            and not self.undetermined
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "detector": self.detector,
            "capability_tag": self.capability_tag,
            "findings": [finding.to_dict() for finding in self.findings],
            "hours": self.hours,
            "examined": self.examined,
            "state": self.state.value,
            "undetermined": self.undetermined,
            "unpriced": self.unpriced,
            "clean": self.clean,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Contribution:
        """The inverse of :meth:`to_dict`. ``clean`` is recomputed, never read back."""
        return cls(
            detector=str(payload["detector"]),
            capability_tag=str(payload["capability_tag"]),
            findings=tuple(Finding.from_dict(entry) for entry in payload["findings"]),
            hours=float(payload["hours"]),
            examined=int(payload["examined"]),
            state=ResultState(payload["state"]),
            undetermined=int(payload.get("undetermined", 0)),
            unpriced=bool(payload.get("unpriced", False)),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class RowResult:
    """One rubric row after projection: its coverage, its rating, and the evidence behind both."""

    code: str
    section: str
    text: str
    tier: Tier
    priority: str | None
    coverage: Coverage
    rating: Rating
    #: The instrument's own label for :attr:`rating`.
    label: str
    #: Rolled-up hours. ``None`` whenever no arithmetic was done, which is not the same as 0.0.
    hours: float | None
    #: Tags as authored on the pack, verbatim, including any this build does not recognise.
    bindings: tuple[str, ...]
    #: Bound tags that resolved to at least one detector in this run.
    resolved_tags: tuple[str, ...] = ()
    #: Bound tags in the vocabulary that no detector implements yet.
    pending_tags: tuple[str, ...] = ()
    #: Bound tags this build does not recognise at all. Each one also raised a warning.
    unknown_tags: tuple[str, ...] = ()
    contributions: tuple[Contribution, ...] = ()
    #: Why the row reads what it reads. Always populated for anything that is not a plain band.
    notes: tuple[str, ...] = ()
    #: Whether this row needs the extraction layer. See :mod:`sightline.rubric.routing`.
    needs_extraction: bool = False
    #: Set when a *banded* row's hours sit within a margin of a band threshold. Computed once here
    #: and persisted with the row so every surface shows the same thing; a distance, never a
    #: confidence. See :mod:`sightline.rubric.effort`.
    near_boundary: NearBoundary | None = None

    @property
    def findings(self) -> tuple[Finding, ...]:
        return tuple(
            finding for contribution in self.contributions for finding in contribution.findings
        )

    @property
    def clean(self) -> bool:
        """True only for ``SUFFICIENTLY_PRESENT``, which only a deterministic Tier A row reaches."""
        return self.rating is Rating.SUFFICIENTLY_PRESENT

    def statement(self) -> str:
        """One declarative line: what was examined, what was found, what the row reads."""
        examined = sum(contribution.examined for contribution in self.contributions)
        found = len(self.findings)
        head = f"{self.code}: {self.label}"
        if self.coverage is Coverage.NO_DETERMINISTIC_COVERAGE:
            return f"{head} — no capability binding on this row; evidence is gathered instead."
        if self.coverage is Coverage.NOT_YET_IMPLEMENTED:
            pending = ", ".join(self.pending_tags)
            return f"{head} — bound to {pending}, which no detector implements yet."
        parts = [
            f"{len(self.contributions)} detector(s) examined {examined} artifact(s)",
            f"found {found} observation(s)",
        ]
        if self.hours is not None and found:
            parts.append(f"an estimated {self.hours:g} h of rolled-up effort")
        return f"{head} — " + ", ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "section": self.section,
            "text": self.text,
            "tier": self.tier.value,
            "priority": self.priority,
            "coverage": self.coverage.value,
            "rating": self.rating.value,
            "label": self.label,
            "hours": self.hours,
            "bindings": list(self.bindings),
            "resolved_tags": list(self.resolved_tags),
            "pending_tags": list(self.pending_tags),
            "unknown_tags": list(self.unknown_tags),
            "contributions": [entry.to_dict() for entry in self.contributions],
            "notes": list(self.notes),
            "needs_extraction": self.needs_extraction,
            "near_boundary": self.near_boundary.to_dict() if self.near_boundary else None,
            "clean": self.clean,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RowResult:
        """The inverse of :meth:`to_dict`. The rating is read back as recorded, not re-derived:
        a review file is what the run proposed, and re-projecting it at export would be a second
        run nobody asked for."""
        hours = payload["hours"]
        near = payload.get("near_boundary")
        return cls(
            code=str(payload["code"]),
            section=str(payload["section"]),
            text=str(payload["text"]),
            tier=Tier(payload["tier"]),
            priority=payload.get("priority"),
            coverage=Coverage(payload["coverage"]),
            rating=Rating(payload["rating"]),
            label=str(payload["label"]),
            hours=float(hours) if hours is not None else None,
            bindings=tuple(payload.get("bindings", ())),
            resolved_tags=tuple(payload.get("resolved_tags", ())),
            pending_tags=tuple(payload.get("pending_tags", ())),
            unknown_tags=tuple(payload.get("unknown_tags", ())),
            contributions=tuple(
                Contribution.from_dict(entry) for entry in payload.get("contributions", ())
            ),
            notes=tuple(payload.get("notes", ())),
            needs_extraction=bool(payload.get("needs_extraction", False)),
            near_boundary=NearBoundary.from_dict(near) if near is not None else None,
        )


# ---------------------------------------------------------------------------------------------
# The projection
# ---------------------------------------------------------------------------------------------

#: How urgently a rating sorts in an action plan. Not a score and never summed or averaged: SPEC
#: §11 forbids an aggregate quality number, and the reference instrument's own inverted VALUE
#: column exists to be averaged, which is exactly why this one is an ordering key and nothing else.
_SEVERITY: Mapping[Rating, int] = {
    Rating.MAJOR: 0,
    Rating.MODERATE: 1,
    Rating.MINOR: 2,
    Rating.NO_RESPONSE: 3,
    Rating.SUFFICIENTLY_PRESENT: 4,
    Rating.NOT_APPLICABLE: 5,
}


@dataclass(frozen=True, kw_only=True, slots=True)
class Projection:
    """Every rendered row of one pack against one detection report."""

    pack_id: str
    pack_name: str
    pack_version: str
    rows: tuple[RowResult, ...]
    warnings: tuple[BindingWarning, ...] = ()
    #: Rows the pack declares but this run omitted, because their section is switched off.
    omitted: tuple[str, ...] = ()
    #: The instrument's own priority labels, most important first. Carried so ordering needs the
    #: projection and not the pack.
    priority_order: tuple[str, ...] = ()
    effort: EffortModel = field(default_factory=default_effort_model)
    #: The instrument's own rating words. Carried so a surface can name a rating no row reached —
    #: "no row reads Sufficiently Present" — without going back to the pack.
    rating_scale: RatingScale | None = None

    def row(self, code: str) -> RowResult | None:
        """The row with this code. Keyed on the code, never on a position."""
        for row in self.rows:
            if row.code == code:
                return row
        return None

    def by_coverage(self, coverage: Coverage) -> tuple[RowResult, ...]:
        return tuple(row for row in self.rows if row.coverage is coverage)

    def by_rating(self, rating: Rating) -> tuple[RowResult, ...]:
        return tuple(row for row in self.rows if row.rating is rating)

    @property
    def needing_extraction(self) -> tuple[RowResult, ...]:
        return tuple(row for row in self.rows if row.needs_extraction)

    def action_plan_order(self) -> tuple[RowResult, ...]:
        """Rows in action-plan order: priority crossed with band, instrument order as the tiebreak.

        Two axes, as SPEC §07 requires. **Priority may be null** — the reference instrument's
        mobile block carries no flag at all — and a null sorts after every labelled priority rather
        than being defaulted to a label nobody wrote.
        """
        order = {row.code: position for position, row in enumerate(self.rows)}
        return tuple(
            sorted(
                self.rows,
                key=lambda row: (
                    self.priority_rank(row.priority),
                    _SEVERITY[row.rating],
                    order[row.code],
                ),
            )
        )

    def priority_rank(self, priority: str | None) -> int:
        """Where a priority label sorts. Null sorts last; an unrecognised label sorts before null.

        Null is not an absence to be filled in. Nine unprioritised rows placed in the middle of an
        action plan on the strength of a default would be Sightline asserting something the
        instrument does not say.
        """
        if priority is None:
            return len(self.priority_order) + 1
        try:
            return self.priority_order.index(priority)
        except ValueError:
            return len(self.priority_order)

    def statement(self) -> str:
        tally = {coverage: len(self.by_coverage(coverage)) for coverage in Coverage}
        parts = [
            f"{self.pack_name} {self.pack_version}: {len(self.rows)} row(s) projected",
            f"{tally[Coverage.DETERMINISTIC]} with deterministic coverage",
            f"{tally[Coverage.NOT_YET_IMPLEMENTED]} bound to a capability no detector implements "
            "yet",
            f"{tally[Coverage.NO_DETERMINISTIC_COVERAGE]} with no binding, gathering evidence "
            "instead",
        ]
        if self.omitted:
            parts.append(f"{len(self.omitted)} omitted with a switched-off section")
        if self.warnings:
            parts.append(f"{len(self.warnings)} binding warning(s)")
        return "; ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "pack_name": self.pack_name,
            "pack_version": self.pack_version,
            "statement": self.statement(),
            "effort_calibrated": self.effort.calibrated,
            "effort_note": self.effort.note,
            "effort_default_hours": self.effort.default_hours,
            "effort_margins": self.effort.margins.to_dict(),
            "effort_sources": list(self.effort.sources),
            "rows": [row.to_dict() for row in self.rows],
            "warnings": [warning.to_dict() for warning in self.warnings],
            "omitted": list(self.omitted),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, pack: RubricPack) -> Projection:
        """Read a projection back from a review file.

        :meth:`to_dict` does not carry :attr:`priority_order` or :attr:`rating_scale` — both are
        facts about the instrument, not about the run — so they come from ``pack``, which must be
        the pack the projection names *at the version it names*. A different version is refused
        rather than read: its priority flags or rating words could differ from the ones the run
        used, and the action plan would be ordered by an instrument the review was not made with.

        The effort model comes back as the run recorded it: whether the table was calibrated, its
        default, its margins and where it came from. The per-detector cost table itself is not in
        the file and is not needed — every hour figure is already on its row.
        """
        if pack.id != payload["pack_id"] or pack.version != payload["pack_version"]:
            raise ValueError(
                f"the projection was made with pack {payload['pack_id']!r} version "
                f"{payload['pack_version']!r}, and the pack given is {pack.id!r} version "
                f"{pack.version!r}"
            )
        return cls(
            pack_id=str(payload["pack_id"]),
            pack_name=str(payload["pack_name"]),
            pack_version=str(payload["pack_version"]),
            rows=tuple(RowResult.from_dict(entry) for entry in payload["rows"]),
            warnings=tuple(
                BindingWarning(
                    criterion=str(entry["criterion"]),
                    tag=str(entry["tag"]),
                    reason=str(entry["reason"]),
                    suggestions=tuple(entry.get("suggestions", ())),
                )
                for entry in payload.get("warnings", ())
            ),
            omitted=tuple(payload.get("omitted", ())),
            priority_order=pack.priority_order,
            effort=EffortModel(
                calibrated=bool(payload["effort_calibrated"]),
                default_hours=float(payload["effort_default_hours"]),
                margins=Margins.from_dict(payload["effort_margins"]),
                sources=tuple(payload.get("effort_sources", ())),
            ),
            rating_scale=pack.rating_scale,
        )


def project(
    pack: RubricPack,
    report: DetectionReport,
    *,
    tags: TagIndex | None = None,
    effort: EffortModel | None = None,
) -> Projection:
    """Project one detection report onto one pack.

    A pure function of its arguments. ``tags`` defaults to the shipped detector registry and
    ``effort`` to the shipped, uncalibrated cost table; both are parameters so the suite can prove
    the arithmetic without the registry and without pretending the costs are calibrated.
    """
    index = tags or TagIndex.build()
    model = effort or default_effort_model()
    results_by_tag = _results_by_tag(report)

    rows: list[RowResult] = []
    warnings: list[BindingWarning] = []
    for criterion in pack.rendered_criteria():
        row, row_warnings = _row(criterion, pack, index, results_by_tag, model)
        rows.append(row)
        warnings.extend(row_warnings)

    return Projection(
        pack_id=pack.id,
        pack_name=pack.name,
        pack_version=pack.version,
        rows=tuple(rows),
        warnings=tuple(warnings),
        omitted=tuple(criterion.code for criterion in pack.omitted_criteria()),
        priority_order=pack.priority_order,
        effort=model,
        rating_scale=pack.rating_scale,
    )


def _results_by_tag(report: DetectionReport) -> Mapping[str, tuple[DetectorResult, ...]]:
    """Group the run's results by the tag each detector declared.

    Grouping on the result rather than on the registry is what makes a detector that could not run
    reachable from the row it feeds: a ``NOT_EXAMINED`` result is still in here, and a row that
    receives one can say so instead of reading clean.
    """
    grouped: dict[str, list[DetectorResult]] = {}
    for result in report.results:
        grouped.setdefault(result.capability_tag, []).append(result)
    return {
        tag: tuple(sorted(results, key=lambda result: result.detector))
        for tag, results in sorted(grouped.items())
    }


def _row(
    criterion: Criterion,
    pack: RubricPack,
    index: TagIndex,
    results_by_tag: Mapping[str, tuple[DetectorResult, ...]],
    model: EffortModel,
) -> tuple[RowResult, list[BindingWarning]]:
    scale = pack.rating_scale
    warnings: list[BindingWarning] = []
    resolved: list[str] = []
    pending: list[str] = []
    unknown: list[str] = []
    not_run: list[str] = []
    contributions: list[Contribution] = []
    notes: list[str] = []

    for tag in criterion.bindings:
        if not index.recognises(tag):
            unknown.append(tag)
            warnings.append(
                BindingWarning(
                    criterion=criterion.code,
                    tag=tag,
                    reason=(
                        "not in this build's capability vocabulary, so it cannot be resolved to a "
                        "detector. The row is treated as having no deterministic coverage and its "
                        "evidence is gathered instead."
                    ),
                    suggestions=index.vocabulary.suggestions(tag),
                )
            )
            continue
        if not index.implements(tag):
            pending.append(tag)
            continue
        results = results_by_tag.get(tag, ())
        if not results:
            # The tag is implemented and nothing in this report carries it: the detector exists and
            # did not run here. That is a hole, not a pass, and it is recorded as one.
            not_run.append(tag)
            notes.append(
                f"{tag}: a detector implements this capability but did not run in this review, so "
                "nothing here establishes the row."
            )
            continue
        resolved.append(tag)
        for result in results:
            contributions.append(
                Contribution(
                    detector=result.detector,
                    capability_tag=tag,
                    findings=result.findings,
                    hours=model.total(result.findings),
                    examined=result.examined,
                    state=result.state,
                    undetermined=len(result.undetermined),
                    unpriced=not model.priced(result.detector),
                )
            )

    coverage = coverage_of(resolved, pending)
    if coverage is Coverage.NO_DETERMINISTIC_COVERAGE and not criterion.bindings:
        notes.append(
            "No capability is bound to this row, which is the ordinary state for an instrument "
            "Sightline does not ship a mapping for. Its evidence is located and quoted rather "
            "than computed."
        )
    for tag in pending:
        notes.append(
            f"{tag}: in the capability vocabulary, with no detector implementing it yet. Nothing "
            "in this review examined it."
        )

    rating, hours, rating_notes = _rate(
        criterion, coverage, contributions, pack, model, unexamined=(*pending, *unknown, *not_run)
    )
    notes.extend(rating_notes)
    near = None
    if rating.is_band and hours is not None:
        # Only a banded row has an effort figure to measure from. Sufficiently Present is "no
        # qualifying defects found", and every other reading did no arithmetic at all.
        near = model.near_boundary(hours, [(band.rating, band.up_to_hours) for band in scale.bands])
        for entry in contributions:
            if entry.unpriced and entry.findings:
                notes.append(
                    f"{entry.detector} has no entry in the cost table, so its "
                    f"{len(entry.findings)} observation(s) are priced at the table's default of "
                    f"{model.default_hours:g} h each rather than at nothing."
                )
    if criterion.note:
        notes.append(criterion.note)

    row = RowResult(
        code=criterion.code,
        section=criterion.section,
        text=criterion.text,
        tier=criterion.tier,
        priority=criterion.priority,
        coverage=coverage,
        rating=rating,
        label=_label(rating, scale),
        hours=hours,
        bindings=criterion.bindings,
        resolved_tags=tuple(resolved),
        pending_tags=tuple(pending),
        unknown_tags=tuple(unknown),
        contributions=tuple(contributions),
        notes=tuple(notes),
        needs_extraction=routes_to_extraction(criterion.tier, coverage),
        near_boundary=near,
    )
    return row, warnings


def _rate(
    criterion: Criterion,
    coverage: Coverage,
    contributions: Sequence[Contribution],
    pack: RubricPack,
    model: EffortModel,
    *,
    unexamined: Sequence[str] = (),
) -> tuple[Rating, float | None, list[str]]:
    """The rating for one row, and the hours behind it.

    Ordered deliberately: the tiers are checked *before* the arithmetic, because a Tier C row must
    stay at ``No Response`` however many findings its bindings produced, and a rule applied after
    banding is a rule somebody removes when the band looks more useful.

    ``unexamined`` are bound tags that contributed nothing to this review — no detector yet, not in
    the vocabulary, or implemented and absent from the report. A Tier A row settles its criterion
    only with *all* of its bindings run clean, so one of these keeps it from reading clean.
    """
    notes: list[str] = []

    if criterion.tier is Tier.D:
        notes.append(
            "Not determinable from course artifacts, so this row is left Not Applicable for a "
            "human to set."
        )
        return Rating.NOT_APPLICABLE, None, notes

    if criterion.tier is Tier.C:
        if contributions:
            found = sum(len(entry.findings) for entry in contributions)
            notes.append(
                f"This is a pedagogical judgement, so no rating is proposed. The {found} "
                "observation(s) below are context for the question, not an answer to it."
            )
        else:
            notes.append(
                "This is a pedagogical judgement, so no rating is proposed; the context below is "
                "assembled for a human to decide on."
            )
        return Rating.NO_RESPONSE, None, notes

    if coverage is not Coverage.DETERMINISTIC:
        # Both remaining states land here, and neither may read clean. Their own notes are already
        # on the row and say which of the two it is.
        return Rating.NO_RESPONSE, None, notes

    unusable = [entry for entry in contributions if entry.state is not ResultState.EXAMINED]
    if unusable:
        names = ", ".join(entry.detector for entry in unusable)
        notes.append(
            f"{names} examined nothing, so this row was not established. It is left at the "
            "instrument's default rather than reported as satisfied."
        )
        return Rating.NO_RESPONSE, None, notes

    unreadable = [entry for entry in contributions if entry.undetermined]
    findings = [finding for entry in contributions for finding in entry.findings]

    if unreadable and not findings:
        count = sum(entry.undetermined for entry in unreadable)
        notes.append(
            f"{count} item(s) could not be read, so an absence of observations here is not "
            "evidence of their absence in the course."
        )
        return Rating.NO_RESPONSE, None, notes

    # SUM, then band. One global rule for every row (see the module docstring).
    hours = model.total(findings)

    if not findings:
        examined = sum(entry.examined for entry in contributions)
        notes.append(
            f"{len(contributions)} detector(s) examined {examined} artifact(s) and recorded no "
            "qualifying observations."
        )
        if criterion.tier is not Tier.A:
            # A binding says the capability is RELEVANT to the row; only Tier A says it SETTLES it
            # (SPEC §02: A is "rating pre-selected", B is "rating proposed"). GSOV 7 asks what the
            # syllabus states and a presence check cannot see that, so its clean run is not a
            # clean row. Roadmap item 28.
            notes.append(
                "Those detectors bear on this criterion without settling it, so finding nothing "
                "does not establish it. It is left at the instrument's default rather than "
                "reported as satisfied, and the passages located for it are the evidence."
            )
            return Rating.NO_RESPONSE, None, notes
        if unexamined:
            # Tier A says the bindings settle the row *together*. ACD 1 binds the tool inventory
            # and the tools' accessibility statements; the inventory alone run clean is not it.
            notes.append(
                f"Bound here and examined by nothing in this review: {', '.join(unexamined)}. "
                "The rest finding nothing does not establish the row, so it is left at the "
                "instrument's default rather than reported as satisfied."
            )
            return Rating.NO_RESPONSE, None, notes
        return Rating.SUFFICIENTLY_PRESENT, hours, notes

    if unreadable:
        count = sum(entry.undetermined for entry in unreadable)
        notes.append(
            f"{count} further item(s) could not be read, so the rolled-up effort is a floor "
            "rather than a total."
        )

    if hours <= 0:
        # Findings exist, so "no qualifying defects found" is false whatever the cost table says.
        # Banding a positive count as clean is the collapse this module exists to prevent.
        notes.append(
            f"{len(findings)} observation(s) were recorded and the cost table prices them at zero, "
            "so the lowest band applies rather than a clean reading."
        )
        band = pack.rating_scale.bands[0] if pack.rating_scale.bands else None
        return (Rating(band.rating) if band else Rating.NO_RESPONSE), hours, notes

    band = pack.rating_scale.band_for(hours)
    if unexamined:
        notes.append(
            f"Bound here and examined by nothing in this review: {', '.join(unexamined)}. The "
            "rolled-up effort covers only what the other capabilities could see."
        )
    if criterion.tier is not Tier.A:
        notes.append(
            "This band is proposed from the observations below. The detectors behind them bear on "
            "this criterion without settling all of it, so the passages located for it may add to "
            "what needs doing."
        )
    if not model.calibrated:
        notes.append(model.note)
    return Rating(band.rating), hours, notes


def _label(rating: Rating, scale: RatingScale) -> str:
    """The instrument's own word for a rating."""
    if rating is Rating.SUFFICIENTLY_PRESENT:
        return scale.sufficiently_present
    if rating is Rating.NOT_APPLICABLE:
        return scale.not_applicable
    if rating is Rating.NO_RESPONSE:
        return scale.no_response
    band = scale.band_named(rating.value)
    return band.label if band is not None else rating.value


def tag_descriptions(declared: Iterable[Detector] | None = None) -> Mapping[str, tuple[str, ...]]:
    """Tag -> what each detector behind it says it contributes.

    This is the catalogue a person authoring a pack reads, and the catalogue roadmap item 22 will
    show a model. It is assembled from the detectors' own words; no criterion appears in it.
    """
    instances = tuple(declared) if declared is not None else detectors()
    grouped: dict[str, list[str]] = {}
    for detector in instances:
        grouped.setdefault(detector.capability_tag, []).append(detector.capability_description)
    return {tag: tuple(entries) for tag, entries in sorted(grouped.items())}


__all__ = [
    "BindingWarning",
    "Contribution",
    "Coverage",
    "Projection",
    "Rating",
    "RowResult",
    "TagIndex",
    "project",
    "tag_descriptions",
]
