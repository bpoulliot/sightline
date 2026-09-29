"""A coverage report about the PACK, not about a course.

    "18 of 43 criteria have deterministic coverage, 25 are evidence-only."

**Why a pack reports on itself.** A gap in a rubric's bindings is information, and information a
person can act on: it tells them which rows a parser will answer, which rows will come back as
located quotes, and which rows are waiting on a detector nobody has written. Without this, the same
gap is silent degradation — the reviewer reads a report, sees rows with no counts on them, and has
no way to tell "nothing was wrong" from "nothing looked". That distinction is the whole of item 04
and SPEC §11's silent under-detection.

**This is computed before any course is touched.** It takes a pack and what this build implements,
and nothing else. So it can be shown on the screen where a rubric is uploaded, which is where the
question "will this actually work?" is asked, and it is the number roadmap item 22 shows moving
when a human accepts a proposed binding.

**It also names every detector feeding the pack that the cost table does not price** (roadmap item
12a). Such a detector is not free — its findings cost the table's documented default — but a gap in
the table is information of the same kind as a gap in the bindings, and it is reported here rather
than discovered in a band.

**A pack with no bindings reports 0 deterministic and every row evidence-only, and that is a
success line, not a failure line.** The wording here is held to that: nothing in this module says
degraded, incomplete, unsupported or missing about an unbound row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..detect.base import Tier
from .effort import EffortModel, default_effort_model
from .pack import Criterion, RubricPack
from .project import TagIndex
from .states import Coverage, coverage_of


@dataclass(frozen=True, kw_only=True, slots=True)
class RowCoverage:
    """What one row of the pack can be answered by, before any course is read."""

    code: str
    section: str
    tier: Tier
    coverage: Coverage
    #: Bound tags that resolve to at least one detector in this build.
    resolved: tuple[str, ...] = ()
    #: Bound tags in the vocabulary that no detector implements yet.
    pending: tuple[str, ...] = ()
    #: Bound tags this build does not recognise at all.
    unknown: tuple[str, ...] = ()
    #: Detector ids that would feed this row.
    detectors: tuple[str, ...] = ()

    @property
    def evidence_only(self) -> bool:
        """True when nothing computed bears on the row and it is not out of scope."""
        return self.coverage is Coverage.NO_DETERMINISTIC_COVERAGE and self.tier is not Tier.D

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "section": self.section,
            "tier": self.tier.value,
            "coverage": self.coverage.value,
            "resolved": list(self.resolved),
            "pending": list(self.pending),
            "unknown": list(self.unknown),
            "detectors": list(self.detectors),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class PackCoverage:
    """Every rendered row of one pack, bucketed by what can answer it.

    The four buckets are exhaustive and disjoint over the rendered rows, which is what makes this
    a checkable artifact rather than a summary: the counts have to add up, and
    :meth:`ok` says whether they do.
    """

    pack_id: str
    pack_name: str
    pack_version: str
    rows: tuple[RowCoverage, ...]
    #: Codes omitted because their section is switched off. Reported, not dropped.
    omitted: tuple[str, ...] = ()
    #: Detectors feeding a rendered row that have no entry in the cost table.
    unpriced: tuple[str, ...] = ()
    #: What each of their findings costs instead. Never zero.
    default_hours: float = 0.0

    @property
    def deterministic(self) -> tuple[RowCoverage, ...]:
        """Rows a parser answers. Never a Tier D row, whatever it binds."""
        return tuple(
            row
            for row in self.rows
            if row.tier is not Tier.D and row.coverage is Coverage.DETERMINISTIC
        )

    @property
    def not_yet_implemented(self) -> tuple[RowCoverage, ...]:
        """Rows bound only to capabilities no detector implements yet. Never a Tier D row."""
        return tuple(
            row
            for row in self.rows
            if row.tier is not Tier.D and row.coverage is Coverage.NOT_YET_IMPLEMENTED
        )

    @property
    def evidence_only(self) -> tuple[RowCoverage, ...]:
        return tuple(row for row in self.rows if row.evidence_only)

    @property
    def not_determinable(self) -> tuple[RowCoverage, ...]:
        """Tier D rows. Out of scope by the instrument's own reading, not a gap in Sightline.

        Bucketed by tier first, whatever the row binds (roadmap item 38): a Tier D row reads Not
        Applicable bound or not, so it is counted here and in no other bucket.
        """
        return tuple(row for row in self.rows if row.tier is Tier.D)

    @property
    def unknown_tags(self) -> tuple[str, ...]:
        seen: list[str] = []
        for row in self.rows:
            for tag in row.unknown:
                if tag not in seen:
                    seen.append(tag)
        return tuple(seen)

    @property
    def counts(self) -> Mapping[str, int]:
        return {
            "criteria": len(self.rows),
            "deterministic": len(self.deterministic),
            "not_yet_implemented": len(self.not_yet_implemented),
            "evidence_only": len(self.evidence_only),
            "not_determinable": len(self.not_determinable),
            "omitted": len(self.omitted),
        }

    @property
    def ok(self) -> bool:
        """Whether the buckets account for every rendered row. An arithmetic self-check.

        Present because a coverage report whose numbers do not add up is worse than no coverage
        report: it reads as authoritative and it is wrong.
        """
        tally = self.counts
        return (
            tally["deterministic"]
            + tally["not_yet_implemented"]
            + tally["evidence_only"]
            + tally["not_determinable"]
            == tally["criteria"]
        )

    def by_section(self) -> Mapping[str, tuple[RowCoverage, ...]]:
        grouped: dict[str, list[RowCoverage]] = {}
        for row in self.rows:
            grouped.setdefault(row.section, []).append(row)
        return {section: tuple(entries) for section, entries in grouped.items()}

    def statement(self) -> str:
        """The headline line. Counts and nothing else."""
        tally = self.counts
        total = tally["criteria"]
        parts = [
            f"{tally['deterministic']} of {total} criteria have deterministic coverage",
            f"{tally['evidence_only']} are evidence-only",
        ]
        if tally["not_yet_implemented"]:
            parts.append(
                f"{tally['not_yet_implemented']} are bound to a capability no detector implements "
                "yet"
            )
        if tally["not_determinable"]:
            parts.append(f"{tally['not_determinable']} are not determinable from course artifacts")
        if tally["omitted"]:
            parts.append(f"{tally['omitted']} more are omitted with a switched-off section")
        if self.unpriced:
            parts.append(
                f"{len(self.unpriced)} detector(s) feeding it have no entry in the cost table and "
                f"are priced at the default {self.default_hours:g} h per observation"
            )
        return f"{self.pack_name} {self.pack_version}: " + ", ".join(parts) + "."

    def detail(self) -> str:
        """The statement plus a per-section breakdown and the tags nothing recognises."""
        lines = [self.statement()]
        answered = {row.code for row in self.deterministic}
        for section, rows in self.by_section().items():
            deterministic = sum(1 for row in rows if row.code in answered)
            lines.append(f"  {section}: {deterministic} of {len(rows)} with coverage")
        if self.not_yet_implemented:
            names = ", ".join(
                sorted({tag for row in self.not_yet_implemented for tag in row.pending})
            )
            lines.append(f"  awaiting a detector: {names}")
        if self.unknown_tags:
            lines.append(
                f"  bound to {len(self.unknown_tags)} tag(s) this build does not recognise: "
                f"{', '.join(self.unknown_tags)}. Those rows gather evidence instead."
            )
        if self.unpriced:
            lines.append(
                f"  priced at the cost table's default of {self.default_hours:g} h per "
                f"observation: {', '.join(self.unpriced)}"
            )
        if not self.ok:
            lines.append("  the buckets above do not account for every row, which is a bug here.")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "pack_name": self.pack_name,
            "pack_version": self.pack_version,
            "statement": self.statement(),
            "counts": dict(self.counts),
            "ok": self.ok,
            "rows": [row.to_dict() for row in self.rows],
            "omitted": list(self.omitted),
            "unknown_tags": list(self.unknown_tags),
            "unpriced": list(self.unpriced),
            "default_hours": self.default_hours,
        }


def pack_coverage(
    pack: RubricPack, *, tags: TagIndex | None = None, effort: EffortModel | None = None
) -> PackCoverage:
    """What this build can answer of one pack, and price. No course involved."""
    index = tags or TagIndex.build()
    model = effort or default_effort_model()
    rows = tuple(_row(criterion, index) for criterion in pack.rendered_criteria())
    return PackCoverage(
        pack_id=pack.id,
        pack_name=pack.name,
        pack_version=pack.version,
        rows=rows,
        omitted=tuple(criterion.code for criterion in pack.omitted_criteria()),
        unpriced=model.unpriced(detector for row in rows for detector in row.detectors),
        default_hours=model.default_hours,
    )


def _row(criterion: Criterion, index: TagIndex) -> RowCoverage:
    resolved: list[str] = []
    pending: list[str] = []
    unknown: list[str] = []
    feeding: list[str] = []
    for tag in criterion.bindings:
        if not index.recognises(tag):
            unknown.append(tag)
        elif index.implements(tag):
            resolved.append(tag)
            feeding.extend(index.detectors_by_tag[tag])
        else:
            pending.append(tag)
    return RowCoverage(
        code=criterion.code,
        section=criterion.section,
        tier=criterion.tier,
        coverage=coverage_of(resolved, pending),
        resolved=tuple(resolved),
        pending=tuple(pending),
        unknown=tuple(unknown),
        detectors=tuple(sorted(set(feeding))),
    )


__all__ = ["PackCoverage", "RowCoverage", "pack_coverage"]
