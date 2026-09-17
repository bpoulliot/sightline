"""Quote verification, from both directions.

Every normalization has to be shown to fold two spellings of the *same* characters together
**and** shown not to let a different word through. A test suite that only proves the first half
proves that the check is lenient, which is not the property anybody wants from it.

SPEC §11: a fabricated quote is the failure that would destroy trust fastest. These are the tests
that stand behind that claim.
"""

from __future__ import annotations

import pytest

from sightline.extract.verify import normalize, verify

SOURCE = (
    "Late work loses 10% per day, to a maximum of three days.\n"
    "I reply to email within 24 hours on weekdays.\n"
    "Don’t submit the draft as a PDF — use the template."
)


# -- what matches ------------------------------------------------------------------------------


def test_an_exact_quote_matches() -> None:
    match = verify("I reply to email within 24 hours on weekdays.", SOURCE)
    assert match is not None
    assert match.text == "I reply to email within 24 hours on weekdays."


def test_the_accepted_text_is_the_sources_own_bytes_not_the_models() -> None:
    """The whole reason the normalizations below are safe to have."""
    match = verify("don't submit the draft as a pdf - use the template.", SOURCE)
    assert match is not None
    assert match.text == "Don’t submit the draft as a PDF — use the template."


def test_whitespace_differences_match() -> None:
    assert verify("Late work loses 10%   per\nday", SOURCE) is not None


def test_a_quote_spanning_a_line_break_matches() -> None:
    match = verify("to a maximum of three days. I reply to email", SOURCE)
    assert match is not None
    assert "\n" in match.text


def test_entities_match_their_characters() -> None:
    assert verify("Smith & Jones", "Submitted to Smith &amp; Jones today.") is not None
    assert verify("Smith &amp; Jones", "Submitted to Smith & Jones today.") is not None


def test_a_quote_the_model_wrapped_in_quotation_marks_matches() -> None:
    match = verify('"I reply to email within 24 hours on weekdays."', SOURCE)
    assert match is not None
    assert match.unwrapped is True
    assert not match.text.startswith('"')


def test_a_no_break_space_matches_a_space() -> None:
    assert verify("10 % per day", "Late work loses 10 % per day.") is not None


def test_occurrences_are_counted() -> None:
    match = verify("read the syllabus", "Read the syllabus. Then read the syllabus again.")
    assert match is not None
    assert match.occurrences == 2


# -- what does not match -----------------------------------------------------------------------


def test_a_changed_number_does_not_match() -> None:
    """The near miss is the dangerous case: the word that changed carries the meaning."""
    assert verify("Late work loses 20% per day", SOURCE) is None
    assert verify("to a maximum of four days", SOURCE) is None


def test_a_changed_modal_does_not_match() -> None:
    assert verify("I may reply to email within 24 hours", SOURCE) is None


def test_a_dropped_word_does_not_match() -> None:
    assert verify("I reply to email within 24 on weekdays.", SOURCE) is None


def test_an_added_word_does_not_match() -> None:
    assert verify("I always reply to email within 24 hours on weekdays.", SOURCE) is None


def test_an_invented_sentence_does_not_match() -> None:
    assert verify("The instructor guarantees feedback within one week.", SOURCE) is None


def test_an_ellipsis_splice_does_not_match() -> None:
    """Two real halves joined by an ellipsis can assert what neither half says. Rejected."""
    assert verify("Late work loses 10% per day … on weekdays.", SOURCE) is None
    assert verify("Late work loses 10% per day ... on weekdays.", SOURCE) is None


def test_a_reordered_quote_does_not_match() -> None:
    assert verify("within 24 hours I reply to email", SOURCE) is None


def test_an_empty_quote_matches_nothing() -> None:
    assert verify("", SOURCE) is None
    assert verify("   ", SOURCE) is None


def test_nothing_matches_an_empty_document() -> None:
    assert verify("anything at all", "") is None


# -- the offset map ----------------------------------------------------------------------------


def test_the_span_points_at_the_quote_in_the_source() -> None:
    match = verify("within 24 hours", SOURCE)
    assert match is not None
    assert SOURCE[match.start : match.end] == "within 24 hours"


def test_the_span_survives_an_entity_before_the_match() -> None:
    source = "Smith &amp; Jones set the deadline at noon."
    match = verify("set the deadline at noon", source)
    assert match is not None
    assert source[match.start : match.end] == "set the deadline at noon"


def test_a_match_that_starts_on_an_entity_keeps_the_entity() -> None:
    source = "Read Smith &amp; Jones before Friday."
    match = verify("Smith & Jones before Friday", source)
    assert match is not None
    assert match.text == "Smith &amp; Jones before Friday"


def test_normalize_maps_every_character_back() -> None:
    normalized = normalize("  Don’t   stop now  ")
    assert normalized.text == "don't stop now"
    assert len(normalized.starts) == len(normalized.text)
    assert len(normalized.ends) == len(normalized.text)
    assert all(end > start for start, end in zip(normalized.starts, normalized.ends, strict=True))


def test_a_prenormalized_source_is_reused_rather_than_recomputed() -> None:
    """The runner normalizes a passage once per call, not once per returned quote."""
    normalized = normalize(SOURCE)
    match = verify("within 24 hours", SOURCE, normalized_source=normalized)
    assert match is not None
    assert match.text == "within 24 hours"


def test_an_empty_span_is_refused() -> None:
    with pytest.raises(ValueError):
        normalize("abc").span(1, 1)
