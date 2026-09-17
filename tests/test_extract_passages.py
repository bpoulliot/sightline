"""Turning an artifact into the document a model is shown.

Two properties carry this module and both are tested by counting rather than by eye: every word of
the body reaches exactly one passage (split, never truncate), and a verified quote can be put back
where it came from (line, and the heading above it).
"""

from __future__ import annotations

import detector_fixtures as fx
from sightline.detect.html_scan import scan
from sightline.extract.passages import WORD_BUDGET, passages_for
from sightline.extract.verify import verify

BODY = """
<h2>Week 2</h2>
<p>Read the chapter and <em>then</em> post a reply.</p>
<h3>Grading</h3>
<p>Late work loses 10% per day.</p>
<ul><li>Bring the handout.</li><li>Charge your laptop.</li></ul>
"""


def test_a_body_becomes_one_passage_of_leaf_blocks() -> None:
    passages = passages_for(fx.page("week-2", BODY))
    assert len(passages) == 1
    passage = passages[0]
    assert passage.part == 1 and passage.parts == 1
    assert passage.text.splitlines() == [
        "Week 2",
        "Read the chapter and then post a reply.",
        "Grading",
        "Late work loses 10% per day.",
        "Bring the handout.",
        "Charge your laptop.",
    ]


def test_an_inline_element_stays_inside_its_sentence() -> None:
    """``own_text`` would have torn ``then`` out of the sentence and put it on its own line."""
    passage = passages_for(fx.page("inline", "<p>Read <em>this</em> carefully.</p>"))[0]
    assert passage.text == "Read this carefully."


def test_a_nested_block_is_not_counted_twice() -> None:
    passage = passages_for(fx.page("nested", "<div><p>One.</p><p>Two.</p></div>"))[0]
    assert passage.text == "One.\nTwo."


def test_every_word_of_the_body_reaches_a_passage() -> None:
    """The guard against a silent loss: the scanner's own word count is the reference."""
    body = fx.page("long", BODY)
    page = scan(body.body_html or "")
    words = sum(passage.words for passage in passages_for(body))
    assert words == page.words


def test_a_long_body_is_split_and_nothing_is_dropped() -> None:
    paragraphs = "".join(f"<p>Paragraph {index} " + "word " * 40 + "</p>" for index in range(30))
    artifact = fx.page("long", paragraphs)
    passages = passages_for(artifact, budget=200)
    assert len(passages) > 1
    assert all(passage.parts == len(passages) for passage in passages)
    assert [passage.part for passage in passages] == list(range(1, len(passages) + 1))
    assert sum(passage.words for passage in passages) == scan(paragraphs).words


def test_a_block_longer_than_the_budget_is_kept_whole() -> None:
    """Truncating it would be the one thing this module may never do."""
    long_paragraph = "<p>" + "word " * 500 + "</p>"
    passages = passages_for(fx.page("one-block", long_paragraph), budget=50)
    assert len(passages) == 1
    assert passages[0].words == 500


def test_a_part_says_which_part_it_is() -> None:
    paragraphs = "".join(f"<p>Paragraph {index} " + "word " * 40 + "</p>" for index in range(10))
    passages = passages_for(fx.page("long", paragraphs), budget=100)
    assert passages[0].label.endswith(f"(part 1 of {len(passages)})")


def test_text_outside_a_block_rebuilds_the_passage_rather_than_losing_it() -> None:
    """Bare text beside a paragraph has no leaf block. Lines are given up; words are not."""
    artifact = fx.page(
        "bare", "<div>An introduction with no element of its own.<p>Then this.</p></div>"
    )
    passage = passages_for(artifact)[0]
    assert passage.rebuilt is True
    assert "An introduction with no element of its own." in passage.text
    assert "Then this." in passage.text
    assert passage.blocks[0].line is None


def test_an_artifact_with_no_body_produces_no_passage() -> None:
    assert passages_for(fx.page("empty", "")) == ()
    assert passages_for(fx.page("blank", "<p>   </p>")) == ()


# -- putting a quote back ----------------------------------------------------------------------


def test_a_verified_quote_is_located_under_the_heading_above_it() -> None:
    passage = passages_for(fx.page("week-2", BODY))[0]
    match = verify(
        "Late work loses 10% per day.", passage.text, normalized_source=passage.normalized
    )
    assert match is not None
    assert passage.within(match) == "under “Grading”"


def test_a_quote_above_the_first_heading_has_no_heading() -> None:
    passage = passages_for(fx.page("no-heading", "<p>First line.</p><h2>Later</h2>"))[0]
    match = verify("First line.", passage.text, normalized_source=passage.normalized)
    assert match is not None
    assert passage.within(match) is None


def test_a_verified_quote_carries_the_source_line_of_its_markup() -> None:
    passage = passages_for(fx.page("week-2", BODY))[0]
    match = verify(
        "Late work loses 10% per day.", passage.text, normalized_source=passage.normalized
    )
    assert match is not None
    # BODY starts with a newline, so the <h2> is on line 2 and this paragraph on line 5.
    assert passage.line_of(match) == 5


def test_a_split_passage_says_which_part_a_quote_was_in() -> None:
    paragraphs = "".join(f"<p>Paragraph {index} " + "word " * 40 + "</p>" for index in range(6))
    passages = passages_for(fx.page("long", paragraphs), budget=100)
    passage = passages[-1]
    match = verify(passage.blocks[0].text, passage.text, normalized_source=passage.normalized)
    assert match is not None
    assert passage.within(match) == f"part {passage.part} of {passage.parts}"


def test_the_default_budget_is_the_documented_one() -> None:
    assert WORD_BUDGET == 900
