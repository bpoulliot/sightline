"""One parse per body per run, and nothing else shared (roadmap item 30).

Twelve HTML detectors used to parse every body for themselves: 180 parses of the sample course's
fifteen bodies, and nine tenths of the detector stage's wall time on a course of realistic size
(``scripts/measure_parse_cost.py`` is the instrument). A run now hands every detector one
:class:`~sightline.detect.base.Parses`, reached only through :meth:`Survey.parse`.

What these tests hold is the other half of that bargain — the things a shared parse must *not*
change. Findings are identical with and without it; each detector is still counted only for what
it walked, so one that walks nothing still raises however many bodies were parsed before it; and
the shared parses answer only for an artifact the asking survey handed out.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path

import pytest

import sightline.detect.base as base
from detector_fixtures import course, page
from sightline.acquire import load_bundle
from sightline.detect import (
    DetectionReport,
    ResultState,
    detectors,
    run_detectors,
)
from sightline.detect.base import Detector, Finding, Parses, Survey, registry_snapshot
from sightline.detect.html_scan import scan
from sightline.model import CourseDocument

with registry_snapshot():

    class Silent(Detector):
        """Opens nothing — the shape of a broken detector — and runs after ones that parsed."""

        id = "T30"
        title = "A test detector that opens nothing"
        collections = ("pages",)
        capability_tag = "page_titles"
        capability_description = "A test detector; any tag in the vocabulary serves here."

        def examine(self, survey: Survey) -> Iterable[Finding]:
            return ()


@pytest.fixture(autouse=True)
def _keep_the_registry_clean() -> Iterator[None]:
    with registry_snapshot():
        yield


@pytest.fixture
def sample(repo_root: Path) -> CourseDocument:
    return load_bundle(repo_root / "examples" / "sample-course")


@pytest.fixture
def parse_calls(monkeypatch: pytest.MonkeyPatch) -> Counter[str]:
    """Every parse the shared cache performs, by markup. ``base.scan`` is the only name it calls."""
    calls: Counter[str] = Counter()

    def counting(html: str):  # type: ignore[no-untyped-def]
        calls[html] += 1
        return scan(html)

    monkeypatch.setattr(base, "scan", counting)
    return calls


def _serialized(report: DetectionReport) -> str:
    return json.dumps(report.to_dict(), sort_keys=True, indent=1)


def test_a_run_parses_each_body_of_the_sample_course_exactly_once(
    sample: CourseDocument, parse_calls: Counter[str]
) -> None:
    bodies = [artifact.body_html or "" for artifact in sample.bodies()]
    assert len(bodies) == 15, "the sample course changed shape; re-read this test's numbers"

    run_detectors(sample)

    assert sum(parse_calls.values()) == len(bodies)
    assert set(parse_calls) == set(bodies)
    assert set(parse_calls.values()) == {1}


def test_the_probe_would_see_one_parse_per_detector_if_the_parse_were_not_shared(
    sample: CourseDocument, parse_calls: Counter[str]
) -> None:
    """The counter above is only evidence if it can read more than one. Run every detector in its
    own run, which is what each did before item 30, and it must see the twelvefold multiple."""
    for detector in detectors():
        run_detectors(sample, only=(detector,))
    assert sum(parse_calls.values()) >= 12 * 15


def test_sharing_the_parse_changes_no_finding_and_no_count(sample: CourseDocument) -> None:
    """Byte-identical output over the sample course, shared against unshared. The unshared run is
    every detector in a run of its own, each with a fresh :class:`Parses`."""
    shared = run_detectors(sample)
    unshared = DetectionReport(
        results=tuple(
            result
            for detector in detectors()
            for result in run_detectors(sample, only=(detector,)).results
        )
    )
    assert _serialized(shared) == _serialized(unshared)
    assert shared.findings, "a comparison of two empty reports proves nothing"


def test_a_detector_that_walks_nothing_still_raises_after_others_parsed_every_body() -> None:
    """The count is per survey. Bodies another detector parsed in the same run are not bodies
    this one examined, and it is reported as having examined nothing."""
    document = course([page("one", "<p>One</p>"), page("two", "<p><img src='a.png'></p>")])
    readers = tuple(detector for detector in detectors() if detector.id in {"D21", "D22"})
    report = run_detectors(document, only=(*readers, Silent()))

    silent = report.by_detector("T30")
    assert silent is not None
    assert silent.state is ResultState.NOT_EXAMINED
    assert silent.examined == 0
    assert silent.clean is False
    for reader in readers:
        result = report.by_detector(reader.id)
        assert result is not None and result.examined == 2


def test_a_survey_refuses_to_parse_an_artifact_it_did_not_hand_out() -> None:
    document = course([page("one", "<p>One</p>"), page("two", "<p>Two</p>")])
    survey = Survey(document, Silent(), parses=Parses())
    first, second = document.pages

    with pytest.raises(LookupError, match="never handed out"):
        survey.parse(first)

    handed = next(survey.bodies())
    assert handed.artifact is first
    assert survey.parse(handed) is survey.parse(first)
    with pytest.raises(LookupError):
        survey.parse(second)
    assert survey.examined == 1


def test_one_parse_is_handed_to_every_survey_in_a_run() -> None:
    document = course([page("one", "<h2>One</h2>")])
    parses = Parses()
    trees = []
    for detector in (Silent(), Silent()):
        survey = Survey(document, detector, parses=parses)
        (body,) = survey.bodies()
        trees.append(survey.parse(body))
    assert trees[0] is trees[1]
    assert len(parses) == 1


def test_the_same_id_with_different_markup_is_never_handed_the_wrong_tree() -> None:
    parses = Parses()
    old = Survey(course([page("one", "<h2>Old</h2>")]), Silent(), parses=parses)
    new = Survey(course([page("one", "<h3>New</h3>")]), Silent(), parses=parses)
    assert old.parse(next(old.bodies())).text == "Old"
    assert new.parse(next(new.bodies())).text == "New"


def test_a_shared_tree_cannot_be_edited_by_the_detector_holding_it() -> None:
    """Sharing is only safe because a :class:`Scan` is immutable, attributes included."""
    (image,) = scan("<img src='a.png' alt='A diagram'>").of("img")
    with pytest.raises(TypeError):
        image.attrs["alt"] = ""  # type: ignore[index]
