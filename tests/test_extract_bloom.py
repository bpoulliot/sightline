"""The task-verb table: the step SPEC §01 deliberately does not give to a model.

What these assert is that the table is a *lookup* — total, inspectable, and silent about quality.
The last two tests are the ones that matter to the product rather than to the code: a verb the
table does not carry is reported as such, and nothing here ever produces a rating.
"""

from __future__ import annotations

from sightline.extract import bloom


def test_the_first_verb_in_the_sentence_is_the_task_verb() -> None:
    match = bloom.classify("Analyze the two proposals and list your conclusions.")
    assert match is not None
    assert (match.verb, match.level) == ("analyze", "analyze")


def test_inflections_are_matched() -> None:
    for sentence, verb in (
        ("Students will be analyzing the data.", "analyze"),
        ("Learners identified the parts.", "identify"),
        ("You will be writing a memo.", "write"),
        ("Students wrote a memo.", "write"),
        ("She summarises the reading.", "summarise"),
    ):
        match = bloom.classify(sentence)
        assert match is not None, sentence
        assert match.verb == verb


def test_both_spellings_are_carried() -> None:
    assert bloom.level_of("analyse") == bloom.level_of("analyze") == "analyze"
    assert bloom.level_of("summarise") == bloom.level_of("summarize") == "understand"
    assert bloom.level_of("organise") == bloom.level_of("organize") == "analyze"


def test_a_two_word_verb_is_matched_whole() -> None:
    match = bloom.classify("You will be carrying out the procedure.")
    assert match is not None
    assert match.verb == "carry out"
    assert match.written == "carrying out"


def test_a_sentence_with_no_table_verb_returns_none_rather_than_a_guess() -> None:
    assert bloom.classify("Read chapter three before Friday.") is None
    assert bloom.classify("") is None


def test_the_distribution_keeps_the_zeros_and_counts_the_unclassified() -> None:
    tally = bloom.distribution(["remember", "remember", "create", None])
    assert tally == {
        "remember": 2,
        "understand": 0,
        "apply": 0,
        "analyze": 0,
        "evaluate": 0,
        "create": 1,
        "no table verb": 1,
    }


def test_the_levels_are_the_taxonomys_six_in_order() -> None:
    assert bloom.LEVELS == (
        "remember",
        "understand",
        "apply",
        "analyze",
        "evaluate",
        "create",
    )
    assert set(bloom.TABLE) == set(bloom.LEVELS)


def test_no_verb_is_claimed_by_two_levels() -> None:
    seen: dict[str, str] = {}
    for level, verbs in bloom.TABLE.items():
        for verb in verbs:
            assert verb not in seen, f"{verb} is in both {seen.get(verb)} and {level}"
            seen[verb] = level


def test_nothing_in_the_table_module_produces_a_rating() -> None:
    """A distribution is a count per level. There is no function here that judges a course."""
    public = [name for name in dir(bloom) if not name.startswith("_")]
    for name in public:
        assert not any(
            word in name.lower() for word in ("rate", "rating", "score", "grade", "quality")
        ), name
    tally = bloom.distribution(["create", None])
    assert all(isinstance(count, int) for count in tally.values())
    assert set(tally) == {*bloom.LEVELS, "no table verb"}
