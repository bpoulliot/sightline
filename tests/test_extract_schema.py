"""The response schema: what is accepted, and what is refused rather than tidied up.

The acceptance criterion these carry is "schema violations rejected rather than coerced". Every
test below that expects a raise is a test that a *convenient* repair was not made — dropping an
extra key, reading a bare list as findings, filling in a missing location. Each of those repairs
would work, and each would hide a model doing something other than what it was asked.
"""

from __future__ import annotations

import json

import pytest

from sightline.extract.schema import (
    MAX_QUOTE_CHARS,
    RESPONSE_SCHEMA,
    SchemaViolation,
    parse_response,
)


def body(*findings: dict[str, str]) -> str:
    return json.dumps({"findings": list(findings)})


# -- what is accepted --------------------------------------------------------------------------


def test_a_conforming_response_parses() -> None:
    parsed = parse_response(body({"quote": "Reply within 24 hours.", "location": "Policies"}))
    assert len(parsed) == 1
    assert parsed[0].quote == "Reply within 24 hours."
    assert parsed[0].location == "Policies"


def test_an_empty_list_is_a_successful_answer_and_not_a_failure() -> None:
    """The abstention path. It has to be as easy to emit as a finding (SPEC §01)."""
    assert parse_response('{"findings": []}') == ()


def test_a_fenced_block_is_unwrapped() -> None:
    """Models emit the fence whatever the instruction says. Unwrapping it is not coercion."""
    fenced = "```json\n" + body({"quote": "Late work loses 10%.", "location": "Grading"}) + "\n```"
    assert parse_response(fenced)[0].quote == "Late work loses 10%."
    plain = "```\n" + body({"quote": "a", "location": "b"}) + "\n```"
    assert parse_response(plain)[0].quote == "a"


def test_the_location_is_stripped_but_kept() -> None:
    parsed = parse_response(body({"quote": "a", "location": "  the second paragraph \n"}))
    assert parsed[0].location == "the second paragraph"


# -- what is refused ---------------------------------------------------------------------------


def test_a_rating_key_is_rejected_rather_than_dropped() -> None:
    """The one that matters most: a model that rated something did not do what it was asked."""
    payload = json.dumps(
        {"findings": [{"quote": "a", "location": "b", "rating": "Minor"}]},
    )
    with pytest.raises(SchemaViolation) as caught:
        parse_response(payload)
    assert "rating" in str(caught.value)
    assert caught.value.path == "response.findings[0]"


def test_a_key_beside_findings_is_rejected() -> None:
    with pytest.raises(SchemaViolation) as caught:
        parse_response(json.dumps({"findings": [], "summary": "The course is well organised."}))
    assert "summary" in str(caught.value)


def test_a_bare_list_is_not_read_as_findings() -> None:
    with pytest.raises(SchemaViolation):
        parse_response(json.dumps([{"quote": "a", "location": "b"}]))


def test_a_missing_location_is_not_filled_in() -> None:
    with pytest.raises(SchemaViolation) as caught:
        parse_response(json.dumps({"findings": [{"quote": "a"}]}))
    assert "location" in str(caught.value)


def test_a_non_string_quote_is_rejected() -> None:
    for value in (3, None, ["a"], {"text": "a"}):
        with pytest.raises(SchemaViolation):
            parse_response(json.dumps({"findings": [{"quote": value, "location": "b"}]}))


def test_an_empty_quote_is_rejected() -> None:
    """Measured, not imagined: a live model returned one of these on the sample course."""
    with pytest.raises(SchemaViolation) as caught:
        parse_response(body({"quote": "   ", "location": "b"}))
    assert "empty" in str(caught.value)


def test_a_quote_the_length_of_the_document_is_rejected() -> None:
    with pytest.raises(SchemaViolation) as caught:
        parse_response(body({"quote": "x" * (MAX_QUOTE_CHARS + 1), "location": "b"}))
    assert "rather than a passage" in str(caught.value)


def test_prose_around_the_json_is_not_mined_for_an_object() -> None:
    """Reasoning is not an answer. The first object in a paragraph of it is not either."""
    with pytest.raises(SchemaViolation):
        parse_response('Here is what I found:\n{"findings": []}\nHope that helps.')


def test_an_empty_response_is_a_violation_and_not_an_empty_result() -> None:
    with pytest.raises(SchemaViolation):
        parse_response("")


def test_findings_that_is_not_a_list_is_rejected() -> None:
    for value in ("one", {"quote": "a"}, 7):
        with pytest.raises(SchemaViolation):
            parse_response(json.dumps({"findings": value}))


def test_a_violation_carries_an_excerpt_for_diagnosis() -> None:
    with pytest.raises(SchemaViolation) as caught:
        parse_response("not json at all")
    assert caught.value.excerpt == "not json at all"


# -- the schema sent to the host ---------------------------------------------------------------


def test_the_schema_sent_to_the_host_permits_nothing_the_parser_refuses() -> None:
    """The host is told the same shape the parser enforces — and is not trusted to honour it."""
    finding = RESPONSE_SCHEMA["properties"]["findings"]["items"]
    assert sorted(finding["properties"]) == ["location", "quote"]
    assert finding["additionalProperties"] is False
    assert sorted(finding["required"]) == ["location", "quote"]
    assert RESPONSE_SCHEMA["additionalProperties"] is False
    assert RESPONSE_SCHEMA["required"] == ["findings"]


def test_the_schema_has_no_field_a_verdict_could_go_in() -> None:
    serialized = json.dumps(RESPONSE_SCHEMA)
    for word in ("rating", "score", "band", "confidence", "summary", "judgement", "assessment"):
        assert word not in serialized
