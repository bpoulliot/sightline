"""The five stages a course goes through unattended, and what counts as one failing.

SPEC §04 lists eight pipeline stages. Five of them run without anybody watching and they are the
ones named here: acquire, detect, project, extract, score. *Normalize* is folded into acquire
because :meth:`sightline.acquire.service.CourseAcquisition.acquire` returns a normalized document
and splitting the name off would invent a stage nothing can observe separately. *Review* and
*emit* are deliberately **not** here: a batch that emitted a report would be the unattended export
SPEC §09 rules out, and :mod:`sightline.batch.gate` is where that rule is enforced rather than
merely stated.

ORDER — A CORRECTION TO SPEC §04, NOT A DEVIATION FROM IT
---------------------------------------------------------
The spec's table lists Extract (4) before Project (5). The batch runner projects first. The reason
is the routing contract that landed with item 04: :func:`sightline.rubric.routing.manifest` decides
which rows a model is asked about from each row's *coverage*, and coverage only exists once the
detection report has been projected onto the pack. Extracting first would mean either asking a
model about all 68 rows of the reference instrument — every passage of the course, per row — or
deciding coverage twice from two different places. Neither is acceptable, so the order here is
acquire, detect, **project**, **extract**, score, and `docs/SPEC.md` is corrected to match.

THE INVARIANT: A STAGE THAT EXAMINED NOTHING IS A FAILURE
---------------------------------------------------------
Item 06 established it for a single detector and this module extends it to a stage: zero examined
is never a pass. A course whose detect stage opened no artifact has not been found clean, it has
been found unread, and the difference is the silent under-detection of SPEC §11. So a stage
reports what it examined alongside what it found, and :class:`NothingExamined` is raised when that
count is zero — loudly, against that one course, with the rest of the batch continuing.

TWO KINDS OF FAILURE, AND KEEPING THEM APART IS THE POINT
----------------------------------------------------------
:class:`StageError` belongs to **one course**. A course whose export never arrived, whose pack row
set is empty, whose detectors all refused — the batch moves to the next course and the summary
names this one.

:class:`BatchAborted` belongs to **the run**. An unreachable model host, a model that is not
installed, a credential that is missing: every remaining course would fail the same way, one
300-second timeout at a time. Item 10 made the same split inside the extraction loop for the same
reason. Aborting is not per-course isolation failing — it is the batch's shared dependency failing,
and the summary says which courses never ran because of it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Stage(StrEnum):
    """One unattended stage of one course. The value is what is written to the run store."""

    ACQUIRE = "acquire"
    DETECT = "detect"
    PROJECT = "project"
    EXTRACT = "extract"
    SCORE = "score"

    @property
    def title(self) -> str:
        return _TITLES[self]

    @property
    def describes(self) -> str:
        """What this stage counts when it reports what it examined."""
        return _EXAMINES[self]


#: The order they run in. One tuple, so nothing downstream re-decides it.
ORDER: tuple[Stage, ...] = (
    Stage.ACQUIRE,
    Stage.DETECT,
    Stage.PROJECT,
    Stage.EXTRACT,
    Stage.SCORE,
)

_TITLES: Mapping[Stage, str] = {
    Stage.ACQUIRE: "Acquire and normalize the course",
    Stage.DETECT: "Run the deterministic detectors",
    Stage.PROJECT: "Project the findings onto the rubric",
    Stage.EXTRACT: "Locate and quote the passages a model is asked for",
    Stage.SCORE: "Roll up effort and order the action plan",
}

_EXAMINES: Mapping[Stage, str] = {
    Stage.ACQUIRE: "artifacts normalized from the course",
    Stage.DETECT: "artifacts opened by the detectors",
    Stage.PROJECT: "rubric rows projected",
    Stage.EXTRACT: "artifacts opened by the extractors",
    Stage.SCORE: "rubric rows rolled up",
}


@dataclass(frozen=True, kw_only=True, slots=True)
class StageOutcome:
    """What one stage of one course did: a count, a declarative line, and its own detail.

    ``examined`` is the count the invariant is checked against, and it is the stage's own unit —
    artifacts for acquire and detect, rows for project and score. ``statement`` is the line a
    reviewer reads; it states counts, never a judgement.
    """

    stage: Stage
    examined: int
    statement: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "examined": self.examined,
            "statement": self.statement,
            "detail": dict(self.detail),
        }


class StageError(RuntimeError):
    """One stage of one course could not complete. That course fails; the batch continues.

    ``remedy`` is what the person reading the run summary should do about it, and it is a separate
    field rather than sentence two of the summary because every surface that renders a failure
    renders both and none of them should have to split a string.
    """

    def __init__(self, stage: Stage, summary: str, *, remedy: str | None = None) -> None:
        super().__init__(summary)
        self.stage = stage
        self.summary = summary
        self.remedy = remedy

    @property
    def message(self) -> str:
        return " ".join(part for part in (self.summary, self.remedy) if part)


class NothingExamined(StageError):
    """The stage ran and opened nothing. Never a clean result — see the module docstring."""

    def __init__(self, stage: Stage, *, detail: str = "", remedy: str | None = None) -> None:
        summary = (
            f"The {stage.value} stage examined nothing: it recorded 0 {stage.describes}. "
            "That is not a clean course, it is an unread one, so this course is reported as "
            "failed rather than as reviewed."
        )
        super().__init__(stage, " ".join(part for part in (summary, detail) if part), remedy=remedy)


class BatchAborted(RuntimeError):
    """A condition of the run rather than of a course. Remaining courses are left unstarted."""

    def __init__(self, summary: str, *, remedy: str | None = None, stage: Stage | None = None):
        super().__init__(summary)
        self.summary = summary
        self.remedy = remedy
        self.stage = stage

    @property
    def message(self) -> str:
        return " ".join(part for part in (self.summary, self.remedy) if part)


__all__ = [
    "ORDER",
    "BatchAborted",
    "NothingExamined",
    "Stage",
    "StageError",
    "StageOutcome",
]
