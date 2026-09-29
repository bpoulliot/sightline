"""The three coverage states, and the ratings a row can read.

**This module is small on purpose.** Conflating the three states below is the failure item 04
exists to prevent, so they live in one leaf module that everything downstream imports and that
imports nothing itself. A second definition of "clean" appearing somewhere else in the tree is the
shape of the bug, and there is nowhere here for one to hide.

    NOT_YET_IMPLEMENTED         the bound capability exists; no detector implements it yet
    NO_DETERMINISTIC_COVERAGE   the row has no binding at all — normal, permanent, routes to
                                the extraction layer
    SUFFICIENTLY_PRESENT        a detector that settles the row (Tier A) ran and found nothing —
                                THE ONLY ONE MEANING CLEAN

If an absent detector or an unbound row can produce "Sufficiently Present", Sightline reports a
course clean on criteria it never examined. That is the silent under-detection SPEC §11 names and
it is the single worst thing this project can do.
"""

from __future__ import annotations

import enum
from collections.abc import Sequence


class Coverage(enum.StrEnum):
    """Whether a rubric row has deterministic coverage, and if not, which kind of not."""

    #: A bound tag that a detector implements, and that detector ran. The only state from which a
    #: clean reading is reachable at all.
    DETERMINISTIC = "deterministic"
    #: Bound to a tag in the vocabulary that no detector implements yet. **Transient** — a
    #: development-phase artifact that disappears as the detector inventory lands.
    NOT_YET_IMPLEMENTED = "not_yet_implemented"
    #: No usable binding. **Permanent for that pack and entirely normal**: a freshly imported
    #: third-party instrument is all of these and that is a success case. Routes to extraction.
    NO_DETERMINISTIC_COVERAGE = "no_deterministic_coverage"


class Rating(enum.StrEnum):
    """What a row reads. Internal names; the pack supplies the instrument's own labels."""

    #: A detector ran and found nothing, on a Tier A row — the tier that says its bindings settle
    #: the criterion rather than merely bearing on it (item 28). **The only value meaning clean.**
    SUFFICIENTLY_PRESENT = "sufficiently_present"
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    #: Tier D, or a feature absent by design.
    NOT_APPLICABLE = "not_applicable"
    #: The instrument's default cell state. Every Tier C row, every Tier B row its detectors found
    #: nothing on, and every row the arithmetic could not establish. Never a pass, never a failure.
    NO_RESPONSE = "no_response"

    @property
    def is_band(self) -> bool:
        return self in (Rating.MINOR, Rating.MODERATE, Rating.MAJOR)


def coverage_of(resolved: Sequence[str], pending: Sequence[str]) -> Coverage:
    """Which of the three states a row is in, given how its bindings resolved.

    **The only place this decision is made.** Two call sites need it — the projection over a course
    and the coverage report over the pack alone — and two copies of it are two chances for one of
    them to drift into treating an unimplemented capability as no binding at all, which is the
    conflation the three states exist to prevent.

    ``resolved`` are bound tags a detector in this build implements; ``pending`` are bound tags in
    the vocabulary that no detector implements yet. A tag the vocabulary does not carry at all is
    in neither list: it was reported as a warning and does not count towards coverage.
    """
    if resolved:
        return Coverage.DETERMINISTIC
    if pending:
        return Coverage.NOT_YET_IMPLEMENTED
    return Coverage.NO_DETERMINISTIC_COVERAGE


__all__ = ["Coverage", "Rating", "coverage_of"]
