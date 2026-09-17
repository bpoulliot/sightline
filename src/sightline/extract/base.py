"""The extractor interface: what a model is asked, what is done with it, and what is refused.

An extractor is to a passage of course text what a detector is to a count: a way of producing a
*checkable artifact*. It locates, it quotes, and it stops. Read
:mod:`sightline.extract.verify` first — the verification step is what makes anything here
trustworthy, and everything else is scaffolding around it.

WHAT AN EXTRACTOR IS ALLOWED TO PRODUCE
---------------------------------------

A verbatim quote, the artifact it was found in, and counts. Nothing else. There is no field on
:class:`Evidence` for a rating, a band, a score, a confidence or a summary sentence, and the JSON
the model is allowed to return has none either (:mod:`sightline.extract.schema`). A Tier C
extractor additionally drafts a *question* for a reviewer, assembled from computed counts — it
never pre-selects an answer to it (SPEC §02).

THE LOCATION IS COMPUTED, NOT BELIEVED
--------------------------------------

The schema requires the model to say where it found each quote, and **that string is never
rendered**. Three models were asked the same question about the same page on the target host and
returned ``"Course policies (page)"``, ``"page"`` and ``"Week 2 overview"`` for that field: it
carries no consistent meaning, so believing it would be inventing precision. What is rendered is
computed instead — the artifact is the one the harness sent (the model never chooses it), the line
is the source line of the block the verified quote was found in, and "under <heading>" is the
heading above that block. Every part of it can be checked against the page.

The field stays in the schema because it holds the model to naming a place inside the document it
was given, and because a later item comparing it against the computed location has a signal to work
with. It is carried on :attr:`Evidence.stated_location` and labelled as unverified wherever it is
serialized.

WHAT AN EMPTY ANSWER MEANS, AND WHAT IT MUST NOT BE CONFUSED WITH
-----------------------------------------------------------------

Four outcomes, and a report that blurs any two of them is worse than one that reports none:

``no evidence found``
    Every passage was sent, answered and verified, and nothing in the course bears on the question.
    A complete, successful, expected answer.
``nothing was examined``
    There was nothing to send. Acquisition says why. Never a pass.
``nothing carried any text``
    Artifacts were opened and every one of them was empty. Not the same as finding nothing in them.
``could not be read``
    A call failed or a response violated the schema. **Never** rendered as "no evidence found",
    which is the specific lie this item guards against: a failure that reads as a clean result.

EXTRACTORS DECLARE NO CAPABILITY TAG, DELIBERATELY
--------------------------------------------------

``src/sightline/capabilities.yaml`` says it in the file: the vocabulary names what a *parser* can
establish, and extractors reach a rubric row through the routing contract in
:mod:`sightline.rubric.routing` rather than through a binding. So an extractor never names a
criterion, never declares a tag, and is never bound to a row. A row that needs one is correctly
left unbound, and :func:`sightline.extract.runner.answer_request` is how it is served.
"""

from __future__ import annotations

import enum
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..detect.base import Location, ResultState, Tier
from ..model import Artifact, ArtifactKind, CourseDocument, Examination, ExaminationState
from .passages import WORD_BUDGET, Passage, passages_for
from .schema import RawFinding
from .verify import Match, verify

#: Artifact kinds that carry a body worth sending. Declared once so an extractor narrows this
#: rather than inventing its own list.
BODY_KINDS: tuple[ArtifactKind, ...] = (
    ArtifactKind.SYLLABUS,
    ArtifactKind.PAGE,
    ArtifactKind.ASSIGNMENT,
    ArtifactKind.QUIZ,
    ArtifactKind.DISCUSSION,
    ArtifactKind.ANNOUNCEMENT,
)

#: Acquisition collection names, by the kind they populate. Used to explain a zero count the way
#: the detector framework does — with acquisition's own record rather than with a guess.
COLLECTION_OF: dict[ArtifactKind, str] = {
    ArtifactKind.SYLLABUS: "syllabus",
    ArtifactKind.PAGE: "pages",
    ArtifactKind.ASSIGNMENT: "assignments",
    ArtifactKind.QUIZ: "quizzes",
    ArtifactKind.DISCUSSION: "discussions",
    ArtifactKind.ANNOUNCEMENT: "announcements",
}

#: The longest excerpt of a rejected quote that reaches a log line. A discarded quote has to be
#: readable to be diagnosed, and it may contain course text, so it is capped rather than dropped
#: or logged whole.
DISCARD_EXCERPT = 120


class FailureKind(enum.StrEnum):
    """Why one passage produced nothing. Both of these are failures, never empty results."""

    #: The response did not conform to the schema and was rejected rather than coerced.
    SCHEMA = "schema"
    #: The host did not answer, or answered with an error.
    HOST = "host"


@dataclass(frozen=True, kw_only=True, slots=True)
class Evidence:
    """One verified quote: what the course says, and where a reviewer can read it.

    ``quote`` is the **source's** text at the match, sliced out of the document, not the string
    the model returned. See :mod:`sightline.extract.verify` for why that distinction is the point
    rather than a detail.
    """

    extractor: str
    tier: Tier
    quote: str
    location: Location
    #: What the model said about where it looked. Unverified, never rendered as evidence.
    stated_location: str
    #: How many places in the passage the quote matches.
    occurrences: int = 1
    #: Anything computed *from* the verified quote — a task verb and its level, a stated response
    #: time. Always derived in code from the quote above, never asked of the model.
    context: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "extractor": self.extractor,
            "tier": self.tier.value,
            "quote": self.quote,
            "location": self.location.to_dict(),
            "occurrences": self.occurrences,
            "model_stated_location_unverified": self.stated_location,
        }
        if self.context:
            payload["context"] = dict(sorted(self.context.items()))
        return payload


@dataclass(frozen=True, kw_only=True, slots=True)
class Discarded:
    """A returned quote that is not in the document. Recorded, logged, and never rendered."""

    extractor: str
    artifact_id: str
    artifact_title: str
    #: The model's string, truncated. Kept so the failure can be diagnosed rather than guessed at.
    excerpt: str
    stated_location: str
    reason: str = "not found in the source document"

    def to_dict(self) -> dict[str, Any]:
        return {
            "extractor": self.extractor,
            "artifact_id": self.artifact_id,
            "artifact_title": self.artifact_title,
            "excerpt": self.excerpt,
            "model_stated_location_unverified": self.stated_location,
            "reason": self.reason,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class Failure:
    """One passage that could not be read. Distinct from a passage that held nothing."""

    extractor: str
    passage: str
    artifact_id: str
    kind: FailureKind
    reason: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "extractor": self.extractor,
            "passage": self.passage,
            "artifact_id": self.artifact_id,
            "kind": self.kind.value,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class CallRecord:
    """What one model call cost and produced. Latency, tokens and model, per SPEC §06."""

    extractor: str
    passage: str
    artifact_id: str
    model: str
    ok: bool
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    thinking_pinned: bool | None = None
    returned: int = 0
    accepted: int = 0
    discarded: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "extractor": self.extractor,
            "passage": self.passage,
            "artifact_id": self.artifact_id,
            "model": self.model,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens_reported": self.prompt_tokens is not None or self.completion_tokens is not None,
            "thinking_pinned": self.thinking_pinned,
            "returned": self.returned,
            "accepted": self.accepted,
            "discarded": self.discarded,
            "error": self.error,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class ExtractorResult:
    """What one extractor did over one course, in full."""

    extractor: str
    title: str
    tier: Tier
    state: ResultState
    #: Artifacts opened. Zero is only ever paired with ``NOT_EXAMINED``.
    examined: int
    #: Passages sent to the model. Fewer than ``examined`` when an artifact carried no text, more
    #: when one was long enough to need several calls.
    passages: int
    #: Passages that came back with a response conforming to the schema.
    answered: int
    evidence: tuple[Evidence, ...] = ()
    discarded: tuple[Discarded, ...] = ()
    failures: tuple[Failure, ...] = ()
    calls: tuple[CallRecord, ...] = ()
    #: Counts computed from the evidence — a verb distribution, a per-module tally. Arithmetic.
    summary: Mapping[str, Any] = field(default_factory=dict)
    #: Tier C only: the question a reviewer is asked, assembled from those counts. Never answered.
    question: str | None = None
    not_examined_because: str | None = None

    @property
    def complete(self) -> bool:
        """True when every passage sent was answered. False is not a result, it is a gap."""
        return self.state is ResultState.EXAMINED and self.answered == self.passages

    @property
    def found_nothing(self) -> bool:
        """True only for the one outcome that may be worded "no evidence found"."""
        return (
            self.state is ResultState.EXAMINED
            and self.complete
            and self.passages > 0
            and not self.evidence
        )

    @property
    def latency_ms(self) -> float:
        return round(sum(call.latency_ms or 0.0 for call in self.calls), 2)

    @property
    def tokens(self) -> int:
        return sum((call.prompt_tokens or 0) + (call.completion_tokens or 0) for call in self.calls)

    def statement(self) -> str:
        """One declarative line of counts. The four outcomes of the module docstring, kept apart."""
        if self.state is ResultState.NOT_EXAMINED:
            because = self.not_examined_because or ""
            return f"{self.extractor}: nothing was examined. {because}".strip()
        if self.passages == 0:
            return (
                f"{self.extractor}: examined {self.examined} artifact(s); none carried any text, "
                "so nothing was sent to the model."
            )
        parts = [f"{self.extractor}: examined {self.examined} artifact(s)"]
        parts.append(f"sent {self.passages} passage(s)")
        if self.evidence:
            parts.append(f"verified {len(self.evidence)} quote(s)")
        elif self.complete:
            parts.append("no evidence found")
        else:
            parts.append("no evidence was verified")
        if self.discarded:
            parts.append(f"discarded {len(self.discarded)} unverifiable quote(s)")
        if self.failures:
            parts.append(f"{len(self.failures)} passage(s) could not be read")
        return ", ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "extractor": self.extractor,
            "title": self.title,
            "tier": self.tier.value,
            "state": self.state.value,
            "examined": self.examined,
            "passages": self.passages,
            "answered": self.answered,
            "complete": self.complete,
            "statement": self.statement(),
            "evidence": [item.to_dict() for item in self.evidence],
            "discarded": [item.to_dict() for item in self.discarded],
            "failures": [item.to_dict() for item in self.failures],
            "calls": [call.to_dict() for call in self.calls],
        }
        if self.summary:
            payload["summary"] = dict(self.summary)
        if self.question is not None:
            payload["question"] = self.question
        if self.not_examined_because is not None:
            payload["not_examined_because"] = self.not_examined_because
        return payload


class Corpus:
    """The only way an extractor reaches the course, and therefore the only place counting happens.

    The same structural rule as :class:`sightline.detect.base.Survey`: an artifact cannot be
    obtained without being counted for it. An extractor never touches the
    :class:`~sightline.model.CourseDocument`.
    """

    def __init__(
        self,
        document: CourseDocument,
        extractor: Extractor,
        *,
        budget: int = WORD_BUDGET,
    ) -> None:
        self._document = document
        self._extractor = extractor
        self._budget = budget
        self._examined: set[str] = set()

    @property
    def examined(self) -> int:
        return len(self._examined)

    def passages(self) -> Iterator[Passage]:
        """Every passage of every artifact this extractor reads, in the document's own order."""
        wanted = set(self._extractor.kinds)
        for artifact in self._document.bodies():
            if artifact.kind not in wanted:
                continue
            self._examined.add(artifact.id)
            yield from passages_for(artifact, budget=self._budget)

    def location(self, artifact: Artifact, *, within: str | None, line: int | None) -> Location:
        return Location(
            artifact_id=artifact.id,
            artifact_kind=artifact.kind,
            title=artifact.title,
            canvas_path=artifact.canvas_path,
            link_exact=artifact.link_exact,
            within=within,
            line=line,
        )

    def examinations(self) -> tuple[Examination, ...]:
        found = []
        for name in self._extractor.collections:
            entry = self._document.examination(name)
            if entry is not None:
                found.append(entry)
        return tuple(found)

    def explained_empty(self) -> str | None:
        """Acquisition's explanation for a zero count, or ``None`` when nothing explains it.

        The same three cases as :meth:`sightline.detect.base.Detector._explained_empty`, and the
        same refusal: a collection acquisition read successfully, paired with an extractor that
        opened none of it, explains nothing and must not be reported as an absence.
        """
        records = self.examinations()
        if not records:
            return None
        unusable = [entry for entry in records if not entry.usable]
        if unusable:
            entry = unusable[0]
            return (
                f"The {entry.collection} collection was not readable ({entry.state.value}): "
                f"{entry.detail}"
            )
        if any(entry.state is not ExaminationState.EMPTY for entry in records):
            return None
        names = ", ".join(entry.collection for entry in records)
        return f"The course holds nothing in {names}, so there was nothing to examine."


class Extractor:
    """Base class for the model-backed extractors. A subclass declares; it does not orchestrate.

    Five declarations and at most three small hooks. The loop — passage, call, parse, verify,
    discard, record — lives in :mod:`sightline.extract.runner` and is the same for every extractor,
    because it is the loop the quote verification sits in and there must be exactly one of it.
    """

    #: ``E01``. Stable; everything downstream keys on it.
    id: ClassVar[str] = ""
    #: What this extractor looks for, in a sentence a non-programmer can check.
    title: ClassVar[str] = ""
    tier: ClassVar[Tier] = Tier.B
    #: Acquisition collections read, for explaining a zero count. Derived from :attr:`kinds` at
    #: class-definition time unless declared, because a subclass that narrows its kinds and forgets
    #: its collections would explain a zero count with a record about a collection it never reads.
    collections: ClassVar[tuple[str, ...]] = ()
    #: Artifact kinds sent to the model.
    kinds: ClassVar[tuple[ArtifactKind, ...]] = BODY_KINDS
    #: The extraction question, in the words that reach the prompt. One near-objective question:
    #: "find every sentence that…". Never "assess", never "how well", never "rate".
    ask: ClassVar[str] = ""

    def __init_subclass__(cls, *, abstract: bool = False, register: bool = True, **kw: Any) -> None:
        super().__init_subclass__(**kw)
        if abstract:
            return
        if not cls.__dict__.get("collections") and cls.kinds:
            cls.collections = tuple(
                dict.fromkeys(COLLECTION_OF[kind] for kind in cls.kinds if kind in COLLECTION_OF)
            )
        for attribute in ("id", "title", "collections", "kinds", "ask"):
            if not getattr(cls, attribute, None):
                raise TypeError(f"{cls.__name__} must declare {attribute}.")
        if cls.tier in (Tier.A, Tier.D):
            raise TypeError(
                f"{cls.__name__} declares tier {cls.tier.value}. Extraction is tier B or C: tier A "
                "is computed by a parser and tier D is not in the artifacts at all, and asking a "
                "model about a tier D criterion is how a fabrication gets in."
            )
        if register:
            _register(cls)

    # -- hooks --------------------------------------------------------------------------------

    def annotate(self, quote: str, passage: Passage) -> Mapping[str, Any]:
        """Anything computed from a verified quote, in code. Default: nothing."""
        return {}

    def summarise(self, evidence: Sequence[Evidence]) -> Mapping[str, Any]:
        """Counts over the verified evidence. Arithmetic only. Default: nothing."""
        return {}

    def question_for(self, evidence: Sequence[Evidence], summary: Mapping[str, Any]) -> str | None:
        """The question a Tier C row puts to a reviewer, assembled from the counts.

        It ends in a question mark and proposes nothing. A Tier C extractor that returned a
        statement about the course would be issuing the verdict the tier exists to withhold.
        """
        return None

    # -- the one thing a subclass may not do --------------------------------------------------

    def accept(self, finding: RawFinding, passage: Passage) -> Match | None:
        """Verify one returned quote against the passage. Final by convention and by test.

        It is here rather than in the runner only so that a subclass overriding it shows up in a
        diff as what it is: the one line standing between a model and a fabricated quote.
        """
        return verify(finding.quote, passage.text, normalized_source=passage.normalized)


def prompt_for(
    extractor: Extractor, passage: Passage, *, ask: str | None = None
) -> list[dict[str, str]]:
    """The messages for one passage. The rules are stated once, here, for every extractor.

    Three things every prompt says, because each of them is a failure mode measured or recorded:
    quote exactly (a paraphrase is discarded by verification and the call is wasted), return an
    empty list when there is nothing (abstention has to be as easy as a finding), and do not rate
    anything (there is no field for it, and a model that tries produces a schema violation).
    """
    kind = passage.artifact.kind.value.replace("_", " ")
    header = f"{passage.label} — a course {kind}"
    if passage.artifact.module_path:
        header += f", in {' › '.join(passage.artifact.module_path)}"
    system = (
        "You locate passages inside one document. You never summarise, never rate, never "
        "evaluate, never explain, and never write prose. You answer with JSON and nothing else."
    )
    user = (
        f"Document: {header}\n\n"
        "---\n"
        f"{passage.text}\n"
        "---\n\n"
        f"{ask or extractor.ask}\n\n"
        "Rules:\n"
        "- Quote exactly as written above, character for character. Do not paraphrase, do not "
        "shorten, do not join two separate sentences together, do not correct anything.\n"
        "- Only quote text that appears in the document above.\n"
        "- Include a passage only if the passage itself says what is asked for. A passage that "
        "mentions the subject without saying it is not one of these.\n"
        '- Give each quote a short "location" naming where in this document you found it.\n'
        "- Most documents contain none of this. If this one contains none, return "
        '{"findings": []}. An empty list is a complete and expected answer.\n'
        "- Do not rate, score or judge anything. There is no field for it."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------------------------------------------------------------------------------------
# Registry — the same shape as the detector registry, for the same reason: defining an extractor
# registers it, so there is no second list to forget.
# ---------------------------------------------------------------------------------------------

_REGISTRY: dict[str, type[Extractor]] = {}


def _register(cls: type[Extractor]) -> type[Extractor]:
    existing = _REGISTRY.get(cls.id)
    if existing is not None and existing is not cls and existing.__qualname__ != cls.__qualname__:
        raise TypeError(
            f"two extractors claim the id {cls.id}: {existing.__module__}.{existing.__qualname__} "
            f"and {cls.__module__}.{cls.__qualname__}. Ids are what evidence is keyed on."
        )
    _REGISTRY[cls.id] = cls
    return cls


@contextmanager
def registry_snapshot() -> Iterator[None]:
    """Put the registry back on the way out, for anything defining an extractor for a moment."""
    saved = dict(_REGISTRY)
    try:
        yield
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)


def registry() -> Mapping[str, type[Extractor]]:
    return dict(sorted(_REGISTRY.items()))


def extractors() -> tuple[Extractor, ...]:
    """One instance of every registered extractor, in id order."""
    return tuple(cls() for cls in registry().values())


__all__ = [
    "BODY_KINDS",
    "COLLECTION_OF",
    "DISCARD_EXCERPT",
    "CallRecord",
    "Corpus",
    "Discarded",
    "Evidence",
    "Extractor",
    "ExtractorResult",
    "Failure",
    "FailureKind",
    "extractors",
    "prompt_for",
    "registry",
    "registry_snapshot",
]
