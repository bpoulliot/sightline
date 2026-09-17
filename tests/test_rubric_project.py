"""Projecting findings onto rubric rows, and the three states that must never collapse into two.

THE HEADLINE TEST IN THIS FILE is
:func:`test_a_pack_with_zero_bindings_loads_runs_and_routes_every_row_to_extraction`. A freshly
imported third-party instrument has no bindings at all, and it has to produce a usable review: rows
marked ``NO_DETERMINISTIC_COVERAGE``, routed to the extraction layer, **not an error and above all
not "Sufficiently Present"**. If one test from this item survives, it is that one.

    NOT_YET_IMPLEMENTED         the bound capability exists; no detector implements it yet
    NO_DETERMINISTIC_COVERAGE   the row has no usable binding — normal, permanent, routes out
    SUFFICIENTLY_PRESENT        a detector ran and found nothing — THE ONLY ONE MEANING CLEAN

If an absent detector or an unbound row could produce "Sufficiently Present", Sightline would
report a course clean on criteria it never examined, which is the silent under-detection of SPEC
§11 and the worst thing this project can do. Every assertion below about a rating is really an
assertion about that.

**The synthetic detectors are the point of this module, not a shortcut.** Projection is a pure
function of a pack and a detection report, so the arithmetic is asserted against detectors that
emit an exact number of findings of an exact severity — which is how "sum, then band" can be told
apart from "worst of", a distinction that is invisible with real findings whose counts drift as the
sample course changes. Every one is defined inside a registry snapshot so none of them ships.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

from detector_fixtures import course, examined, page, refused
from sightline.detect import run_detectors
from sightline.detect.base import (
    Detector,
    Finding,
    Severity,
    Survey,
    registry_snapshot,
)
from sightline.detect.runner import DetectionReport
from sightline.rubric.effort import EffortModel, placeholder_effort_model
from sightline.rubric.pack import RubricPack, load, loads
from sightline.rubric.project import (
    Coverage,
    Projection,
    Rating,
    TagIndex,
    project,
    tag_descriptions,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rubric"
TINY = FIXTURES / "tiny-pack.yaml"
UNBOUND = FIXTURES / "unbound-pack.yaml"

#: A cost table with round numbers, so a band boundary can be crossed on purpose. Uncalibrated,
#: like the shipped placeholder, and stated as such by ``calibrated=False``.
PROBE_COSTS = EffortModel(
    costs={Severity.BARRIER: 0.25, Severity.FRICTION: 0.1, Severity.ADVISORY: 0.05},
    calibrated=False,
)


# -- synthetic detectors, none of which ship -----------------------------------------------------


class _Emitter(Detector, abstract=True):
    """Emits a fixed number of findings of a fixed severity over every body it opens."""

    count: int = 1
    severity: Severity = Severity.BARRIER
    collections = ("pages",)

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for body in survey.bodies():
            survey.counted(self.count)
            for position in range(self.count):
                yield survey.finding(
                    code="probe",
                    severity=self.severity,
                    location=survey.location(body.artifact, within=f"item {position + 1}"),
                    observed=f"probe {position + 1}",
                    evidence="<p>as written</p>",
                )


with registry_snapshot():

    class Alt(_Emitter):
        """Two barriers per page, under the alt-text capability."""

        id = "P01"
        title = "A probe that reports two observations per page"
        capability_tag = "alt_text"
        capability_description = "Two observations per page, for the arithmetic tests."
        count = 2

    class Headings(_Emitter):
        """Two barriers per page, under a different capability."""

        id = "P02"
        title = "A second probe that reports two observations per page"
        capability_tag = "heading_structure"
        capability_description = "Two more observations per page, for the fan-in test."
        count = 2

    class Links(_Emitter):
        """Two barriers per page, under a third capability."""

        id = "P03"
        title = "A third probe that reports two observations per page"
        capability_tag = "link_text"
        capability_description = "Two more observations per page, for the fan-in test."
        count = 2

    class Quiet(Detector):
        """Opens every page and finds nothing. The only shape that may read as clean."""

        id = "P04"
        title = "A probe that opens every page and reports nothing"
        collections = ("pages",)
        capability_tag = "alt_text"
        capability_description = "Opens everything and reports nothing, for the clean case."

        def examine(self, survey: Survey) -> Iterable[Finding]:
            for _ in survey.bodies():
                survey.counted()
            return ()

    class Unreadable(Detector):
        """Opens every page and cannot read any of them."""

        id = "P05"
        title = "A probe that opens every page and cannot read one of them"
        collections = ("pages",)
        capability_tag = "alt_text"
        capability_description = "Records what it could not read, for the undetermined case."

        def examine(self, survey: Survey) -> Iterable[Finding]:
            for body in survey.bodies():
                survey.undetermined(
                    survey.location(body.artifact), reason="the bytes were never acquired"
                )
            return ()


PROBES = {probe.id: probe for probe in (Alt(), Headings(), Links(), Quiet(), Unreadable())}


def probe_index(*ids: str) -> TagIndex:
    """A tag index over the named probes only. Never the shipped registry."""
    return TagIndex.build([PROBES[probe_id] for probe_id in ids])


def probe_report(*ids: str, pages: int = 1, **kwargs: object) -> DetectionReport:
    document = kwargs.pop("document", None) or course(
        [page(f"p{number}", f"<p>Page {number}</p>") for number in range(pages)]
    )
    return run_detectors(document, only=[PROBES[probe_id] for probe_id in ids])


def tiny() -> RubricPack:
    return load(TINY)


def pack_from(criteria: list[dict[str, object]], **overrides: object) -> RubricPack:
    """A one-section pack over the given rows. Uses the reference instrument's default bands."""
    import yaml

    payload: dict[str, object] = {
        "schema_version": 1,
        "id": "probe",
        "name": "Probe",
        "version": "1",
        "sections": [{"code": "S", "title": "A section"}],
        "criteria": criteria,
    }
    payload.update(overrides)
    return loads(yaml.safe_dump(payload, sort_keys=False), origin="probe.yaml")


def row(projection: Projection, code: str):
    found = projection.row(code)
    assert found is not None, f"{code} is not in the projection"
    return found


# ================================================================================================
# THE HEADLINE TEST
# ================================================================================================


def test_a_pack_with_zero_bindings_loads_runs_and_routes_every_row_to_extraction() -> None:
    """The one test to keep if only one survives.

    A rubric somebody has just uploaded has no bindings. It must LOAD, it must RUN, and every row
    must come back ``NO_DETERMINISTIC_COVERAGE`` and routed to the extraction layer. Three things
    it must not be: an error, a degraded review, and — above all — a row reading Sufficiently
    Present, which would be Sightline reporting a course clean on a criterion nothing examined.
    """
    pack = load(UNBOUND)
    projection = project(pack, probe_report("P01"), tags=probe_index("P01"), effort=PROBE_COSTS)

    assert len(projection.rows) == 4
    for entry in projection.rows:
        assert entry.coverage is Coverage.NO_DETERMINISTIC_COVERAGE, entry.code
        assert entry.rating is Rating.NO_RESPONSE, entry.code
        assert entry.rating is not Rating.SUFFICIENTLY_PRESENT, entry.code
        assert entry.clean is False, entry.code
        assert entry.needs_extraction is True, entry.code
        assert entry.hours is None, entry.code
        assert entry.label == "No Response", entry.code

    # Nothing about it reads as broken, and it raised nothing.
    assert projection.warnings == ()
    assert "4 with no binding, gathering evidence instead" in projection.statement()
    assert len(projection.needing_extraction) == 4


def test_a_row_bound_only_to_an_unimplemented_capability_reads_not_yet_implemented() -> None:
    """The second state, and it is distinct from both of the others.

    The tag is in the vocabulary and no detector implements it yet — transient, a development-phase
    artifact that disappears as the inventory lands. It must not read as "no binding" (which is
    permanent and normal) and it must not read as clean (which would be a lie).
    """
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Graded work carries a due date.",
                "tier": "A",
                "bindings": ["due_date_coverage"],
            }
        ]
    )
    projection = project(pack, probe_report("P01"), tags=probe_index("P01"), effort=PROBE_COSTS)
    entry = row(projection, "S 1")

    assert entry.coverage is Coverage.NOT_YET_IMPLEMENTED
    assert entry.coverage is not Coverage.NO_DETERMINISTIC_COVERAGE
    assert entry.rating is Rating.NO_RESPONSE
    assert entry.clean is False
    assert entry.pending_tags == ("due_date_coverage",)
    assert entry.needs_extraction is True
    assert any("no detector implementing it yet" in note for note in entry.notes)
    assert "which no detector implements yet" in entry.statement()


def test_the_three_states_are_told_apart_on_one_pack() -> None:
    """All three side by side, because the failure this item exists to prevent is any two of them
    reading the same."""
    projection = project(tiny(), probe_report("P01"), tags=probe_index("P01"), effort=PROBE_COSTS)
    assert row(projection, "DES 1").coverage is Coverage.DETERMINISTIC
    assert row(projection, "REA 3").coverage is Coverage.NOT_YET_IMPLEMENTED
    assert row(projection, "REA 1").coverage is Coverage.NO_DETERMINISTIC_COVERAGE
    # And only one of the three can read clean.
    clean = [entry.code for entry in projection.rows if entry.clean]
    assert clean == []


# ================================================================================================
# Only a detector that ran may read clean
# ================================================================================================


def test_sufficiently_present_requires_a_detector_that_ran_and_found_nothing() -> None:
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Images are described.",
                "tier": "A",
                "bindings": ["alt_text"],
            }
        ]
    )
    projection = project(pack, probe_report("P04"), tags=probe_index("P04"), effort=PROBE_COSTS)
    entry = row(projection, "S 1")
    assert entry.coverage is Coverage.DETERMINISTIC
    assert entry.rating is Rating.SUFFICIENTLY_PRESENT
    assert entry.clean is True
    assert entry.needs_extraction is False
    assert any("recorded no qualifying observations" in note for note in entry.notes)


def test_a_detector_that_examined_nothing_never_reads_clean() -> None:
    """The refused-collection case. Acquisition could not read the pages, so the detector opened
    none of them — and a row fed only by that detector has not been established. It is left at the
    instrument's default with the reason on it, rather than reported as satisfied."""
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Images are described.",
                "tier": "A",
                "bindings": ["alt_text"],
            }
        ]
    )
    document = course([], examinations=[refused("pages")])
    report = run_detectors(document, only=[PROBES["P04"]])
    projection = project(pack, report, tags=probe_index("P04"), effort=PROBE_COSTS)
    entry = row(projection, "S 1")

    assert entry.coverage is Coverage.DETERMINISTIC
    assert entry.rating is Rating.NO_RESPONSE
    assert entry.clean is False
    assert any("examined nothing" in note for note in entry.notes)
    assert any("rather than reported as satisfied" in note for note in entry.notes)


def test_a_course_that_genuinely_has_no_pages_still_never_reads_clean() -> None:
    """Different fact, same rule. Acquisition says the course has no pages, which is honest and
    not a fault — but nothing examined the criterion, so nothing may say it is satisfied."""
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Images are described.",
                "tier": "A",
                "bindings": ["alt_text"],
            }
        ]
    )
    document = course([], examinations=[examined("pages", 0)])
    report = run_detectors(document, only=[PROBES["P04"]])
    entry = row(project(pack, report, tags=probe_index("P04")), "S 1")
    assert entry.rating is Rating.NO_RESPONSE
    assert entry.clean is False


def test_a_detector_that_could_not_read_what_it_opened_never_reads_clean() -> None:
    """An absence of observations over something unreadable is not evidence of absence in the
    course, and saying so is the whole of :class:`~sightline.detect.base.Undetermined`."""
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Attached documents carry structure.",
                "tier": "A",
                "bindings": ["alt_text"],
            }
        ]
    )
    projection = project(pack, probe_report("P05"), tags=probe_index("P05"), effort=PROBE_COSTS)
    entry = row(projection, "S 1")
    assert entry.rating is Rating.NO_RESPONSE
    assert entry.clean is False
    assert any("could not be read" in note for note in entry.notes)


def test_a_bound_tag_whose_detector_did_not_run_in_this_review_never_reads_clean() -> None:
    """A hole, not a pass. The detector exists in this build and this report does not carry it —
    which is what a partial run looks like — so nothing here establishes the row."""
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Images are described.",
                "tier": "A",
                "bindings": ["alt_text"],
            }
        ]
    )
    # The index knows P01 implements alt_text; the report is of a different detector's run.
    projection = project(
        pack, probe_report("P02"), tags=probe_index("P01", "P02"), effort=PROBE_COSTS
    )
    entry = row(projection, "S 1")
    assert entry.coverage is Coverage.NO_DETERMINISTIC_COVERAGE
    assert entry.rating is Rating.NO_RESPONSE
    assert entry.clean is False
    assert any("did not run in this review" in note for note in entry.notes)


# ================================================================================================
# Many-to-many, in both directions
# ================================================================================================


def test_one_detector_fans_out_to_several_rows() -> None:
    """The syllabus check reaches four getting-started rows of the reference instrument at once.

    Here, one probe under one capability feeds three rows, and every one of them carries the same
    findings and the same rolled-up effort. A projection that consumed a detector's findings on
    the first row that claimed them would pass a naive single-row test and fail this.
    """
    pack = pack_from(
        [
            {
                "code": f"S {number}",
                "section": "S",
                "text": f"A row about images, number {number}.",
                "tier": "A",
                "bindings": ["alt_text"],
            }
            for number in (1, 2, 3)
        ]
    )
    projection = project(
        pack, probe_report("P01", pages=2), tags=probe_index("P01"), effort=PROBE_COSTS
    )

    assert [entry.code for entry in projection.rows] == ["S 1", "S 2", "S 3"]
    for entry in projection.rows:
        assert entry.coverage is Coverage.DETERMINISTIC, entry.code
        assert [contribution.detector for contribution in entry.contributions] == ["P01"]
        # Two pages, two findings each.
        assert len(entry.findings) == 4, entry.code
        assert entry.hours == pytest.approx(1.0), entry.code
        assert entry.rating is Rating.MODERATE, entry.code
    # The findings are the same findings, not copies with different locations.
    assert row(projection, "S 1").findings == row(projection, "S 3").findings


def test_several_detectors_fan_in_to_one_row_and_their_effort_sums_before_banding() -> None:
    """The decisive test for the one global roll-up rule: **sum, then band. Never worst-of.**

    Two probes feed one row, each reporting two barriers over one page — 0.5 hours apiece. Summed
    that is 1.0 hours, which is the middle band. Worst-of would be 0.5 hours, which is the lowest
    band, and the two arithmetics are therefore distinguishable here by the *rating* rather than
    only by a number. The single-detector rows in the same projection are the control.
    """
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Fed by one capability.",
                "tier": "A",
                "bindings": ["heading_structure"],
            },
            {
                "code": "S 2",
                "section": "S",
                "text": "Fed by two capabilities.",
                "tier": "A",
                "bindings": ["heading_structure", "link_text"],
            },
            {
                "code": "S 3",
                "section": "S",
                "text": "Fed by the other one capability.",
                "tier": "A",
                "bindings": ["link_text"],
            },
        ]
    )
    projection = project(
        pack, probe_report("P02", "P03"), tags=probe_index("P02", "P03"), effort=PROBE_COSTS
    )

    one = row(projection, "S 1")
    both = row(projection, "S 2")
    other = row(projection, "S 3")

    assert one.hours == pytest.approx(0.5)
    assert other.hours == pytest.approx(0.5)
    assert one.rating is Rating.MINOR
    assert other.rating is Rating.MINOR

    # Summed, not worst-of: 0.5 + 0.5 = 1.0, and 1.0 is a band up from 0.5.
    assert both.hours == pytest.approx(1.0)
    assert both.rating is Rating.MODERATE
    assert both.hours == pytest.approx(one.hours + other.hours)
    assert [contribution.detector for contribution in both.contributions] == ["P02", "P03"]
    assert len(both.findings) == 4


def test_two_detectors_sharing_one_capability_both_reach_the_row() -> None:
    """Several detectors may declare the same tag, and a row bound to it receives from all of
    them. The table-semantics case in the real inventory is exactly this."""
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Images are described.",
                "tier": "A",
                "bindings": ["alt_text"],
            }
        ]
    )
    index = probe_index("P01", "P04")
    assert index.detectors_by_tag["alt_text"] == ("P01", "P04")
    projection = project(pack, probe_report("P01", "P04"), tags=index, effort=PROBE_COSTS)
    entry = row(projection, "S 1")
    assert [contribution.detector for contribution in entry.contributions] == ["P01", "P04"]
    # One of them found nothing; the row is not clean, because the other one found two things.
    assert entry.rating is Rating.MINOR
    assert entry.clean is False


def test_the_projection_is_many_to_many_in_one_pass() -> None:
    """Fan-out and fan-in at once, which is the shape the reference instrument actually has:
    one detector reaching several rows while one of those rows also receives from another."""
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Headings only.",
                "tier": "A",
                "bindings": ["heading_structure"],
            },
            {
                "code": "S 2",
                "section": "S",
                "text": "Headings and links.",
                "tier": "A",
                "bindings": ["heading_structure", "link_text"],
            },
        ]
    )
    projection = project(
        pack, probe_report("P02", "P03"), tags=probe_index("P02", "P03"), effort=PROBE_COSTS
    )
    fed_by = {
        entry.code: [contribution.detector for contribution in entry.contributions]
        for entry in projection.rows
    }
    assert fed_by == {"S 1": ["P02"], "S 2": ["P02", "P03"]}
    # P02 reached both rows; S 2 received from two detectors. Both directions, one pass.
    assert sum("P02" in detectors for detectors in fed_by.values()) == 2


# ================================================================================================
# The tiers override the arithmetic
# ================================================================================================


def test_a_tier_c_row_stays_at_no_response_even_when_its_bindings_produced_findings() -> None:
    """A pedagogical judgement is never pre-rated, whatever the arithmetic says (SPEC §02/§07).

    The findings are still carried on the row — they are the context for the question a human
    answers — and they still do not produce a rating. The assertion that matters is the pair: the
    findings are present *and* the rating is the instrument's default.
    """
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "The reading load suits the level of the course.",
                "tier": "C",
                "bindings": ["alt_text"],
            }
        ]
    )
    projection = project(
        pack, probe_report("P01", pages=4), tags=probe_index("P01"), effort=PROBE_COSTS
    )
    entry = row(projection, "S 1")

    assert entry.coverage is Coverage.DETERMINISTIC
    assert len(entry.findings) == 8
    assert entry.rating is Rating.NO_RESPONSE
    assert entry.rating.is_band is False
    assert entry.hours is None
    assert entry.clean is False
    assert any("no rating is proposed" in note for note in entry.notes)
    assert any("context for the question, not an answer" in note for note in entry.notes)
    # And it still goes out for located evidence, because context is what it needs.
    assert entry.needs_extraction is True


def test_a_tier_c_row_with_enough_findings_to_reach_the_top_band_still_stays_at_no_response() -> (
    None
):
    """Stated separately because the tempting bug is an early return that pre-rates the loud
    cases. Forty observations here would band as a major revision if the tier were ignored."""
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "A judgement call.",
                "tier": "C",
                "bindings": ["alt_text"],
            }
        ]
    )
    projection = project(
        pack, probe_report("P01", pages=20), tags=probe_index("P01"), effort=PROBE_COSTS
    )
    entry = row(projection, "S 1")
    assert len(entry.findings) == 40
    assert PROBE_COSTS.total(entry.findings) == pytest.approx(10.0)
    assert entry.rating is Rating.NO_RESPONSE


def test_a_tier_d_row_defaults_to_not_applicable_with_a_note() -> None:
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "The course was tested on several physical devices.",
                "tier": "D",
                "note": "No course artifact records what anybody tested it on.",
            }
        ]
    )
    projection = project(pack, probe_report("P01"), tags=probe_index("P01"), effort=PROBE_COSTS)
    entry = row(projection, "S 1")
    assert entry.rating is Rating.NOT_APPLICABLE
    assert entry.label == "Not Applicable"
    assert entry.hours is None
    assert any("Not determinable from course artifacts" in note for note in entry.notes)
    assert "No course artifact records" in " ".join(entry.notes)
    # And it is NOT routed for evidence: asking a model about something the artifacts cannot
    # answer is how a fabrication gets in.
    assert entry.needs_extraction is False


def test_a_tier_d_row_bound_to_a_working_detector_is_still_not_applicable() -> None:
    """The tier is checked before the arithmetic, not after it."""
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Out of scope by the instrument's own reading.",
                "tier": "D",
                "bindings": ["alt_text"],
            }
        ]
    )
    entry = row(
        project(pack, probe_report("P01"), tags=probe_index("P01"), effort=PROBE_COSTS), "S 1"
    )
    assert entry.rating is Rating.NOT_APPLICABLE


# ================================================================================================
# An unknown tag warns; it does not raise
# ================================================================================================


def test_a_pack_binding_an_unknown_tag_warns_and_degrades_that_row() -> None:
    """The pack side of the asymmetry, which is the half that keeps third-party rubrics possible.

    A *detector* declaring an unknown tag fails the build — see ``tests/test_detect_base.py``. A
    *pack* doing it is reported, and that row falls back to gathering evidence. Anything stricter
    would mean a rubric somebody uploaded could take Sightline down, and that is not a bargain a
    tool gets to offer its users.
    """
    projection = project(tiny(), probe_report("P01"), tags=probe_index("P01"), effort=PROBE_COSTS)
    entry = row(projection, "REA 5")

    assert entry.unknown_tags == ("format_alternatives_nobody_declared",)
    assert entry.coverage is Coverage.NO_DETERMINISTIC_COVERAGE
    assert entry.rating is Rating.NO_RESPONSE
    assert entry.clean is False
    assert entry.needs_extraction is True

    assert len(projection.warnings) == 1
    warning = projection.warnings[0]
    assert warning.criterion == "REA 5"
    assert warning.tag == "format_alternatives_nobody_declared"
    assert "not in this build's capability vocabulary" in warning.reason
    assert "1 binding warning(s)" in projection.statement()


def test_an_unknown_tag_does_not_take_a_usable_binding_on_the_same_row_with_it() -> None:
    """A row bound to one good tag and one bad one keeps the good one. Otherwise a typo in a
    forty-row pack would silently strip coverage from the rows it appeared on."""
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Bound twice, one of them unrecognised.",
                "tier": "A",
                "bindings": ["alt_text", "no_such_capability"],
            }
        ]
    )
    projection = project(pack, probe_report("P01"), tags=probe_index("P01"), effort=PROBE_COSTS)
    entry = row(projection, "S 1")
    assert entry.coverage is Coverage.DETERMINISTIC
    assert entry.resolved_tags == ("alt_text",)
    assert entry.unknown_tags == ("no_such_capability",)
    assert len(projection.warnings) == 1


# ================================================================================================
# Omitted sections, ordering, and what the projection reports about itself
# ================================================================================================


def test_a_switched_off_section_is_omitted_from_the_projection_and_reported() -> None:
    projection = project(
        tiny().with_section_enabled("SML", False),
        probe_report("P01"),
        tags=probe_index("P01"),
        effort=PROBE_COSTS,
    )
    assert [entry.code for entry in projection.rows] == [
        "DES 1",
        "DES 2",
        "DES 3",
        "REA 1",
        "REA 2",
        "REA 3",
        "REA 4",
        "REA 5",
    ]
    assert projection.omitted == ("SML 1", "SML 2")
    assert "2 omitted with a switched-off section" in projection.statement()


def test_action_plan_ordering_tolerates_a_null_priority() -> None:
    """Two axes, priority crossed with band (SPEC §07), and a null priority sorts last rather
    than being defaulted to a label nobody wrote."""
    projection = project(
        tiny(), probe_report("P01", pages=10), tags=probe_index("P01"), effort=PROBE_COSTS
    )
    ordered = projection.action_plan_order()
    assert len(ordered) == len(projection.rows)
    priorities = [entry.priority for entry in ordered]
    # Every labelled priority comes before every null one, and Critical before Worth doing.
    assert priorities.index("Critical") < priorities.index("Worth doing")
    assert all(value is not None for value in priorities[: priorities.index(None)])
    assert all(value is None for value in priorities[priorities.index(None) :])
    # Within one priority, the heaviest band sorts first.
    critical = [entry for entry in ordered if entry.priority == "Critical"]
    assert critical[0].code == "DES 1"


def test_the_projection_reports_counts_rather_than_a_verdict() -> None:
    projection = project(tiny(), probe_report("P01"), tags=probe_index("P01"), effort=PROBE_COSTS)
    statement = projection.statement()
    assert "10 row(s) projected" in statement
    for word in ("score", "grade", "fail", "degraded", "unsupported"):
        assert word not in statement.lower()


def test_an_uncalibrated_band_says_so_on_the_row_that_carries_it() -> None:
    """The costs are a labelled placeholder until roadmap item 12b fits them, so a band is
    offered as an estimate rather than stated as a measurement."""
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Images are described.",
                "tier": "A",
                "bindings": ["alt_text"],
            }
        ]
    )
    projection = project(pack, probe_report("P01"), tags=probe_index("P01"), effort=PROBE_COSTS)
    entry = row(projection, "S 1")
    assert entry.rating.is_band
    assert any("uncalibrated cost table" in note for note in entry.notes)
    assert projection.to_dict()["effort_calibrated"] is False


def test_the_shipped_effort_model_is_labelled_uncalibrated() -> None:
    model = placeholder_effort_model()
    assert model.calibrated is False
    assert "uncalibrated" in model.note
    assert "12b" in model.note


def test_findings_that_price_at_zero_still_do_not_read_as_clean() -> None:
    """Findings exist, so "no qualifying defects found" is false whatever the cost table says.
    A cost of zero is a fact about the table, not about the course."""
    pack = pack_from(
        [
            {
                "code": "S 1",
                "section": "S",
                "text": "Images are described.",
                "tier": "A",
                "bindings": ["alt_text"],
            }
        ]
    )
    free = EffortModel(costs={}, calibrated=False)
    entry = row(project(pack, probe_report("P01"), tags=probe_index("P01"), effort=free), "S 1")
    assert entry.findings
    assert entry.hours == 0.0
    assert entry.rating is Rating.MINOR
    assert entry.clean is False
    assert any("prices them at zero" in note for note in entry.notes)


def test_the_row_dictionary_carries_the_coverage_state_and_never_only_a_rating() -> None:
    """Every rendering surface is a fresh chance to collapse the three states at the last moment
    before a human reads the result, so the state travels with the row into the dictionary."""
    projection = project(tiny(), probe_report("P01"), tags=probe_index("P01"), effort=PROBE_COSTS)
    payload = projection.to_dict()
    for entry in payload["rows"]:
        assert "coverage" in entry
        assert "rating" in entry
        assert "needs_extraction" in entry
        assert entry["clean"] is (entry["rating"] == "sufficiently_present")


# ================================================================================================
# The tag index and the catalogue
# ================================================================================================


def test_the_tag_index_knows_which_capabilities_this_build_implements() -> None:
    index = probe_index("P01", "P02")
    assert index.implements("alt_text")
    assert index.implements("heading_structure")
    assert index.implements("due_date_coverage") is False
    assert index.recognises("due_date_coverage") is True
    assert index.recognises("no_such_capability") is False
    assert "2 detector(s) implement 2 of" in index.statement()


def test_the_detector_catalogue_names_no_criterion() -> None:
    """What a person authoring a pack reads, and what roadmap item 22 will show a model. It is
    assembled from the detectors' own descriptions and there is no criterion in it."""
    catalogue = tag_descriptions()
    assert catalogue["alt_text"]
    for descriptions in catalogue.values():
        for text in descriptions:
            assert text.strip()
            assert "ACD" not in text
            assert "GSOV" not in text
