"""The routing contract: which rubric rows the extraction layer is asked about, and what it is told.

**This module defines an interface and implements no extraction.** Roadmap item 10 builds the
extraction layer against what is here; nothing in this file calls a model, and nothing in it may.
What it owns is the rule for *which* rows need one and the shape of the request handed over, so
that item 10 can be written without re-deciding either.

**Why a row needs extraction at all.** A pack with no bindings is normal, not broken: binding is an
optimization, not a prerequisite, and a freshly imported third-party instrument has none. Those
rows still owe the reviewer evidence, and an extraction-only row breaks no rule of this project —
it carries verbatim quotes with locations, verified against the source before acceptance, and the
determinacy tiers already keep that evidence linguistically and visually distinct from Tier A
arithmetic. What it must never do is read as satisfied.

THE RULE, stated once (:func:`routes_to_extraction`)
----------------------------------------------------

=============================  =========================  ===================================
Tier                           Coverage                   Routes?
=============================  =========================  ===================================
A                              deterministic              **no** — the arithmetic answered it
A                              no binding, or pending      yes — nothing else can answer it
B                              any                         yes — Tier B *is* located evidence
C                              any                         yes — context for a human's question
D                              any                        **no** — not in the artifacts at all
=============================  =========================  ===================================

Tier B and Tier C route regardless of their bindings, and that is not a redundancy. A Tier B row's
deliverable is a quote a reviewer can search the page for; counts alone do not discharge it. A Tier
C row's deliverable is the context behind a question, and it stays at ``No Response`` whatever comes
back. Tier D never routes: a criterion about testing on physical devices is not in the course
artifacts, and asking a model about it is how a fabrication gets in.

**What the request does and does not carry.** It carries the row's code, the criterion text as
written on the instrument, the tier, and the reason it is being asked. It carries no rating, no
proposed rating, no band and no score, because there is no field here in which a model could put
one. :attr:`ExtractionRequest.expects` states the same thing in the words that go into a prompt.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..detect.base import Tier
from .states import Coverage

#: What an extractor is asked for, per tier, in the words that reach a prompt. There is no entry
#: for a rating because a rating is never asked for.
EXPECTATIONS: dict[Tier, str] = {
    Tier.A: (
        "Passages in the course that bear on this criterion, each quoted verbatim with its "
        "location. No parser established this row, so quotes are the only evidence available for "
        "it. Return an empty list if there are none; an empty list is a valid and expected answer."
    ),
    Tier.B: (
        "Passages in the course that bear on this criterion, each quoted verbatim with its "
        "location. Locate and quote; do not summarise, do not rate, and do not write prose. "
        "Return an empty list if there are none; an empty list is a valid and expected answer."
    ),
    Tier.C: (
        "The context a reviewer needs to answer this for themselves: the relevant passages quoted "
        "verbatim with their locations, and the counts that describe their distribution. This row "
        "stays at the instrument's default rating whatever comes back, so do not propose one."
    ),
}


class RoutingContractError(ValueError):
    """An extraction request was asked for a row that must not be routed."""


def routes_to_extraction(tier: Tier, coverage: Coverage) -> bool:
    """Whether a row goes to the extraction layer. The single statement of the rule above."""
    if tier is Tier.D:
        return False
    if tier in (Tier.B, Tier.C):
        return True
    return coverage is not Coverage.DETERMINISTIC


def routing_reason(tier: Tier, coverage: Coverage) -> str:
    """Why this row is being routed, in a sentence that can be shown to a reviewer.

    Stated per *state* rather than per row, because the three coverage states are the thing this
    must not blur: a row with no binding and a row whose detector is not written yet are both
    routed, and they are routed for different reasons that a reviewer should be able to tell apart.
    """
    if tier is Tier.C:
        return (
            "This criterion is a pedagogical judgement. Context is assembled for a human to decide "
            "on; no rating is proposed for it."
        )
    if tier is Tier.B:
        return (
            "This criterion is answered by a passage of the course rather than by a count, so the "
            "evidence for it is located and quoted."
        )
    if coverage is Coverage.NO_DETERMINISTIC_COVERAGE:
        return (
            "No capability is bound to this row of the instrument, so nothing computed bears on "
            "it. Its evidence is located and quoted instead. This is the ordinary state for an "
            "instrument Sightline does not ship a mapping for and it is not a fault in the rubric."
        )
    if coverage is Coverage.NOT_YET_IMPLEMENTED:
        return (
            "This row is bound to a capability no detector implements yet, so nothing computed "
            "bears on it in this build. Its evidence is located and quoted instead."
        )
    return "This row is routed for located evidence."


@dataclass(frozen=True, kw_only=True, slots=True)
class ExtractionRequest:
    """One row handed to the extraction layer. The interface roadmap item 10 implements against.

    Note what is absent: no rating, no proposed rating, no band, no score, no confidence. A model
    is asked where something is, never what it is worth.
    """

    #: The rubric row's code. The key everything downstream joins on, never a position.
    criterion: str
    section: str
    #: The criterion as written on the instrument, verbatim. What the extractor is searching for.
    text: str
    tier: Tier
    coverage: Coverage
    #: Why this row is being asked about, in a sentence a reviewer can read.
    reason: str
    #: What the extractor must return, in the words that reach the prompt.
    expects: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "section": self.section,
            "text": self.text,
            "tier": self.tier.value,
            "coverage": self.coverage.value,
            "reason": self.reason,
            "expects": self.expects,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ExtractionRequest:
        """Rebuild a request from its own serialization. Kept beside :meth:`to_dict` on purpose.

        A recorded request outlives the process that made it — roadmap item 11 writes the
        disagreement set to a file and item 18 re-runs it later, possibly on another day. The
        inverse therefore has to live next to the forward direction: a field added to this class
        and to ``to_dict`` while the reader sits in another module is a field that silently stops
        round-tripping, and the subset item 18 re-runs quietly becomes a different subset. The
        suite asserts the pair covers every field of the dataclass.
        """
        return cls(
            criterion=str(payload["criterion"]),
            section=str(payload["section"]),
            text=str(payload["text"]),
            tier=Tier(payload["tier"]),
            coverage=Coverage(payload["coverage"]),
            reason=str(payload["reason"]),
            expects=str(payload["expects"]),
        )


def extraction_request(row: Any) -> ExtractionRequest:
    """Build the request for one projected row.

    Takes a :class:`~sightline.rubric.project.RowResult` structurally rather than by import, so
    this module stays the lower layer and the projection depends on it and not the other way
    round.
    """
    if not routes_to_extraction(row.tier, row.coverage):
        raise RoutingContractError(
            f"{row.code} is tier {row.tier.value} with {row.coverage.value} coverage, which the "
            "routing contract does not route. Asking a model about a row the artifacts cannot "
            "answer is how a fabrication gets in."
        )
    return ExtractionRequest(
        criterion=row.code,
        section=row.section,
        text=row.text,
        tier=row.tier,
        coverage=row.coverage,
        reason=routing_reason(row.tier, row.coverage),
        expects=EXPECTATIONS[row.tier],
    )


def extraction_requests(rows: Iterable[Any]) -> tuple[ExtractionRequest, ...]:
    """Requests for every row the contract routes, in the order the rows were given."""
    return tuple(
        extraction_request(row) for row in rows if routes_to_extraction(row.tier, row.coverage)
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class RoutingManifest:
    """What was routed and what was not, as a checkable artifact rather than a side effect."""

    requests: tuple[ExtractionRequest, ...]
    #: Codes of rows the contract did not route, with the reason each was held back.
    withheld: tuple[tuple[str, str], ...] = ()

    def statement(self) -> str:
        by_tier: dict[str, int] = {}
        for request in self.requests:
            by_tier[request.tier.value] = by_tier.get(request.tier.value, 0) + 1
        breakdown = ", ".join(f"{count} tier {tier}" for tier, count in sorted(by_tier.items()))
        head = f"{len(self.requests)} row(s) routed for located evidence"
        if breakdown:
            head += f" ({breakdown})"
        return f"{head}; {len(self.withheld)} row(s) not routed."

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement(),
            "requests": [request.to_dict() for request in self.requests],
            "withheld": [{"criterion": code, "reason": reason} for code, reason in self.withheld],
        }


def manifest(rows: Sequence[Any]) -> RoutingManifest:
    """Every row split into routed and withheld, each withheld row carrying why."""
    routed: list[ExtractionRequest] = []
    withheld: list[tuple[str, str]] = []
    for row in rows:
        if routes_to_extraction(row.tier, row.coverage):
            routed.append(extraction_request(row))
        elif row.tier is Tier.D:
            withheld.append(
                (
                    row.code,
                    "Not determinable from course artifacts, so there is nothing in them to quote.",
                )
            )
        else:
            withheld.append(
                (
                    row.code,
                    "Established by a parser: the counts and locations are computed, and a quote "
                    "would add nothing a reviewer cannot already check.",
                )
            )
    return RoutingManifest(requests=tuple(routed), withheld=tuple(withheld))


__all__ = [
    "EXPECTATIONS",
    "ExtractionRequest",
    "RoutingContractError",
    "RoutingManifest",
    "extraction_request",
    "extraction_requests",
    "manifest",
    "routes_to_extraction",
    "routing_reason",
]
