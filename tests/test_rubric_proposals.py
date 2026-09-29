"""Roadmap item 22, part one: model-proposed bindings and their command-line review.

Against recorded hosts, never a live one. The acceptance criteria, one or more tests each:

    nothing is accepted by default                  -> test_nothing_is_accepted_by_default...
    accept-by-name and accept-all are the only ways -> test_accept_by_name..., test_accept_all...
    no confidence-shaped field in the output         -> test_no_confidence_shaped_field...
    each WHY span verified or reported absent       -> test_a_verified_span..., test_an_empty_span
    a planted fabricated span is discarded          -> test_a_fabricated_span_is_discarded...
    a conflict is surfaced, not applied             -> test_a_conflict_is_surfaced...
    an unknown tag is dropped and reported          -> test_an_unknown_tag_is_dropped...
    the delta equals coverage run before and after  -> test_the_coverage_delta_equals...
    the output is a mapping `import --map` applies  -> test_the_mapping_applies_through_import...
"""

from __future__ import annotations

import asyncio
import io
import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import openpyxl
import pytest

import ollama_recordings as recordings
from sightline.detect.base import Tier
from sightline.extract.schema import SchemaViolation
from sightline.llm.client import OllamaClient
from sightline.logging import configure_logging
from sightline.rubric import __main__ as cli
from sightline.rubric.coverage import pack_coverage
from sightline.rubric.mapping import loads as load_mapping_text
from sightline.rubric.pack import RubricPack, load
from sightline.rubric.proposals import (
    SPAN_ABSENT,
    SPAN_DISCARDED,
    SPAN_VERIFIED,
    ProposalError,
    ProposalSet,
    accept,
    consequence,
    coverage_delta,
    fingerprint,
    loads,
    mapping_from_pack,
    parse_proposals,
    prompt_for,
    propose,
    response_schema,
    review,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "rubric"
UNBOUND = FIXTURES / "unbound-pack.yaml"
TINY = FIXTURES / "tiny-pack.yaml"
RSI_PACK = REPO / "rubrics" / "packs" / "uccs-oscqr-rsi.yaml"
RSI_MAP = REPO / "rubrics" / "maps" / "uccs-oscqr-rsi.yaml"
RSI_BOOK = REPO / "rubrics" / "source" / "UCCS OSCQR RSI Review.xlsm"
REF_PACK = REPO / "rubrics" / "packs" / "uccs-oscqr-3-1-3.yaml"
REF_MAP = REPO / "rubrics" / "maps" / "uccs-oscqr-3-1-3.yaml"
REF_BOOK = REPO / "rubrics" / "source" / "UCCS OSCQR 3.1.3.xlsx"

MODEL = "llama3.1:8b"
CLOCK = "2026-09-29T00:00:00+00:00"

#: Words that would make a proposal read as more or less likely to be right. None may appear as a
#: key anywhere in a proposal file, nor as a word in anything the tool prints about a proposal.
CONFIDENCE_WORDS = (
    "confidence",
    "confident",
    "score",
    "scores",
    "probability",
    "probable",
    "likely",
    "likelihood",
    "rank",
    "ranked",
    "ranking",
    "strength",
    "strong",
    "weak",
    "certain",
    "certainty",
    "weight",
)


@pytest.fixture(autouse=True)
def quiet_log() -> Iterator[io.StringIO]:
    """Model-call log lines go to a buffer, so stdout is only what a command printed."""
    stream = io.StringIO()
    configure_logging(level="INFO", fmt="json", stream=stream)
    yield stream
    configure_logging(level="INFO", fmt="json")


def answer(*pairs: tuple[str, str]) -> str:
    return json.dumps({"proposals": [{"tag": tag, "span": span} for tag, span in pairs]})


def run(pack: RubricPack, *bodies: str) -> tuple[ProposalSet, recordings.FakeHost]:
    host = recordings.only([MODEL])
    host.chat_responses = [recordings.chat_response(MODEL, body) for body in bodies]

    async def go() -> ProposalSet:
        async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
            return await propose(pack, client, MODEL, clock=lambda: CLOCK)

    return asyncio.run(go()), host


def unbound_set() -> tuple[RubricPack, ProposalSet]:
    """The four-row unbound checklist: a span, a fabricated span, nothing, and an unknown tag."""
    pack = load(UNBOUND)
    proposals, _ = run(
        pack,
        answer(("course_schedule", "published before the first day")),
        answer(("welcome_announcement", "a welcome message"), ("not_a_real_tag", "")),
        answer(),
        answer(("instructor_presence", "the instructor replies within two hours")),
    )
    return pack, proposals


def keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k in value] + [k for v in value.values() for k in keys(v)]
    if isinstance(value, list):
        return [k for v in value for k in keys(v)]
    return []


# -- the response -------------------------------------------------------------------------------


def test_an_empty_list_is_a_valid_answer() -> None:
    assert parse_proposals('{"proposals": []}') == ()


@pytest.mark.parametrize(
    "body",
    [
        '{"proposals": [], "confidence": 0.9}',
        '{"proposals": [{"tag": "link_text", "span": "x", "confidence": "high"}]}',
        '{"proposals": [{"tag": "link_text", "span": "x", "reason": "because"}]}',
        '{"proposals": [{"tag": "link_text"}]}',
        '{"proposals": "link_text"}',
        '["link_text"]',
        "The criterion is about link text.",
    ],
)
def test_a_response_of_another_shape_is_refused_not_coerced(body: str) -> None:
    with pytest.raises(SchemaViolation):
        parse_proposals(body)


def test_the_schema_has_no_field_but_a_tag_and_a_span() -> None:
    item = response_schema()["properties"]["proposals"]["items"]
    assert sorted(item["properties"]) == ["span", "tag"]
    assert item["additionalProperties"] is False


def test_the_schema_is_sent_as_a_constraint_and_the_row_code_is_not_sent() -> None:
    pack = load(UNBOUND)
    _, host = run(pack, answer(), answer(), answer(), answer())
    payloads = host.chat_payloads()
    assert len(payloads) == 4, "one call per row, concurrency 1"
    assert payloads[0]["format"] == response_schema()
    user = payloads[0]["messages"][1]["content"]
    assert pack.criteria[0].text in user
    assert "A1" not in user.split("Criterion:")[1]


def test_the_prompt_carries_the_catalogue() -> None:
    messages = prompt_for(load(UNBOUND).criteria[0], "- link_text: Links say where they go.")
    assert "link_text" in messages[1]["content"]


# -- nothing happens by inertia -----------------------------------------------------------------


def test_proposing_changes_nothing_and_accepts_nothing() -> None:
    pack, proposals = unbound_set()
    assert load(UNBOUND) == pack
    document = proposals.to_dict()
    assert document["accepted"].startswith("none")
    assert "accepted" not in keys(document["proposals"])


def test_nothing_is_accepted_by_default() -> None:
    pack, proposals = unbound_set()
    with pytest.raises(ProposalError, match="nothing is accepted by default"):
        accept(proposals, pack)


def test_nothing_is_accepted_by_default_at_the_terminal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, proposals = unbound_set()
    file = tmp_path / "proposals.yaml"
    file.write_text(proposals.to_yaml())
    assert cli.main(["accept", "--proposals", str(file), "--pack", str(UNBOUND)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "nothing is accepted by default" in captured.err


def test_accept_by_name_accepts_that_proposal_and_no_other() -> None:
    pack, proposals = unbound_set()
    done = accept(proposals, pack, names=[1])
    assert [(p.code, p.tag) for p in done.accepted] == [("A1", "course_schedule")]
    assert done.mapping.criteria["A1"].bindings == ("course_schedule",)
    assert done.mapping.criteria["A2"].bindings == ()


def test_accept_all_accepts_every_proposal_in_the_file() -> None:
    pack, proposals = unbound_set()
    done = accept(proposals, pack, accept_all=True)
    assert {(p.code, p.tag) for p in done.accepted} == {
        (p.code, p.tag) for p in proposals.proposals
    }


def test_a_name_that_is_not_in_the_file_is_refused() -> None:
    pack, proposals = unbound_set()
    with pytest.raises(ProposalError, match="there is no proposal 99"):
        accept(proposals, pack, names=[99])


def test_accept_all_and_names_together_are_refused_at_the_terminal() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(
            ["accept", "--proposals", "p", "--pack", "q", "--accept", "1", "--accept-all"]
        )


# -- WHY is a verified span or its stated absence -----------------------------------------------


def test_a_verified_span_keeps_the_criterions_own_words() -> None:
    _, proposals = unbound_set()
    welcome = next(p for p in proposals.proposals if p.tag == "welcome_announcement")
    assert welcome.span_state == SPAN_VERIFIED
    # The model lower-cased it; what is kept is what the criterion says.
    assert welcome.span == "A welcome message"


def test_an_empty_span_is_reported_as_absent() -> None:
    pack = load(UNBOUND)
    proposals, _ = run(pack, answer(("course_schedule", "  ")), answer(), answer(), answer())
    only = proposals.proposals[0]
    assert only.span_state == SPAN_ABSENT and only.span is None
    assert "no supporting span" in review(proposals)


def test_a_fabricated_span_is_discarded_and_never_shown() -> None:
    _, proposals = unbound_set()
    planted = next(p for p in proposals.proposals if p.tag == "instructor_presence")
    assert planted.span_state == SPAN_DISCARDED and planted.span is None
    fabricated = "the instructor replies within two hours"
    assert fabricated not in review(proposals)
    assert fabricated not in proposals.to_yaml()
    assert "discarded" in planted.why()


def test_every_span_is_verified_or_its_absence_stated() -> None:
    _, proposals = unbound_set()
    for proposal in proposals.proposals:
        assert proposal.span_state in (SPAN_VERIFIED, SPAN_ABSENT, SPAN_DISCARDED)
        assert (proposal.span is not None) == (proposal.span_state == SPAN_VERIFIED)


# -- the vocabulary rule ------------------------------------------------------------------------


def test_an_unknown_tag_is_dropped_reported_and_never_written() -> None:
    pack, proposals = unbound_set()
    assert all(p.tag != "not_a_real_tag" for p in proposals.proposals)
    row = next(row for row in proposals.rows if row.code == "A2")
    assert row.unknown == ("not_a_real_tag",)
    assert "'not_a_real_tag' is not a capability tag this build knows" in review(proposals)
    written = accept(proposals, pack, accept_all=True).mapping
    assert all("not_a_real_tag" not in row.bindings for row in written.criteria.values())


def test_a_hand_added_unknown_tag_in_the_file_is_refused_at_acceptance() -> None:
    pack, proposals = unbound_set()
    edited = proposals.to_yaml().replace("tag: course_schedule", "tag: made_up_tag", 1)
    with pytest.raises(ProposalError, match="made_up_tag"):
        accept(loads(edited), pack, names=[1])


# -- no confidence signal -----------------------------------------------------------------------


def test_no_confidence_shaped_field_exists_anywhere_in_the_output() -> None:
    _, proposals = unbound_set()
    document = proposals.to_dict()
    found = [key for key in keys(document) if any(w in key.lower() for w in CONFIDENCE_WORDS)]
    assert found == []


def test_the_confidence_scan_fires_on_a_planted_field() -> None:
    """The scan above proves nothing unless it can fail: plant one and watch it."""
    planted = {"proposals": [{"tag": "alt_text", "match_strength": 3}]}
    assert [k for k in keys(planted) if any(w in k.lower() for w in CONFIDENCE_WORDS)] == [
        "match_strength"
    ]


def test_no_confidence_shaped_word_is_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, proposals = unbound_set()
    file = tmp_path / "proposals.yaml"
    file.write_text(proposals.to_yaml())
    assert cli.main(["review", "--proposals", str(file)]) == 0
    assert (
        cli.main(["accept", "--proposals", str(file), "--pack", str(UNBOUND), "--accept-all"]) == 0
    )
    captured = capsys.readouterr()
    text = "\n".join((proposals.to_yaml(), captured.out, captured.err))
    pattern = re.compile(r"\b(" + "|".join(CONFIDENCE_WORDS) + r")\b", re.IGNORECASE)
    assert pattern.findall(text) == []


def test_proposals_are_in_instrument_order_and_alphabetical_within_a_row() -> None:
    """Not in the model's order: the order a model returns tags in would read as a ranking."""
    pack = load(UNBOUND)
    proposals, _ = run(
        pack,
        answer(("welcome_announcement", ""), ("course_schedule", ""), ("alt_text", "")),
        answer(),
        answer(),
        answer(),
    )
    assert [p.tag for p in proposals.proposals] == [
        "alt_text",
        "course_schedule",
        "welcome_announcement",
    ]
    assert [p.id for p in proposals.proposals] == [1, 2, 3]


# -- existing bindings are never silently replaced ----------------------------------------------


def asked(pack: RubricPack) -> list[Any]:
    """The rows the proposer sends, in order: every row but Tier D."""
    return [c for c in pack.criteria if c.tier is not Tier.D]


def tiny_set() -> tuple[RubricPack, ProposalSet]:
    """One bound row gets its own tag again plus a new one; one unbound row gets a tag."""
    pack = load(TINY)
    rows = asked(pack)
    bound = next(c for c in rows if c.bindings)
    unbound = next(c for c in rows if not c.bindings)
    assert "course_schedule" not in bound.bindings
    bodies = []
    for criterion in rows:
        if criterion is bound:
            bodies.append(answer((criterion.bindings[0], ""), ("course_schedule", "")))
        elif criterion is unbound:
            bodies.append(answer(("welcome_announcement", "")))
        else:
            bodies.append(answer())
    proposals, _ = run(pack, *bodies)
    return pack, proposals


def test_a_tier_d_row_is_not_asked_and_says_why() -> None:
    pack = load(TINY)
    proposals, host = run(pack, *[answer() for _ in asked(pack)])
    assert len(host.chat_payloads()) == len(asked(pack)) < len(pack.criteria)
    row = next(r for r in proposals.rows if r.tier is Tier.D)
    assert row.not_asked is not None and not row.proposals
    assert "not asked: Tier D" in review(proposals)
    assert proposals.calls == len(asked(pack))


def test_a_conflict_is_surfaced_and_never_swept_up_by_accept_all() -> None:
    pack, proposals = tiny_set()
    bound = next(c for c in pack.criteria if c.bindings)
    conflicts = proposals.conflicts
    assert [p.code for p in conflicts] == [bound.code]
    assert "CONFLICT" in review(proposals)
    row = next(r for r in proposals.rows if r.code == bound.code)
    assert row.already_bound == (bound.bindings[0],)

    done = accept(proposals, pack, accept_all=True)
    assert conflicts[0] not in done.accepted
    assert done.mapping.criteria[bound.code].bindings == bound.bindings


def test_a_conflict_needs_its_own_act_by_number() -> None:
    pack, proposals = tiny_set()
    conflict = proposals.conflicts[0]
    with pytest.raises(ProposalError, match="--accept-conflict"):
        accept(proposals, pack, names=[conflict.id])
    done = accept(proposals, pack, conflicts=[conflict.id])
    row = done.mapping.criteria[conflict.code]
    assert row.bindings == (*conflict.bound, conflict.tag), "added beside, never replacing"


def test_a_non_conflict_named_as_a_conflict_is_refused() -> None:
    pack, proposals = tiny_set()
    plain = next(p for p in proposals.proposals if not p.conflict)
    with pytest.raises(ProposalError, match="not a conflict"):
        accept(proposals, pack, conflicts=[plain.id])


# -- the proposal file is the record ------------------------------------------------------------


def test_the_file_round_trips_and_says_it_is_one_run() -> None:
    _, proposals = unbound_set()
    text = proposals.to_yaml()
    assert loads(text) == proposals
    assert "not deterministic at temperature 0" in text
    assert "not deterministic at temperature 0" in review(proposals)


def test_proposals_made_against_another_state_of_the_pack_are_refused() -> None:
    pack, proposals = unbound_set()
    changed = RubricPack(**{**_fields(pack), "version": "2027.1"})
    assert fingerprint(changed) != fingerprint(pack)
    with pytest.raises(ProposalError, match="is not that pack now"):
        accept(proposals, changed, accept_all=True)


def _fields(pack: RubricPack) -> dict[str, Any]:
    return {name: getattr(pack, name) for name in RubricPack.__dataclass_fields__}


def test_a_failed_row_is_a_failure_and_not_none_proposed() -> None:
    pack = load(UNBOUND)
    proposals, _ = run(pack, answer(), "not json at all", answer(), answer())
    row = proposals.rows[1]
    assert row.failure is not None and "schema" in row.failure
    assert "could not be asked" in review(proposals)
    assert proposals.failures == (row,)


# -- how a binding changes the review -----------------------------------------------------------


def test_a_tier_c_row_stays_at_no_response_whatever_it_binds_to() -> None:
    text = consequence(Tier.C, (), "alt_text")
    assert "stays at No Response whatever it binds to" in text


def test_a_tier_b_row_says_clean_is_still_no_response() -> None:
    text = consequence(Tier.B, (), "alt_text")
    assert "moves from evidence-only to deterministic" in text
    assert "still reads No Response" in text


def test_a_tier_a_row_says_it_stops_going_to_the_model() -> None:
    text = consequence(Tier.A, (), "alt_text")
    assert "not sent to the model for quotes" in text
    assert "Unbinding moves it back" in text


def test_a_tier_d_row_says_nothing_changes() -> None:
    assert consequence(Tier.D, (), "alt_text").startswith("None.")


def test_a_tag_with_no_detector_says_nothing_computed_changes() -> None:
    from sightline.rubric.project import TagIndex

    index = TagIndex.build()
    pending = next(tag for tag in index.vocabulary.tags if not index.implements(tag))
    assert "no detector implements" in consequence(Tier.B, (), pending)
    assert "keeps the row from reading Sufficiently Present" in consequence(Tier.A, (), pending)


# -- the mapping, through the existing path -----------------------------------------------------


def _import(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> str:
    assert cli.main(list(argv)) == 0
    return capsys.readouterr().out


def _coverage(pack_file: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, int]:
    assert cli.main(["coverage", "--pack", str(pack_file), "--json"]) == 0
    return json.loads(capsys.readouterr().out)["counts"]


def _accept_through_cli(
    tmp_path: Path,
    proposals: ProposalSet,
    pack_file: Path,
    capsys: pytest.CaptureFixture[str],
    *extra: str,
) -> tuple[Path, str]:
    file = tmp_path / "proposals.yaml"
    file.write_text(proposals.to_yaml())
    assert cli.main(["accept", "--proposals", str(file), "--pack", str(pack_file), *extra]) == 0
    captured = capsys.readouterr()
    mapping = tmp_path / "accepted-map.yaml"
    mapping.write_text(captured.out)
    return mapping, captured.err


def _delta_numbers(err: str) -> tuple[int, int]:
    moved = re.search(r"(\d+) row\(s\) moved from evidence-only to deterministic", err)
    remain = re.search(r"(\d+) remain evidence-only", err)
    assert remain is not None
    return (int(moved.group(1)) if moved else 0), int(remain.group(1))


def _proposals_for_unbound_rows(pack: RubricPack, tag: str) -> list[str]:
    return [answer() if c.bindings else answer((tag, "")) for c in asked(pack)]


@pytest.mark.parametrize(
    ("pack_file", "map_file", "book"),
    [(RSI_PACK, RSI_MAP, RSI_BOOK), (REF_PACK, REF_MAP, REF_BOOK)],
    ids=["rsi", "reference"],
)
def test_the_mapping_applies_through_import_and_the_delta_equals_coverage(
    pack_file: Path,
    map_file: Path,
    book: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Both shipped packs: the printed mapping, applied by `import --map` to the same workbook,
    gives the pack with the accepted tags added, and the delta printed at acceptance is exactly
    what `coverage` reports on the pack before and after."""
    pack = load(pack_file)
    proposals, _ = run(pack, *_proposals_for_unbound_rows(pack, "alt_text"))
    assert proposals.proposals, "the shipped pack has an unbound row to propose for"

    mapping, err = _accept_through_cli(
        tmp_path, proposals, pack_file, capsys, "--map", str(map_file), "--accept-all"
    )
    rebuilt = tmp_path / "rebuilt.yaml"
    rebuilt.write_text(_import(["import", "--workbook", str(book), "--map", str(mapping)], capsys))
    after = load(rebuilt)
    for criterion in pack.criteria:
        added = ("alt_text",) if not criterion.bindings and criterion.tier is not Tier.D else ()
        assert after.criterion(criterion.code).bindings == (*criterion.bindings, *added)
        assert after.criterion(criterion.code).tier is criterion.tier

    before_counts = _coverage(pack_file, capsys)
    after_counts = _coverage(rebuilt, capsys)
    moved, remain = _delta_numbers(err)
    assert moved == after_counts["deterministic"] - before_counts["deterministic"]
    assert remain == after_counts["evidence_only"]
    assert moved > 0


def test_the_coverage_delta_equals_coverage_when_a_tier_d_row_is_bound() -> None:
    """Roadmap item 38: a hand-written mapping may bind a Tier D row, which the proposer never
    asks about. Binding the reference pack's MOB 5 alongside an unbound Tier B row, the delta moves
    only the Tier B row, and every bucket's change in `coverage` is exactly what the delta moved
    into it less what it moved out."""
    pack = load(REF_PACK)
    tier_b = next(c for c in pack.criteria if c.tier is Tier.B and not c.bindings)
    updated = replace(
        pack,
        criteria=tuple(
            (
                replace(c, bindings=(*c.bindings, "alt_text"))
                if c.code in {"MOB 5", tier_b.code}
                else c
            )
            for c in pack.criteria
        ),
    )
    before, after = pack_coverage(pack), pack_coverage(updated)
    assert before.ok and after.ok, after.detail()

    delta = coverage_delta(pack, updated)
    assert dict(delta.moved) == {("evidence-only", "deterministic"): (tier_b.code,)}
    names = {
        "deterministic": "deterministic",
        "not_yet_implemented": "bound to a capability no detector implements yet",
        "evidence_only": "evidence-only",
        "not_determinable": "not determinable",
    }
    for key, name in names.items():
        into = sum(len(codes) for (_, end), codes in delta.moved.items() if end == name)
        out = sum(len(codes) for (start, _), codes in delta.moved.items() if start == name)
        assert after.counts[key] - before.counts[key] == into - out, key


def test_a_shipped_pack_without_its_mapping_is_refused_because_of_its_join_key() -> None:
    pack = load(RSI_PACK)
    with pytest.raises(ProposalError, match="join key"):
        mapping_from_pack(pack)


def test_a_mapping_the_pack_was_not_built_from_is_refused() -> None:
    pack = load(RSI_PACK)
    proposals, _ = run(pack, *_proposals_for_unbound_rows(pack, "alt_text"))
    other = load_mapping_text(RSI_MAP.read_text().replace("tier: B", "tier: C", 1))
    with pytest.raises(ProposalError, match="is not the mapping"):
        accept(proposals, pack, accept_all=True, base=other)


def _synthetic_workbook(path: Path) -> None:
    """An invented four-row checklist. Traces to no real department or course."""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Checklist"
    sheet["A1"] = "Departmental Online Course Checklist"
    sheet["A3"] = "#"
    sheet["B3"] = "Criterion"
    rows = (
        "The course shell is published before the first day of class.",
        "A welcome message tells learners where to begin.",
        "Every image carries a text alternative that describes it.",
        "The instructor posts at least once a week in the discussion forum.",
    )
    for number, text in enumerate(rows, start=1):
        sheet.cell(row=3 + number, column=1, value=number)
        sheet.cell(row=3 + number, column=2, value=text)
    book.save(path)


def test_a_zero_binding_pack_is_a_success_case_end_to_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An uploaded rubric with nothing mapped: every row gets a proposal or "none", and the
    mapping printed at acceptance re-imports to the same pack with the accepted tags added."""
    book = tmp_path / "checklist.xlsx"
    _synthetic_workbook(book)
    pack_file = tmp_path / "checklist.yaml"
    pack_file.write_text(_import(["import", "--workbook", str(book), "--id", "checklist"], capsys))
    pack = load(pack_file)
    assert not any(c.bindings for c in pack.criteria)

    proposals, _ = run(
        pack,
        answer(),
        answer(("welcome_announcement", "A welcome message")),
        answer(("alt_text", "a text alternative")),
        answer(),
    )
    assert all(row.proposals or row.failure is None for row in proposals.rows)
    assert sum(1 for row in proposals.rows if not row.proposals) == 2
    assert "none proposed" in review(proposals)

    mapping, err = _accept_through_cli(tmp_path, proposals, pack_file, capsys, "--accept", "2")
    rebuilt = tmp_path / "rebuilt.yaml"
    rebuilt.write_text(_import(["import", "--workbook", str(book), "--map", str(mapping)], capsys))
    after = load(rebuilt)
    assert after.id == "checklist"
    assert [c.bindings for c in after.criteria] == [(), (), ("alt_text",), ()]
    moved, remain = _delta_numbers(err)
    assert (moved, remain) == (1, 3)
    assert _coverage(rebuilt, capsys)["evidence_only"] == 3


def test_the_propose_command_prints_the_file_to_stdout_and_the_review_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _, proposals = unbound_set()

    async def fake(pack: RubricPack, *, endpoint: str, model: str) -> ProposalSet:
        return proposals

    monkeypatch.setattr(cli, "_ask", fake)
    code = cli.main(
        [
            "propose",
            "--pack",
            str(UNBOUND),
            "--model",
            MODEL,
            "--endpoint",
            recordings.EXAMPLE_ENDPOINT,
        ]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert loads(captured.out) == proposals
    assert "Nothing below is accepted" in captured.err
