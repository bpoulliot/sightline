"""The three-run router, against recorded hosts: the item's acceptance criteria, one per test.

    runs that disagree produce a flag        -> test_runs_that_returned_different_spans_are_flagged
    runs that agree produce no annotation    -> test_runs_that_agree_produce_no_annotation...
    nothing derives a rating from agreement  -> test_no_rating_score_or_confidence_reaches...
                                                test_the_router_module_contains_no_voting...
    the flagged subset is recorded           -> test_the_disagreement_set_round_trips...

The two halves of the router have to be tested from both directions, because a router that flags
everything and a router that flags nothing are equally useless and each of them passes half a
suite. So every disagreement test has an agreement twin, and the agreement twin asserts on the
*absence* of output rather than on a boolean, since a payload carrying ``"agreed": true`` would
satisfy a boolean check while violating the rule the boolean exists to enforce.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

import detector_fixtures as fx
import ollama_recordings as recordings
from sightline.detect.base import ResultState, Tier
from sightline.extract.base import Evidence, Failure, FailureKind
from sightline.extract.router import (
    ATTENTION_NOTE,
    DISAGREEMENT_FORMAT,
    RUNS,
    Comparison,
    DisagreementSet,
    RouterReport,
    route_extractors,
    route_request,
    route_requests,
    span_key,
)
from sightline.extract.runner import run_extractors
from sightline.llm.client import OllamaClient
from sightline.model import CourseDocument
from sightline.rubric.routing import ExtractionRequest
from sightline.rubric.states import Coverage

MODEL = "llama3.1:8b"

LATE = "Late work loses 10% per day, to a maximum of three days."
EMAIL = "I reply to email within 24 hours on weekdays."
SYLLABUS = f"<h2>Course policies</h2><p>{LATE}</p><p>{EMAIL}</p>"

PAGE_SENTENCE = "Assignments submitted after the due date receive no feedback."
PAGE_BODY = f"<h2>Week 2</h2><p>{PAGE_SENTENCE}</p>"

#: Words that must not appear anywhere in the serialized result of a subject whose runs agreed.
#: A marker, an adjective and a boolean are all the same defect: agreement is not information
#: (SPEC §01), and anything downstream can sort on any of the three.
MARKERS = (
    "attention",
    "agree",
    "disagree",
    "unanimous",
    "consensus",
    "confiden",
    "consistent",
    "stable",
    "certain",
    "reliab",
    "corroborat",
)


def findings(*quotes: str) -> str:
    return json.dumps({"findings": [{"quote": quote, "location": "policies"} for quote in quotes]})


def host_replying(*bodies: str, model: str = MODEL) -> recordings.FakeHost:
    """A host that answers the calls in order, repeating the last body once it runs out."""
    host = recordings.only([model])
    host.chat_responses = [recordings.chat_response(model, body) for body in bodies]
    return host


def one_page_course() -> CourseDocument:
    return fx.course(syllabus=fx.syllabus(SYLLABUS))


def two_artifact_course() -> CourseDocument:
    return fx.course([fx.page("week-2", PAGE_BODY)], syllabus=fx.syllabus(SYLLABUS))


async def route(
    host: recordings.FakeHost,
    document: CourseDocument | None = None,
    *,
    extractor_id: str = "E09",
    runs: int = RUNS,
) -> RouterReport:
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        return await route_extractors(
            document if document is not None else one_page_course(),
            client,
            MODEL,
            only=[extractor_id],
            runs=runs,
        )


def a_row() -> ExtractionRequest:
    return ExtractionRequest(
        criterion="GSOV 5",
        section="GSOV",
        text="Course policies for behaviour and interaction are clearly stated.",
        tier=Tier.B,
        coverage=Coverage.NO_DETERMINISTIC_COVERAGE,
        reason="This criterion is answered by a passage of the course rather than by a count.",
        expects="Passages in the course that bear on this criterion.",
    )


def keys_in(payload: Any) -> list[str]:
    """Every key anywhere in a serialized payload, at any depth."""
    out: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            out.append(str(key))
            out.extend(keys_in(value))
    elif isinstance(payload, list):
        for item in payload:
            out.extend(keys_in(item))
    return out


def strings_in(payload: Any) -> list[str]:
    """Every key and every string value anywhere in a serialized payload."""
    out: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            out.append(str(key))
            out.extend(strings_in(value))
    elif isinstance(payload, list):
        for item in payload:
            out.extend(strings_in(item))
    elif isinstance(payload, str):
        out.append(payload)
    return out


# -- a fixture where the runs disagree produces a flag -----------------------------------------


async def test_runs_that_returned_different_spans_are_flagged() -> None:
    """Run 2 found a sentence the others did not. The subject is marked, and it names which."""
    host = host_replying(findings(LATE), findings(EMAIL), findings(LATE))
    comparison = (await route(host)).by_subject("E09")
    assert comparison is not None
    assert comparison.needs_attention is True

    attention = comparison.attention
    assert attention is not None
    assert [span.evidence.quote for span in attention.contested] == [LATE, EMAIL]
    late, email = attention.contested
    assert late.returned_by == (1, 3)
    assert late.missing_from == (2,)
    assert email.returned_by == (2,)
    assert email.missing_from == (1, 3)


async def test_the_flag_says_it_is_not_a_statement_about_which_run_was_right() -> None:
    """The flag reads as "the machine is unsure" unless it says otherwise. It says otherwise."""
    host = host_replying(findings(LATE), findings(EMAIL), findings(LATE))
    payload = (await route(host)).by_subject("E09").to_dict()
    assert payload["needs_attention"]["note"] == ATTENTION_NOTE
    assert "no rating is derived from it" in ATTENTION_NOTE


async def test_a_flagged_subject_says_how_many_spans_differed_and_not_how_confident_it_is() -> None:
    host = host_replying(findings(LATE), findings(EMAIL), findings(LATE))
    statement = (await route(host)).by_subject("E09").statement()
    assert "Needs attention: 2 span(s) were returned by some runs and not by others." in statement
    assert not any(marker in statement.lower() for marker in ("confiden", "%", "score"))


# -- a fixture where the runs agree produces no annotation of any kind -------------------------


async def test_runs_that_agree_produce_no_annotation_of_any_kind() -> None:
    """Not ``needs_attention: false``. The key is absent and no marker word appears anywhere."""
    host = host_replying(findings(LATE), findings(LATE), findings(LATE))
    comparison = (await route(host)).by_subject("E09")
    assert comparison is not None
    assert comparison.needs_attention is False
    assert comparison.attention is None

    payload = comparison.to_dict()
    assert "needs_attention" not in payload
    written = " ".join(strings_in(payload)).lower()
    for marker in MARKERS:
        assert marker not in written, f"an agreeing subject was annotated with {marker!r}"


async def test_an_agreeing_subject_carries_exactly_what_one_run_produced() -> None:
    """The evidence is byte-identical to a single run's. No run count rides along on a quote."""
    agreeing = host_replying(findings(LATE), findings(LATE), findings(LATE))
    routed = (await route(agreeing)).by_subject("E09")

    single = host_replying(findings(LATE))
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=single.transport()) as client:
        report = await run_extractors(one_page_course(), client, MODEL, only=["E09"])
    once = report.by_extractor("E09")

    assert once is not None
    assert routed is not None
    assert routed.to_dict()["evidence"] == [item.to_dict() for item in once.evidence]


async def test_a_span_returned_with_different_typography_is_the_same_span() -> None:
    """Comparison is after normalization: the source writes a non-breaking hyphen, run 2 shouted.

    Three runs, three spellings of the same characters, one span — and the text that survives is
    the document's, so a reviewer searching the page finds it.
    """
    document = fx.course(syllabus=fx.syllabus("<p>I reply to e‑mail within 24 hours.</p>"))
    quote = "I reply to e-mail within 24 hours."
    host = host_replying(findings(quote), findings(quote.upper()), findings(quote))
    comparison = (await route(host, document)).by_subject("E09")
    assert comparison is not None
    assert len(comparison.spans) == 1
    assert comparison.needs_attention is False
    assert comparison.evidence[0].quote == "I reply to e‑mail within 24 hours."


async def test_a_quote_one_run_invented_does_not_flag_the_subject() -> None:
    """A discard is not evidence, so a run that fabricated one still returned the same spans."""
    host = host_replying(
        findings(LATE),
        findings(LATE, "The instructor guarantees feedback within one week."),
        findings(LATE),
    )
    comparison = (await route(host)).by_subject("E09")
    assert comparison is not None
    assert [item.quote for item in comparison.evidence] == [LATE]
    assert comparison.runs[1].discarded  # it happened, and it is recorded on the run
    assert comparison.needs_attention is False


# -- the evidence is the union, and nothing selects on how many runs agreed --------------------


async def test_the_evidence_is_the_union_of_every_run() -> None:
    """Run 3 found nothing at all. Both other spans survive: agreement filters nothing."""
    host = host_replying(findings(LATE), findings(EMAIL), findings())
    comparison = (await route(host)).by_subject("E09")
    assert comparison is not None
    assert [item.quote for item in comparison.evidence] == [LATE, EMAIL]
    assert comparison.needs_attention is True


async def test_a_span_only_one_run_returned_is_kept_and_is_not_labelled_on_the_quote() -> None:
    host = host_replying(findings(LATE), findings(LATE, EMAIL), findings(LATE))
    comparison = (await route(host)).by_subject("E09")
    assert comparison is not None
    rendered = comparison.to_dict()["evidence"]
    assert [item["quote"] for item in rendered] == [LATE, EMAIL]
    for item in rendered:
        assert "returned_by_runs" not in item
        assert "not_returned_by_runs" not in item


async def test_one_sentence_in_two_places_of_one_artifact_survives_the_union() -> None:
    """A syllabus restates a policy under two headings. Both verified quotes reach the reviewer.

    Regression. Keyed on the artifact and the text alone, these two are one span and the union
    keeps the first — a verified quote with a location a reviewer could have opened, dropped by
    the comparison rather than by any filter. Measured against the single-run result, which is the
    only definition of "nothing was lost" that cannot itself be wrong.
    """
    filler = "".join(f"<p>Week {n} covers its own material in some detail.</p>" for n in range(400))
    body = f"<h2>Grading</h2><p>{LATE}</p>{filler}<h2>Late work</h2><p>{LATE}</p>"
    document = fx.course(syllabus=fx.syllabus(body))
    answers = [findings(LATE)] * 30

    single = host_replying(*answers)
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=single.transport()) as client:
        once = (await run_extractors(document, client, MODEL, only=["E09"])).by_extractor("E09")
    assert once is not None
    where = [item.location.within for item in once.evidence]
    assert where == ["under “Grading”", "under “Late work”"]

    comparison = (await route(host_replying(*answers), document)).by_subject("E09")
    assert comparison is not None
    assert [item.to_dict() for item in comparison.evidence] == [
        item.to_dict() for item in once.evidence
    ]
    assert comparison.needs_attention is False


def test_the_same_sentence_at_two_positions_is_two_spans() -> None:
    """The place is part of the identity, and the harness computes it rather than the model."""
    first = evidence_at("syllabus:4410", LATE, within="under “Grading”", line=3)
    second = evidence_at("syllabus:4410", LATE, within="under “Late work”", line=91)
    assert span_key(first) != span_key(second)


def test_the_model_s_own_account_of_where_it_looked_is_not_part_of_the_identity() -> None:
    """``stated_location`` is unverified, so a run that worded it differently has not disagreed."""
    from dataclasses import replace

    one = evidence_at("syllabus:4410", LATE, within="under “Grading”", line=3)
    assert span_key(one) == span_key(replace(one, stated_location="the grading section, I think"))


# -- the counts and the Tier C question describe the spans actually shown -----------------------


SECOND = "Teams present their findings to a panel of practising clinicians in week 10."
PAGE_TWO = f"<h2>Week 2</h2><p>{PAGE_SENTENCE}</p><p>{SECOND}</p>"


async def test_a_tier_c_subject_still_drafts_its_question_under_the_router() -> None:
    """For three of the ten extractors the drafted question *is* the deliverable (SPEC §02).

    A router that ran the extraction three times and then printed no question would be charging
    three times the calls for less output than one pass.
    """
    document = fx.course([fx.page("week-2", PAGE_TWO)])
    host = host_replying(*[findings(PAGE_SENTENCE)] * 3)
    comparison = (await route(host, document, extractor_id="E05")).by_subject("E05")
    assert comparison is not None
    assert comparison.tier is Tier.C
    assert comparison.question is not None
    assert comparison.question.endswith("?")
    assert comparison.to_dict()["question"] == comparison.question
    assert comparison.to_dict()["summary"] == {"statements": 1, "artifacts": 1}


async def test_the_counts_describe_the_union_and_not_whichever_run_came_first() -> None:
    """Run 2 found a second passage. The count under the quotes counts the quotes above it.

    Taking run 1's summary would print *1 passage(s)* beside two quotes — a machine-authored line
    a reviewer falsifies by counting what is under it, which is the one thing SPEC §01 says every
    line has to survive.
    """
    document = fx.course([fx.page("week-2", PAGE_TWO)])
    host = host_replying(
        findings(PAGE_SENTENCE),
        findings(PAGE_SENTENCE, SECOND),
        findings(PAGE_SENTENCE),
    )
    comparison = (await route(host, document, extractor_id="E05")).by_subject("E05")
    assert comparison is not None
    assert len(comparison.evidence) == 2
    assert comparison.summary == {"statements": 2, "artifacts": 1}
    assert comparison.question is not None
    assert comparison.question.startswith("2 passage(s)")
    assert comparison.runs[0].summary == {"statements": 1, "artifacts": 1}
    assert comparison.needs_attention is True


async def test_the_question_is_still_only_ever_asked() -> None:
    """Three runs of a pedagogical criterion are three extractions, and none of them is a rating."""
    document = fx.course([fx.page("week-2", PAGE_TWO)])
    host = host_replying(*[findings(PAGE_SENTENCE)] * 3)
    comparison = (await route(host, document, extractor_id="E05")).by_subject("E05")
    assert comparison is not None
    assert comparison.question is not None
    assert "Nothing here reads them." in comparison.question
    for marker in ("meets", "does not meet", "satisfactory", "score", "rating"):
        assert marker not in comparison.question.lower()


async def test_a_tier_b_subject_drafts_no_question() -> None:
    host = host_replying(*[findings(LATE)] * 3)
    comparison = (await route(host)).by_subject("E09")
    assert comparison is not None
    assert comparison.tier is Tier.B
    assert comparison.question is None
    assert "question" not in comparison.to_dict()


# -- the runs did not read the same course text ------------------------------------------------


async def test_a_passage_read_in_one_run_and_not_in_another_is_a_disagreement() -> None:
    """Run 2 answered with something that is not JSON. It saw less of the course than the others."""
    host = host_replying(findings(LATE), "not json at all", findings(LATE))
    comparison = (await route(host)).by_subject("E09")
    assert comparison is not None
    assert len(comparison.unread) == 1
    unread = comparison.unread[0]
    assert unread.unread_by == (2,)
    assert unread.read_by == (1, 3)
    assert comparison.needs_attention is True
    assert "did not see the same course text" in comparison.attention.statement()


async def test_a_run_that_could_not_read_the_artifact_did_not_disagree_about_its_spans() -> None:
    """One failure is one disagreement, reported once and as the thing it is.

    Run 2 never read the syllabus, so it did not fail to find the sentence in it — it never
    looked. Counting that as a contested span would report the same failure twice, and as a
    difference of opinion rather than as a gap.
    """
    host = host_replying(findings(LATE), "not json at all", findings(LATE))
    comparison = (await route(host)).by_subject("E09")
    assert comparison is not None
    assert comparison.contested == ()
    span = comparison.spans[0]
    assert span.returned_by == (1, 3)
    assert span.missing_from == ()
    assert span.unread_by == (2,)
    assert comparison.attention.reasons() == (
        "1 passage(s) were read in some runs and not in others, so the runs did not see the "
        "same course text.",
    )


async def test_a_passage_every_run_failed_is_not_reported_as_a_disagreement() -> None:
    """Every run failed identically. That is an incomplete result, which item 10 already reports."""
    host = host_replying("not json at all")
    comparison = (await route(host)).by_subject("E09")
    assert comparison is not None
    assert comparison.unread == ()
    assert comparison.needs_attention is False
    assert all(result.complete is False for result in comparison.runs)
    assert all(result.found_nothing is False for result in comparison.runs)


def test_a_run_that_examined_and_a_run_that_did_not_are_never_reported_as_agreeing() -> None:
    """Built by hand: the document makes this unreachable, and that is why it is asserted here."""
    examined = fx_result(ResultState.EXAMINED)
    skipped = fx_result(ResultState.NOT_EXAMINED)
    comparison = Comparison(
        subject="E09",
        title="t",
        tier=Tier.B,
        runs=(examined, examined, skipped),
        spans=(),
        extractor_id="E09",
    )
    assert comparison.divergent_states == ("examined", "not_examined")
    assert comparison.needs_attention is True
    assert "did not all examine the course" in comparison.attention.statement()


def fx_result(state: ResultState):
    from sightline.extract.base import ExtractorResult

    return ExtractorResult(
        extractor="E09",
        title="t",
        tier=Tier.B,
        state=state,
        examined=1 if state is ResultState.EXAMINED else 0,
        passages=1 if state is ResultState.EXAMINED else 0,
        answered=1 if state is ResultState.EXAMINED else 0,
        not_examined_because=None if state is ResultState.EXAMINED else "nothing to open",
    )


# -- no code path derives a rating from run agreement -------------------------------------------


async def test_no_rating_score_or_confidence_is_a_field_of_the_serialized_report() -> None:
    """Keys, not prose. The attention note says the words *no rating is derived from it*, which is
    the sentence that keeps a renderer from inventing one — so the check is structural."""
    host = host_replying(findings(LATE), findings(EMAIL), findings(LATE))
    payload = (await route(host)).to_dict()
    for key in keys_in(payload):
        for forbidden in ("rating", "score", "confidence", "majority", "vote", "band"):
            assert forbidden not in key.lower(), f"the router emitted a {key!r} field"


async def test_the_only_place_the_report_says_rating_is_where_it_disclaims_one() -> None:
    host = host_replying(findings(LATE), findings(EMAIL), findings(LATE))
    payload = (await route(host)).to_dict()
    saying_it = [text for text in strings_in(payload) if "rating" in text.lower()]
    assert saying_it
    assert set(saying_it) == {ATTENTION_NOTE}


def test_the_router_module_contains_no_voting_or_averaging_construct() -> None:
    """Structural, not textual: the docstring explains *why not* and must not fail its own check.

    The identifiers are read off the AST, so the prose that argues against majority voting is
    invisible here while a call to ``statistics.mode`` would not be. The same discipline as the
    data-root scan in ``tests/test_paths.py``.
    """
    source = Path("src/sightline/extract/router.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    #: Matched whole, because each is a word a longer legitimate identifier contains — ``mode``
    #: is inside ``model`` and ``mean`` is inside ``meaning``.
    exactly = {
        "mode",
        "median",
        "mean",
        "fmean",
        "most_common",
        "Counter",
        "vote",
        "votes",
        "average",
        "Rating",
        "intersection",
    }
    #: Matched as a stem, because these have no innocent longer form here and the interesting
    #: violation is always a compound: ``confidence_score``, ``majority_span``, ``row_rating``.
    anywhere = ("confiden", "majorit", "consensus", "rating", "score", "unanimous")
    seen: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            seen.add(node.id)
        elif isinstance(node, ast.Attribute):
            seen.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                seen.add(alias.name.split(".")[-1])
        elif isinstance(node, ast.arg):
            seen.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            seen.add(node.name)
    offenders = sorted(
        name for name in seen if name in exactly or any(stem in name.lower() for stem in anywhere)
    )
    assert not offenders, f"the router names {offenders}, which is voting by another name"


def test_nothing_on_a_comparison_can_be_read_as_a_rating() -> None:
    comparison = Comparison(
        subject="E09", title="t", tier=Tier.B, runs=(), spans=(), extractor_id="E09"
    )
    for attribute in ("rating", "score", "confidence", "band", "winner", "consensus"):
        assert not hasattr(comparison, attribute)


async def test_one_run_is_refused_because_it_cannot_disagree_with_itself() -> None:
    """Refused before the first call, so a pointless pass cannot cost a course's worth of them."""
    host = host_replying(findings(LATE))
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        with pytest.raises(ValueError) as caught:
            await route_extractors(one_page_course(), client, MODEL, only=["E09"], runs=1)
    assert "nothing to disagree with" in str(caught.value)
    assert host.chat_payloads() == []


# -- a rubric row is compared as a criterion ----------------------------------------------------


async def test_a_routed_row_is_compared_and_flagged_by_its_criterion_code() -> None:
    host = host_replying(findings(LATE), findings(EMAIL), findings(LATE))
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        comparison = await route_request(a_row(), one_page_course(), client, MODEL)
    assert comparison.subject == "GSOV 5"
    assert comparison.criterion == "GSOV 5"
    assert comparison.section == "GSOV"
    assert comparison.extractor_id is None
    assert comparison.request is not None
    assert comparison.needs_attention is True
    assert comparison.to_dict()["needs_attention"]["criterion"] == "GSOV 5"


def test_a_comparison_must_say_which_kind_of_subject_it_is() -> None:
    """Item 18 re-runs from this record, and cannot rebuild a request it was never given."""
    with pytest.raises(ValueError) as caught:
        Comparison(subject="x", title="t", tier=Tier.B, runs=(), spans=())
    assert "cannot rebuild a request" in str(caught.value)
    with pytest.raises(ValueError):
        Comparison(
            subject="x",
            title="t",
            tier=Tier.B,
            runs=(),
            spans=(),
            extractor_id="E09",
            request=a_row(),
        )


# -- the disagreement set, for item 18 ----------------------------------------------------------


async def test_the_disagreement_set_names_only_the_flagged_subjects() -> None:
    host = host_replying(findings(LATE), findings(LATE), findings(LATE))
    agreeing = (await route(host)).disagreements()
    assert agreeing.entries == ()
    assert agreeing.statement().startswith("0 subject(s) flagged")

    host = host_replying(findings(LATE), findings(EMAIL), findings(LATE))
    flagged = (await route(host)).disagreements()
    assert [entry.subject for entry in flagged.entries] == ["E09"]
    assert flagged.entries[0].extractor_id == "E09"
    assert flagged.entries[0].request is None
    assert flagged.entries[0].reasons
    # a topic extractor round-trips too: the request is absent rather than empty
    restored = DisagreementSet.from_dict(json.loads(json.dumps(flagged.to_dict())))
    assert restored == flagged
    assert "request" not in flagged.to_dict()["entries"][0]


async def test_the_disagreement_set_names_exactly_the_artifacts_that_differed() -> None:
    """Two artifacts, one of them identical across runs. Escalation re-runs one, not the course."""
    document = two_artifact_course()
    host = host_replying(
        findings(LATE),  # run 1, syllabus
        findings(PAGE_SENTENCE),  # run 1, page
        findings(LATE, EMAIL),  # run 2, syllabus — differs
        findings(PAGE_SENTENCE),  # run 2, page
        findings(LATE),  # run 3, syllabus
        findings(PAGE_SENTENCE),  # run 3, page
    )
    report = await route(host, document)
    comparison = report.by_subject("E09")
    assert comparison is not None
    assert comparison.artifact_ids == ("syllabus:4410",)
    entry = report.disagreements().entries[0]
    assert entry.artifact_ids == ("syllabus:4410",)


async def test_the_disagreement_set_round_trips_so_item_18_can_rebuild_the_requests() -> None:
    host = host_replying(findings(LATE), findings(EMAIL), findings(LATE))
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        report = await route_requests([a_row()], one_page_course(), client, MODEL)
    recorded = report.disagreements()
    assert recorded.entries[0].request == a_row()

    document = json.loads(json.dumps(recorded.to_dict()))
    assert document["format"] == DISAGREEMENT_FORMAT
    restored = DisagreementSet.from_dict(document)
    assert restored == recorded
    assert restored.entries[0].request == a_row()


def test_a_disagreement_set_of_an_unknown_format_is_refused() -> None:
    with pytest.raises(ValueError) as caught:
        DisagreementSet.from_dict({"format": 99, "model": "m", "runs": 3, "entries": []})
    assert "silently becomes the wrong subset" in str(caught.value)


# -- cost --------------------------------------------------------------------------------------


async def test_three_runs_cost_three_times_one_run_and_ask_the_same_question() -> None:
    """Concurrency is 1, so the cost is the sum. Identical prompts, or a difference between two
    runs would be a difference between two questions."""
    host = host_replying(findings(LATE))
    report = await route(host)
    prompts = [payload["messages"] for payload in host.chat_payloads()]
    assert len(prompts) == 3 * 1  # one passage, three runs
    assert prompts[0] == prompts[1] == prompts[2]
    assert report.calls == 3
    assert report.runs == 3
    assert report.elapsed_ms >= 0


async def test_the_report_states_what_the_pass_cost() -> None:
    host = host_replying(findings(LATE))
    report = await route(host)
    assert "run 3 time(s) each on llama3.1:8b" in report.statement()
    assert "3 model call(s)" in report.statement()
    assert "0 subject(s) need attention." in report.statement()


# -- the span key -------------------------------------------------------------------------------


def test_the_same_text_in_two_artifacts_is_two_spans() -> None:
    first = evidence_at("syllabus:4410", LATE)
    second = evidence_at("page:week-2", LATE)
    assert span_key(first) != span_key(second)


def test_a_folded_spelling_of_the_same_characters_is_one_span() -> None:
    assert span_key(evidence_at("a", "don’t submit late")) == span_key(
        evidence_at("a", "Don't  submit late")
    )


def test_one_changed_word_is_two_spans() -> None:
    assert span_key(evidence_at("a", "three assignments")) != span_key(
        evidence_at("a", "four assignments")
    )


def evidence_at(
    artifact_id: str, quote: str, *, within: str | None = None, line: int | None = None
) -> Evidence:
    from sightline.detect.base import Location
    from sightline.model import ArtifactKind

    return Evidence(
        extractor="E09",
        tier=Tier.B,
        quote=quote,
        location=Location(
            artifact_id=artifact_id,
            artifact_kind=ArtifactKind.SYLLABUS,
            title="Syllabus",
            canvas_path="/courses/4410/assignments/syllabus",
            within=within,
            line=line,
        ),
        stated_location="somewhere",
    )


def test_a_failure_is_never_reworded_as_an_absence_of_evidence() -> None:
    """The unread record carries extraction's own reason, not a sentence about finding nothing."""
    from sightline.extract.router import _unread_passages

    read = fx_result(ResultState.EXAMINED)
    failed = fx_result(ResultState.EXAMINED)
    failed = type(failed)(
        **{
            **{
                field: getattr(failed, field)
                for field in (
                    "extractor",
                    "title",
                    "tier",
                    "state",
                    "examined",
                    "passages",
                    "answered",
                )
            },
            "failures": (
                Failure(
                    extractor="E09",
                    passage="Syllabus",
                    artifact_id="syllabus:4410",
                    kind=FailureKind.SCHEMA,
                    reason="is not JSON",
                ),
            ),
        }
    )
    unread = _unread_passages((read, failed, read))
    assert len(unread) == 1
    assert unread[0].reasons == ("is not JSON",)
    assert "no evidence" not in " ".join(unread[0].reasons).lower()
