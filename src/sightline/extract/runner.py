"""The one extraction loop: passage, call, parse, verify, record.

There is exactly one of this loop and every extractor goes through it, because it is the loop the
quote verification sits inside. An extractor that could run its own would be an extractor that
could skip the check (SPEC §11).

**The thinking pin is chosen here, from the measurement in SPEC §06.** On the target host,
``think: false`` does not suppress a thinking model's reasoning — it stops *separating* it, and the
reasoning arrives inside ``message.content`` ahead of the answer. Since what this module parses is
``content``, the pin goes **on** wherever the model reports the capability and is omitted entirely
where it does not. Both branches were measured; neither is a preference.

**Failures are per passage, except the ones that are not.** A schema violation or a server error
belongs to one passage and the run continues past it, recorded. A host that cannot be reached, a
model that is not installed, a model refused for memory or one that cannot take the thinking pin
are conditions of the *run*: every remaining call would fail the same way, slowly, and the useful
thing to do with 300-second timeouts is stop. Those propagate.

**Nothing here retries.** A retry on a schema violation is a second chance for the same model on
the same passage at the same temperature, which is a different answer to the same question — and
choosing between two answers to the same question is the redundancy SPEC §01 rules out. Roadmap
item 11 runs three times *as a router* and reports the disagreement instead of resolving it.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from ..detect.base import ResultState, Tier
from ..llm.client import ChatResult, ModelCard, OllamaClient, Thinking
from ..llm.errors import (
    ModelHostNotConfiguredError,
    ModelHostNotOllamaError,
    ModelHostOutOfMemoryError,
    ModelHostServerError,
    ModelHostUnreachableError,
    ModelNotFoundError,
    ModelNotUsableError,
    ModelResponseError,
    ModelThinkingUnsupportedError,
)
from ..logging import get_logger
from ..model import CourseDocument
from ..rubric.routing import ExtractionRequest
from .base import (
    BODY_KINDS,
    DISCARD_EXCERPT,
    CallRecord,
    Corpus,
    Discarded,
    Evidence,
    Extractor,
    ExtractorResult,
    Failure,
    FailureKind,
    prompt_for,
)
from .base import (
    extractors as registered_extractors,
)
from .passages import WORD_BUDGET, Passage
from .schema import RESPONSE_SCHEMA, SchemaViolation, parse_response

log = get_logger(__name__)

#: Failures that end the run rather than the passage. Every one of them would repeat identically
#: on every remaining call, and each carries its own remedy already.
FATAL = (
    ModelHostNotConfiguredError,
    ModelHostNotOllamaError,
    ModelHostOutOfMemoryError,
    ModelHostUnreachableError,
    ModelNotFoundError,
    ModelNotUsableError,
    ModelThinkingUnsupportedError,
)


def thinking_for(card: ModelCard) -> Thinking:
    """On where the model has the capability, off where it does not. See the module docstring."""
    return Thinking.ON if card.supports_thinking else Thinking.OFF


@dataclass(frozen=True, kw_only=True, slots=True)
class ExtractionReport:
    """Every extractor's result over one course, plus what the whole pass cost."""

    results: tuple[ExtractorResult, ...]
    model: str
    thinking_pinned: bool | None = None

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        return tuple(item for result in self.results for item in result.evidence)

    @property
    def discarded(self) -> tuple[Discarded, ...]:
        return tuple(item for result in self.results for item in result.discarded)

    @property
    def failures(self) -> tuple[Failure, ...]:
        return tuple(item for result in self.results for item in result.failures)

    @property
    def calls(self) -> tuple[CallRecord, ...]:
        return tuple(call for result in self.results for call in result.calls)

    @property
    def tokens(self) -> int:
        return sum(result.tokens for result in self.results)

    @property
    def latency_ms(self) -> float:
        return round(sum(result.latency_ms for result in self.results), 2)

    def by_extractor(self, extractor_id: str) -> ExtractorResult | None:
        for result in self.results:
            if result.extractor == extractor_id:
                return result
        return None

    def statement(self) -> str:
        ran = sum(1 for result in self.results if result.state is ResultState.EXAMINED)
        parts = [
            f"{ran} of {len(self.results)} extractor(s) examined the course",
            f"{len(self.calls)} model call(s) on {self.model}",
            f"{len(self.evidence)} quote(s) verified",
        ]
        if self.discarded:
            parts.append(f"{len(self.discarded)} discarded as unverifiable")
        if self.failures:
            parts.append(f"{len(self.failures)} passage(s) could not be read")
        return "; ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement(),
            "model": self.model,
            "thinking_pinned": self.thinking_pinned,
            "results": [result.to_dict() for result in self.results],
        }


async def run_extractors(
    document: CourseDocument,
    client: OllamaClient,
    model: ModelCard | str,
    *,
    only: Sequence[str] | None = None,
    budget: int = WORD_BUDGET,
    require_usable: bool = True,
) -> ExtractionReport:
    """Run every registered extractor over one course.

    ``only`` narrows to a list of extractor ids. The model is resolved once, because resolving it
    per extractor costs an ``/api/show`` each time and the thinking pin depends on it.
    """
    card = model if isinstance(model, ModelCard) else await client.card(model)
    thinking = thinking_for(card)
    chosen = [
        extractor
        for extractor in registered_extractors()
        if only is None or extractor.id in set(only)
    ]
    results = []
    for extractor in chosen:
        results.append(
            await run_extractor(
                extractor,
                document,
                client,
                card,
                thinking=thinking,
                budget=budget,
                require_usable=require_usable,
            )
        )
    report = ExtractionReport(
        results=tuple(results),
        model=card.name,
        thinking_pinned=thinking is Thinking.ON if card.supports_thinking else None,
    )
    log.info(
        "extraction_finished",
        model=card.name,
        extractors=len(report.results),
        calls=len(report.calls),
        verified=len(report.evidence),
        discarded=len(report.discarded),
        failures=len(report.failures),
        tokens=report.tokens,
        latency_ms=report.latency_ms,
    )
    return report


async def run_extractor(
    extractor: Extractor,
    document: CourseDocument,
    client: OllamaClient,
    card: ModelCard,
    *,
    thinking: Thinking,
    budget: int = WORD_BUDGET,
    require_usable: bool = True,
    ask: str | None = None,
) -> ExtractorResult:
    """One extractor over one course. The loop the module docstring describes."""
    corpus = Corpus(document, extractor, budget=budget)
    evidence: list[Evidence] = []
    discarded: list[Discarded] = []
    failures: list[Failure] = []
    calls: list[CallRecord] = []
    answered = 0
    sent = 0

    for passage in corpus.passages():
        sent += 1
        started = time.perf_counter()
        try:
            completion = await client.chat(
                card,
                prompt_for(extractor, passage, ask=ask),
                thinking=thinking,
                operation=f"extract.{extractor.id}",
                response_format=RESPONSE_SCHEMA,
                require_usable=require_usable,
            )
        except FATAL:
            raise
        except (ModelHostServerError, ModelResponseError) as exc:
            calls.append(_failed_call(extractor, passage, card, started, reason=exc.summary))
            failures.append(
                Failure(
                    extractor=extractor.id,
                    passage=passage.label,
                    artifact_id=passage.artifact.id,
                    kind=FailureKind.HOST,
                    reason=exc.summary,
                    detail=exc.detail,
                )
            )
            _log_failure(extractor, passage, card, FailureKind.HOST, exc.summary)
            continue

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        try:
            returned = parse_response(completion.content)
        except SchemaViolation as violation:
            calls.append(
                _call_record(extractor, passage, card, completion, latency_ms, error=str(violation))
            )
            failures.append(
                Failure(
                    extractor=extractor.id,
                    passage=passage.label,
                    artifact_id=passage.artifact.id,
                    kind=FailureKind.SCHEMA,
                    reason=violation.reason,
                    detail=violation.path,
                )
            )
            _log_failure(extractor, passage, card, FailureKind.SCHEMA, violation.reason)
            continue

        answered += 1
        accepted = 0
        for finding in returned:
            match = extractor.accept(finding, passage)
            if match is None:
                discarded.append(
                    Discarded(
                        extractor=extractor.id,
                        artifact_id=passage.artifact.id,
                        artifact_title=passage.artifact.title,
                        excerpt=_excerpt(finding.quote),
                        stated_location=finding.location,
                    )
                )
                log.warning(
                    "quote_discarded",
                    extractor=extractor.id,
                    model=card.name,
                    passage=passage.label,
                    artifact_id=passage.artifact.id,
                    reason="not found in the source document",
                    excerpt=_excerpt(finding.quote),
                )
                continue
            accepted += 1
            evidence.append(
                Evidence(
                    extractor=extractor.id,
                    tier=extractor.tier,
                    quote=match.text,
                    location=corpus.location(
                        passage.artifact,
                        within=passage.within(match),
                        line=passage.line_of(match),
                    ),
                    stated_location=finding.location,
                    occurrences=match.occurrences,
                    context=dict(extractor.annotate(match.text, passage)),
                )
            )
        calls.append(
            _call_record(
                extractor,
                passage,
                card,
                completion,
                latency_ms,
                returned=len(returned),
                accepted=accepted,
                discarded=len(returned) - accepted,
            )
        )

    examined = corpus.examined
    if examined == 0:
        because = corpus.explained_empty()
        if because is None:
            because = (
                "The course document holds no artifact this extractor reads and no examination "
                "record says the course has none. Nothing was asked of the model, and this is "
                "not a clean result."
            )
        return ExtractorResult(
            extractor=extractor.id,
            title=extractor.title,
            tier=extractor.tier,
            state=ResultState.NOT_EXAMINED,
            examined=0,
            passages=0,
            answered=0,
            not_examined_because=because,
        )

    summary = dict(extractor.summarise(evidence))
    question = extractor.question_for(evidence, summary) if extractor.tier is Tier.C else None
    result = ExtractorResult(
        extractor=extractor.id,
        title=extractor.title,
        tier=extractor.tier,
        state=ResultState.EXAMINED,
        examined=examined,
        passages=sent,
        answered=answered,
        evidence=tuple(evidence),
        discarded=tuple(discarded),
        failures=tuple(failures),
        calls=tuple(calls),
        summary=summary,
        question=question,
    )
    log.info(
        "extractor_finished",
        extractor=extractor.id,
        model=card.name,
        examined=result.examined,
        passages=result.passages,
        answered=result.answered,
        verified=len(result.evidence),
        discarded=len(result.discarded),
        failures=len(result.failures),
        complete=result.complete,
        tokens=result.tokens,
        latency_ms=result.latency_ms,
    )
    return result


# ---------------------------------------------------------------------------------------------
# The routing contract: a rubric row, served by the same machinery
# ---------------------------------------------------------------------------------------------


class RowExtractor(Extractor, register=False):
    """The extractor a routed rubric row gets, built from its :class:`ExtractionRequest`.

    Not registered, because it is not one of the ten: it exists once per routed row rather than
    once per build, and a registry it appeared in would report a different inventory per course.

    **This is the only thing in the extraction layer that knows a criterion exists**, and it knows
    it because the row handed it over — the routing contract passes the criterion's own text. A
    pack with no bindings is answered entirely through here (SPEC §04, item 04), which is what
    makes an uploaded third-party instrument usable on the day it is uploaded.
    """

    id = "ROW"
    title = "Passages bearing on one row of the instrument"
    tier = Tier.B
    kinds = BODY_KINDS
    ask = "Find every passage bearing on the criterion given."

    def __init__(self, request: ExtractionRequest) -> None:
        self.request = request
        self.id = f"ROW {request.criterion}"
        self.tier = request.tier
        self.title = f"Passages bearing on {request.criterion}"
        self.ask = (
            "The criterion this course is being read against is:\n"
            f"    “{request.text}”\n\n"
            "Find every passage in the document above that bears on that criterion — a passage a "
            "reviewer would point at when deciding it. Quote the passage itself.\n\n"
            f"{request.expects}"
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class RowExtraction:
    """What the extraction layer returns for one routed row. Evidence, and why it was asked for."""

    criterion: str
    section: str
    tier: Tier
    reason: str
    result: ExtractorResult

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion": self.criterion,
            "section": self.section,
            "tier": self.tier.value,
            "reason": self.reason,
            "result": self.result.to_dict(),
        }


async def answer_request(
    request: ExtractionRequest,
    document: CourseDocument,
    client: OllamaClient,
    model: ModelCard | str,
    *,
    budget: int = WORD_BUDGET,
    require_usable: bool = True,
) -> RowExtraction:
    """Serve one row routed by :mod:`sightline.rubric.routing`.

    **It costs one call per passage of the course, per row.** That is the honest price of a rubric
    Sightline ships no mapping for, and it is why the ten topic extractors exist alongside this:
    they answer a course once, and this answers a row. Which rows are routed is the routing
    contract's decision and is not re-made here.
    """
    card = model if isinstance(model, ModelCard) else await client.card(model)
    extractor = RowExtractor(request)
    result = await run_extractor(
        extractor,
        document,
        client,
        card,
        thinking=thinking_for(card),
        budget=budget,
        require_usable=require_usable,
        ask=extractor.ask,
    )
    return RowExtraction(
        criterion=request.criterion,
        section=request.section,
        tier=request.tier,
        reason=request.reason,
        result=result,
    )


async def answer_requests(
    requests: Iterable[ExtractionRequest],
    document: CourseDocument,
    client: OllamaClient,
    model: ModelCard | str,
    **kwargs: Any,
) -> tuple[RowExtraction, ...]:
    """Every routed row, in the order the contract produced them."""
    card = model if isinstance(model, ModelCard) else await client.card(model)
    return tuple(
        [await answer_request(request, document, client, card, **kwargs) for request in requests]
    )


# ---------------------------------------------------------------------------------------------


def _call_record(
    extractor: Extractor,
    passage: Passage,
    card: ModelCard,
    completion: ChatResult,
    latency_ms: float,
    *,
    returned: int = 0,
    accepted: int = 0,
    discarded: int = 0,
    error: str | None = None,
) -> CallRecord:
    record = CallRecord(
        extractor=extractor.id,
        passage=passage.label,
        artifact_id=passage.artifact.id,
        model=completion.model or card.name,
        ok=error is None,
        latency_ms=latency_ms,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        thinking_pinned=completion.thinking_pinned,
        returned=returned,
        accepted=accepted,
        discarded=discarded,
        error=error,
    )
    log.info("extraction_call", **record.to_dict())
    return record


def _failed_call(
    extractor: Extractor,
    passage: Passage,
    card: ModelCard,
    started: float,
    *,
    reason: str,
) -> CallRecord:
    record = CallRecord(
        extractor=extractor.id,
        passage=passage.label,
        artifact_id=passage.artifact.id,
        model=card.name,
        ok=False,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        error=reason,
    )
    log.info("extraction_call", **record.to_dict())
    return record


def _log_failure(
    extractor: Extractor,
    passage: Passage,
    card: ModelCard,
    kind: FailureKind,
    reason: str,
) -> None:
    log.warning(
        "extraction_failed",
        extractor=extractor.id,
        model=card.name,
        passage=passage.label,
        artifact_id=passage.artifact.id,
        kind=kind.value,
        reason=reason,
    )


def _excerpt(text: str, limit: int = DISCARD_EXCERPT) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


__all__ = [
    "FATAL",
    "ExtractionReport",
    "RowExtraction",
    "RowExtractor",
    "answer_request",
    "answer_requests",
    "run_extractor",
    "run_extractors",
    "thinking_for",
]
