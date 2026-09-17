"""``examples/sample-course/defects.json`` as a live contract on the detectors that ship.

The sample course already declares, per detector, what is planted where and whether the detector
should find it. ``tests/test_sample_course.py`` proves those markers are still in the bodies they
claim to be in. This file proves the other half, which is the half that matters once detectors
exist: that the detector named actually *fires* on a planted defect and actually *stays quiet* on
the case planted to be clean.

Both directions are load-bearing. A detector that fires on everything satisfies every ``defect``
row and is the one a faculty member dismisses the tool over; a detector that fires on nothing
satisfies every ``clean`` row and is the silent under-detection SPEC §11 is about.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sightline.acquire import load_bundle
from sightline.detect import DetectionReport, ResultState, registry, run_detectors
from sightline.model import CourseDocument

#: The detectors shipped so far — the accessibility cluster of item 06 and the Canvas structure
#: cluster of item 07. Rows naming anything else are a later item's contract.
SHIPPED = tuple(f"D{number:02d}" for number in (*range(1, 21), *range(21, 31)))


@pytest.fixture
def document(repo_root: Path) -> CourseDocument:
    return load_bundle(repo_root / "examples" / "sample-course")


@pytest.fixture
def report(document: CourseDocument) -> DetectionReport:
    """The sample course carries no document bytes, so no file source is supplied — which is
    itself the case D30's ``not_covered`` row describes."""
    return run_detectors(document)


@pytest.fixture
def planted(repo_root: Path) -> list[dict[str, str]]:
    payload = json.loads(
        (repo_root / "examples" / "sample-course" / "defects.json").read_text(encoding="utf-8")
    )
    return [row for row in payload["planted"] if row["detector"] in SHIPPED]


def test_the_sample_exercises_every_detector_this_item_ships(planted: list[dict[str, str]]) -> None:
    named = {row["detector"] for row in planted}
    assert named == set(SHIPPED)


def test_a_course_level_finding_is_located_on_the_course_and_links_into_it(
    report: DetectionReport, document: CourseDocument
) -> None:
    """The structure cluster's answers are often absences, and an absence still has to open
    somewhere. Every finding on the course artifact links inside the course, never above it."""
    course_id = document.course_artifact.id
    on_course = [
        finding for finding in report.findings if finding.location.artifact_id == course_id
    ]
    assert on_course, "no detector reported anything about the course as a whole"
    for finding in on_course:
        assert finding.location.canvas_path.startswith(f"/courses/{document.course.id}")


def test_no_finding_states_a_conclusion_where_a_count_belongs(report: DetectionReport) -> None:
    """The register check, applied to every observation rather than to the summary alone. These
    words are how a finding turns into a verdict about a course or a person (SPEC §11)."""
    forbidden = ("should", "must", "poor", "inadequate", "fails to", "failure to", "unacceptable")
    for finding in report.findings:
        lowered = finding.observed.lower()
        for word in forbidden:
            assert word not in lowered, f"{finding.detector}/{finding.code}: {finding.observed}"


def test_every_detector_examined_the_sample_course(report: DetectionReport) -> None:
    """None of them may come back in the ``NOT_EXAMINED`` state over a course with fifteen bodies,
    six files, four modules and a full navigation menu in it. One that did would have found
    nothing for a reason nobody saw."""
    assert report.not_examined == ()
    for detector_id in SHIPPED:
        result = report.by_detector(detector_id)
        assert result is not None
        assert result.state is ResultState.EXAMINED
        assert result.examined > 0


def test_every_finding_carries_a_location_a_reviewer_can_open(
    report: DetectionReport, document: CourseDocument
) -> None:
    """Including the course-level ones. A finding about the course as a whole is located on the
    course artifact, which ``by_id`` resolves — so "no syllabus" opens somewhere."""
    for finding in report.findings:
        assert document.by_id(finding.location.artifact_id) is not None, finding.code
        assert finding.location.canvas_path.startswith("/courses/")
        assert finding.examined > 0
        assert finding.observed


def test_no_finding_carries_the_canvas_hostname(report: DetectionReport) -> None:
    """A finding is written to disk and the base URL identifies the institution (SPEC §11). It is
    joined on the way out, so nothing serialized here may already contain it."""
    serialized = json.dumps(report.to_dict())
    assert "canvas.example.edu" not in serialized


def test_each_planted_defect_is_found_where_the_registry_says_it_is(
    report: DetectionReport, planted: list[dict[str, str]]
) -> None:
    for row in planted:
        if row["expectation"] != "defect":
            continue
        result = report.by_detector(row["detector"])
        assert result is not None
        located = [
            finding
            for finding in result.findings
            if finding.location.artifact_id == row.get("artifact")
        ]
        assert located, f"{row['detector']} found nothing on {row.get('artifact')}: {row['note']}"


def test_each_case_planted_to_be_clean_is_left_alone(
    report: DetectionReport, planted: list[dict[str, str]]
) -> None:
    for row in planted:
        if row["expectation"] != "clean":
            continue
        result = report.by_detector(row["detector"])
        assert result is not None
        located = [
            finding
            for finding in result.findings
            if finding.location.artifact_id == row.get("artifact")
        ]
        assert not located, (
            f"{row['detector']} fired on {row.get('artifact')}, which is planted clean: "
            f"{[finding.code for finding in located]}"
        )


def test_a_row_the_sample_cannot_exercise_is_recorded_as_unread_not_as_clean(
    report: DetectionReport, planted: list[dict[str, str]]
) -> None:
    """``not_covered`` is the sample saying it lacks what the detector needs — D02, D30 and D36
    need document bytes and the sample ships metadata only. The detector must say the same thing,
    by recording what it could not read, rather than coming back clean."""
    for row in planted:
        if row["expectation"] != "not_covered":
            continue
        result = report.by_detector(row["detector"])
        assert result is not None
        assert result.clean is False
        assert any(
            entry.location.artifact_id == row.get("artifact") for entry in result.undetermined
        ), f"{row['detector']} said nothing about {row.get('artifact')}"


def test_the_registry_holds_what_these_items_ship_and_nothing_stray() -> None:
    assert sorted(registry()) == sorted(SHIPPED)


def test_the_summary_is_counts_rather_than_a_verdict(report: DetectionReport) -> None:
    statement = report.statement()
    assert statement.startswith(f"{len(SHIPPED)} of {len(SHIPPED)} detector(s) examined the course")
    for word in ("score", "grade", "fail", "poor", "good"):
        assert word not in statement.lower()
