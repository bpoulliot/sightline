"""The two packs Sightline ships, checked against the workbooks they came from.

**Every number here was read out of the committed files.** Where SPEC §03 states the same number it
is noted as an agreement rather than cited as the source; where the spec and the file disagree the
file wins and the disagreement is reported, never edited away.

The two instruments are structurally unalike and that is the point of shipping both. One has ten
sheets, a category column, a composite code, an OPEN SUNY number, an origin flag and a priority
flag; the other has one sheet, section headings sitting in the index column, none of those
identifiers, and does not open in openpyxl's default mode at all. **No importer code distinguishes
them.** The only difference between the two packs is a mapping file, which is the claim roadmap
item 04 made and what these tests exist to hold.

The traps asserted below, all of them properties of the real files:

* **A category-and-index join is wrong and looks right.** The index column mixes float and int
  inside one sheet, so a join between two sheets of the same workbook on category-plus-index
  matches 7 rows of 69 while column F matches 69 of 69.
* **The assessment block skips an index**, running 1, 2, 3, 4, 5, 7.
* **The mobile block carries no priority flag at all**, and nothing fills one in.
* **Regenerating a pack is byte-identical**, because the import date is fixed in the mapping file
  rather than read off the clock.
* **Neither pack refers to the other.** No crosswalk exists in either direction.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import openpyxl
import pytest

from sightline.capabilities import vocabulary
from sightline.detect.base import Tier
from sightline.detect.runner import DetectionReport
from sightline.rubric.coverage import PackCoverage, pack_coverage
from sightline.rubric.importer import import_workbook
from sightline.rubric.mapping import load as load_mapping
from sightline.rubric.pack import RubricPack
from sightline.rubric.project import TagIndex, project
from sightline.rubric.shipped import pack_paths, packs_dir, shipped_pack, shipped_packs
from sightline.rubric.states import Coverage, Rating

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "rubrics" / "source"
MAPS = REPO / "rubrics" / "maps"

REFERENCE_ID = "uccs-oscqr-3-1-3"
RSI_ID = "uccs-oscqr-rsi"

REFERENCE_WORKBOOK = SOURCE / "UCCS OSCQR 3.1.3.xlsx"
RSI_WORKBOOK = SOURCE / "UCCS OSCQR RSI Review.xlsm"

#: Counted category by category off the Instructional Designer sheet. SPEC §03 agrees.
REFERENCE_CATEGORIES = {
    "COAL": 7,
    "GSOV": 12,
    "OREP": 11,
    "ACD": 11,
    "ENIN": 12,
    "ASFE": 6,
    "MOB": 9,
}
#: The RSI instrument's six section headings, in sheet order, with the rows under each.
RSI_SECTIONS = {"1": 2, "2": 2, "3": 1, "4": 6, "5": 5, "6": 2}

REFERENCE_TOTAL = 68
RSI_TOTAL = 18

#: The tier each pack was authored at, counted. Locked so that a change to a mapping file shows up
#: here and gets read, rather than arriving inside a 68-row diff. SPEC §02's own coverage table
#: says 29 / 21 / 14 / 2 for the reference instrument and does not reconcile with §05's detector
#: inventory under any rule tried; see rubrics/maps/uccs-oscqr-3-1-3.yaml for the rule used.
REFERENCE_TIERS = {Tier.A: 23, Tier.B: 35, Tier.C: 9, Tier.D: 1}
RSI_TIERS = {Tier.A: 7, Tier.B: 9, Tier.C: 2}

#: What each pack can be answered by **in this build**, measured. These move as detectors land —
#: items 07 and 08 turn most of the middle column into the first — and a change here is expected
#: and should be read, not silenced. The three numbers are kept apart on purpose: summing them,
#: or reporting the middle one as though it were the third, is the silent under-detection SPEC §11
#: names.
REFERENCE_COVERAGE = {
    "deterministic": 10,
    "not_yet_implemented": 39,
    "evidence_only": 18,
    "not_determinable": 1,
}
RSI_COVERAGE = {
    "deterministic": 5,
    "not_yet_implemented": 9,
    "evidence_only": 4,
    "not_determinable": 0,
}


@lru_cache(maxsize=1)
def reference() -> RubricPack:
    return shipped_pack(REFERENCE_ID)


@lru_cache(maxsize=1)
def rsi() -> RubricPack:
    return shipped_pack(RSI_ID)


@lru_cache(maxsize=2)
def coverage_of_pack(pack_id: str) -> PackCoverage:
    return pack_coverage(shipped_pack(pack_id))


def both() -> list[RubricPack]:
    return [reference(), rsi()]


# ---------------------------------------------------------------------------------------------
# They are shipped, and they load
# ---------------------------------------------------------------------------------------------


def test_exactly_two_packs_are_shipped() -> None:
    assert sorted(path.name for path in pack_paths()) == [
        "uccs-oscqr-3-1-3.yaml",
        "uccs-oscqr-rsi.yaml",
    ]
    assert packs_dir() == (REPO / "rubrics" / "packs").resolve()


def test_both_packs_load_and_validate() -> None:
    packs = shipped_packs()
    assert sorted(packs) == [REFERENCE_ID, RSI_ID]
    for pack in packs.values():
        assert pack.schema_version == 1
        assert pack.criteria


def test_an_unknown_pack_id_names_what_is_available() -> None:
    with pytest.raises(KeyError, match=RSI_ID):
        shipped_pack("quality-matters")


# ---------------------------------------------------------------------------------------------
# The counts, read from the workbooks
# ---------------------------------------------------------------------------------------------


def test_the_reference_pack_has_sixty_eight_rows_in_seven_categories() -> None:
    pack = reference()
    assert len(pack.criteria) == REFERENCE_TOTAL
    counts = Counter(criterion.section for criterion in pack.criteria)
    assert dict(counts) == REFERENCE_CATEGORIES
    assert sum(counts.values()) == REFERENCE_TOTAL
    assert [section.code for section in pack.sections] == list(REFERENCE_CATEGORIES)


def test_the_rsi_pack_has_eighteen_rows_in_six_sections() -> None:
    pack = rsi()
    assert len(pack.criteria) == RSI_TOTAL
    counts = Counter(criterion.section for criterion in pack.criteria)
    assert dict(counts) == RSI_SECTIONS
    assert sum(counts.values()) == RSI_TOTAL


def test_the_assessment_block_skips_an_index() -> None:
    """ASFE runs 1, 2, 3, 4, 5, **7**. Anything walking positions misaligns every row after it."""
    codes = [c.code for c in reference().criteria if c.section == "ASFE"]
    assert codes == ["ASFE 1", "ASFE 2", "ASFE 3", "ASFE 4", "ASFE 5", "ASFE 7"]


def test_every_row_code_is_unique_in_each_pack() -> None:
    for pack in both():
        codes = [criterion.code for criterion in pack.criteria]
        assert len(set(codes)) == len(codes)


def test_criterion_text_came_from_the_workbook_not_the_lossy_csv_export() -> None:
    """The RSI CSV export has already lost its curly quotes to U+FFFD; the workbook has not."""
    row = rsi().criterion("8")
    assert row is not None
    assert "�" not in row.text
    assert "“click here”" in row.text
    for pack in both():
        for criterion in pack.criteria:
            assert "�" not in criterion.text


# ---------------------------------------------------------------------------------------------
# The mobile block, which carries no priority flag
# ---------------------------------------------------------------------------------------------


def test_the_nine_mobile_rows_carry_a_null_priority() -> None:
    mobile = [criterion for criterion in reference().criteria if criterion.section == "MOB"]
    assert len(mobile) == 9
    assert [criterion.priority for criterion in mobile] == [None] * 9


def test_every_row_outside_the_mobile_block_carries_a_priority() -> None:
    """Nine nulls, and exactly nine: a null anywhere else would be a dropped flag, not the
    instrument's own silence."""
    unflagged = [
        criterion.code
        for criterion in reference().criteria
        if criterion.priority is None and criterion.section != "MOB"
    ]
    assert unflagged == []
    assert reference().priority_order == ("Essential", "Important")


def test_a_null_priority_sorts_after_every_labelled_one() -> None:
    pack = reference()
    ordered = sorted(pack.criteria, key=lambda criterion: pack.priority_rank(criterion.priority))
    ranks = [pack.priority_rank(criterion.priority) for criterion in ordered]
    assert ranks == sorted(ranks)
    assert all(criterion.priority is None for criterion in ordered[-9:])
    # And nothing filled the gap in with a default on the way through.
    assert sum(1 for criterion in ordered if criterion.priority is None) == 9


def test_the_rsi_instrument_has_no_priority_column_at_all() -> None:
    pack = rsi()
    assert pack.priority_order == ()
    assert all(criterion.priority is None for criterion in pack.criteria)


# ---------------------------------------------------------------------------------------------
# THE JOIN KEY. Column F, and the plausible wrong answer beside it.
# ---------------------------------------------------------------------------------------------


def test_every_reference_row_carries_the_column_f_join_key() -> None:
    keys = [criterion.source_ids.get("join_key") for criterion in reference().criteria]
    assert all(isinstance(key, str) and key for key in keys)
    assert len(set(keys)) == REFERENCE_TOTAL
    assert keys[0] == "1a"
    assert keys[-9:] == [f"M-{letter}" for letter in "ABCDEFGHI"]


def test_a_category_and_index_join_would_have_been_wrong() -> None:
    """**The trap, reproduced from the committed workbook.**

    Column Q holds the within-category index and mixes float with int *inside one sheet* — and
    differently between sheets — so ``COAL 1.0`` and ``COAL 1`` both occur in the raw data. Joining
    the canonical sheet to its duplicate on category-plus-index matches a handful of rows out of
    sixty-nine and looks like a join. Column F matches every one of them.

    This is what the pack's ``join_key`` is for and why the mapping file declares the column rather
    than letting anything guess it.
    """
    book = openpyxl.load_workbook(REFERENCE_WORKBOOK, data_only=True)

    def keys(sheet_name: str) -> tuple[set[str], set[str]]:
        sheet = book[sheet_name]
        column_f: set[str] = set()
        category_and_index: set[str] = set()
        for row in range(1, min(sheet.max_row, 600) + 1):
            text = sheet.cell(row, 18).value  # column R, the criterion text
            if not isinstance(text, str) or len(text) < 25:
                continue
            column_f.add(str(sheet.cell(row, 6).value))
            category_and_index.add(f"{sheet.cell(row, 16).value} {sheet.cell(row, 17).value}")
        return column_f, category_and_index

    try:
        canonical_f, canonical_pair = keys("Instructional Designer")
        duplicate_f, duplicate_pair = keys("Faculty")
    finally:
        book.close()

    assert len(canonical_f) == len(duplicate_f) == 69  # 68 criteria plus one heading row
    assert canonical_f == duplicate_f  # column F: 69 of 69
    assert len(canonical_pair & duplicate_pair) == 7  # category+index: 7 of 69
    assert len(canonical_pair & duplicate_pair) < len(duplicate_pair) / 5


def test_the_index_column_really_does_mix_float_and_int() -> None:
    """The mechanism behind the test above, measured directly rather than inferred from it."""
    book = openpyxl.load_workbook(REFERENCE_WORKBOOK, data_only=True)
    try:
        sheet = book["Instructional Designer"]
        types = Counter(type(sheet.cell(row, 17).value).__name__ for row in range(7, 86))
    finally:
        book.close()
    assert types["float"] == 62
    assert types["int"] == 6
    assert types["float"] + types["int"] == REFERENCE_TOTAL


def test_a_join_key_that_repeats_is_refused() -> None:
    """Declaring a column is not the same as trusting it. Column E holds the within-category
    letter, which repeats across categories, and offering it as a join key raises."""
    from sightline.rubric.importer import RubricImportError
    from sightline.rubric.mapping import loads as load_mapping_text

    mapping = load_mapping_text(
        'pack: probe\nname: Probe\nversion: "1"\njoin_key_column: E\ncriteria: {}\n',
        origin="<probe>",
    )
    with pytest.raises(RubricImportError, match="join key"):
        import_workbook(REFERENCE_WORKBOOK, mapping=mapping)


# ---------------------------------------------------------------------------------------------
# Regeneration. One command, and the same bytes.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("workbook", "mapping_name", "pack_name"),
    [
        (REFERENCE_WORKBOOK, "uccs-oscqr-3-1-3.yaml", "uccs-oscqr-3-1-3.yaml"),
        (RSI_WORKBOOK, "uccs-oscqr-rsi.yaml", "uccs-oscqr-rsi.yaml"),
    ],
    ids=["reference", "rsi"],
)
def test_regenerating_a_pack_reproduces_the_committed_file_byte_for_byte(
    workbook: Path, mapping_name: str, pack_name: str
) -> None:
    """What ``scripts/build-packs.sh`` does, asserted on the bytes.

    A pack that cannot be regenerated is a pack that stops being regenerated, and the instruments
    will be revised (SPEC §11). The import date lives in the mapping file rather than coming off
    the clock, which is what makes this comparison possible at all.
    """
    result = import_workbook(workbook, mapping=load_mapping(MAPS / mapping_name))
    committed = (packs_dir() / pack_name).read_text(encoding="utf-8")
    assert result.pack.to_yaml() == committed


def test_the_mapping_files_name_every_row_and_no_row_that_does_not_exist() -> None:
    for workbook, mapping_name, total in (
        (REFERENCE_WORKBOOK, "uccs-oscqr-3-1-3.yaml", REFERENCE_TOTAL),
        (RSI_WORKBOOK, "uccs-oscqr-rsi.yaml", RSI_TOTAL),
    ):
        result = import_workbook(workbook, mapping=load_mapping(MAPS / mapping_name))
        assert result.report.rows_read == total
        assert result.report.rows_mapped == total
        assert result.report.rows_unmapped == ()


def test_a_mapping_naming_a_row_the_workbook_does_not_have_is_fatal() -> None:
    """SPEC §11's rubric drift, arriving through the instrument. A binding written against a row
    that has been renumbered or removed points at nothing, and the one outcome never to allow is
    for that to pass quietly."""
    from sightline.rubric.importer import RubricImportError
    from sightline.rubric.mapping import loads as load_mapping_text

    mapping = load_mapping_text(
        'pack: probe\nname: Probe\nversion: "1"\ncriteria:\n  "ACD 99":\n    tier: A\n',
        origin="<probe>",
    )
    with pytest.raises(RubricImportError, match="ACD 99"):
        import_workbook(REFERENCE_WORKBOOK, mapping=mapping)


# ---------------------------------------------------------------------------------------------
# NO CROSSWALK, IN EITHER DIRECTION
# ---------------------------------------------------------------------------------------------

#: A criterion identifier of the reference instrument: a category code followed by an index.
OSCQR_CODE = re.compile(r"\b(COAL|GSOV|OREP|ACD|ENIN|ASFE|MOB)[\s-]*\d")


def test_the_rsi_pack_contains_no_reference_instrument_criterion_identifiers() -> None:
    """SPEC §03 observes that the RSI instrument collapses the 68 into 18 and that its criterion 8
    is ACD 5 verbatim. That observation is why detectors are primary; it is **not** a mapping. This
    pack reaches ``link_text`` because its own text is about link text, and nothing in the file
    mentions the other instrument's numbering."""
    text = (packs_dir() / "uccs-oscqr-rsi.yaml").read_text(encoding="utf-8")
    assert OSCQR_CODE.search(text) is None
    assert REFERENCE_ID not in text
    assert "3.1.3" not in text


def test_the_reference_pack_does_not_mention_the_rsi_pack() -> None:
    text = (packs_dir() / "uccs-oscqr-3-1-3.yaml").read_text(encoding="utf-8")
    assert RSI_ID not in text
    assert "RSI" not in text


def test_neither_mapping_file_refers_to_the_other_instrument() -> None:
    """The crosswalk must not exist in the authoring files either — that is where one would be
    written first."""
    reference_map = (MAPS / "uccs-oscqr-3-1-3.yaml").read_text(encoding="utf-8")
    rsi_map = (MAPS / "uccs-oscqr-rsi.yaml").read_text(encoding="utf-8")
    assert RSI_ID not in reference_map
    assert REFERENCE_ID not in rsi_map
    # The RSI mapping may *explain* that it is not a crosswalk; it may not carry one, and a
    # binding keyed on the other instrument's codes is what one would look like.
    assert not any(
        OSCQR_CODE.match(code) for code in load_mapping(MAPS / "uccs-oscqr-rsi.yaml").criteria
    )


def test_criterion_eight_and_acd_five_reach_the_same_capability_independently() -> None:
    """Both rows are about link text, so both bind ``link_text``. **That is the architecture
    working**: one detector fires once and each pack renders it at its own row. Neither pack knows
    the other exists, which is exactly what a third-party rubric needs to be true."""
    acd_5 = reference().criterion("ACD 5")
    rsi_8 = rsi().criterion("8")
    assert acd_5 is not None and rsi_8 is not None
    assert acd_5.bindings == ("link_text",)
    assert rsi_8.bindings == ("link_text",)


# ---------------------------------------------------------------------------------------------
# The bands. Pack data on both, matching SPEC §07.
# ---------------------------------------------------------------------------------------------


def test_both_packs_declare_the_same_three_bands_as_pack_data() -> None:
    """Both instruments print these three ranges as column headers — the reference sheet on rows
    3-4, the RSI sheet on rows 6-7. Two independent instruments printing the same thresholds is a
    corroboration of SPEC §07, not a reason to compile them in."""
    expected = [
        ("minor", "Minor Revision", 0.5, "1/2 hour or less"),
        ("moderate", "Moderate Revision", 2.0, "1/2-2 hours"),
        ("major", "Major Revision", None, "2+ hours"),
    ]
    for pack in both():
        bands = [
            (band.rating, band.label, band.up_to_hours, band.range_label)
            for band in pack.rating_scale.bands
        ]
        assert bands == expected
        # Declared in the file rather than defaulted in by the loader.
        text = (packs_dir() / f"{pack.id}.yaml").read_text(encoding="utf-8")
        assert "1/2 hour or less" in text
        assert "2+ hours" in text


def test_both_instruments_really_do_print_the_three_ranges_the_packs_declare() -> None:
    """**The corroboration, measured off the files rather than asserted about them.**

    SPEC §07 says the bands are the instrument's own and need no calibration, and three docstrings
    in this codebase repeat where they are printed. Nothing read them. This does: the reference
    sheet's rating names are on row 3 with the ranges directly beneath on row 4, and the RSI sheet
    prints the same pair on rows 6 and 7. Two unrelated instruments printing identical thresholds
    is what makes §07 a reading rather than a guess — and the day a revised instrument prints
    different ones, this is where it surfaces (SPEC §11) instead of a pack quietly banding a course
    against thresholds its own rubric no longer uses.

    **The rating NAMES differ between the two and the ranges do not.** The RSI sheet prints
    "Minor Revisions" where the reference prints "Minor Revision", and "Not applicable" for "Not
    Applicable". SPEC §07's table is the canonical wording and both packs carry it; the difference
    is recorded here rather than smoothed over, because it is the sort of thing that looks like a
    bug to the next person to open the file.
    """
    declared = [band.range_label for band in reference().rating_scale.bands]
    assert declared == [band.range_label for band in rsi().rating_scale.bands]

    book = openpyxl.load_workbook(REFERENCE_WORKBOOK, data_only=True)
    try:
        sheet = book["Instructional Designer"]
        # Columns T, U, V: the three revision ratings, and the ranges directly beneath them.
        printed_names = [sheet.cell(3, column).value for column in (20, 21, 22)]
        printed_ranges = [sheet.cell(4, column).value for column in (20, 21, 22)]
    finally:
        book.close()
    assert printed_ranges == declared
    assert printed_names == [band.label for band in reference().rating_scale.bands]

    # read_only, for the reason the workaround test below states in full.
    book = openpyxl.load_workbook(RSI_WORKBOOK, data_only=True, read_only=True)
    try:
        rows = list(book["Course Review"].iter_rows(6, 7, 4, 6, values_only=True))
    finally:
        book.close()
    rsi_names, rsi_ranges = list(rows[0]), list(rows[1])
    assert rsi_ranges == declared
    # Plural on this instrument, singular on the other and in SPEC §07. Stated, not corrected.
    assert rsi_names == ["Minor Revisions", "Moderate Revisions", "Major Revisions"]
    assert rsi_names != [band.label for band in rsi().rating_scale.bands]


def test_the_last_band_is_open_ended_and_sufficiently_present_has_none() -> None:
    for pack in both():
        assert pack.rating_scale.bands[-1].up_to_hours is None
        assert pack.rating_scale.sufficiently_present == "Sufficiently Present"
        assert pack.rating_scale.no_response == "No Response"


# ---------------------------------------------------------------------------------------------
# THE THREE STATES, kept apart
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pack_id", "expected"),
    [(REFERENCE_ID, REFERENCE_COVERAGE), (RSI_ID, RSI_COVERAGE)],
    ids=["reference", "rsi"],
)
def test_the_coverage_report_reports_all_three_states_separately(
    pack_id: str, expected: dict[str, int]
) -> None:
    report = coverage_of_pack(pack_id)
    tally = report.counts
    assert {key: tally[key] for key in expected} == expected
    # Exhaustive and disjoint over every rendered row — the report's own arithmetic self-check.
    assert report.ok


def test_no_bucket_is_summed_into_another() -> None:
    """A row bound to a capability nothing implements yet is neither covered nor evidence-only.
    Reporting it as either is the conflation the three states exist to prevent."""
    for pack_id in (REFERENCE_ID, RSI_ID):
        report = coverage_of_pack(pack_id)
        pending = {row.code for row in report.not_yet_implemented}
        assert pending.isdisjoint({row.code for row in report.deterministic})
        assert pending.isdisjoint({row.code for row in report.evidence_only})
        for row in report.not_yet_implemented:
            assert row.coverage is Coverage.NOT_YET_IMPLEMENTED
            assert row.resolved == ()
            assert row.pending != ()
            assert row.detectors == ()


def test_the_coverage_report_never_words_an_unbound_row_as_a_fault() -> None:
    """An unbound row is the ordinary, permanent state for a rubric Sightline ships no mapping
    for, and 18 of the reference instrument's rows are in it today."""
    for pack_id in (REFERENCE_ID, RSI_ID):
        detail = coverage_of_pack(pack_id).detail().casefold()
        for word in ("degraded", "incomplete", "unsupported", "missing", "failed"):
            assert word not in detail


def test_nothing_this_build_does_turns_not_yet_implemented_into_a_clean_reading() -> None:
    """**The headline invariant.** Projecting a run in which nothing ran must leave every row that
    is waiting on a detector at the instrument's default. If an absent detector could produce
    Sufficiently Present, Sightline would report a course clean on criteria it never examined."""
    for pack in both():
        projection = project(pack, DetectionReport(results=()))
        by_code = {row.code: row for row in projection.rows}
        pending = coverage_of_pack(pack.id).not_yet_implemented
        assert pending
        for entry in pending:
            row = by_code[entry.code]
            assert row.coverage is Coverage.NOT_YET_IMPLEMENTED
            assert row.rating is not Rating.SUFFICIENTLY_PRESENT
            assert row.rating is Rating.NO_RESPONSE
        assert all(row.rating is not Rating.SUFFICIENTLY_PRESENT for row in projection.rows)


def test_an_unbound_row_routes_to_extraction_rather_than_reading_clean() -> None:
    for pack in both():
        projection = project(pack, DetectionReport(results=()))
        unbound = [row for row in projection.rows if not row.bindings and row.tier is not Tier.D]
        assert unbound
        for row in unbound:
            assert row.coverage is Coverage.NO_DETERMINISTIC_COVERAGE
            assert row.needs_extraction
            assert row.rating is not Rating.SUFFICIENTLY_PRESENT


def test_the_tier_d_row_defaults_to_not_applicable_and_is_not_routed() -> None:
    projection = project(reference(), DetectionReport(results=()))
    row = next(entry for entry in projection.rows if entry.code == "MOB 5")
    assert row.tier is Tier.D
    assert row.rating is Rating.NOT_APPLICABLE
    assert not row.needs_extraction


# ---------------------------------------------------------------------------------------------
# The bindings themselves
# ---------------------------------------------------------------------------------------------


def test_every_binding_is_a_capability_tag_this_build_recognises() -> None:
    """Our own typo check. A *third-party* pack binding an unknown tag is a reported warning and
    its row degrades — that asymmetry is deliberate and is item 04's — but the two packs Sightline
    ships are ours, and a tag misspelled in one of them is a bug."""
    known = set(vocabulary().tags)
    for pack in both():
        for criterion in pack.criteria:
            unknown = set(criterion.bindings) - known
            assert not unknown, f"{pack.id} {criterion.code} binds {sorted(unknown)}"
        assert coverage_of_pack(pack.id).unknown_tags == ()


def test_no_binding_is_a_detector_id() -> None:
    """A pack binds concepts so that it survives a detector refactor. ``D21`` is not a concept."""
    for pack in both():
        for criterion in pack.criteria:
            for tag in criterion.bindings:
                assert not re.fullmatch(r"[DE]\d{2}", tag)


def test_every_capability_in_the_vocabulary_is_bound_by_a_shipped_pack() -> None:
    """A concept nothing points at is a concept nobody asked for. The reference instrument alone
    binds all 46, which is a fact about that instrument's breadth rather than a requirement."""
    bound = {tag for pack in both() for criterion in pack.criteria for tag in criterion.bindings}
    assert bound == set(vocabulary().tags)


def test_tier_d_rows_carry_no_binding() -> None:
    """Out of scope by the instrument's own reading, so nothing computed may attach to it — and a
    row counted both as covered and as not determinable would break the coverage arithmetic."""
    for pack in both():
        for criterion in pack.criteria:
            if criterion.tier is Tier.D:
                assert criterion.bindings == ()


def test_every_tier_a_row_binds_at_least_one_capability() -> None:
    """Tier A means *computed by a parser*. A row declaring that with nothing bound to it is a
    contradiction: there is no parser, and the row would quietly route for quotes instead."""
    for pack in both():
        for criterion in pack.criteria:
            if criterion.tier is Tier.A:
                assert criterion.bindings, f"{pack.id} {criterion.code}"


def test_every_row_with_deterministic_coverage_today_is_tier_a() -> None:
    """**A guard, and it is expected to fire.**

    A Tier A row is settled by its detectors and is not routed for quotes; a Tier B row is not, and
    gathers located evidence as well. Today every row a detector answers is Tier A, so a clean run
    can only close a row the mapping says it settles.

    Items 07 and 08 will bind capabilities to Tier B rows — ``ACD 10`` binds
    ``timed_assessment_disclosure``, which measures disclosure where the row also asks about lead
    time — and this test will fail when the first of them lands. **That is the moment to look**:
    the pack format has one binding channel and it carries both *contributes evidence* and *can
    settle this row*, so a partial capability on a Tier B row can read Sufficiently Present. See
    roadmap item 28. Update the exception list below only with that read done.
    """
    allowed_tier_b_with_coverage: set[tuple[str, str]] = set()
    for pack in both():
        for row in coverage_of_pack(pack.id).deterministic:
            if (pack.id, row.code) in allowed_tier_b_with_coverage:
                continue
            assert row.tier is Tier.A, f"{pack.id} {row.code} is tier {row.tier.value}"


@pytest.mark.parametrize(
    ("pack_id", "expected"),
    [(REFERENCE_ID, REFERENCE_TIERS), (RSI_ID, RSI_TIERS)],
    ids=["reference", "rsi"],
)
def test_the_tier_distribution_is_the_one_the_mapping_file_declares(
    pack_id: str, expected: dict[Tier, int]
) -> None:
    counts = Counter(criterion.tier for criterion in shipped_pack(pack_id).criteria)
    assert dict(counts) == expected


# ---------------------------------------------------------------------------------------------
# The optional section, which is item 08's render flag
# ---------------------------------------------------------------------------------------------


def test_the_mobile_section_is_the_only_optional_one_and_ships_switched_on() -> None:
    pack = reference()
    optional = [section.code for section in pack.sections if section.optional]
    assert optional == ["MOB"]
    assert all(section.enabled for section in pack.sections)
    assert pack.section("MOB") is not None and pack.section("MOB").note


def test_switching_the_mobile_section_off_reports_its_rows_as_omitted() -> None:
    """Omitted, not vanished. Nine rows leave the review and the report says which nine."""
    pack = reference().with_section_enabled("MOB", False)
    assert len(pack.rendered_criteria()) == REFERENCE_TOTAL - 9
    report = pack_coverage(pack)
    assert len(report.omitted) == 9
    assert report.counts["criteria"] == REFERENCE_TOTAL - 9
    assert report.ok
    assert set(report.omitted) == {f"MOB {number}" for number in range(1, 10)}


def test_a_mandatory_section_cannot_be_switched_off() -> None:
    from sightline.rubric.pack import RubricPackError

    with pytest.raises(RubricPackError):
        reference().with_section_enabled("ACD", False)


def test_the_rsi_pack_declares_no_optional_section() -> None:
    """There is no mobile block on that instrument, and nothing invented one."""
    assert all(not section.optional for section in rsi().sections)
    assert len(rsi().sections) == 6


# ---------------------------------------------------------------------------------------------
# The workbooks themselves, so a library change is found here rather than by a user
# ---------------------------------------------------------------------------------------------


def test_the_rsi_workbook_still_needs_the_read_only_workaround() -> None:
    """``load_workbook(..., data_only=True)`` raises ``TypeError: expected <class 'int'>`` on this
    file: it carries whole-row merged-cell references (``ref="8:8"``) that Excel accepts and
    openpyxl's ``CellRange`` refuses. The file is not corrupt and must not be "fixed" — it is a
    committed artifact of another department's instrument.

    The day openpyxl changes behaviour here, this test is how we find out, not a user. Its failure
    would be good news and the fix is to delete the fallback, not to work around it.
    """
    with pytest.raises(TypeError):
        openpyxl.load_workbook(RSI_WORKBOOK, data_only=True)
    book = openpyxl.load_workbook(RSI_WORKBOOK, data_only=True, read_only=True)
    try:
        assert book.sheetnames == ["Course Review"]
    finally:
        book.close()


def test_the_reference_workbook_opens_the_ordinary_way() -> None:
    """Stated beside the one that does not, so the fallback is visibly the exception."""
    book = openpyxl.load_workbook(REFERENCE_WORKBOOK, data_only=True)
    try:
        assert "Instructional Designer" in book.sheetnames
    finally:
        book.close()


def test_the_packs_and_the_detectors_agree_on_what_a_capability_is() -> None:
    """The joint itself: every tag a detector declares is a tag a pack may bind, and the ten this
    build implements are all reachable from the reference pack."""
    index = TagIndex.build()
    bound = {tag for criterion in reference().criteria for tag in criterion.bindings}
    assert set(index.detectors_by_tag) <= bound
