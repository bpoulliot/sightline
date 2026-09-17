"""Three runs of the same extraction, compared on the text they found, and nothing else.

WHAT THIS IS, AND THE THING IT IS NOT
-------------------------------------

The extraction layer asks a model where a passage is. Ask it three times and the three answers
differ — measured on the target host in item 09, where two identical runs at ``temperature: 0``
over the same course returned eighteen verified quotes and then sixteen. This module runs each
extraction three times and reports where the runs did not return the same text.

**It never votes and it never averages.** Majority voting works on maths and code because those
have a ground-truth attractor: correct answers concentrate and wrong ones scatter. Rubric judgement
has no attractor, so the mode of N samples recovers the *model's prior* rather than the right
answer — and a mildly agreeable model returns three confident agreements on a weak discussion
prompt with no variance at all, manufacturing high confidence in a wrong call. That is strictly
worse than one uncertain call (SPEC §01). Verbalized self-confidence is worse still.

So variance is used here as a **triage signal and never as a truth signal**:

*Disagreement is information.* The runs differed about what the course says, a person should look,
and the subject is marked *needs attention*.

*Agreement is not information.* Three runs returning the same spans says the model is stable, which
is not the same as being right — the zero-variance failure above is exactly that case. A subject
whose runs agree is therefore **not marked at all**. It carries no adjective, no marker and no
count of how many runs returned each span; what it gets is what one run would have produced, plus
the arithmetic of how many runs were made.

HOW THE RUNS ARE COMPARED
--------------------------

On the **extracted spans**, after normalization. Not on prose, not on any rating, not on a count of
findings. A span's identity is *where in the course it sits* — the artifact, and the position the
harness computed for it — plus its text folded through :func:`sightline.extract.verify.normalize`,
the same fold the quote check already uses, which maps two spellings of the same characters
together and cannot turn one word into another. :func:`span_key` says why the position belongs in
the identity rather than only the text; the short version is that one sentence can appear twice in
one artifact, and keying on the text alone would drop the second verified quote from the union.

It is worth being plain about the limit of that rule: two runs that quote *the same sentence with
different boundaries* — one including the trailing clause, one not — are two different spans and
the subject is flagged. That is the conservative direction and it is deliberate. The dominant
observed variance is whole findings appearing and disappearing between runs, not boundary jitter,
and a router that quietly merged near-misses would be doing approximate matching in the one place
this project refuses it.

Two further differences between runs are also disagreements, and they are kept apart from span
disagreement rather than folded into it:

*A passage read in one run and not in another.* A schema violation or a host error belongs to one
passage, and a run that could not read a page has a smaller view of the course than a run that
could. A passage **every** run failed is not a disagreement — it is an incomplete result, which
:attr:`sightline.extract.base.ExtractorResult.complete` already reports.

*A run that examined the course paired with one that did not.* It should not be reachable, since
what gets examined is computed from the document rather than from the model. It is compared anyway,
because the failure it would cause — reporting agreement between a run that looked and a run that
did not — is exactly the one this project keeps guarding against.

**Discards are not compared**, and that is a decision rather than an omission. A quote the source
does not contain never becomes evidence, so a run that fabricated one and a run that did not
returned the same spans. The discard itself is recorded on its own run and logged where item 10
already put it, so nothing is lost by declining to call it a disagreement about the course.

WHAT DOWNSTREAM GETS, AND WHY IT IS THE UNION
----------------------------------------------

Three runs produce three evidence sets and a reviewer needs one. Three ways to build it:

*Intersection* — keep what every run found. That is agreement used as a filter, which is variance
used as a truth signal, which is the thing this module exists not to do. Rejected outright.

*One designated run* — arbitrary, and it throws away verified quotes for no reason.

*Union* — kept. Every span in it was checked against the source document before it was accepted
(:mod:`sightline.extract.verify`), so a span only one run returned is a passage that demonstrably
exists in the course, not a candidate awaiting confirmation. Dropping it would be the silent
under-detection SPEC §11 names, and keeping it lets nothing select on how many runs agreed.

**How many runs returned a span is recorded only inside the disagreement record**, where a reviewer
resolving the difference needs it. It is never attached to an accepted span, never a weight, never a
threshold, and nothing anywhere compares it to a number other than "was it every run". A field
holding "2 of 3" beside a quote would become a confidence score within a release.

WHAT IS RECORDED FOR ITEM 18
-----------------------------

:class:`DisagreementSet` is the subset an escalation adapter re-runs: per flagged subject, the
extractor id or the full rubric-row request, and **exactly the artifacts implicated in the
difference** rather than the whole course. It round-trips through ``to_dict``/``from_dict`` so the
requests can be rebuilt from a file written days earlier. The adapter itself is roadmap item 18 and
is blocked; nothing here calls one.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..detect.base import Tier
from ..llm.client import ModelCard, OllamaClient, Thinking
from ..logging import get_logger
from ..model import CourseDocument
from ..rubric.routing import ExtractionRequest
from .base import Evidence, Extractor, ExtractorResult
from .base import extractors as registered_extractors
from .passages import WORD_BUDGET
from .runner import RowExtractor, run_extractor, thinking_for
from .verify import normalize

log = get_logger(__name__)

#: Runs per extraction. Three is the figure SPEC §06 and roadmap item 11 name. It is a parameter
#: rather than a constant at the call sites so the measured cost of changing it is one argument.
RUNS = 3

#: Version of the recorded disagreement set. Written into every file so that item 18 reads a
#: document whose shape it can check rather than one it has to guess at.
DISAGREEMENT_FORMAT = 1

#: Filename the disagreement set is written under, inside a run directory of the data root. Named
#: here rather than at the one call site so the reader of it — item 18 — imports the name.
DISAGREEMENTS_FILENAME = "disagreements.json"

#: Carried in every attention payload. A renderer that shows the flag shows this beside it, because
#: the flag is read as "the machine is unsure here" by default and that reading is wrong in a way
#: that matters: nothing here knows which run was right, or whether any of them was.
ATTENTION_NOTE = (
    "Repeated runs of the same question over the same text did not come out the same. That is a "
    "reason for a person to look at this row. It is not a statement about which run is right, and "
    "no rating is derived from it."
)


#: The identity of one extracted span. See :func:`span_key`.
SpanKey = tuple[str, str, int | None, str]


def span_key(evidence: Evidence) -> SpanKey:
    """The identity of one extracted span: where in the course it sits, and its text normalized.

    Normalizing with the quote checker's own fold is what makes the text half safe to compare: it
    maps two spellings of the same characters together and cannot map two different words
    together. See :mod:`sightline.extract.verify`.

    **The place is part of the identity, and it has to be.** One sentence can appear twice in one
    artifact — a policy restated under two headings is ordinary in a syllabus — and extraction
    verifies and locates each occurrence separately, so one run legitimately returns two quotes of
    the same text at two locations. Keyed on the text alone, the union would keep the first and
    drop the second: a verified quote, with a location a reviewer could have opened, silently gone.
    That is the under-detection this module refuses in the paragraph above, arriving through the
    comparison instead of through a filter.

    The place is safe to key on because **the harness computes it, not the model**: ``within`` and
    ``line`` come from where the quote was found in the document (:mod:`sightline.extract.passages`
    — the model's own account of where it looked is kept apart in ``stated_location`` and never
    read here). Two runs that return the same sentence therefore land on the same key. Two runs
    that return it from different places did report different things about the course, and the
    subject is flagged, which is the conservative direction this module takes everywhere.
    """
    location = evidence.location
    return (
        location.artifact_id,
        location.within or "",
        location.line,
        normalize(evidence.quote).text,
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class Span:
    """One extracted span, and what each run did about it.

    Three outcomes per run, exhaustive and kept apart, because two of them are the difference
    between a disagreement and an absence of observation: a run that read the artifact and did not
    return this span **disagrees**; a run that could not read the artifact **did not look**, and
    counting that as a disagreement would report one failure twice and as the wrong thing.

    ``returned_by``, ``missing_from`` and ``unread_by`` are serialized only where this is rendered
    — inside a disagreement record. An accepted span carries none of them: see the module
    docstring.
    """

    evidence: Evidence
    #: 1-based run numbers that returned this span, ascending.
    returned_by: tuple[int, ...]
    #: Runs that read the artifact and did not return it. Empty when nothing disagreed.
    missing_from: tuple[int, ...]
    #: Runs that could not read the artifact this span is in, so they never had the chance.
    unread_by: tuple[int, ...] = ()

    @property
    def key(self) -> SpanKey:
        return span_key(self.evidence)

    @property
    def contested(self) -> bool:
        """True only for a genuine difference in what was found, never for a passage nobody read."""
        return bool(self.missing_from)

    def to_dict(self) -> dict[str, Any]:
        payload = self.evidence.to_dict()
        payload["returned_by_runs"] = list(self.returned_by)
        payload["not_returned_by_runs"] = list(self.missing_from)
        if self.unread_by:
            payload["artifact_unread_by_runs"] = list(self.unread_by)
        return payload


@dataclass(frozen=True, kw_only=True, slots=True)
class UnreadPassage:
    """One passage read in some runs and not in others. Never a passage every run failed."""

    passage: str
    artifact_id: str
    read_by: tuple[int, ...]
    unread_by: tuple[int, ...]
    #: Why each failing run could not read it, in run order. The reasons extraction already
    #: records — a schema violation or a host error — never reworded as an absence of evidence.
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passage": self.passage,
            "artifact_id": self.artifact_id,
            "read_by_runs": list(self.read_by),
            "unread_by_runs": list(self.unread_by),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class Attention:
    """The *needs attention* marker. It exists only when the runs differed.

    There is deliberately no counterpart for the agreeing case. A class named ``Confidence`` or a
    field reading ``agreed: true`` is the first step to a threshold somebody sorts on, and the
    whole point of a router is that it has nothing to sort.
    """

    subject: str
    criterion: str | None
    contested: tuple[Span, ...]
    unread: tuple[UnreadPassage, ...]
    #: The distinct result states across the runs, when they were not all the same.
    states: tuple[str, ...]

    def reasons(self) -> tuple[str, ...]:
        out: list[str] = []
        if self.contested:
            out.append(
                f"{len(self.contested)} span(s) were returned by some runs and not by others."
            )
        if self.unread:
            out.append(
                f"{len(self.unread)} passage(s) were read in some runs and not in others, so the "
                "runs did not see the same course text."
            )
        if self.states:
            out.append("The runs did not all examine the course: " + ", ".join(self.states) + ".")
        return tuple(out)

    def statement(self) -> str:
        return " ".join(self.reasons())

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "subject": self.subject,
            "reasons": list(self.reasons()),
            "statement": self.statement(),
            "note": ATTENTION_NOTE,
            "contested_spans": [span.to_dict() for span in self.contested],
            "unread_passages": [item.to_dict() for item in self.unread],
        }
        if self.criterion is not None:
            payload["criterion"] = self.criterion
        if self.states:
            payload["states"] = list(self.states)
        return payload


@dataclass(frozen=True, kw_only=True, slots=True)
class Comparison:
    """What several runs of one extraction subject found, compared span by span.

    The subject is one of the ten topic extractors or one routed rubric row. Exactly one of
    :attr:`extractor_id` and :attr:`request` is set, so that a flagged subject can always be
    re-run without guessing which kind of thing it was.
    """

    subject: str
    title: str
    tier: Tier
    #: Every run, whole and unmerged. Nothing is discarded by comparing.
    runs: tuple[ExtractorResult, ...]
    #: The union of the runs' evidence, first appearance winning, in run-then-document order.
    spans: tuple[Span, ...]
    #: Counts over the spans above, recomputed rather than taken from a run. See
    #: :func:`_arithmetic_over`.
    summary: Mapping[str, Any] = field(default_factory=dict)
    #: Tier C only: the question a reviewer is asked, assembled from those counts. Never answered.
    question: str | None = None
    criterion: str | None = None
    section: str | None = None
    extractor_id: str | None = None
    request: ExtractionRequest | None = None

    def __post_init__(self) -> None:
        if (self.extractor_id is None) == (self.request is None):
            raise ValueError(
                "a comparison is of one topic extractor or of one routed rubric row, and it has "
                "to say which: item 18 re-runs the subset from this record and cannot rebuild a "
                "request it was not given."
            )

    @property
    def run_count(self) -> int:
        return len(self.runs)

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        """The union. Every span here was verified against the source before it was accepted."""
        return tuple(span.evidence for span in self.spans)

    @property
    def contested(self) -> tuple[Span, ...]:
        return tuple(span for span in self.spans if span.contested)

    @property
    def unread(self) -> tuple[UnreadPassage, ...]:
        return _unread_passages(self.runs)

    @property
    def divergent_states(self) -> tuple[str, ...]:
        states = sorted({result.state.value for result in self.runs})
        return tuple(states) if len(states) > 1 else ()

    @property
    def needs_attention(self) -> bool:
        return bool(self.contested or self.unread or self.divergent_states)

    @property
    def attention(self) -> Attention | None:
        """The marker, or ``None``. ``None`` is the whole of what an agreeing subject gets."""
        if not self.needs_attention:
            return None
        return Attention(
            subject=self.subject,
            criterion=self.criterion,
            contested=self.contested,
            unread=self.unread,
            states=self.divergent_states,
        )

    @property
    def artifact_ids(self) -> tuple[str, ...]:
        """Exactly the artifacts the runs differed over. What item 18 re-runs, not the course."""
        out: list[str] = []
        for span in self.contested:
            if span.evidence.location.artifact_id not in out:
                out.append(span.evidence.location.artifact_id)
        for item in self.unread:
            if item.artifact_id not in out:
                out.append(item.artifact_id)
        return tuple(out)

    @property
    def calls(self) -> int:
        return sum(len(result.calls) for result in self.runs)

    @property
    def tokens(self) -> int:
        return sum(result.tokens for result in self.runs)

    @property
    def latency_ms(self) -> float:
        return round(sum(result.latency_ms for result in self.runs), 2)

    def statement(self) -> str:
        """One line of counts, and the disagreement when there is one. Never the absence of one."""
        examined = self.runs[0].examined if self.runs else 0
        parts = [
            f"{self.subject}: {self.run_count} run(s) over {examined} artifact(s)",
            f"{self.calls} model call(s)",
            f"{len(self.spans)} span(s) found",
        ]
        head = ", ".join(parts)
        attention = self.attention
        if attention is None:
            return head + "."
        return f"{head}. Needs attention: {attention.statement()}"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "subject": self.subject,
            "title": self.title,
            "tier": self.tier.value,
            "runs": self.run_count,
            "calls": self.calls,
            "statement": self.statement(),
            "evidence": [item.to_dict() for item in self.evidence],
        }
        if self.summary:
            payload["summary"] = dict(self.summary)
        if self.question is not None:
            payload["question"] = self.question
        if self.criterion is not None:
            payload["criterion"] = self.criterion
        if self.section is not None:
            payload["section"] = self.section
        attention = self.attention
        if attention is not None:
            payload["needs_attention"] = attention.to_dict()
        return payload


@dataclass(frozen=True, kw_only=True, slots=True)
class DisagreementEntry:
    """One flagged subject, with enough to re-run exactly it and nothing else."""

    subject: str
    title: str
    tier: Tier
    reasons: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    criterion: str | None = None
    extractor_id: str | None = None
    request: ExtractionRequest | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "subject": self.subject,
            "title": self.title,
            "tier": self.tier.value,
            "reasons": list(self.reasons),
            "artifact_ids": list(self.artifact_ids),
        }
        if self.criterion is not None:
            payload["criterion"] = self.criterion
        if self.extractor_id is not None:
            payload["extractor"] = self.extractor_id
        if self.request is not None:
            payload["request"] = self.request.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DisagreementEntry:
        request = payload.get("request")
        return cls(
            subject=str(payload["subject"]),
            title=str(payload.get("title", "")),
            tier=Tier(payload["tier"]),
            reasons=tuple(payload.get("reasons", ())),
            artifact_ids=tuple(payload.get("artifact_ids", ())),
            criterion=payload.get("criterion"),
            extractor_id=payload.get("extractor"),
            request=ExtractionRequest.from_dict(request) if request is not None else None,
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class DisagreementSet:
    """The subset item 18 re-runs, as a document rather than as a live object.

    It is written and read rather than held, because the escalation it feeds happens later, on a
    different host, possibly on a different day. ``to_dict`` and ``from_dict`` are inverse and the
    suite asserts it: a record that cannot be read back is a record of nothing.
    """

    model: str
    runs: int
    entries: tuple[DisagreementEntry, ...]
    course_id: str | None = None

    def statement(self) -> str:
        rows = sum(1 for entry in self.entries if entry.request is not None)
        topics = len(self.entries) - rows
        parts = [f"{len(self.entries)} subject(s) flagged for attention over {self.runs} run(s)"]
        if rows:
            parts.append(f"{rows} rubric row(s)")
        if topics:
            parts.append(f"{topics} extractor(s)")
        return "; ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "format": DISAGREEMENT_FORMAT,
            "model": self.model,
            "runs": self.runs,
            "statement": self.statement(),
            "entries": [entry.to_dict() for entry in self.entries],
        }
        if self.course_id is not None:
            payload["course_id"] = self.course_id
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DisagreementSet:
        version = payload.get("format")
        if version != DISAGREEMENT_FORMAT:
            raise ValueError(
                f"disagreement set format {version!r}, expected {DISAGREEMENT_FORMAT}. "
                "Reading a record of an unknown shape is how a subset silently becomes the wrong "
                "subset."
            )
        return cls(
            model=str(payload["model"]),
            runs=int(payload["runs"]),
            entries=tuple(DisagreementEntry.from_dict(entry) for entry in payload["entries"]),
            course_id=payload.get("course_id"),
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class RouterReport:
    """Every subject routed over one course, and what the pass cost in wall-clock time."""

    comparisons: tuple[Comparison, ...]
    model: str
    runs: int
    #: Wall clock for the whole pass, at concurrency 1. The figure that decides whether three is
    #: the right number of runs, so it is measured rather than summed from per-call latencies.
    elapsed_ms: float
    thinking_pinned: bool | None = None
    course_id: str | None = None

    @property
    def flagged(self) -> tuple[Comparison, ...]:
        return tuple(item for item in self.comparisons if item.needs_attention)

    @property
    def calls(self) -> int:
        return sum(item.calls for item in self.comparisons)

    @property
    def tokens(self) -> int:
        return sum(item.tokens for item in self.comparisons)

    def by_subject(self, subject: str) -> Comparison | None:
        for item in self.comparisons:
            if item.subject == subject:
                return item
        return None

    def disagreements(self) -> DisagreementSet:
        return DisagreementSet(
            model=self.model,
            runs=self.runs,
            course_id=self.course_id,
            entries=tuple(
                DisagreementEntry(
                    subject=item.subject,
                    title=item.title,
                    tier=item.tier,
                    reasons=item.attention.reasons() if item.attention else (),
                    artifact_ids=item.artifact_ids,
                    criterion=item.criterion,
                    extractor_id=item.extractor_id,
                    request=item.request,
                )
                for item in self.flagged
            ),
        )

    def statement(self) -> str:
        return (
            f"{len(self.comparisons)} subject(s) run {self.runs} time(s) each on {self.model}; "
            f"{self.calls} model call(s) in {self.elapsed_ms / 1000:.1f} s; "
            f"{len(self.flagged)} subject(s) need attention."
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "statement": self.statement(),
            "model": self.model,
            "runs": self.runs,
            "calls": self.calls,
            "tokens": self.tokens,
            "elapsed_ms": self.elapsed_ms,
            "thinking_pinned": self.thinking_pinned,
            "comparisons": [item.to_dict() for item in self.comparisons],
            "disagreements": self.disagreements().to_dict(),
        }
        if self.course_id is not None:
            payload["course_id"] = self.course_id
        return payload


# ---------------------------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------------------------


async def route_extractor(
    extractor: Extractor,
    document: CourseDocument,
    client: OllamaClient,
    card: ModelCard,
    *,
    thinking: Thinking,
    runs: int = RUNS,
    budget: int = WORD_BUDGET,
    require_usable: bool = True,
    ask: str | None = None,
    criterion: str | None = None,
    section: str | None = None,
    request: ExtractionRequest | None = None,
) -> Comparison:
    """Run one extractor several times over one course and compare what came back.

    The runs are sequential because concurrency is 1 by design (SPEC §06), and they are identical:
    same model, same prompt, same pin. A difference between two of them is therefore a difference
    the host produced, never a difference between two questions.
    """
    _check_runs(runs)
    results: list[ExtractorResult] = []
    for index in range(1, runs + 1):
        result = await run_extractor(
            extractor,
            document,
            client,
            card,
            thinking=thinking,
            budget=budget,
            require_usable=require_usable,
            ask=ask,
        )
        log.info(
            "router_run_finished",
            subject=criterion or extractor.id,
            run=index,
            of=runs,
            model=card.name,
            verified=len(result.evidence),
            discarded=len(result.discarded),
            failures=len(result.failures),
            latency_ms=result.latency_ms,
        )
        results.append(result)

    spans = _union(results)
    summary, question = _arithmetic_over(extractor, tuple(span.evidence for span in spans))
    comparison = Comparison(
        subject=criterion or extractor.id,
        title=extractor.title,
        tier=extractor.tier,
        runs=tuple(results),
        spans=spans,
        summary=summary,
        question=question,
        criterion=criterion,
        section=section,
        extractor_id=None if request is not None else extractor.id,
        request=request,
    )
    log.info(
        "router_compared",
        subject=comparison.subject,
        runs=comparison.run_count,
        spans=len(comparison.spans),
        contested=len(comparison.contested),
        unread=len(comparison.unread),
        needs_attention=comparison.needs_attention,
    )
    return comparison


async def route_extractors(
    document: CourseDocument,
    client: OllamaClient,
    model: ModelCard | str,
    *,
    only: Sequence[str] | None = None,
    runs: int = RUNS,
    budget: int = WORD_BUDGET,
    require_usable: bool = True,
) -> RouterReport:
    """Every registered extractor, each run ``runs`` times, compared.

    Cost is ``runs`` times a single pass and it is not amortized: concurrency is 1, so the wall
    clock is the sum. :attr:`RouterReport.elapsed_ms` measures it rather than estimating it.
    """
    _check_runs(runs)
    card = model if isinstance(model, ModelCard) else await client.card(model)
    thinking = thinking_for(card)
    chosen = [
        extractor
        for extractor in registered_extractors()
        if only is None or extractor.id in set(only)
    ]
    started = time.perf_counter()
    comparisons = [
        await route_extractor(
            extractor,
            document,
            client,
            card,
            thinking=thinking,
            runs=runs,
            budget=budget,
            require_usable=require_usable,
        )
        for extractor in chosen
    ]
    return _report(comparisons, card, runs, started, document)


async def route_request(
    request: ExtractionRequest,
    document: CourseDocument,
    client: OllamaClient,
    model: ModelCard | str,
    *,
    runs: int = RUNS,
    budget: int = WORD_BUDGET,
    require_usable: bool = True,
) -> Comparison:
    """One routed rubric row, run several times and compared. The criterion-level flag."""
    _check_runs(runs)
    card = model if isinstance(model, ModelCard) else await client.card(model)
    extractor = RowExtractor(request)
    return await route_extractor(
        extractor,
        document,
        client,
        card,
        thinking=thinking_for(card),
        runs=runs,
        budget=budget,
        require_usable=require_usable,
        ask=extractor.ask,
        criterion=request.criterion,
        section=request.section,
        request=request,
    )


async def route_requests(
    requests: Iterable[ExtractionRequest],
    document: CourseDocument,
    client: OllamaClient,
    model: ModelCard | str,
    *,
    runs: int = RUNS,
    budget: int = WORD_BUDGET,
    require_usable: bool = True,
) -> RouterReport:
    """Every routed row, in the order the routing contract produced them."""
    _check_runs(runs)
    card = model if isinstance(model, ModelCard) else await client.card(model)
    started = time.perf_counter()
    comparisons = [
        await route_request(
            request,
            document,
            client,
            card,
            runs=runs,
            budget=budget,
            require_usable=require_usable,
        )
        for request in requests
    ]
    return _report(comparisons, card, runs, started, document)


# ---------------------------------------------------------------------------------------------


def _report(
    comparisons: Sequence[Comparison],
    card: ModelCard,
    runs: int,
    started: float,
    document: CourseDocument,
) -> RouterReport:
    report = RouterReport(
        comparisons=tuple(comparisons),
        model=card.name,
        runs=runs,
        elapsed_ms=round((time.perf_counter() - started) * 1000, 2),
        thinking_pinned=thinking_for(card) is Thinking.ON if card.supports_thinking else None,
        course_id=document.course.id,
    )
    log.info(
        "router_finished",
        model=report.model,
        runs=report.runs,
        subjects=len(report.comparisons),
        calls=report.calls,
        flagged=len(report.flagged),
        tokens=report.tokens,
        elapsed_ms=report.elapsed_ms,
    )
    return report


def _check_runs(runs: int) -> int:
    if runs < 2:
        raise ValueError(
            f"a router compares runs and was given {runs}. One run has nothing to disagree with, "
            "and a router that can never flag is indistinguishable from one switched off."
        )
    return runs


def _union(results: Sequence[ExtractorResult]) -> tuple[Span, ...]:
    """Every span any run returned, first appearance winning, with what each run did about it.

    The union rather than the intersection, for the reason in the module docstring: filtering by
    how many runs agreed is variance used as a truth signal.

    A run that could not read the artifact a span sits in is recorded as not having looked rather
    than as having disagreed. The granularity is the artifact rather than the passage, which is
    deliberate and conservative: an artifact long enough to be split reports a read failure
    against one of its parts, and claiming the other parts were read while a quote's part is
    unknown would be inventing precision.
    """
    everyone = tuple(range(1, len(results) + 1))
    unreadable: dict[int, set[str]] = {
        index: {failure.artifact_id for failure in result.failures}
        for index, result in enumerate(results, start=1)
    }
    first: dict[SpanKey, Evidence] = {}
    returned_by: dict[SpanKey, list[int]] = {}
    for index, result in enumerate(results, start=1):
        for item in result.evidence:
            key = span_key(item)
            if key not in first:
                first[key] = item
                returned_by[key] = []
            if index not in returned_by[key]:
                returned_by[key].append(index)

    spans: list[Span] = []
    for key, evidence in first.items():
        artifact = evidence.location.artifact_id
        returned = tuple(returned_by[key])
        silent = tuple(run for run in everyone if run not in returned)
        unread = tuple(run for run in silent if artifact in unreadable[run])
        spans.append(
            Span(
                evidence=evidence,
                returned_by=returned,
                missing_from=tuple(run for run in silent if run not in unread),
                unread_by=unread,
            )
        )
    return tuple(spans)


def _arithmetic_over(
    extractor: Extractor, evidence: Sequence[Evidence]
) -> tuple[Mapping[str, Any], str | None]:
    """The counts and the Tier C question, computed over the union rather than copied from a run.

    A single run's counts describe that run's evidence. What the router shows is the union, so
    taking run 1's summary would print a count beside a list of quotes it does not count — a
    machine-authored line a reviewer would falsify by counting the quotes under it, which is the
    one thing SPEC §01 says every line must survive. Both of these are pure functions of the
    evidence and neither goes near a model, so recomputing them is arithmetic and not a second
    opinion.

    The Tier C question is drafted and not answered, exactly as in a single pass: three runs of a
    pedagogical criterion are still three extractions and none of them is a rating.
    """
    summary = dict(extractor.summarise(evidence))
    question = extractor.question_for(evidence, summary) if extractor.tier is Tier.C else None
    return summary, question


def _unread_passages(results: Sequence[ExtractorResult]) -> tuple[UnreadPassage, ...]:
    """Passages some runs could not read. A passage every run failed is not a disagreement."""
    everyone = tuple(range(1, len(results) + 1))
    failed: dict[tuple[str, str], dict[int, str]] = {}
    for index, result in enumerate(results, start=1):
        for failure in result.failures:
            failed.setdefault((failure.artifact_id, failure.passage), {})[index] = failure.reason
    out: list[UnreadPassage] = []
    for (artifact_id, passage), reasons in failed.items():
        if len(reasons) >= len(results):
            continue
        out.append(
            UnreadPassage(
                passage=passage,
                artifact_id=artifact_id,
                read_by=tuple(run for run in everyone if run not in reasons),
                unread_by=tuple(sorted(reasons)),
                reasons=tuple(reasons[run] for run in sorted(reasons)),
            )
        )
    return tuple(out)


__all__ = [
    "ATTENTION_NOTE",
    "DISAGREEMENTS_FILENAME",
    "DISAGREEMENT_FORMAT",
    "RUNS",
    "Attention",
    "Comparison",
    "DisagreementEntry",
    "DisagreementSet",
    "RouterReport",
    "Span",
    "SpanKey",
    "UnreadPassage",
    "route_extractor",
    "route_extractors",
    "route_request",
    "route_requests",
    "span_key",
]
