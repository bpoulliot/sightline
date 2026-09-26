"""Recording a reviewer's rating beside the machine proposal it replaces.

SPEC §09: *"Overrides are first-class and persisted, with the original machine proposal retained.
That record is the calibration data."* Roadmap item 12b fits the per-defect costs against exactly
this record, so it keeps both halves whole — the proposal as the effort model made it (rating,
rolled-up hours, near-boundary marker, the findings that were summed, whether the table behind them
was calibrated) and the rating a person chose, with who, when, and the rubric pack and version the
band was cut from.

**This module owns the write path and nothing on a screen.** Roadmap item 16 builds the review
screen and calls :func:`record_override`; nothing here renders anything. The store keeps the table
append-only (:meth:`sightline.store.RunStore.record_override`): an override never overwrites the
proposal, and a second override of the same row is a second record.

**A run nobody overrode writes nothing,** and that is the normal case.

A proposal can be built from a live :class:`~sightline.rubric.project.Projection` or from the
projection dictionary persisted in a course's review file — the latter is what a review screen
actually has in hand. Both produce the same proposal, and the suite says so.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .project import Projection
from .states import Rating

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..store import RunStore


@dataclass(frozen=True, kw_only=True, slots=True)
class MachineProposal:
    """What the effort model proposed for one row, as it was persisted."""

    criterion: str
    pack_id: str
    pack_version: str
    rating: Rating
    #: ``None`` when the row was never banded — which is not the same as zero hours.
    hours: float | None
    near_boundary: Mapping[str, Any] | None
    #: Every finding that was summed into ``hours``, as its own dictionary.
    findings: tuple[Mapping[str, Any], ...]
    calibrated: bool

    @classmethod
    def from_projection(cls, projection: Projection, criterion: str) -> MachineProposal:
        """The proposal for one row of a projection that is still in memory."""
        return cls.from_review(projection.to_dict(), criterion)

    @classmethod
    def from_review(cls, projection: Mapping[str, Any], criterion: str) -> MachineProposal:
        """The proposal for one row of a persisted projection (``review["projection"]``).

        Keyed on the row code, never on a position. An unknown code is a ``KeyError``: an override
        recorded against a row the review never had is a record 12b would silently mis-join.
        """
        for row in projection.get("rows", ()):
            if row.get("code") == criterion:
                break
        else:
            raise KeyError(criterion)
        findings = tuple(
            finding
            for contribution in row.get("contributions", ())
            for finding in contribution.get("findings", ())
        )
        return cls(
            criterion=criterion,
            pack_id=str(projection["pack_id"]),
            pack_version=str(projection["pack_version"]),
            rating=Rating(row["rating"]),
            hours=row.get("hours"),
            near_boundary=row.get("near_boundary"),
            findings=findings,
            calibrated=bool(projection.get("effort_calibrated", False)),
        )


def record_override(
    store: RunStore,
    *,
    course_id: str,
    proposal: MachineProposal,
    human: Rating,
    reviewer: str,
    note: str | None = None,
) -> int:
    """Append one override record: the machine proposal and the human rating, side by side.

    Returns the record's id. Recording a rating equal to the proposal is allowed — a reviewer
    confirming a band on purpose is calibration data too — but nothing here writes one unasked.
    """
    return store.record_override(
        course_id,
        criterion=proposal.criterion,
        pack_id=proposal.pack_id,
        pack_version=proposal.pack_version,
        machine_rating=proposal.rating.value,
        machine_hours=proposal.hours,
        machine_near_boundary=(
            dict(proposal.near_boundary) if proposal.near_boundary is not None else None
        ),
        machine_findings=[dict(finding) for finding in proposal.findings],
        effort_calibrated=proposal.calibrated,
        human_rating=Rating(human).value,
        reviewer=reviewer,
        note=note,
    )


__all__ = ["MachineProposal", "record_override"]
