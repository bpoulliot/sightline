"""The capability-tag vocabulary, and the asymmetry in how it is enforced.

A capability tag is the only thing joining a detector to a rubric row, and it joins in one
direction: a detector declares a concept, a pack binds its own rows to concepts, and neither names
the other. Everything here is about keeping that joint honest.

**The asymmetry is the design, not an oversight.** A *detector* declaring an unknown tag fails hard
— we write the detectors, so a typo in one is our bug and it should stop the build. A *pack*
binding an unknown tag is a reported warning and that row degrades to no deterministic coverage —
packs are third-party, and an uploaded rubric must never be able to hard-fail Sightline. Both
halves are asserted: this module owns the vocabulary and the detector side,
``tests/test_rubric_project.py`` owns the pack side.
"""

from __future__ import annotations

import re

import pytest

from sightline.capabilities import (
    TAG_PATTERN,
    VOCABULARY_FILENAME,
    CapabilityVocabularyError,
    UnknownCapabilityTag,
    known,
    parse_vocabulary,
    require,
    vocabulary,
)
from sightline.detect import detectors

#: The reference instrument's category codes. A tag naming one of these would have smuggled a
#: criterion into the layer built to keep criteria out.
CRITERION_SHAPED = re.compile(r"\b(COAL|GSOV|OREP|ACD|ENIN|ASFE|MOB)\b", re.I)


# -- the shipped vocabulary ----------------------------------------------------------------------


def test_the_shipped_vocabulary_loads_and_is_not_empty() -> None:
    """Asserted non-empty first. A vocabulary that parsed to nothing would agree with every
    detector and every pack, and would report both as fine."""
    words = vocabulary()
    assert len(words) > 20
    assert words.version == 1
    assert words.groups


def test_every_tag_is_lower_snake_case_and_filed_under_a_declared_group() -> None:
    words = vocabulary()
    for tag, capability in words.capabilities.items():
        assert TAG_PATTERN.match(tag), tag
        assert capability.group in words.groups, tag
        assert capability.summary.strip(), tag


def test_no_tag_or_summary_names_a_rubric_criterion() -> None:
    """The boundary this whole file exists to hold.

    A tag names a course-quality concept — ``alt_text``, ``due_date_coverage`` — in words a
    non-programmer reads. The moment one of them says ``ACD 8`` the vocabulary has become a
    crosswalk to one particular instrument, and a pack from Quality Matters can no longer use it
    without being mapped onto OSCQR first. That is the design error item 04 was re-scoped to avoid.
    """
    for tag, capability in vocabulary().capabilities.items():
        assert not CRITERION_SHAPED.search(tag), tag
        assert not CRITERION_SHAPED.search(capability.summary), tag
        assert "oscqr" not in capability.summary.lower(), tag
        assert "open suny" not in capability.summary.lower(), tag


def test_no_tag_names_a_detector() -> None:
    """A tag is portable across detector refactors, which it stops being the moment it says
    ``D21``. Packs bind to concepts; the detector ids are Sightline's internal numbering."""
    ids = {detector.id for detector in detectors()}
    for tag in vocabulary().tags:
        assert tag.upper() not in ids, tag
        assert not re.match(r"^[de]\d{2}$", tag, re.I), tag


def test_the_vocabulary_statement_is_counts_rather_than_a_verdict() -> None:
    statement = vocabulary().statement()
    assert "capability tag(s)" in statement
    assert str(len(vocabulary())) in statement


def test_suggestions_name_a_near_miss_rather_than_only_the_mistake() -> None:
    """Fifty entries long and hand-typed, so "that is wrong" is not enough to fix it with."""
    words = vocabulary()
    assert words.suggestions("alt") == ("alt_text",)
    assert words.suggestions("tables") == ("table_semantics",)
    assert words.suggestions("contrast") == ("colour_contrast",)
    assert words.suggestions("nothing_like_any_of_them") == ()


# -- the detector side: hard failure -------------------------------------------------------------


def test_require_accepts_a_known_tag_and_returns_it() -> None:
    assert require("alt_text", declared_by="a test") == "alt_text"


def test_require_raises_on_an_unknown_tag_and_says_who_declared_it() -> None:
    with pytest.raises(UnknownCapabilityTag) as caught:
        require("alt_txt", declared_by="tests.Misspelled")
    message = str(caught.value)
    assert "tests.Misspelled" in message
    assert VOCABULARY_FILENAME in message
    assert "alt_text" in message


@pytest.mark.parametrize("bad", ["", "Alt_Text", "alt text", "alt-text", "D21"])
def test_require_raises_on_anything_not_spelled_like_a_tag(bad: str) -> None:
    with pytest.raises(UnknownCapabilityTag):
        require(bad, declared_by="a test")


def test_known_is_the_soft_check_and_never_raises() -> None:
    """What the pack loader uses. It answers about a tag it has never heard of rather than
    refusing to; that is the whole of the asymmetry in one function."""
    assert known("alt_text") is True
    assert known("no_such_capability") is False
    assert known("") is False


# -- the vocabulary file is itself validated -----------------------------------------------------


def test_a_duplicate_tag_in_the_vocabulary_is_rejected() -> None:
    text = """
version: 1
groups: {a: A group}
tags:
  - {tag: one, group: a, summary: The first.}
  - {tag: one, group: a, summary: The same name again.}
"""
    with pytest.raises(CapabilityVocabularyError, match="declared twice"):
        parse_vocabulary(text, origin="probe")


def test_a_tag_in_an_undeclared_group_is_rejected() -> None:
    text = """
version: 1
groups: {a: A group}
tags:
  - {tag: one, group: elsewhere, summary: Filed under nothing.}
"""
    with pytest.raises(CapabilityVocabularyError, match="not one of"):
        parse_vocabulary(text, origin="probe")


def test_a_tag_with_no_summary_is_rejected() -> None:
    """The summary is what a person authoring a pack reads to decide whether to bind the tag. A
    bare identifier with nothing behind it is a binding nobody can make on purpose."""
    text = """
version: 1
groups: {a: A group}
tags:
  - {tag: one, group: a, summary: "  "}
"""
    with pytest.raises(CapabilityVocabularyError, match="'summary' is required"):
        parse_vocabulary(text, origin="probe")


@pytest.mark.parametrize(
    "text",
    [
        "version: one\ngroups: {a: A}\ntags: [{tag: x, group: a, summary: Y.}]",
        "version: 1\ngroups: {}\ntags: [{tag: x, group: a, summary: Y.}]",
        "version: 1\ngroups: {a: A}\ntags: []",
        "- not a mapping",
    ],
)
def test_a_malformed_vocabulary_is_rejected_rather_than_read_as_empty(text: str) -> None:
    """An empty or half-read vocabulary would make every detector's tag unknown, which the
    interface turns into a hard failure — loud, but for the wrong reason. It raises here instead,
    naming the file."""
    with pytest.raises(CapabilityVocabularyError):
        parse_vocabulary(text, origin="probe")
