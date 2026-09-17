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

from collections.abc import Iterable
from pathlib import Path

import pytest

from detector_fixtures import BASE_URL, course, course_file, examined, page, refused
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
from sightline.model import ArtifactKind, CourseDocument, CourseInfo

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

        def examine(self, survey: Survey) -> Iterable[Finding]:
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


# -- the interface is final ----------------------------------------------------------------------


def test_a_detector_that_overrides_run_fails_at_class_definition() -> None:
    with pytest.raises(TypeError, match="overrides run"):

        class Cheating(Detector):
            id = "T90"
            title = "Takes its own count"
            collections = ("pages",)

            def examine(self, survey: Survey) -> Iterable[Finding]:
                return ()

            def run(  # type: ignore[override]
                self, document: CourseDocument, *, files: object = None
            ) -> DetectorResult:
                raise AssertionError("never reached")


@pytest.mark.parametrize("missing", ["id", "title", "collections"])
def test_a_detector_missing_a_declaration_fails_at_class_definition(missing: str) -> None:
    body = {"id": "T91", "title": "Incomplete", "collections": ("pages",)}
    body.pop(missing)
    with pytest.raises(TypeError, match=f"must declare {missing}"):
        type("Incomplete", (Detector,), body)


def test_an_abstract_base_is_not_registered() -> None:
    class Shared(Detector, abstract=True):
        pass

    assert "" not in registry()
    assert Shared not in registry().values()


def test_two_detectors_cannot_claim_the_same_id() -> None:
    body = {"id": "T92", "title": "First", "collections": ("pages",)}
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

    source = ExportFileSource(root)
    inside = course_file("5001", "syllabus.pdf", local_path="web_resources/syllabus.pdf")
    assert source.read(inside) == b"%PDF-1.7\n"

    escaping = course_file("5002", "secret.txt", local_path="../../secret.txt")
    assert source.read(escaping) is None

    absent = course_file("5003", "gone.pdf", local_path="web_resources/gone.pdf")
    assert source.read(absent) is None
    assert source.read(course_file("5004", "no-path.pdf")) is None


def test_an_export_source_stops_at_its_read_cap(tmp_path: Path) -> None:
    root = tmp_path / "files"
    root.mkdir()
    (root / "big.pdf").write_bytes(b"x" * 500)
    source = ExportFileSource(root, max_bytes=100)
    item = course_file("5005", "big.pdf", local_path="big.pdf")
    assert source.read(item) == b"x" * 100


# -- the shipped set -----------------------------------------------------------------------------


def test_the_accessibility_cluster_is_registered_and_declares_what_it_reads() -> None:
    shipped = {detector.id: detector for detector in detectors() if detector.id.startswith("D")}
    assert sorted(shipped) == [f"D{number}" for number in range(21, 31)]
    for detector in shipped.values():
        assert detector.title and not detector.title.endswith(".")
        assert detector.collections
        assert detector.tier in {Tier.A, Tier.B}


def test_no_detector_names_a_rubric_criterion() -> None:
    """Detectors emit facts about artifacts; a rubric pack projects them onto its own rows
    (SPEC §04). A detector that named a criterion would bake one instrument into the code and make
    every third-party rubric a rewrite."""
    import re

    pattern = re.compile(r"\b(?:COAL|GSOV|OREP|ACD|ENIN|ASFE|MOB)\s*\d+\b")
    for detector in detectors():
        assert not pattern.search(detector.title), detector.id
