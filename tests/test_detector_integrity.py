"""The detector integrity guard, and the proof that it can fail.

Four guards, each demonstrated firing in this file on purpose. That is the whole point of the item
and not a flourish: **a guard that has never failed is indistinguishable from a guard that cannot
fail.** The case this is modelled on was a 31-suite JS test set on this network that gated nothing
for months, because the obvious one-line runner ran a *directory*, printed one opaque error and
exited 0. Green job, zero tests, silent under-collection — the same class of bug as the silent
under-detection SPEC §11 names, and it was found only because somebody measured instead of reading
the exit code.

So every guard below is paired with a test that breaks something and watches it go red:

============================  ==========================================================
Guard                         Shown firing by
============================  ==========================================================
Coverage over the registry    a detector defined with no test; and a ledger that observed
                              nothing, which must read as *nothing is covered* rather
                              than as *nothing is wrong*
The zero-examined invariant   a sabotaged examined count, in both directions — a count
                              fabricated over an empty course, and a detector that reads
                              the document behind the survey's back so its count stays 0
Capability declarations       a declaration with no tag, one with an unknown tag, and one
                              with no description — each fed in and watched go red
The instrument itself         discovery asserted non-empty and anchored to a named test,
                              and the observation asserted to have no holes in it
============================  ==========================================================

**One rule here came out of a drill rather than a design, and it is the reason this file is worth
more than its line count.** The first version of the coverage guard counted every observed run.
Fired at — a detector added to the shipping registry with no test, on purpose — it reported the
detector *covered*, because one test in the suite iterates the registry and runs everything in it.
So the guard passed on exactly the thing it was built to catch, and nothing but the drill would
have said so. A test that ran every registered detector is now credited to none of them.

The documented command is in the README beside the test command:

    ./scripts/dev.sh pytest tests/test_detector_integrity.py -q -s

``-s`` is not decoration. Without it the command's whole output is one line saying how many tests
passed, which is the opaque green this item exists because of; with it the guard prints the counts
a human can check.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import pytest

from detector_fixtures import course, page
from detector_ledger import EXCLUDED, build_ledger, detector_test_modules, ledger
from sightline.detect import NothingExamined, detectors, registry
from sightline.detect.base import Detector, Finding, Severity, Survey, registry_snapshot
from sightline.detect.integrity import (
    CoverageLedger,
    Declaration,
    Outcome,
    capabilities,
    coverage,
    declarations,
    empty_document,
    invariant,
)

# -- (a) every registered detector is covered by a fixture test ---------------------------------


def test_every_registered_detector_is_covered_by_a_fixture_test() -> None:
    """The guard. A detector added with no test fails the build, by name.

    Coverage is an *observation*: the ledger calls the suite's detector tests with ``Detector.run``
    instrumented and records the ids that came through it. A detector merely imported by a test
    does not count, because the thing being prevented is a check that looks like it is running.
    """
    report = coverage(registry(), ledger())
    assert report.ok, report.failure()
    assert report.uncovered == ()


def test_the_coverage_guard_fires_on_a_detector_nobody_tests() -> None:
    """Guard (a), fired. Defining a detector registers it; nothing registers a test for it."""
    with registry_snapshot():

        class Untested(Detector):
            id = "D99"
            title = "A detector added with no test behind it"
            collections = ("pages",)
            capability_tag = "page_titles"
            capability_description = "A drill detector; any tag in the vocabulary serves here."

            def examine(self, survey: Survey) -> Iterable[Finding]:
                return ()

        assert "D99" in registry()
        report = coverage(registry(), ledger())

    assert report.ok is False
    assert report.uncovered == ("D99",)
    failure = report.failure()
    assert "D99" in failure
    assert "no fixture test was observed running it" in failure
    # And it is named rather than counted: a build that says "1 detector is uncovered" gets fixed
    # by guessing.
    assert "1 registered detector(s) have no fixture test" in failure


def test_an_instrument_that_observed_nothing_reads_as_nothing_covered() -> None:
    """Guard (a), fired the other way, and this is the one the item was written for.

    A ledger that collected nothing is what a broken harness produces — the JS runner that pointed
    at a directory and exited 0 collected nothing and reported success. Fed an empty ledger, the
    guard must report every registered detector uncovered. Anything that reads an empty measurement
    as a clean result is the bug, not the guard.
    """
    report = coverage(registry(), CoverageLedger())
    assert report.ok is False
    assert report.uncovered == tuple(sorted(registry()))
    assert report.test_count == 0
    for detector_id in registry():
        assert detector_id in report.failure()


# -- the instrument itself ----------------------------------------------------------------------


def test_the_ledger_scanned_real_modules_and_called_real_tests() -> None:
    """The measurement is asserted non-empty in both dimensions before anything is concluded from
    it. An instrument that scanned no module and called no test agrees with a perfect suite."""
    recorded = ledger()
    assert recorded.modules_scanned
    assert "test_detect_accessibility.py" in recorded.modules_scanned
    assert recorded.tests_called > 0


def test_an_async_test_is_counted_rather_than_called() -> None:
    """A coroutine called without a loop observes nothing and looks like a test that ran.

    The extraction suite (item 10) is the first async code in a scanned module, which is what
    surfaced this: the harness was calling those functions, recording nothing, and emitting a
    RuntimeWarning per call. They are counted now, and the count is in the statement, so a
    detector whose only test is async reads as uncovered rather than as observed.
    """
    assert ledger().tests_awaiting_a_loop > 0
    assert "async test(s) were not called here" in coverage(registry(), ledger()).statement()


def test_the_ledger_names_the_tests_it_saw_running_each_detector() -> None:
    """Anchored to a test that exists, so a ledger recording ids from somewhere else is visible.

    If this line ever has to change because the named test was renamed, that is the instrument
    being re-anchored rather than the guard weakening.
    """
    tests = ledger().tests_for("D21")
    assert tests
    assert any(
        node == "test_detect_accessibility.py::"
        "test_d21_finds_nothing_when_every_image_is_labelled_or_correctly_decorative"
        for node in tests
    ), tests


def test_the_observation_has_no_holes_in_it() -> None:
    """A test that raised before it reached its detector is a test the ledger could not see
    through, and a hole in a measurement reads as an absence. The suite proper owns whether a test
    passes; what is asserted here is that the instrument got a clean look at all of them."""
    raised = ledger().tests_that_raised
    assert raised == (), f"{len(raised)} test(s) raised while being observed: {list(raised)}"


def test_a_registry_sweep_is_credited_to_no_detector() -> None:
    """The rule a drill forced in. A test that runs everything in the registry tests the registry.

    Exactly one such test exists today. Counting it would mean every detector added after it
    arrives already covered by a test nobody wrote for it, which is how this guard would come to
    pass on the thing it is for.
    """
    assert ledger().sweeps == (
        "test_detect_base.py::test_every_shipped_detector_raises_on_an_empty_document_set",
    )
    for tests in ledger().tests_by_detector.values():
        assert not set(tests) & set(ledger().sweeps)


def test_the_only_module_the_scan_excludes_is_the_guard_itself() -> None:
    """The guard defines sabotaged detectors, and calling it from inside itself is circular. That
    is the entire reason for the exclusion list, and a second entry in it is a detector test being
    quietly removed from the measurement."""
    assert set(EXCLUDED) == {"test_detector_integrity.py"}
    assert all(path.name not in EXCLUDED for path in detector_test_modules())


def test_the_ledger_is_the_same_whether_or_not_the_rest_of_the_suite_ran() -> None:
    """Built fresh, the ledger agrees with the cached one. This is what lets the documented
    one-line command give the same answer as the full suite: the harness calls the tests itself
    rather than watching whatever the surrounding session happened to select."""
    assert build_ledger().tests_by_detector == ledger().tests_by_detector


def test_the_ledger_does_not_leave_a_test_detector_in_the_registry() -> None:
    """Several detector tests define a detector, and defining one registers it. The harness takes
    a snapshot around every call; without it the guard would be asked to account for detectors
    nobody ships."""
    before = dict(registry())
    build_ledger()
    assert dict(registry()) == before


# -- (b) the zero-examined invariant holds for every registered detector -------------------------


def test_every_registered_detector_refuses_an_empty_document_set() -> None:
    """The guard. Run each registered detector over a course holding nothing and require it to
    raise: a detector that returns a result there has reported a count over nothing."""
    report = invariant(detectors())
    assert report.ok, report.failure()
    assert len(report.checked) == len(registry())


def test_the_invariant_guard_fires_on_a_fabricated_examined_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard (b), fired, by breaking the examined count the way it would really break.

    ``Survey.examined`` is the count. Sabotaged to claim one artifact, every detector sails past
    the refusal and comes back ``clean`` over a course with nothing in it — which is precisely the
    failure the invariant exists to prevent, arriving as a reassuring result rather than an error.
    """
    monkeypatch.setattr(Survey, "examined", property(lambda self: 1))

    report = invariant(detectors())

    assert report.ok is False
    assert len(report.breaches) == len(registry())
    assert {breach.outcome for breach in report.breaches} == {Outcome.RETURNED}
    failure = report.failure()
    assert "clean=True" in failure
    assert "examined=1" in failure
    for detector_id in registry():
        assert detector_id in failure


def test_the_invariant_guard_fires_on_a_detector_that_crashes_instead_of_refusing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guard (b), fired a third way. A detector that blows up over an empty course is a different
    defect from one that refuses, and reporting the two as the same outcome loses the distinction
    a maintainer needs."""

    def explode(self: Survey) -> Iterable[object]:
        raise ValueError("the survey fell over")

    monkeypatch.setattr(Survey, "bodies", explode)

    report = invariant(detectors())

    assert report.ok is False
    assert Outcome.CRASHED in {breach.outcome for breach in report.breaches}
    assert "the survey fell over" in report.failure()


def test_a_detector_that_reaches_past_the_survey_is_caught_by_its_own_zero_count() -> None:
    """The sabotage in the other direction, and the one a real bug looks like.

    Nothing fabricates a count here. This detector reads the document *behind* the survey, which is
    how a count silently stops being taken, and it does it over a course with two readable pages —
    so it produces a finding and still reports having examined nothing. The framework raises rather
    than returning a result, which is the invariant catching an under-count on a live course rather
    than on an empty one.
    """
    with registry_snapshot():

        class BackDoor(Detector):
            id = "T21"
            title = "A detector that reaches the document past the survey that counts"
            collections = ("pages",)
            capability_tag = "page_titles"
            capability_description = "A drill detector; any tag in the vocabulary serves here."

            def examine(self, survey: Survey) -> Iterable[Finding]:
                # The back door. Every artifact reached this way is an artifact the survey never
                # handed out and therefore never counted.
                for artifact in survey._document.bodies():
                    yield survey.finding(
                        code="seen",
                        severity=Severity.ADVISORY,
                        location=survey.location(artifact),
                        observed=artifact.title,
                    )

        document = course([page("one", "<p>One</p>"), page("two", "<p>Two</p>")])
        with pytest.raises(NothingExamined) as caught:
            BackDoor().run(document)

    assert caught.value.detector == "T21"
    assert "examined 0 artifacts" in str(caught.value)


# -- (c) every registered detector declares a capability a rubric can bind -----------------------


def test_every_registered_detector_declares_a_valid_capability_tag() -> None:
    """The guard. A detector with no usable capability tag is reported against nothing.

    The tag is the only thing joining a detector to a rubric row (item 04), and it joins in one
    direction: packs bind their rows to tags, and a detector never names a row. So a detector that
    ships without a tag runs, finds things, and no instrument can ever show what it found. **This
    is the check that makes the detectors of roadmap items 07 and 08 comply without their prompts
    having to know tags exist.**
    """
    report = capabilities(declarations(detectors()))
    assert report.ok, report.failure()
    assert report.undeclared == ()
    assert len(report.checked) == len(registry())


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        (Declaration(detector="D98", tag="", description="Anything."), "no capability tag at all"),
        (
            Declaration(detector="D98", tag="alt_txt", description="Anything."),
            "not in the vocabulary",
        ),
        (Declaration(detector="D98", tag="alt_text", description="   "), "with no description"),
    ],
)
def test_the_capability_guard_fires_on_a_declaration_that_will_not_resolve(
    declaration: Declaration, expected: str
) -> None:
    """Guard (c), fired, three ways.

    It is a pure function over the *declarations* rather than over the registry, and that is
    deliberate: the detector interface already refuses an unknown tag at class-definition time, so
    a detector that reached the registry cannot carry one — and a guard whose input cannot be wrong
    is indistinguishable from a guard that cannot fail. Handing it a bad declaration is how it gets
    shown failing. The interface's own refusal is asserted in ``tests/test_detect_base.py``.
    """
    report = capabilities([declaration])
    assert report.ok is False
    assert [entry.detector for entry in report.undeclared] == ["D98"]
    assert expected in report.undeclared[0].reason
    failure = report.failure()
    assert "D98" in failure
    assert "reported against nothing" in failure


def test_the_capability_guard_accepts_several_detectors_sharing_one_tag() -> None:
    """Sharing is by design, and it is the fan-in the rubric layer is built for.

    Two parsers can produce facts about the same course-quality concept — a table checker and a
    mobile-layout checker both find things about table semantics — and a row bound to that concept
    receives from both, their effort summed. A guard that treated a shared tag as a collision would
    forbid the case the projection exists to handle.
    """
    report = capabilities(
        [
            Declaration(detector="D24", tag="table_semantics", description="Headers and scope."),
            Declaration(detector="D40", tag="table_semantics", description="Layout misuse."),
        ]
    )
    assert report.ok
    assert report.shared == {"table_semantics": ("D24", "D40")}
    assert "shared by D24, D40" in report.statement()


def test_a_vocabulary_tag_with_no_detector_is_reported_and_not_a_fault() -> None:
    """The reason ``NOT_YET_IMPLEMENTED`` exists as a state rather than as a silence.

    Most of the vocabulary is concepts some future detector implements. A pack bound to one of
    those has to read as "nothing examined this yet" — transient and honest — rather than as
    either a clean row or a broken rubric, and the count is reported here so the gap is visible.
    """
    report = capabilities(declarations(detectors()))
    assert report.ok
    assert report.unimplemented, "the inventory is not finished; some tag has no detector"
    assert "document_printability" in report.unimplemented
    assert "alt_text" not in report.unimplemented


# -- (d) what the documented command prints -------------------------------------------------------


def test_the_guard_reports_counts_rather_than_a_verdict() -> None:
    """The statement a human reads, printed here so the documented command shows it.

    It is the same register the rest of the tool reports in (SPEC §08): computed counts and names,
    nothing that reads as a grade. A guard that printed "detector health: good" would be the thing
    this repository is built not to do.
    """
    report = coverage(registry(), ledger())
    invariant_report = invariant(detectors())
    capability_report = capabilities(declarations(detectors()))

    statement = report.statement()
    print("\n" + statement)
    print(invariant_report.statement())
    print(capability_report.statement())
    print(
        f"Empty-course document used: course id {empty_document().course.id}, "
        f"{len(tuple(empty_document().artifacts()))} artifact(s)."
    )

    assert f"{len(registry())} detector(s) registered" in statement
    assert "0 uncovered." in statement
    for detector_id in registry():
        assert f"  {detector_id}: " in statement
    # Whole words, not substrings. The statement names the test modules it observed, and a
    # substring search reads a verdict out of a filename: `test_extract_passages.py` contains
    # "pass". A lint that fires on the name of a file is a lint nobody can trust the next time it
    # fires for real.
    for word in ("score", "grade", "healthy", "good", "pass"):
        pattern = re.compile(rf"\b{word}\b")
        assert not pattern.search(statement.lower())
        assert not pattern.search(capability_report.statement().lower())
