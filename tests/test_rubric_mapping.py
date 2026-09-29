"""The mapping file format — the only thing that differs between two shipped packs.

A mapping file is authored by hand, so every error it can raise names the key that is wrong. The
tests below are mostly about what it **refuses**, because the failures worth catching here are the
quiet ones: a misspelled top-level key that imports as an unmapped pack and looks like a successful
run, an unquoted version that YAML reads as a float, a tag listed twice that doubles a row's effort
inside a sum that is then banded.

One thing it deliberately does **not** refuse is an unrecognised capability tag. That asymmetry is
item 04's and it is load-bearing: a pack binding a tag this build does not know is a reported
warning whose row degrades, never a hard failure, because packs are third-party. Validating tags
here would put a second and stricter rule in front of that one.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from sightline.detect.base import Tier
from sightline.rubric.mapping import MappingError, dumps, load, loads

MINIMAL = 'pack: probe\nname: Probe\nversion: "1"\ncriteria: {}\n'


def test_a_mapping_may_say_almost_nothing() -> None:
    """Every field is optional except the rows, because the format has to serve an instrument that
    carries none of the reference one's identifiers."""
    mapping = loads("criteria: {}\n", origin="<probe>")
    assert mapping.pack_id is None
    assert mapping.criteria == {}
    assert mapping.sections == {}
    assert mapping.join_key_column is None


def test_the_fields_parse() -> None:
    mapping = loads(
        "pack: probe\n"
        "name: Probe instrument\n"
        'version: "2026.1"\n'
        "description: A probe.\n"
        "source_title: Probe — Sheet One\n"
        "sheet: Sheet One\n"
        "imported_at: 2026-09-17\n"
        "join_key_column: f\n"
        "sections:\n"
        "  MOB:\n"
        "    optional: true\n"
        "    note: Applies to a course reviewed for mobile delivery.\n"
        "criteria:\n"
        "  A 1:\n"
        "    tier: A\n"
        "    bindings: [alt_text, link_text]\n"
        "    note: A statement beside this row.\n"
        "  A 2:\n",
        origin="<probe>",
    )
    assert mapping.pack_id == "probe"
    assert mapping.version == "2026.1"
    assert mapping.imported_at == dt.date(2026, 9, 17)
    assert mapping.join_key_column == "F"  # normalized, so `f` and `F` are the same column
    assert mapping.sections["MOB"].optional is True
    assert mapping.sections["MOB"].enabled is None  # unset, not false
    assert mapping.criteria["A 1"].tier is Tier.A
    assert mapping.criteria["A 1"].bindings == ("alt_text", "link_text")
    assert mapping.criteria["A 2"].bindings == ()
    assert mapping.criteria["A 2"].tier is None


def test_a_row_with_no_body_is_a_row_that_keeps_its_defaults() -> None:
    """Naming a row and saying nothing about it is how an author records *checked, nothing to add*.
    It is not the same as leaving the row out, which the import report names separately."""
    mapping = loads("criteria:\n  A 1:\n", origin="<probe>")
    assert list(mapping.criteria) == ["A 1"]
    assert mapping.criteria["A 1"].tier is None
    assert mapping.criteria["A 1"].bindings == ()
    assert mapping.criteria["A 1"].note is None


def test_an_empty_file_is_refused() -> None:
    with pytest.raises(MappingError, match="empty"):
        loads("", origin="<probe>")


def test_a_misspelled_top_level_key_is_refused() -> None:
    """``critera:`` would otherwise import as a pack nothing was mapped onto, which reads as a
    successful run with a suspiciously unbound rubric."""
    with pytest.raises(MappingError, match="critera"):
        loads("pack: probe\ncritera: {}\n", origin="<probe>")


def test_a_misspelled_row_key_is_refused() -> None:
    with pytest.raises(MappingError, match="binding"):
        loads("criteria:\n  A 1:\n    binding: [alt_text]\n", origin="<probe>")


def test_a_misspelled_section_key_is_refused() -> None:
    with pytest.raises(MappingError, match="optionl"):
        loads("sections:\n  MOB:\n    optionl: true\ncriteria: {}\n", origin="<probe>")


def test_an_unquoted_version_is_refused() -> None:
    """YAML reads ``2026.1`` as a float and ``2026.10`` as ``2026.1``. An instrument's version is a
    string; a pack whose version silently changed is worse than one that would not build."""
    with pytest.raises(MappingError, match="quoted"):
        loads("version: 2026.1\ncriteria: {}\n", origin="<probe>")


def test_an_unknown_tier_letter_is_refused_and_says_which_are_allowed() -> None:
    with pytest.raises(MappingError, match="A, B, C, D"):
        loads("criteria:\n  A 1:\n    tier: E\n", origin="<probe>")


def test_a_tier_letter_is_case_insensitive() -> None:
    mapping = loads("criteria:\n  A 1:\n    tier: c\n", origin="<probe>")
    assert mapping.criteria["A 1"].tier is Tier.C


def test_a_single_tag_still_has_to_be_a_list() -> None:
    """``bindings: alt_text`` would otherwise read as the eight tags ``a``, ``l``, ``t``…"""
    with pytest.raises(MappingError, match="list of capability tags"):
        loads("criteria:\n  A 1:\n    bindings: alt_text\n", origin="<probe>")


def test_a_tag_listed_twice_is_refused() -> None:
    """A row's contributing effort is summed and then banded. A tag counted twice doubles a rating
    on a copy-paste, which is exactly the arithmetic error nobody notices."""
    with pytest.raises(MappingError, match="twice"):
        loads("criteria:\n  A 1:\n    bindings: [alt_text, alt_text]\n", origin="<probe>")


def test_a_row_code_written_twice_is_refused_and_says_which_line() -> None:
    """**YAML keeps the last of a duplicated pair and says nothing**, so the first block — its
    tier, its bindings, its note — would be dropped without a word, and the pack would then import,
    validate and ship with a row nobody meant to change. Nothing downstream can notice: the row is
    present and the counts are right. The same instinct as the test above it, one level up.
    """
    text = (
        "criteria:\n"
        "  ACD 5:\n"
        "    tier: A\n"
        "    bindings: [link_text]\n"
        "  ACD 5:\n"
        "    tier: B\n"
    )
    with pytest.raises(MappingError, match="written twice") as raised:
        loads(text, origin="<probe>")
    assert "ACD 5" in str(raised.value)
    assert "line 5" in str(raised.value)  # the second one, which is the one to delete


def test_the_duplicate_check_reaches_every_level_of_the_file() -> None:
    """A repeated ``criteria:`` block is the same mistake at the top level, and a repeated key
    inside one row is the same mistake below it."""
    with pytest.raises(MappingError, match="written twice"):
        loads("criteria:\n  A 1:\n    tier: A\ncriteria:\n  A 2:\n    tier: B\n", origin="<probe>")
    with pytest.raises(MappingError, match="written twice"):
        loads("criteria:\n  A 1:\n    tier: A\n    tier: B\n", origin="<probe>")


def test_a_key_appearing_once_in_each_of_two_rows_is_not_a_duplicate() -> None:
    """The check is per mapping, not per file. Without this the format could not express a second
    row at all, which is the failure mode of a duplicate check written too broadly."""
    mapping = loads("criteria:\n  A 1:\n    tier: A\n  A 2:\n    tier: A\n", origin="<probe>")
    assert sorted(mapping.criteria) == ["A 1", "A 2"]
    assert all(row.tier is Tier.A for row in mapping.criteria.values())


def test_an_unrecognised_capability_tag_is_NOT_refused_here() -> None:
    """**The asymmetry, asserted rather than assumed.** A detector declaring an unknown tag fails
    at class-definition time; a pack binding one is a warning and that row degrades. The mapping
    file feeds the pack side, so it inherits the pack side's rule."""
    mapping = loads("criteria:\n  A 1:\n    bindings: [not_a_real_capability]\n", origin="<probe>")
    assert mapping.criteria["A 1"].bindings == ("not_a_real_capability",)


def test_a_bad_join_key_column_is_refused() -> None:
    with pytest.raises(MappingError, match="column letter"):
        loads("join_key_column: 6\ncriteria: {}\n", origin="<probe>")


def test_a_bad_import_date_is_refused() -> None:
    with pytest.raises(MappingError, match="not a date"):
        loads('imported_at: "the seventeenth"\ncriteria: {}\n', origin="<probe>")


def test_the_origin_is_in_every_message() -> None:
    with pytest.raises(MappingError, match="my-rubric.yaml"):
        loads("nonsense: true\ncriteria: {}\n", origin="my-rubric.yaml")


def test_the_statement_counts_rather_than_concludes() -> None:
    mapping = loads(
        "criteria:\n"
        "  A 1:\n"
        "    bindings: [alt_text]\n"
        "  A 2:\n"
        "    bindings: [link_text, alt_text]\n"
        "  A 3:\n",
        origin="<probe>",
    )
    statement = mapping.statement()
    assert "3 row(s) mapped" in statement
    assert "2 of them bound" in statement
    assert "2 distinct capability tag(s)" in statement


def test_the_top_level_must_be_a_mapping() -> None:
    with pytest.raises(MappingError, match="top level"):
        loads("- a\n- b\n", origin="<probe>")


def test_invalid_yaml_says_so() -> None:
    with pytest.raises(MappingError, match="not valid YAML"):
        loads("criteria: [\n", origin="<probe>")


def test_a_minimal_mapping_round_trips_through_the_public_loader() -> None:
    assert loads(MINIMAL, origin="<probe>").pack_id == "probe"


@pytest.mark.parametrize("name", ["uccs-oscqr-3-1-3", "uccs-oscqr-rsi"])
def test_dumps_writes_what_loads_reads_back(name: str) -> None:
    """Item 22 prints accepted bindings as a mapping file, so the writer has to be the loader's
    exact inverse over everything the format carries — both shipped mappings, field for field."""
    path = Path(__file__).resolve().parents[1] / "rubrics" / "maps" / f"{name}.yaml"
    original = load(path)
    text = dumps(original)
    assert loads(text, origin=original.origin) == original
    assert dumps(loads(text, origin=original.origin)) == text, "a second dump is byte-identical"
