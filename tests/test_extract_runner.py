"""The extraction loop, against recorded hosts: the item's acceptance criteria, one per test.

    schema violations rejected rather than coerced   -> test_a_schema_violation_is_a_failure...
    a fabricated quote is caught and discarded       -> test_a_fabricated_quote_is_discarded
    an empty result renders as "no evidence found"   -> test_an_empty_result_reads_as_no_evidence
    every call logged with latency, tokens and model -> test_every_call_is_logged_with...

The host is the recorded one from item 09 — a test suite that needs a GPU is a test suite that does
not run in CI, and the failures that matter here cannot be produced on demand against a working
host anyway.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import httpx
import pytest

import detector_fixtures as fx
import ollama_recordings as recordings
from sightline.detect.base import ResultState, Tier
from sightline.extract.base import FailureKind
from sightline.extract.runner import (
    ExtractionReport,
    answer_request,
    run_extractor,
    run_extractors,
    thinking_for,
)
from sightline.extract.schema import RESPONSE_SCHEMA
from sightline.llm.client import OllamaClient, Thinking
from sightline.llm.errors import ModelHostUnreachableError
from sightline.logging import configure_logging
from sightline.rubric.routing import ExtractionRequest
from sightline.rubric.states import Coverage

THINKING_MODEL = "gemma4:e4b"
PLAIN_MODEL = "llama3.1:8b"

SYLLABUS = (
    "<h2>Course policies</h2>"
    "<p>Late work loses 10% per day, to a maximum of three days.</p>"
    "<p>I reply to email within 24 hours on weekdays.</p>"
)


def findings(*pairs: tuple[str, str]) -> str:
    return json.dumps({"findings": [{"quote": q, "location": loc} for q, loc in pairs]})


def host_with(*bodies: str, model: str = PLAIN_MODEL) -> recordings.FakeHost:
    host = recordings.only([model])
    host.chat_responses = [recordings.chat_response(model, body) for body in bodies]
    return host


def one_page_course() -> object:
    return fx.course(syllabus=fx.syllabus(SYLLABUS))


async def run_one(host: recordings.FakeHost, extractor_id: str = "E09", model: str = PLAIN_MODEL):
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        return await run_extractors(one_page_course(), client, model, only=[extractor_id])


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    configure_logging(level="DEBUG", fmt="json", stream=stream)
    yield stream
    configure_logging(level="INFO", fmt="json")


def events(captured: str, name: str) -> list[dict]:
    return [
        payload
        for payload in (json.loads(line) for line in captured.splitlines() if line.startswith("{"))
        if payload.get("event") == name
    ]


# -- the quote is verified ---------------------------------------------------------------------


async def test_a_verified_quote_is_kept_with_a_computed_location() -> None:
    host = host_with(findings(("Late work loses 10% per day, to a maximum of three days.", "top")))
    report = await run_one(host)
    result = report.by_extractor("E09")
    assert result is not None
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    assert evidence.quote == "Late work loses 10% per day, to a maximum of three days."
    assert evidence.location.title == "Syllabus"
    assert evidence.location.within == "under “Course policies”"
    assert evidence.tier is Tier.B


async def test_a_fabricated_quote_is_discarded_and_never_rendered(log_stream: io.StringIO) -> None:
    """The acceptance criterion SPEC §11 names: a plausible invention, caught mechanically."""
    host = host_with(
        findings(("The instructor guarantees feedback within one week.", "Course policies"))
    )
    report = await run_one(host)
    result = report.by_extractor("E09")
    assert result is not None
    assert result.evidence == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason == "not found in the source document"
    assert "guarantees feedback" in result.discarded[0].excerpt
    logged = events(log_stream.getvalue(), "quote_discarded")
    assert len(logged) == 1
    assert logged[0]["extractor"] == "E09"


async def test_a_near_miss_is_discarded_and_the_real_quote_beside_it_is_kept() -> None:
    """One changed number. The other finding in the same response is unaffected."""
    host = host_with(
        findings(
            ("Late work loses 20% per day, to a maximum of three days.", "policies"),
            ("I reply to email within 24 hours on weekdays.", "policies"),
        )
    )
    result = (await run_one(host)).by_extractor("E09")
    assert result is not None
    assert [item.quote for item in result.evidence] == [
        "I reply to email within 24 hours on weekdays."
    ]
    assert len(result.discarded) == 1


async def test_the_models_own_location_is_recorded_and_never_rendered() -> None:
    """Three models gave three different answers for this field on the target host."""
    host = host_with(findings(("I reply to email within 24 hours on weekdays.", "page")))
    result = (await run_one(host)).by_extractor("E09")
    assert result is not None
    evidence = result.evidence[0]
    assert evidence.stated_location == "page"
    assert evidence.location.within == "under “Course policies”"
    assert evidence.to_dict()["model_stated_location_unverified"] == "page"


# -- the four outcomes, kept apart ---------------------------------------------------------------


async def test_an_empty_result_reads_as_no_evidence_found_and_not_as_a_failure() -> None:
    result = (await run_one(host_with('{"findings": []}'))).by_extractor("E09")
    assert result is not None
    assert result.state is ResultState.EXAMINED
    assert result.complete is True
    assert result.found_nothing is True
    assert result.failures == ()
    assert "no evidence found" in result.statement()


async def test_a_schema_violation_is_a_failure_and_never_reads_as_no_evidence() -> None:
    """The lie this guards against: a failed run rendered as a clean course."""
    host = host_with(json.dumps({"findings": [{"quote": "a", "location": "b", "rating": "Minor"}]}))
    result = (await run_one(host)).by_extractor("E09")
    assert result is not None
    assert result.evidence == ()
    assert len(result.failures) == 1
    assert result.failures[0].kind is FailureKind.SCHEMA
    assert "rating" in result.failures[0].reason
    assert result.complete is False
    assert result.found_nothing is False
    assert "no evidence found" not in result.statement()
    assert "could not be read" in result.statement()


async def test_a_server_error_on_one_passage_is_recorded_and_the_run_goes_on() -> None:
    host = recordings.only([PLAIN_MODEL])
    host.failures["/api/chat"] = (500, {"error": "internal"})
    result = (await run_one(host)).by_extractor("E09")
    assert result is not None
    assert result.failures[0].kind is FailureKind.HOST
    assert result.answered == 0
    assert result.complete is False
    assert "no evidence found" not in result.statement()


async def test_an_unreachable_host_stops_the_run_rather_than_failing_every_passage() -> None:
    transport = recordings.raising_transport(httpx.ConnectError("no route"))
    document = fx.course(
        syllabus=fx.syllabus(SYLLABUS),
        pages=[fx.page("one", "<p>A page.</p>"), fx.page("two", "<p>Another page.</p>")],
    )
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=transport) as client:
        with pytest.raises(ModelHostUnreachableError):
            await run_extractors(document, client, PLAIN_MODEL, only=["E09"])


async def test_an_extractor_with_nothing_to_read_is_reported_and_is_never_clean() -> None:
    document = fx.course()
    host = host_with('{"findings": []}')
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        report = await run_extractors(document, client, PLAIN_MODEL, only=["E09"])
    result = report.by_extractor("E09")
    assert result is not None
    assert result.state is ResultState.NOT_EXAMINED
    assert result.found_nothing is False
    assert "nothing was examined" in result.statement()
    assert "holds nothing in" in (result.not_examined_because or "")
    assert host.chat_payloads() == []


async def test_an_artifact_with_no_text_is_not_reported_as_finding_nothing() -> None:
    document = fx.course(syllabus=fx.syllabus("<p>   </p>"))
    host = host_with('{"findings": []}')
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        report = await run_extractors(document, client, PLAIN_MODEL, only=["E09"])
    result = report.by_extractor("E09")
    assert result is not None
    assert result.examined == 1
    assert result.passages == 0
    assert result.found_nothing is False
    assert "none carried any text" in result.statement()


# -- the call itself ----------------------------------------------------------------------------


async def test_every_call_is_logged_with_latency_tokens_and_model(log_stream: io.StringIO) -> None:
    await run_one(host_with(findings(("I reply to email within 24 hours on weekdays.", "x"))))
    calls = events(log_stream.getvalue(), "extraction_call")
    assert len(calls) == 1
    call = calls[0]
    assert call["model"] == PLAIN_MODEL
    assert call["latency_ms"] >= 0
    assert call["prompt_tokens"] == 32
    assert call["completion_tokens"] == 12
    assert call["tokens_reported"] is True
    assert call["accepted"] == 1
    assert events(log_stream.getvalue(), "model_call")


async def test_the_schema_is_sent_to_the_host_as_a_constraint() -> None:
    host = host_with('{"findings": []}')
    await run_one(host)
    payload = host.chat_payloads()[0]
    assert payload["format"] == RESPONSE_SCHEMA
    assert payload["options"]["num_ctx"] == 8192
    assert payload["options"]["temperature"] == 0.0


async def test_thinking_is_pinned_on_for_a_thinking_model_and_omitted_otherwise() -> None:
    """SPEC §06's measurement: with the pin off, a thinking model's reasoning lands in content."""
    thinking_host = host_with('{"findings": []}', model=THINKING_MODEL)
    await run_one(thinking_host, model=THINKING_MODEL)
    assert thinking_host.chat_payloads()[0]["think"] is True

    plain_host = host_with('{"findings": []}')
    await run_one(plain_host)
    assert "think" not in plain_host.chat_payloads()[0]


async def test_one_call_per_passage_and_the_artifact_is_counted_once() -> None:
    body = "".join(f"<p>Paragraph {i} " + "word " * 40 + "</p>" for i in range(6))
    long_page = fx.page("long", body)
    document = fx.course(pages=[long_page])
    host = host_with('{"findings": []}')
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        from sightline.extract.extractors import E06Progression

        result = await run_extractor(
            E06Progression(),
            document,
            client,
            await client.card(PLAIN_MODEL),
            thinking=Thinking.OFF,
            budget=100,
        )
    assert result.examined == 1
    assert result.passages > 1
    assert len(host.chat_payloads()) == result.passages


async def test_the_document_sent_is_one_artifact_at_a_time() -> None:
    document = fx.course(
        syllabus=fx.syllabus(SYLLABUS), pages=[fx.page("other", "<p>Unrelated page text.</p>")]
    )
    host = host_with('{"findings": []}')
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        await run_extractors(document, client, PLAIN_MODEL, only=["E09"])
    prompts = [payload["messages"][1]["content"] for payload in host.chat_payloads()]
    assert len(prompts) == 2
    assert "Late work loses 10%" in prompts[0]
    assert "Unrelated page text." not in prompts[0]


# -- what the extractors compute -----------------------------------------------------------------


async def test_e01_takes_the_task_verb_from_the_verified_quote() -> None:
    body = "<p>Students will analyze two competing budget proposals.</p>"
    document = fx.course(pages=[fx.page("objectives", body)])
    host = host_with(findings(("Students will analyze two competing budget proposals.", "p1")))
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        report = await run_extractors(document, client, PLAIN_MODEL, only=["E01"])
    result = report.by_extractor("E01")
    assert result is not None
    assert result.evidence[0].context["task_verb"] == "analyze"
    assert result.evidence[0].context["bloom_level"] == "analyze"
    assert result.summary["task_verb_distribution"]["analyze"] == 1
    assert result.summary["task_verb_distribution"]["create"] == 0


async def test_a_tier_c_extractor_drafts_a_question_and_answers_nothing() -> None:
    body = "<p>Prepare a briefing for the city council on the proposed budget.</p>"
    document = fx.course(assignments=[fx.assignment("9001", "Briefing", body=body)])
    host = host_with(
        findings(("Prepare a briefing for the city council on the proposed budget.", "p1"))
    )
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        report = await run_extractors(document, client, PLAIN_MODEL, only=["E05"])
    result = report.by_extractor("E05")
    assert result is not None
    assert result.tier is Tier.C
    assert result.question is not None
    assert result.question.endswith("?")
    assert result.evidence[0].tier is Tier.C


async def test_a_tier_b_extractor_drafts_no_question() -> None:
    result = (await run_one(host_with('{"findings": []}'))).by_extractor("E09")
    assert result is not None
    assert result.question is None


# -- the routing contract -------------------------------------------------------------------------


async def test_a_routed_row_is_served_by_the_same_machinery() -> None:
    request = ExtractionRequest(
        criterion="GSOV 10",
        section="GSOV",
        text="Course policies are stated, including a policy on late work.",
        tier=Tier.B,
        coverage=Coverage.NO_DETERMINISTIC_COVERAGE,
        reason="No capability is bound to this row.",
        expects="Passages in the course that bear on this criterion.",
    )
    host = host_with(findings(("Late work loses 10% per day, to a maximum of three days.", "top")))
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        answer = await answer_request(request, one_page_course(), client, PLAIN_MODEL)
    assert answer.criterion == "GSOV 10"
    assert answer.result.extractor == "ROW GSOV 10"
    assert len(answer.result.evidence) == 1
    prompt = host.chat_payloads()[0]["messages"][1]["content"]
    assert "Course policies are stated" in prompt
    assert "rating" not in prompt.lower()
    assert "GSOV 10" not in prompt


async def test_a_row_extraction_serialises_without_a_rating_anywhere() -> None:
    request = ExtractionRequest(
        criterion="ENIN 6",
        section="ENIN",
        text="Course activities are authentic.",
        tier=Tier.C,
        coverage=Coverage.NO_DETERMINISTIC_COVERAGE,
        reason="This criterion is a pedagogical judgement.",
        expects="The context a reviewer needs.",
    )
    host = host_with('{"findings": []}')
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        answer = await answer_request(request, one_page_course(), client, PLAIN_MODEL)
    serialized = json.dumps(answer.to_dict())
    for word in ('"rating"', '"score"', '"band"', '"confidence"'):
        assert word not in serialized


# -- the report ------------------------------------------------------------------------------------


async def test_the_report_counts_what_it_did() -> None:
    host = host_with(findings(("I reply to email within 24 hours on weekdays.", "x")))
    report = await run_one(host)
    assert isinstance(report, ExtractionReport)
    assert report.model == PLAIN_MODEL
    assert len(report.calls) == 1
    assert len(report.evidence) == 1
    assert report.tokens == 44
    assert "1 quote(s) verified" in report.statement()
    assert report.to_dict()["statement"] == report.statement()


async def test_thinking_for_reads_the_capability_rather_than_the_name() -> None:
    async with OllamaClient(
        recordings.EXAMPLE_ENDPOINT, transport=recordings.load().transport()
    ) as client:
        assert thinking_for(await client.card(THINKING_MODEL)) is Thinking.ON
        assert thinking_for(await client.card(PLAIN_MODEL)) is Thinking.OFF
