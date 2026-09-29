"""The detector interface, and the invariant it carries.

SPEC §11 names the failure this file exists for: the dangerous bug is not a red test, it is a
detector that quietly finds nothing because it broke, handed to a designer as a course with nothing
wrong. So the assertions below are mostly about *refusals* — the paths on which the framework
declines to produce a result rather than producing a reassuring one.

``tests/test_detector_integrity.py`` is the guard that fires this deliberately across every
registered detector and proves the guard itself can fail. What is here is the interface's own
behaviour.
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterable
from pathlib import Path

import pytest

from detector_fixtures import BASE_URL, course, course_file, examined, page, refused
from sightline.capabilities import UnknownCapabilityTag
from sightline.capabilities import known as known_capability
from sightline.detect import (
    DetectorResult,
    NothingExamined,
    ResultState,
    detectors,
    registry,
    run_detectors,
)
from sightline.detect.base import (
    Detector,
    Finding,
    Location,
    Severity,
    Survey,
    Tier,
    registry_snapshot,
)
from sightline.detect.sources import ExportFileSource, MappingFileSource, NoFileSource
from sightline.logging import configure_logging
from sightline.model import ArtifactKind, CourseDocument, CourseInfo
from sightline.paths import DataPaths

# Defining a detector registers it. These two exist only for this module, so they are defined
# inside a snapshot that puts the registry back on the way out — the classes stay usable, the
# registry never hears about them, and the coverage guard is never asked to account for a
# detector nobody ships.
with registry_snapshot():

    class Counting(Detector):
        """A detector that opens every body and reports one finding per page."""

        id = "T01"
        title = "A test detector that opens every body"
        collections = ("pages",)
        capability_tag = "page_titles"
        capability_description = "A test detector; any tag in the vocabulary serves here."

        def examine(self, survey: Survey) -> Iterable[Finding]:
            for body in survey.bodies():
                survey.counted()
                yield survey.finding(
                    code="seen",
                    severity=Severity.ADVISORY,
                    location=survey.location(body.artifact),
                    observed=body.artifact.title,
                )

    class Silent(Detector):
        """A detector that opens nothing at all. The shape of a broken one."""

        id = "T02"
        title = "A test detector that opens nothing"
        collections = ("pages",)
        capability_tag = "page_titles"
        capability_description = "A test detector; any tag in the vocabulary serves here."

        def examine(self, survey: Survey) -> Iterable[Finding]:
            return ()

    class Crashing(Detector):
        """A detector that opens a page and raises on it — the parser handed markup nobody
        anticipated. Its message quotes the course and a hostname, as a real one would."""

        id = "T05"
        title = "A test detector that raises"
        collections = ("pages",)
        capability_tag = "page_titles"
        capability_description = "A test detector; any tag in the vocabulary serves here."

        def examine(self, survey: Survey) -> Iterable[Finding]:
            for body in survey.bodies():
                raise ValueError(
                    f"unexpected markup in {body.artifact.title!r} near "
                    f"{BASE_URL}/courses/4410/pages/start"
                )
            return ()

    class CrashingInALibrary(Detector):
        """A detector whose crash happens inside code it called, not in its own lines."""

        id = "T06"
        title = "A test detector that a library raises under"
        collections = ("pages",)
        capability_tag = "page_titles"
        capability_description = "A test detector; any tag in the vocabulary serves here."

        def examine(self, survey: Survey) -> Iterable[Finding]:
            for body in survey.bodies():
                json.loads(body.html)
            return ()


@pytest.fixture(autouse=True)
def _keep_the_registry_clean() -> Iterable[None]:
    """The tests below define detectors of their own. Nothing here may leak into
    :func:`detectors`, which every other suite and the coverage guard read."""
    with registry_snapshot():
        yield


# -- the invariant -------------------------------------------------------------------------------


def test_a_detector_handed_an_empty_document_set_raises() -> None:
    empty = CourseDocument(base_url=BASE_URL, course=CourseInfo(id="4410", name="Empty"))
    with pytest.raises(NothingExamined) as caught:
        Counting().run(empty)
    assert caught.value.detector == "T01"
    assert "examined 0 artifacts" in str(caught.value)


def test_every_shipped_detector_raises_on_an_empty_document_set() -> None:
    empty = CourseDocument(base_url=BASE_URL, course=CourseInfo(id="4410", name="Empty"))
    for detector in detectors():
        with pytest.raises(NothingExamined):
            detector.run(empty)


def test_a_detector_that_opens_nothing_over_a_course_that_has_pages_raises() -> None:
    """The case that matters most. Acquisition read four pages; the detector opened none of them.
    Nothing explains that but a broken detector, and a pass here is a course reported clean on a
    criterion nobody checked."""
    document = course([page("one", "<p>One</p>"), page("two", "<p>Two</p>")])
    with pytest.raises(NothingExamined):
        Silent().run(document)


def test_a_course_that_genuinely_has_none_is_reported_rather_than_raised() -> None:
    document = course([], examinations=[examined("pages", 0)])
    result = Silent().run(document)
    assert result.state is ResultState.NOT_EXAMINED
    assert result.examined == 0
    assert result.clean is False
    assert result.not_examined_because is not None
    assert "holds nothing" in result.not_examined_because


def test_a_refused_collection_is_reported_as_refused_and_never_as_clean() -> None:
    document = course([], examinations=[refused("pages")])
    result = Silent().run(document)
    assert result.state is ResultState.NOT_EXAMINED
    assert result.clean is False
    assert result.not_examined_because is not None
    assert "not readable (refused)" in result.not_examined_because
    assert result.unreadable and result.unreadable[0].remedy


def test_the_runner_reports_a_detector_that_could_not_run_rather_than_dropping_it() -> None:
    empty = CourseDocument(base_url=BASE_URL, course=CourseInfo(id="4410", name="Empty"))
    report = run_detectors(empty, only=[Counting(), Silent()])
    assert [result.detector for result in report.results] == ["T01", "T02"]
    assert len(report.not_examined) == 2
    assert all(result.clean is False for result in report.results)
    assert "could not run (T01, T02)" in report.statement()


# -- a detector that crashes ---------------------------------------------------------------------


def test_a_detector_run_directly_still_raises_whatever_it_raised() -> None:
    """The runner is the layer that turns a crash into a reported non-result, and only the runner.
    A detector test that got a quiet ``NOT_EXAMINED`` back instead of a traceback would be a test
    that stopped testing."""
    document = course([page("start", "<p>Start</p>")])
    with pytest.raises(ValueError, match="unexpected markup"):
        Crashing().run(document)


def test_a_crashing_detector_is_reported_by_name_and_the_others_still_report() -> None:
    document = course([page("start", "<p>Start</p>"), page("two", "<p>Two</p>")])
    alone = run_detectors(document, only=[Counting()])
    report = run_detectors(document, only=[Counting(), Crashing()])

    assert [result.detector for result in report.results] == ["T01", "T05"]
    # The survivor's result is exactly what it would have been with no crash beside it.
    assert report.by_detector("T01") == alone.by_detector("T01")
    assert report.by_detector("T01").examined == 2  # type: ignore[union-attr]

    crashed = report.by_detector("T05")
    assert crashed is not None
    assert crashed.state is ResultState.NOT_EXAMINED
    assert crashed.clean is False
    assert crashed.examined == 0
    assert crashed.crashed == "ValueError"
    assert crashed in report.not_examined
    assert report.crashed == (crashed,)
    assert crashed.not_examined_because is not None
    assert crashed.not_examined_because.startswith("This detector crashed: it raised ValueError")
    assert "test_detect_base.py:" in crashed.not_examined_because
    assert "(examine)" in crashed.not_examined_because


def test_every_shipped_detector_survives_one_that_crashes_beside_it() -> None:
    """Item 27's measurement, kept. Before the fix, one raising detector took every shipped one
    down with it and there was no report at all."""
    document = course([page("start-here", "<h2>Start</h2><p>Welcome. <img src='a.png'></p>")])
    shipped = list(detectors())
    alone = run_detectors(document, only=shipped)
    report = run_detectors(document, only=[*shipped, Crashing()])

    assert len(report.results) == len(shipped) + 1
    assert [result for result in report.results if result.detector != "T05"] == list(alone.results)
    assert [result.detector for result in report.crashed] == ["T05"]


def test_nothing_a_reviewer_reads_carries_the_exception_message() -> None:
    """The message can quote the course and name the institution's host (SPEC §11). The reason
    goes onto rubric rows and into files on disk, so it carries the type and the code location
    and never the text."""
    document = course([page("notes", "<p>Notes</p>", title="Week 1 Private Notes")])
    report = run_detectors(document, only=[Crashing()])
    reviewer_surfaces = report.statement() + json.dumps(report.to_dict())
    # The probe must have fired, or the absences below prove nothing.
    assert report.crashed and report.crashed[0].detector == "T05"
    assert "unexpected markup" not in reviewer_surfaces
    assert "canvas.example.edu" not in reviewer_surfaces
    assert "Week 1 Private Notes" not in reviewer_surfaces
    # And no host directory layout either: paths are package-relative.
    assert "/home/" not in reviewer_surfaces
    assert "/app/" not in reviewer_surfaces


def test_the_log_carries_the_message_and_traceback_scrubbed() -> None:
    stream = io.StringIO()
    configure_logging(level="DEBUG", fmt="json", stream=stream)
    try:
        run_detectors(course([page("start", "<p>Start</p>")]), only=[Crashing()])
    finally:
        configure_logging(level="INFO", fmt="json")
    events = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    crashes = [event for event in events if event["event"] == "detector_crashed"]
    assert len(crashes) == 1
    event = crashes[0]
    assert event["level"] == "error"
    assert event["detector"] == "T05"
    assert event["error_type"] == "ValueError"
    assert "unexpected markup in 'Start'" in event["error"]
    assert "Traceback" in event["traceback"]
    assert "canvas.example.edu" not in json.dumps(event)


def test_a_crash_inside_a_library_names_the_detector_line_that_called_it() -> None:
    document = course([page("start", "<p>not json</p>")])
    result = run_detectors(document, only=[CrashingInALibrary()]).by_detector("T06")
    assert result is not None and result.not_examined_because is not None
    assert result.crashed == "JSONDecodeError"
    assert re.search(r"json/decoder\.py:\d+ \(\w+\)", result.not_examined_because)
    assert "called from sightline/detect/base.py:" in result.not_examined_because


def test_the_summary_names_a_crash_apart_from_a_detector_with_nothing_to_read() -> None:
    document = course([page("start", "<p>Start</p>")])
    report = run_detectors(document, only=[Counting(), Crashing()])
    starved = run_detectors(
        course([], examinations=[examined("pages", 0)]), only=[Silent()]
    ).results
    mixed = type(report)(results=(*report.results, *starved))
    statement = mixed.statement()
    assert "1 of 3 detector(s) examined the course" in statement
    assert "1 crashed and could not run (T05)" in statement
    assert "1 could not run (T02)" in statement
    # The crash is named before the ordinary gaps, not filed among them.
    assert statement.index("crashed") < statement.index("could not run (T02)")


def test_an_interrupt_is_not_contained() -> None:
    """Only ``Exception`` is reported as a crash. Ctrl-C, or a process being told to exit, still
    ends the run: containing those would make the review impossible to stop."""

    with registry_snapshot():

        class Interrupted(Detector):
            id = "T07"
            title = "A test detector interrupted mid-run"
            collections = ("pages",)
            capability_tag = "page_titles"
            capability_description = "A test detector; any tag in the vocabulary serves here."

            def examine(self, survey: Survey) -> Iterable[Finding]:
                for _body in survey.bodies():
                    raise KeyboardInterrupt
                return ()

    with pytest.raises(KeyboardInterrupt):
        run_detectors(course([page("start", "<p>Start</p>")]), only=[Counting(), Interrupted()])


# -- the interface is final ----------------------------------------------------------------------


def test_a_detector_that_overrides_run_fails_at_class_definition() -> None:
    with pytest.raises(TypeError, match="overrides run"):

        class Cheating(Detector):
            id = "T90"
            title = "Takes its own count"
            collections = ("pages",)
            capability_tag = "page_titles"
            capability_description = "A test detector; any tag in the vocabulary serves here."

            def examine(self, survey: Survey) -> Iterable[Finding]:
                return ()

            def run(  # type: ignore[override]
                self, document: CourseDocument, *, files: object = None
            ) -> DetectorResult:
                raise AssertionError("never reached")


#: A complete declaration, for the tests below that remove one thing from it.
COMPLETE = {
    "id": "T91",
    "title": "Incomplete",
    "collections": ("pages",),
    "capability_tag": "page_titles",
    "capability_description": "A test detector; any tag in the vocabulary serves here.",
}


@pytest.mark.parametrize("missing", sorted(COMPLETE))
def test_a_detector_missing_a_declaration_fails_at_class_definition(missing: str) -> None:
    body = dict(COMPLETE)
    body.pop(missing)
    with pytest.raises(TypeError, match=f"must declare {missing}"):
        type("Incomplete", (Detector,), body)


def test_a_detector_declaring_a_capability_nobody_recognises_fails_hard() -> None:
    """The asymmetry, from the detector side.

    We write the detectors, so a tag outside the vocabulary is our typo and it stops the build at
    class-definition time. A *pack* naming an unknown tag is the opposite — reported, and that row
    degrades — because packs are third-party and an uploaded rubric must never be able to
    hard-fail Sightline. ``tests/test_rubric_project.py`` holds the other half.
    """
    body = dict(COMPLETE) | {"id": "T93", "capability_tag": "alt_txt"}
    with pytest.raises(UnknownCapabilityTag, match="not in the vocabulary") as caught:
        type("Misspelled", (Detector,), body)
    # And the message names a near miss rather than only the mistake, because the vocabulary is
    # fifty entries long and "that is wrong" is not enough to fix it with.
    assert "alt_text" in str(caught.value)
    assert caught.value.suggestions == ("alt_text",)


def test_a_detector_declaring_an_unknown_capability_is_never_registered() -> None:
    """The failure has to keep it out of the registry, not merely announce itself.

    A class that raised in ``__init_subclass__`` and landed in the registry anyway would be worse
    than one that never failed: the coverage guard would report a detector nothing ships, and
    ``detectors()`` would hand a broken instance to the runner.
    """
    body = dict(COMPLETE) | {"id": "T94", "capability_tag": "no_such_capability"}
    with pytest.raises(UnknownCapabilityTag):
        type("Unknown", (Detector,), body)
    assert "T94" not in registry()


def test_every_shipped_detector_declares_a_capability_and_never_a_criterion() -> None:
    """Each detector says which course-quality concept it produces facts about.

    The tag is the *only* thing joining a detector to a rubric, and it joins in one direction:
    packs bind their own rows to tags. So a detector naming a criterion — ``ACD 8``, ``GSOV 5`` —
    is the bug, and this asserts none of them does, in the title and the description a person
    actually reads as well as in the tag itself.
    """
    pattern = re.compile(r"\b(COAL|GSOV|OREP|ACD|ENIN|ASFE|MOB)\s*-?\d", re.I)
    for detector in detectors():
        assert known_capability(detector.capability_tag), detector.id
        assert detector.capability_description.strip(), detector.id
        for text in (detector.title, detector.capability_description, detector.capability_tag):
            assert not pattern.search(text), f"{detector.id} names a rubric criterion: {text!r}"


def test_an_abstract_base_is_not_registered() -> None:
    class Shared(Detector, abstract=True):
        pass

    assert "" not in registry()
    assert Shared not in registry().values()


def test_two_detectors_cannot_claim_the_same_id() -> None:
    body = dict(COMPLETE) | {"id": "T92", "title": "First"}
    type("First", (Detector,), dict(body))
    with pytest.raises(TypeError, match="claim the id T92"):
        type("Second", (Detector,), dict(body))


# -- what a result carries -----------------------------------------------------------------------


def test_every_finding_is_stamped_with_what_the_detector_examined() -> None:
    document = course([page("one", "<p>One</p>"), page("two", "<p>Two</p>")])
    result = Counting().run(document)
    assert result.examined == 2
    assert result.subjects == 2
    assert [finding.examined for finding in result.findings] == [2, 2]


def test_examined_counts_distinct_artifacts_not_repeated_passes() -> None:
    class TwicePast(Detector):
        id = "T03"
        title = "Walks the bodies twice"
        collections = ("pages",)
        capability_tag = "page_titles"
        capability_description = "A test detector; any tag in the vocabulary serves here."

        def examine(self, survey: Survey) -> Iterable[Finding]:
            list(survey.bodies())
            list(survey.bodies())
            return ()

    document = course([page("one", "<p>One</p>")])
    assert TwicePast().run(document).examined == 1


def test_clean_means_it_ran_and_found_nothing_it_could_not_account_for() -> None:
    class Undeterminable(Detector):
        id = "T04"
        title = "Opens a file it cannot read"
        collections = ("files",)
        capability_tag = "page_titles"
        capability_description = "A test detector; any tag in the vocabulary serves here."

        def examine(self, survey: Survey) -> Iterable[Finding]:
            for item in survey.files():
                survey.undetermined(survey.location(item), reason="no bytes were acquired")
            return ()

    quiet = course([page("one", "<p>One</p>")])
    assert Counting().run(quiet).clean is False  # it found something

    document = course([], files=[course_file("5001", "syllabus.pdf")])
    result = Undeterminable().run(document)
    assert result.state is ResultState.EXAMINED
    assert result.findings == ()
    assert result.undetermined and result.clean is False


def test_a_location_keeps_the_path_and_joins_the_host_only_on_the_way_out() -> None:
    document = course([page("start-here", "<p>One</p>")])
    finding = Counting().run(document).findings[0]
    assert finding.location.canvas_path == "/courses/4410/pages/start-here"
    assert BASE_URL not in finding.to_dict()["location"]["canvas_path"]
    assert finding.location.deep_link(BASE_URL) == f"{BASE_URL}/courses/4410/pages/start-here"


def test_a_result_serializes_to_counts_a_person_can_check() -> None:
    document = course([page("one", "<p>One</p>")])
    payload = Counting().run(document).to_dict()
    assert payload["examined"] == 1
    assert payload["state"] == "examined"
    assert payload["clean"] is False
    assert payload["findings"][0]["examined"] == 1
    assert payload["findings"][0]["location"]["artifact_kind"] == ArtifactKind.PAGE.value


def test_a_result_states_its_counts_in_the_declarative_register() -> None:
    document = course([page("one", "<p>One</p>")])
    assert Counting().run(document).statement() == (
        "T01: examined 1 artifact(s), holding 1 item(s) of interest, found 1 observation(s)."
    )


def test_a_location_only_reports_a_search_link_as_a_search_link() -> None:
    location = Location(
        artifact_id="file:1",
        artifact_kind=ArtifactKind.FILE,
        title="notes.docx",
        canvas_path="/courses/4410/files?search_term=notes.docx",
        link_exact=False,
    )
    assert location.to_dict()["link_exact"] is False


# -- file sources --------------------------------------------------------------------------------


def test_no_file_source_returns_nothing_rather_than_raising() -> None:
    assert NoFileSource().read(course_file("5001", "syllabus.pdf")) is None


def test_a_mapping_source_answers_by_id_filename_or_export_path() -> None:
    item = course_file("5001", "syllabus.pdf", local_path="web_resources/syllabus.pdf")
    assert MappingFileSource({item.id: b"by id"}).read(item) == b"by id"
    assert MappingFileSource({"syllabus.pdf": b"by name"}).read(item) == b"by name"
    assert MappingFileSource({item.local_path or "": b"by path"}).read(item) == b"by path"
    assert MappingFileSource({}).read(item) is None


def test_an_export_source_reads_under_its_root_and_refuses_a_path_that_climbs_out(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export" / "files"
    (root / "web_resources").mkdir(parents=True)
    (root / "web_resources" / "syllabus.pdf").write_bytes(b"%PDF-1.7\n")
    (tmp_path / "secret.txt").write_bytes(b"not yours")

    source = ExportFileSource(DataPaths.from_setting(tmp_path), "export/files")
    inside = course_file("5001", "syllabus.pdf", local_path="web_resources/syllabus.pdf")
    assert source.read(inside) == b"%PDF-1.7\n"

    escaping = course_file("5002", "secret.txt", local_path="../../secret.txt")
    assert source.read(escaping) is None

    absent = course_file("5003", "gone.pdf", local_path="web_resources/gone.pdf")
    assert source.read(absent) is None
    assert source.read(course_file("5004", "no-path.pdf")) is None

    absolute = course_file("5006", "passwd", local_path="/etc/passwd")
    assert source.read(absolute) is None


def test_an_export_source_refuses_a_symlink_out_of_the_data_root(tmp_path: Path) -> None:
    """The archive cannot plant one, but a directory named by hand can hold one — and the gate
    that refuses it is :meth:`DataPaths.resolve`, not a check written here."""
    root = tmp_path / "data" / "files"
    (root / "web_resources").mkdir(parents=True)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.7 not yours\n")
    (root / "web_resources" / "linked.pdf").symlink_to(outside)
    (root / "web_resources" / "real.pdf").write_bytes(b"%PDF-1.7 yours\n")

    source = ExportFileSource(DataPaths.from_setting(root))
    linked = course_file("5007", "linked.pdf", local_path="web_resources/linked.pdf")
    real = course_file("5008", "real.pdf", local_path="web_resources/real.pdf")
    assert source.read(linked) is None
    assert source.read(real) == b"%PDF-1.7 yours\n"


def test_an_export_source_stops_at_its_read_cap(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    (root / "big.pdf").write_bytes(b"x" * 500)
    source = ExportFileSource(DataPaths.from_setting(root), max_bytes=100)
    item = course_file("5005", "big.pdf", local_path="big.pdf")
    assert source.read(item) == b"x" * 100


# -- the shipped set -----------------------------------------------------------------------------


def test_the_shipped_clusters_are_registered_and_declare_what_they_read() -> None:
    """The accessibility cluster (item 06), the Canvas structure cluster (item 07) and the mobile
    cluster (item 08).

    Written as the explicit list rather than as a count, because the gap is the point: SPEC §05
    assigns D31-D38 and D46-D47 to the content cluster, which is not built, and a range check would
    read a missing detector as an off-by-one somebody adjusts."""
    shipped = {detector.id: detector for detector in detectors() if detector.id.startswith("D")}
    assert sorted(shipped) == [f"D{number:02d}" for number in (*range(1, 31), *range(39, 46))]
    for detector in shipped.values():
        assert detector.title and not detector.title.endswith(".")
        assert detector.collections
        # Tier C arrives with the mobile cluster: one detector gathers context for a judgement a
        # human makes and proposes no rating. Tier D never has a detector at all, by definition.
        assert detector.tier in {Tier.A, Tier.B, Tier.C}


def test_no_detector_names_a_rubric_criterion() -> None:
    """Detectors emit facts about artifacts; a rubric pack projects them onto its own rows
    (SPEC §04). A detector that named a criterion would bake one instrument into the code and make
    every third-party rubric a rewrite."""
    import re

    pattern = re.compile(r"\b(?:COAL|GSOV|OREP|ACD|ENIN|ASFE|MOB)\s*\d+\b")
    for detector in detectors():
        assert not pattern.search(detector.title), detector.id
