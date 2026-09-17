"""The routing contract: which rows the extraction layer is asked about, and what it is told.

**Nothing here exercises extraction, because nothing here implements it.** Roadmap item 10 builds
that against this contract; what is asserted below is the rule for which rows need one and the
shape of the request handed over, so item 10 does not have to re-decide either.

The two assertions worth reading twice:

* **A request carries no rating, no band and no score, and there is no field for one.** A model is
  asked where something is, never what it is worth.
* **A Tier D row is never routed.** A criterion about what somebody tested the course on is not in
  the course artifacts, and asking a model about it is how a fabrication gets in.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

import pytest

from sightline.detect.base import Tier
from sightline.rubric.pack import load
from sightline.rubric.project import Coverage, TagIndex, project
from sightline.rubric.routing import (
    EXPECTATIONS,
    ExtractionRequest,
    RoutingContractError,
    extraction_request,
    extraction_requests,
    manifest,
    routes_to_extraction,
    routing_reason,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rubric"
TINY = FIXTURES / "tiny-pack.yaml"
UNBOUND = FIXTURES / "unbound-pack.yaml"


@dataclass(frozen=True)
class FakeRow:
    """The structural shape the contract reads. Enough of a row and nothing else."""

    code: str
    section: str
    text: str
    tier: Tier
    coverage: Coverage


# -- the rule ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tier", "coverage", "expected"),
    [
        (Tier.A, Coverage.DETERMINISTIC, False),
        (Tier.A, Coverage.NOT_YET_IMPLEMENTED, True),
        (Tier.A, Coverage.NO_DETERMINISTIC_COVERAGE, True),
        (Tier.B, Coverage.DETERMINISTIC, True),
        (Tier.B, Coverage.NOT_YET_IMPLEMENTED, True),
        (Tier.B, Coverage.NO_DETERMINISTIC_COVERAGE, True),
        (Tier.C, Coverage.DETERMINISTIC, True),
        (Tier.C, Coverage.NOT_YET_IMPLEMENTED, True),
        (Tier.C, Coverage.NO_DETERMINISTIC_COVERAGE, True),
        (Tier.D, Coverage.DETERMINISTIC, False),
        (Tier.D, Coverage.NOT_YET_IMPLEMENTED, False),
        (Tier.D, Coverage.NO_DETERMINISTIC_COVERAGE, False),
    ],
)
def test_the_routing_rule_is_stated_once_and_holds_for_every_combination(
    tier: Tier, coverage: Coverage, expected: bool
) -> None:
    """The whole table, exhaustively. Twelve cases is few enough to write out, and writing them
    out is what makes a later change to the rule visible as a change rather than as a test edit."""
    assert routes_to_extraction(tier, coverage) is expected


def test_a_tier_d_row_is_never_routed_whatever_its_coverage() -> None:
    """Stated separately because it is the one case where *not* asking is the safety property.
    A model asked about something the artifacts cannot answer will answer anyway."""
    for coverage in Coverage:
        assert routes_to_extraction(Tier.D, coverage) is False


def test_a_tier_a_row_a_parser_answered_is_not_routed() -> None:
    """The counts and locations are computed and checkable; a quote would add nothing a reviewer
    cannot already verify, and every extraction call costs a model round trip."""
    assert routes_to_extraction(Tier.A, Coverage.DETERMINISTIC) is False


def test_the_two_uncovered_states_route_for_different_stated_reasons() -> None:
    """Both go out; a reviewer can still tell which is which. One is permanent and ordinary, the
    other is a detector nobody has written yet, and collapsing them is the failure item 04 is
    about."""
    unbound = routing_reason(Tier.A, Coverage.NO_DETERMINISTIC_COVERAGE)
    pending = routing_reason(Tier.A, Coverage.NOT_YET_IMPLEMENTED)
    assert unbound != pending
    assert "No capability is bound to this row" in unbound
    assert "it is not a fault in the rubric" in unbound
    assert "no detector implements yet" in pending


# -- the request ---------------------------------------------------------------------------------


def test_a_request_carries_no_rating_no_band_and_no_score() -> None:
    """Asserted on the type rather than on an instance, so a field added later fails here.

    A model is asked where something is, never what it is worth. There is deliberately nowhere in
    this dataclass for a rating to be written, which is a stronger guarantee than a convention
    about not writing one.
    """
    names = {field.name for field in fields(ExtractionRequest)}
    assert names == {"criterion", "section", "text", "tier", "coverage", "reason", "expects"}
    for forbidden in ("rating", "band", "score", "hours", "confidence", "verdict", "severity"):
        assert forbidden not in names


def test_a_request_quotes_the_criterion_as_the_instrument_wrote_it() -> None:
    projection = project(load(UNBOUND), _no_findings(), tags=TagIndex.build([]))
    request = extraction_request(projection.rows[0])
    assert request.criterion == "A1"
    assert request.text == "The course shell is published before the first day of class."
    assert request.tier is Tier.B
    assert request.coverage is Coverage.NO_DETERMINISTIC_COVERAGE


def test_what_an_extractor_is_asked_for_forbids_a_rating_in_its_own_words() -> None:
    """The prompt text itself, not only the schema. Both halves matter: the type has no field for
    a rating and the instruction says not to propose one."""
    for tier, expects in EXPECTATIONS.items():
        assert "verbatim" in expects
        if tier is Tier.C:
            assert "do not propose one" in expects
        else:
            assert "empty list is a valid and expected answer" in expects
    assert "do not rate" in EXPECTATIONS[Tier.B]


def test_a_tier_c_request_says_the_row_stays_at_the_default_rating() -> None:
    """Context is assembled, the question is drafted, and nothing is pre-selected (SPEC §02)."""
    expects = EXPECTATIONS[Tier.C]
    assert "answer this for themselves" in expects
    assert "stays at the instrument's default rating" in expects


def test_asking_for_a_request_on_a_row_the_contract_does_not_route_raises() -> None:
    """A caller that routed everything would get a fabrication on the Tier D rows, so the
    contract refuses rather than obliging."""
    withheld = FakeRow(
        code="S 1",
        section="S",
        text="Tested on physical devices.",
        tier=Tier.D,
        coverage=Coverage.NO_DETERMINISTIC_COVERAGE,
    )
    with pytest.raises(RoutingContractError, match="does not route") as caught:
        extraction_request(withheld)
    assert "S 1" in str(caught.value)
    assert "how a fabrication gets in" in str(caught.value)


def test_requests_are_built_only_for_the_rows_the_contract_routes() -> None:
    rows = [
        FakeRow(code="A", section="S", text="x", tier=Tier.A, coverage=Coverage.DETERMINISTIC),
        FakeRow(
            code="B",
            section="S",
            text="x",
            tier=Tier.A,
            coverage=Coverage.NO_DETERMINISTIC_COVERAGE,
        ),
        FakeRow(code="C", section="S", text="x", tier=Tier.D, coverage=Coverage.DETERMINISTIC),
    ]
    assert [request.criterion for request in extraction_requests(rows)] == ["B"]


# -- the manifest --------------------------------------------------------------------------------


def test_the_manifest_says_what_was_withheld_and_why() -> None:
    """Routed and withheld together, each withheld row carrying its reason. A manifest listing
    only what went out would make the rows that did not look like rows that were forgotten."""
    projection = project(load(TINY), _no_findings(), tags=TagIndex.build([]))
    plan = manifest(projection.rows)

    routed = {request.criterion for request in plan.requests}
    withheld = dict(plan.withheld)
    assert routed | set(withheld) == {entry.code for entry in projection.rows}
    assert not routed & set(withheld)

    # The Tier D row is the one held back, and the reason says what cannot be quoted.
    assert set(withheld) == {"REA 4"}
    assert "nothing in them to quote" in withheld["REA 4"]


def test_a_manifest_over_a_fully_covered_pack_withholds_the_parser_answered_rows() -> None:
    rows = [
        FakeRow(code="A", section="S", text="x", tier=Tier.A, coverage=Coverage.DETERMINISTIC),
        FakeRow(code="B", section="S", text="x", tier=Tier.B, coverage=Coverage.DETERMINISTIC),
    ]
    plan = manifest(rows)
    assert [request.criterion for request in plan.requests] == ["B"]
    assert dict(plan.withheld)["A"].startswith("Established by a parser")


def test_the_manifest_statement_is_counts_by_tier() -> None:
    projection = project(load(UNBOUND), _no_findings(), tags=TagIndex.build([]))
    plan = manifest(projection.rows)
    statement = plan.statement()
    assert "4 row(s) routed for located evidence" in statement
    assert "2 tier B" in statement
    assert "2 tier C" in statement
    assert "0 row(s) not routed." in statement


def test_every_row_of_an_unbound_pack_is_routed() -> None:
    """The routing half of the headline claim: a rubric with no bindings still produces a review,
    and what produces it is this contract."""
    projection = project(load(UNBOUND), _no_findings(), tags=TagIndex.build([]))
    plan = manifest(projection.rows)
    assert len(plan.requests) == len(projection.rows) == 4
    assert plan.withheld == ()
    for request in plan.requests:
        assert request.reason
        assert request.expects
        assert "no rating" not in request.to_dict()


def _no_findings():
    """A detection report from a run that examined nothing of its own.

    The routing contract is a function of a row's tier and coverage, so the findings are beside
    the point here — what matters is that the rows exist and the contract sorts them.
    """
    from sightline.detect.runner import DetectionReport

    return DetectionReport(results=())
