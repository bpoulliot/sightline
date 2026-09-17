"""The coverage report a pack gives about itself, before any course is read.

    "18 of 43 criteria have deterministic coverage, 25 are evidence-only."

A gap in a rubric's bindings is *information*. Without this report the same gap is silent
degradation: a reviewer opens a report, sees rows with no counts on them, and has no way to tell
"nothing was wrong" from "nothing looked". Keeping those apart is the whole of item 04.

**The unbound case is asserted to read as a success line.** A rubric somebody just uploaded reports
zero deterministic and every row evidence-only, and no wording in the report calls that degraded,
incomplete, unsupported or missing — because it is none of those, it is how every third-party
instrument starts, and the review it produces is a real one.
"""

from __future__ import annotations

from pathlib import Path

from sightline.detect import detectors
from sightline.rubric.coverage import pack_coverage
from sightline.rubric.pack import load
from sightline.rubric.project import TagIndex
from sightline.rubric.states import Coverage

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rubric"
TINY = FIXTURES / "tiny-pack.yaml"
UNBOUND = FIXTURES / "unbound-pack.yaml"

#: The shipped registry's view, which is what a person uploading a rubric would actually be told.
SHIPPED = TagIndex.build(detectors())


def test_the_report_is_correct_on_a_partially_bound_pack() -> None:
    """Every bucket asserted by name, not only the headline.

    The ten-row fixture is built to put one row in every state: three rows a parser answers, one
    bound to a capability no detector implements yet, one in an optional section bound to another,
    one with no binding, one with an unrecognised binding, one out of scope, and a judgement call.
    """
    report = pack_coverage(load(TINY), tags=SHIPPED)

    assert dict(report.counts) == {
        "criteria": 10,
        "deterministic": 4,
        "not_yet_implemented": 2,
        "evidence_only": 3,
        "not_determinable": 1,
        "omitted": 0,
    }
    assert {entry.code for entry in report.deterministic} == {
        "DES 1",
        "DES 2",
        "DES 3",
        "REA 2",
    }
    assert {entry.code for entry in report.not_yet_implemented} == {"REA 3", "SML 1"}
    assert {entry.code for entry in report.evidence_only} == {"REA 1", "REA 5", "SML 2"}
    assert {entry.code for entry in report.not_determinable} == {"REA 4"}


def test_the_buckets_account_for_every_row() -> None:
    """The report's own arithmetic self-check. A coverage report whose numbers do not add up is
    worse than none: it reads as authoritative and it is wrong."""
    for path in (TINY, UNBOUND):
        report = pack_coverage(load(path), tags=SHIPPED)
        assert report.ok, report.detail()
        tally = report.counts
        assert (
            tally["deterministic"]
            + tally["not_yet_implemented"]
            + tally["evidence_only"]
            + tally["not_determinable"]
            == tally["criteria"]
        )


def test_the_headline_statement_is_the_sentence_the_item_asked_for() -> None:
    report = pack_coverage(load(TINY), tags=SHIPPED)
    statement = report.statement()
    assert "4 of 10 criteria have deterministic coverage" in statement
    assert "3 are evidence-only" in statement
    assert "2 are bound to a capability no detector implements yet" in statement
    assert "1 are not determinable from course artifacts" in statement


def test_an_unbound_pack_reports_zero_coverage_and_reads_as_a_success_line() -> None:
    """The state every third-party rubric starts in.

    It is reported in counts, and none of the counts is dressed as a fault. A user uploading their
    own instrument is being told what Sightline will compute and what it will quote — not that
    their rubric is unsupported, which it is not.
    """
    report = pack_coverage(load(UNBOUND), tags=SHIPPED)

    assert dict(report.counts) == {
        "criteria": 4,
        "deterministic": 0,
        "not_yet_implemented": 0,
        "evidence_only": 4,
        "not_determinable": 0,
        "omitted": 0,
    }
    assert report.ok
    text = report.detail().lower()
    for word in ("degraded", "incomplete", "unsupported", "missing", "fail", "error", "invalid"):
        assert word not in text, word


def test_the_report_names_which_capabilities_are_awaiting_a_detector() -> None:
    """Named rather than counted, because "2 rows are waiting" is not something anybody can act
    on and "waiting on document_printability" is."""
    detail = pack_coverage(load(TINY), tags=SHIPPED).detail()
    assert "awaiting a detector: content_chunking, document_printability" in detail


def test_the_report_names_a_tag_this_build_does_not_recognise() -> None:
    report = pack_coverage(load(TINY), tags=SHIPPED)
    assert report.unknown_tags == ("format_alternatives_nobody_declared",)
    assert "does not recognise" in report.detail()
    assert "gather evidence instead" in report.detail()


def test_a_row_records_which_detectors_would_feed_it() -> None:
    """So the claim can be checked against the detector list rather than taken on trust."""
    report = pack_coverage(load(TINY), tags=SHIPPED)
    rows = {entry.code: entry for entry in report.rows}
    assert rows["DES 1"].detectors == ("D21",)
    assert rows["DES 2"].detectors == ("D22", "D23")
    assert rows["REA 3"].detectors == ()
    assert rows["REA 3"].pending == ("document_printability",)


def test_a_switched_off_section_is_counted_as_omitted_rather_than_dropped() -> None:
    report = pack_coverage(load(TINY).with_section_enabled("SML", False), tags=SHIPPED)
    assert report.counts["criteria"] == 8
    assert report.omitted == ("SML 1", "SML 2")
    assert report.ok
    assert "2 more are omitted with a switched-off section" in report.statement()


def test_the_report_needs_no_course_to_compute() -> None:
    """It takes a pack and what this build implements, and nothing else — so it can be shown on
    the screen where a rubric is uploaded, which is where "will this work?" gets asked."""
    report = pack_coverage(load(UNBOUND), tags=SHIPPED)
    assert report.pack_id == "unbound-checklist"
    assert report.to_dict()["counts"]["criteria"] == 4


def test_a_per_section_breakdown_is_grouped_in_instrument_order() -> None:
    report = pack_coverage(load(TINY), tags=SHIPPED)
    assert list(report.by_section()) == ["DES", "REA", "SML"]
    assert "DES: 3 of 3 with coverage" in report.detail()
    assert "SML: 0 of 2 with coverage" in report.detail()


def test_coverage_is_computed_against_the_index_it_is_given() -> None:
    """A build with no detectors at all reports every bound row as awaiting one, and reports no
    row as clean. The report is a function of what is implemented, not of what is hoped for."""
    empty = TagIndex.build([])
    report = pack_coverage(load(TINY), tags=empty)
    assert report.counts["deterministic"] == 0
    assert report.counts["not_yet_implemented"] == 6
    assert report.ok
    assert all(entry.coverage is not Coverage.DETERMINISTIC for entry in report.rows)
