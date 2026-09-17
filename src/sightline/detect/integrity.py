"""The guard that proves the under-detection invariant is still holding.

Item 06 built the invariant: a detector reports what it examined, and zero examined is an error
rather than a pass (SPEC §11). This module is the guard over it, and it exists as its own thing
for one reason — **a guard that has never failed is indistinguishable from a guard that cannot
fail.** So everything here is a pure function over a *report*, and the report is a dataclass a test
can build by hand with a detector deliberately broken in it. Firing the guard costs a fixture, not
a repository edit.

Three checks, and each one answers a different question.

**Coverage.** Is every registered detector actually exercised by a fixture test? A detector nobody
runs is a detector nobody has seen fail, and it ships. :func:`coverage` takes the registry and a
:class:`CoverageLedger` of what the suite was *observed* running, and names by id anything with no
test behind it. The ledger is an observation rather than a grep, because the failure this whole
item is modelled on — a 31-suite JS test set that gated nothing for months — looked exactly like a
check that was running.

**A sweep is not coverage, and finding that out cost this item a rewrite.** The first version of
this guard counted any observed run, and a drill — an untested detector added to the shipping
registry on purpose — reported it *covered*. One test in the suite iterates the registry and runs
everything in it, so every future detector was born covered by a test nobody wrote for it. The rule
now is narrow: **a test that ran every registered detector has tested the registry, not any
detector in it**, and the ledger drops it. A detector is covered when something reached it by name.

**The invariant itself.** Does every registered detector still refuse a document set with nothing
in it? :func:`zero_examined_breaches` runs each one against :func:`empty_document` and requires
:class:`~sightline.detect.base.NothingExamined`. Three outcomes and only one of them passes: it
raised, it *returned a result* — a detector claiming it examined something over an empty course,
which is a fabricated count and the worst of the three — or it raised something else, which is a
detector that crashes rather than refuses.

**Nothing here is a verdict either.** Both reports state counts and name names. ``statement()`` is
what a human reads; ``failure()`` is what an assertion prints, and it lists every id rather than
the first one, because a build that names one of four missing detectors gets fixed four times.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from ..model import CourseDocument, CourseInfo
from .base import Detector, NothingExamined

#: The course id the empty document carries. Any value works; a constant makes the failure text
#: stable enough to assert on.
EMPTY_COURSE_ID = "0"


def empty_document() -> CourseDocument:
    """A course with nothing in it and no examination record explaining why.

    The document every detector must refuse. ``base_url`` is deliberately empty rather than a
    plausible-looking hostname: this value is institution-identifying (SPEC §11) and a guard that
    carried one would put it in every failure message the guard ever printed.
    """
    return CourseDocument(
        base_url="",
        course=CourseInfo(id=EMPTY_COURSE_ID, name="An empty course, for the integrity guard"),
    )


# ---------------------------------------------------------------------------------------------
# Coverage: is every registered detector exercised by a fixture test?
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class CoverageLedger:
    """What the fixture suite was *observed* running, not what it appears to run.

    Built by ``tests/detector_ledger.py``, which calls the suite's detector tests with
    :meth:`Detector.run` instrumented. The distinction matters: a ledger assembled by searching
    test sources for a class name records a detector that is imported and never run, which is the
    exact shape of the green-and-empty test job this item was written after.
    """

    #: Detector id -> the test functions observed running it *by name*, as ``module.py::function``.
    tests_by_detector: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Test modules the harness looked in. Named so an empty ledger can be told from a bad scan.
    modules_scanned: tuple[str, ...] = ()
    #: Tests that ran every registered detector and are therefore credited to none of them. A
    #: sweep proves the registry can be iterated; it says nothing about any detector in it, and
    #: counting it is how a detector added tomorrow is born covered.
    sweeps: tuple[str, ...] = ()
    #: Test functions actually called.
    tests_called: int = 0
    #: Test functions the harness could not call because they ask for a pytest fixture.
    tests_needing_fixtures: int = 0
    #: Tests that raised while being observed, as ``module.py::function: ExceptionType``. Not a
    #: failure here — the suite proper owns whether a test passes — but recorded, because a test
    #: that raised before reaching its detector is a hole in the observation and not a silence.
    tests_that_raised: tuple[str, ...] = ()

    def tests_for(self, detector_id: str) -> tuple[str, ...]:
        return tuple(self.tests_by_detector.get(detector_id, ()))

    @property
    def observed(self) -> frozenset[str]:
        return frozenset(self.tests_by_detector)


@dataclass(frozen=True, kw_only=True, slots=True)
class CoverageReport:
    """Every registered detector, and the fixture tests observed running it."""

    #: Registered detector ids, in id order.
    registered: tuple[str, ...]
    #: Registered ids that at least one test ran, mapped to those tests.
    covered: Mapping[str, tuple[str, ...]]
    #: Registered ids no test ran. The thing this check exists to name.
    uncovered: tuple[str, ...]
    #: Ids the ledger saw run that nothing registers — the throwaway detectors the interface tests
    #: define inside a :func:`~sightline.detect.base.registry_snapshot`. Normal, and reported
    #: rather than dropped: it is the line that shows the snapshot doing its job.
    unregistered: tuple[str, ...]
    ledger: CoverageLedger

    @property
    def ok(self) -> bool:
        return not self.uncovered

    @property
    def test_count(self) -> int:
        return sum(len(tests) for tests in self.covered.values())

    def statement(self) -> str:
        """Counts, declaratively. What the documented command prints on a good day."""
        lines = [
            f"{len(self.registered)} detector(s) registered; "
            f"{len(self.covered)} covered by {self.test_count} fixture test(s); "
            f"{len(self.uncovered)} uncovered.",
            f"Observed by calling {self.ledger.tests_called} test function(s) across "
            f"{len(self.ledger.modules_scanned)} module(s): "
            f"{', '.join(self.ledger.modules_scanned) or 'none'}.",
        ]
        if self.ledger.sweeps:
            lines.append(
                f"  {len(self.ledger.sweeps)} registry sweep(s) credited to no detector: "
                f"{', '.join(self.ledger.sweeps)}"
            )
        for detector_id in self.registered:
            tests = self.covered.get(detector_id, ())
            lines.append(f"  {detector_id}: {len(tests)} test(s)")
        if self.ledger.tests_that_raised:
            lines.append(
                f"  {len(self.ledger.tests_that_raised)} test(s) raised while being observed: "
                f"{', '.join(self.ledger.tests_that_raised)}"
            )
        if self.unregistered:
            lines.append(
                f"  also ran, and ships with nothing: {', '.join(self.unregistered)} "
                "(detectors a test defines inside a registry snapshot)"
            )
        return "\n".join(lines)

    def failure(self) -> str:
        """The assertion message. Names every uncovered detector, not the first one."""
        if self.ok:
            return ""
        named = "\n".join(
            f"  {detector_id}: no fixture test was observed running it"
            for detector_id in self.uncovered
        )
        return (
            f"{len(self.uncovered)} registered detector(s) have no fixture test:\n{named}\n"
            "A detector nobody runs is a detector nobody has seen fail, and it still ships and "
            "still reports the course clean. Add a test that names it and runs it against a "
            "fixture course — one that fires on a planted defect and one that stays quiet on a "
            "clean case. A test that iterates the registry does not count: it tests the registry.\n"
            f"{self.statement()}"
        )


def coverage(registered: Iterable[str], ledger: CoverageLedger) -> CoverageReport:
    """Which registered detectors the ledger saw run, and which it did not."""
    ids = tuple(sorted(registered))
    covered = {
        detector_id: ledger.tests_for(detector_id)
        for detector_id in ids
        if ledger.tests_for(detector_id)
    }
    return CoverageReport(
        registered=ids,
        covered=covered,
        uncovered=tuple(detector_id for detector_id in ids if detector_id not in covered),
        unregistered=tuple(sorted(ledger.observed - set(ids))),
        ledger=ledger,
    )


# ---------------------------------------------------------------------------------------------
# The invariant: does every registered detector still refuse an empty document set?
# ---------------------------------------------------------------------------------------------


class Outcome(enum.StrEnum):
    """What a detector did when it was handed a course with nothing in it."""

    #: It raised ``NothingExamined``. The only passing outcome.
    REFUSED = "refused"
    #: It returned a result. Something reported a count over an empty course.
    RETURNED = "returned"
    #: It raised something else. A detector that crashes rather than refuses.
    CRASHED = "crashed"


@dataclass(frozen=True, kw_only=True, slots=True)
class Breach:
    """One detector that did not refuse an empty document set, and what it did instead."""

    detector: str
    outcome: Outcome
    detail: str

    def line(self) -> str:
        return f"  {self.detector}: {self.outcome.value} — {self.detail}"


@dataclass(frozen=True, kw_only=True, slots=True)
class InvariantReport:
    """Every registered detector run against an empty course, and what each one did."""

    checked: tuple[str, ...]
    breaches: tuple[Breach, ...]

    @property
    def ok(self) -> bool:
        return not self.breaches

    def statement(self) -> str:
        return (
            f"The zero-examined invariant holds for "
            f"{len(self.checked) - len(self.breaches)} of {len(self.checked)} registered "
            f"detector(s)."
        )

    def failure(self) -> str:
        if self.ok:
            return ""
        lines = "\n".join(breach.line() for breach in self.breaches)
        return (
            f"{len(self.breaches)} of {len(self.checked)} detector(s) did not refuse an empty "
            f"document set:\n{lines}\n"
            "A detector that reports a count over a course holding nothing has fabricated the "
            "count, and a course it examined nothing of reads as a course with nothing wrong.\n"
            f"{self.statement()}"
        )


def zero_examined_breaches(detectors: Sequence[Detector]) -> tuple[Breach, ...]:
    """Run each detector against an empty course; return the ones that did not refuse."""
    document = empty_document()
    breaches: list[Breach] = []
    for detector in detectors:
        try:
            result = detector.run(document)
        except NothingExamined:
            continue
        except Exception as exc:  # noqa: BLE001 - the outcome is the finding
            breaches.append(
                Breach(
                    detector=detector.id,
                    outcome=Outcome.CRASHED,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
        else:
            breaches.append(
                Breach(
                    detector=detector.id,
                    outcome=Outcome.RETURNED,
                    detail=(
                        f"returned state={result.state.value} examined={result.examined} "
                        f"clean={result.clean} over a document holding no artifacts"
                    ),
                )
            )
    return tuple(breaches)


def invariant(detectors: Sequence[Detector]) -> InvariantReport:
    """The zero-examined invariant, checked across every detector given."""
    return InvariantReport(
        checked=tuple(detector.id for detector in detectors),
        breaches=zero_examined_breaches(detectors),
    )


__all__ = [
    "EMPTY_COURSE_ID",
    "Breach",
    "CoverageLedger",
    "CoverageReport",
    "InvariantReport",
    "Outcome",
    "coverage",
    "empty_document",
    "invariant",
    "zero_examined_breaches",
]
