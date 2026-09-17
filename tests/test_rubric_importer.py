"""The workbook importer, run against both committed instruments.

**Every number in here was verified by reading the files, not by reading the spec.** SPEC §03 says
68 criteria for the reference instrument and 18 for the RSI one; both agree with what the importer
reads, and the per-category counts agree cell for cell. That is worth stating because the spec has
been wrong about these files before — its "51 detectors covering all 68 criteria" claim was false
and was corrected to 61 covering 66 — so a disagreement between a count here and a count there is
resolved by reading the workbook, never by editing the test.

The traps, all of them observed in the real files and all of them asserted below:

* ``UCCS OSCQR RSI Review.xlsm`` **does not open in openpyxl's default mode at all.** It raises on
  a malformed merged-cell range. The importer takes a stated fallback and records it; it never
  catches the error and returns a zero-row pack.
* **An import yielding zero rows is an error.** A pack with zero *bindings* is not, and the two
  are asserted separately so they cannot be conflated.
* **One category skips an index.** The assessment block runs 1, 2, 3, 4, 5, 7.
* **The index column mixes float and int inside one sheet**, so ``1.0`` and ``1`` both occur and
  both must render as ``1``.
* **The mobile block carries no priority flag.** Null, not "Important".
* **The two sheets have different column sets**, and one of them has none of the other's
  identifiers.
* **The two CSV exports differ in encoding.** One is clean UTF-8; the other has already lost its
  curly quotes to U+FFFD before Sightline sees it.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from functools import lru_cache
from pathlib import Path

import openpyxl
import pytest

from sightline.rubric.importer import (
    ColumnMap,
    Import,
    RubricImportError,
    import_csv,
    import_workbook,
)
from sightline.rubric.pack import loads, validate

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "rubrics" / "source"
REFERENCE = SOURCE / "UCCS OSCQR 3.1.3.xlsx"
RSI = SOURCE / "UCCS OSCQR RSI Review.xlsm"
REFERENCE_CSV = SOURCE / "UCCS OSCQR 3.1.3 - Instructional Designer.csv"
RSI_CSV = SOURCE / "UCCS OSCQR RSI Review.csv"

#: Read from the workbook, category by category. Matches SPEC §03, which is stated as a fact about
#: the agreement rather than as the source of these numbers.
REFERENCE_CATEGORIES = {
    "COAL": 7,
    "GSOV": 12,
    "OREP": 11,
    "ACD": 11,
    "ENIN": 12,
    "ASFE": 6,
    "MOB": 9,
}
REFERENCE_TOTAL = 68
RSI_TOTAL = 18


@lru_cache(maxsize=1)
def reference() -> Import:
    """Cached: reading a ten-sheet workbook forty times over is the import cost, not a test."""
    return import_workbook(
        REFERENCE,
        sheet="Instructional Designer",
        pack_id="reference-probe",
        name="Reference instrument",
        version="3.1.3",
        imported_at=dt.date(2026, 9, 17),
    )


@lru_cache(maxsize=1)
def rsi() -> Import:
    return import_workbook(
        RSI,
        pack_id="rsi-probe",
        name="RSI instrument",
        version="3.1.3",
        imported_at=dt.date(2026, 9, 17),
    )


# -- the reference instrument --------------------------------------------------------------------


def test_the_reference_workbook_imports_the_row_count_read_from_the_file() -> None:
    result = reference()
    assert len(result.pack.criteria) == REFERENCE_TOTAL
    assert result.report.rows_read == REFERENCE_TOTAL
    assert (result.report.first_row, result.report.last_row) == (7, 85)


def test_the_reference_category_counts_match_the_file_cell_for_cell() -> None:
    counts = Counter(criterion.section for criterion in reference().pack.criteria)
    assert dict(counts) == REFERENCE_CATEGORIES
    assert sum(counts.values()) == REFERENCE_TOTAL


def test_the_count_the_importer_reads_agrees_with_the_spec() -> None:
    """Asserted as an agreement, in that direction.

    The spec has been wrong about these files before. If this ever fails, the workbook is the
    authority and the spec is the thing to correct — not this number.
    """
    assert len(reference().pack.criteria) == REFERENCE_TOTAL  # SPEC §03 says 68
    assert len(rsi().pack.criteria) == RSI_TOTAL  # SPEC §03 says 18


def test_the_assessment_category_skips_index_six() -> None:
    """1, 2, 3, 4, 5, 7. An importer that walked positions would key the sixth row as ``ASFE 6``
    and every finding about the seventh criterion would be reported against a row that does not
    exist on the instrument — silently, because there is nothing to notice."""
    codes = [
        criterion.code for criterion in reference().pack.criteria if criterion.section == "ASFE"
    ]
    assert codes == ["ASFE 1", "ASFE 2", "ASFE 3", "ASFE 4", "ASFE 5", "ASFE 7"]
    assert "ASFE 6" not in codes
    assert reference().pack.criterion("ASFE 6") is None
    assert reference().pack.criterion("ASFE 7") is not None


def test_the_mobile_block_carries_no_priority_at_all() -> None:
    """Null, not "Important". Only the locally-authored criteria are prioritised on this
    instrument; the integrated mobile set has a blank column, and filling it in with a default
    would put nine rows into an action plan on the strength of a guess."""
    pack = reference().pack
    mobile = [criterion for criterion in pack.criteria if criterion.section == "MOB"]
    assert len(mobile) == 9
    assert {criterion.priority for criterion in mobile} == {None}
    # And every other row does carry one, so the null is a property of that block rather than of
    # the import.
    elsewhere = [criterion for criterion in pack.criteria if criterion.section != "MOB"]
    assert all(criterion.priority is not None for criterion in elsewhere)
    assert Counter(criterion.priority for criterion in elsewhere) == {
        "Essential": 32,
        "Important": 27,
    }


def test_the_null_priority_is_reported_rather_than_left_to_be_discovered() -> None:
    notes = " ".join(reference().report.notes)
    assert "9 row(s) carry no priority flag" in notes
    assert "Null, not a default" in notes


def test_an_integral_float_index_renders_without_its_decimal_point() -> None:
    """The index column mixes float and int inside one sheet — 62 float and 6 int on this one —
    so the raw data holds both ``1.0`` and ``1``. Both have to become ``COAL 1`` or the same
    criterion gets two different codes depending on which cell type Excel happened to store."""
    codes = reference().pack.codes
    assert "COAL 1" in codes
    assert "COAL 1.0" not in codes
    assert not any("." in code for code in codes)


def test_the_reference_sections_are_the_instrument_s_own_categories() -> None:
    pack = reference().pack
    assert [section.code for section in pack.sections] == list(REFERENCE_CATEGORIES)
    assert pack.section("MOB").title == "Mobile Standards"
    assert pack.section("COAL").title == "Course Alignment"


def test_the_source_identifiers_are_kept_verbatim() -> None:
    """So a re-import of a revised instrument diffs against something rather than silently
    renumbering criteria (SPEC §11). The spreadsheet row is in there too, because it is what a
    person opens the file to."""
    first = reference().pack.criterion("COAL 1")
    assert first.source_ids["row"] == 7
    assert first.source_ids["category"] == "COAL"
    assert first.source_ids["index"] == "1"
    assert first.source_ids["origin"] == "UCCS"
    # The composite key the instrument prints, carried under the column it came from.
    assert first.source_ids["column_F"] == "1a"

    mobile = reference().pack.criterion("MOB 1")
    assert mobile.source_ids["origin"] == "INT"
    assert mobile.source_ids["column_F"] == "M-A"


def test_the_import_names_which_column_it_read_each_thing_from() -> None:
    """A checkable artifact about the import itself. "Which column did you think held the
    criterion?" is the question a person needs answered before trusting sixty-eight rows."""
    statement = reference().report.columns.statement()
    assert "text=R" in statement
    assert "category=P" in statement
    assert "index=Q" in statement
    assert "priority=H" in statement
    assert "section=C" in statement


def test_the_reference_import_keys_on_the_category_and_index() -> None:
    assert reference().report.key_source == "category_and_index"
    assert reference().pack.source.key_source == "category_and_index"


def test_the_reference_pack_records_where_it_came_from_by_filename_only() -> None:
    """A path can name somebody's home directory, and this file is written into a repository that
    is public."""
    source = reference().pack.source
    assert source.workbook == "UCCS OSCQR 3.1.3.xlsx"
    assert "/" not in source.workbook
    assert source.sheet == "Instructional Designer"
    assert source.imported_at == dt.date(2026, 9, 17)


# -- the RSI instrument, which shares none of the above ------------------------------------------


def test_the_rsi_workbook_does_not_open_in_openpyxls_default_mode() -> None:
    """The trap, asserted against the real file rather than described.

    Nineteen merged-cell records, five of them whole-row references with no column letters, which
    Excel accepts and openpyxl's ``CellRange`` refuses. If this ever stops raising, the fallback
    below has become dead code and should be reconsidered — which is a better thing to find out
    from a red test than from a silent behaviour change.
    """
    with pytest.raises(TypeError):
        openpyxl.load_workbook(RSI, data_only=True)


def test_the_importer_takes_the_read_only_fallback_and_says_it_did() -> None:
    """Never a caught exception and a zero-row pack. The recovery is stated in the report, and the
    rows are actually there."""
    result = rsi()
    notes = " ".join(result.report.notes)
    assert "does not open in openpyxl's default mode" in notes
    assert "TypeError" in notes
    assert "re-opened read-only" in notes
    assert len(result.pack.criteria) == RSI_TOTAL


def test_the_rsi_workbook_imports_eighteen_rows() -> None:
    result = rsi()
    assert len(result.pack.criteria) == RSI_TOTAL
    assert (result.report.first_row, result.report.last_row) == (10, 42)
    assert [criterion.code for criterion in result.pack.criteria] == [
        str(number) for number in range(1, 19)
    ]


def test_the_rsi_scan_stops_rather_than_walking_the_sheets_declared_extent() -> None:
    """The read-only sheet reports 1,048,561 rows against a last criterion on row 42. A scan that
    trusted that number would read a million empty rows on every import."""
    book = openpyxl.load_workbook(RSI, data_only=True, read_only=True)
    try:
        assert book["Course Review"].max_row > 1_000_000
    finally:
        book.close()
    assert rsi().report.last_row == 42


def test_the_rsi_sheet_has_none_of_the_reference_sheets_identifier_columns() -> None:
    """The two instruments are structurally unalike, which is the point: an importer that assumed
    the reference sheet's columns would read nothing here."""
    columns = rsi().report.columns
    assert columns.text == 2
    assert columns.category is None
    assert columns.priority is None
    assert columns.origin is None
    assert columns.letter is None
    # Its sections sit in the same column as its numbering, which the reference sheet's do not.
    assert columns.section == columns.index == 1


def test_the_rsi_sections_come_from_headings_in_a_column_rather_than_a_category_code() -> None:
    pack = rsi().pack
    assert [section.title for section in pack.sections] == [
        "Course Alignment",
        "Getting Started and Overviews",
        "Organization and Representation",
        "Accessible Course Design",
        "Engagement and Interaction",
        "Assessment and Feedback",
    ]
    # No mobile block on this instrument at all.
    assert all("Mobile" not in section.title for section in pack.sections)


def test_the_rsi_criterion_eight_is_the_reference_instruments_link_text_row_verbatim() -> None:
    """The many-to-one relationship SPEC §03 describes, and the reason detectors are primary and
    rubrics are projections: one link-text parser serves both instruments and neither is mapped
    onto the other."""
    eight = rsi().pack.criterion("8")
    reference_row = reference().pack.criterion("ACD 5")
    assert eight.text == reference_row.text
    assert "descriptive text" in eight.text


def test_the_rsi_import_says_its_codes_are_positional_in_substance() -> None:
    """Its numbering is its own — it is printed on the instrument — and it is still a position: a
    revised edition that inserts a criterion renumbers everything after it and a re-import will
    not diff cleanly. There is nothing to be done about that, so it is stated."""
    notes = " ".join(rsi().report.notes)
    assert "positional in substance" in notes
    assert "renumbers everything after it" in notes
    # The reference instrument's codes are not positional, and it gets no such note.
    assert "positional in substance" not in " ".join(reference().report.notes)


def test_the_rsi_instrument_has_no_priority_column_and_says_so() -> None:
    assert all(criterion.priority is None for criterion in rsi().pack.criteria)
    assert rsi().pack.priority_order == ()
    assert "No priority column was found" in " ".join(rsi().report.notes)


# -- what an import produces ---------------------------------------------------------------------


@pytest.mark.parametrize("build", [reference, rsi])
def test_an_imported_pack_has_empty_bindings_and_is_immediately_usable(build) -> None:
    """The shape of the whole item. An import is finished when the rows are in, not when somebody
    has mapped them: no workbook has a column naming a detector, so no import can produce a
    binding, and a rubric that only worked after forty hand-authored bindings would be a
    roadblock this project does not put in front of anybody."""
    pack = build().pack
    assert pack.criteria
    assert all(criterion.bindings == () for criterion in pack.criteria)
    # Usable means it validates and round-trips, not that it is a stub awaiting authoring.
    validate(pack.to_dict(), origin="imported")
    assert loads(pack.to_yaml(), origin="imported") == pack


@pytest.mark.parametrize("build", [reference, rsi])
def test_an_imported_pack_carries_the_bands_printed_on_the_instrument(build) -> None:
    """Both instruments print the same three ranges — the reference sheet on rows 3-4 and the RSI
    sheet on rows 6-7 — so both imports carry them, and they need no calibration."""
    bands = build().pack.rating_scale.bands
    assert [(band.rating, band.up_to_hours) for band in bands] == [
        ("minor", 0.5),
        ("moderate", 2.0),
        ("major", None),
    ]


@pytest.mark.parametrize("build", [reference, rsi])
def test_no_imported_row_names_a_detector_or_a_capability(build) -> None:
    """Neither workbook has a column naming one. The crosswalk columns on the reference sheet map
    its numbering to another OSCQR variant's; they say nothing about detectors and are not a
    binding source."""
    for criterion in build().pack.criteria:
        assert criterion.bindings == ()
        assert "capability" not in criterion.text.lower()


def test_the_import_report_states_counts_rather_than_a_verdict() -> None:
    statement = reference().report.statement()
    assert "68 criterion row(s) read from rows 7-85" in statement
    assert "in 7 section(s)" in statement
    for word in ("success", "failed", "healthy", "valid"):
        assert word not in statement.lower()


# -- choosing a sheet ----------------------------------------------------------------------------


def test_naming_no_sheet_chooses_the_criterion_grid_and_says_what_it_chose_between() -> None:
    """The reference workbook holds ten sheets, of which a blank metadata template, a 459-row
    action-plan aggregate and a change log all carry criterion-shaped text. The choice is scored
    on the identifier columns beside the text, which is what separates a criterion grid from
    something built out of one — and every candidate is reported, so the choice is checkable."""
    result = import_workbook(REFERENCE, pack_id="probe", name="Probe", version="3.1.3")
    assert result.report.sheet == "Instructional Designer"
    assert len(result.pack.criteria) == REFERENCE_TOTAL
    notes = " ".join(result.report.notes)
    assert "No sheet was named" in notes
    assert "'Self-Assessment Action Plan' (hidden, 459 row(s), 1 identifier column(s))" in notes
    assert "Name the sheet explicitly to override this" in notes


def test_naming_a_sheet_that_is_not_there_lists_the_ones_that_are() -> None:
    with pytest.raises(RubricImportError, match="no sheet named 'Nope'") as caught:
        import_workbook(REFERENCE, sheet="Nope")
    assert "Instructional Designer" in str(caught.value)


def test_naming_the_wrong_sheet_is_visible_in_the_report_rather_than_silent() -> None:
    """Every sheet of this workbook carries *some* prose, so naming the wrong one cannot be
    detected by the importer — and the honest answer is not to guess but to say what was read.

    The report names the column and the row count, which is what makes a plainly wrong import
    plainly wrong: five rows out of the change log rather than sixty-eight out of the criterion
    grid. The same report is what makes a *right* import checkable, which is the point.
    """
    result = import_workbook(REFERENCE, sheet="Change Log", pack_id="probe", name="Probe")
    assert result.report.sheet == "Change Log"
    assert result.report.rows_read < 10
    assert "criterion row(s) read from rows" in result.report.statement()
    assert "columns: text=B" in result.report.statement()


# -- zero rows is an error -----------------------------------------------------------------------


def test_an_import_yielding_zero_rows_raises(tmp_path: Path) -> None:
    """The distinction this whole module turns on: **zero ROWS is an error; zero BINDINGS is
    not.** A zero-row pack reported as an empty rubric is a course reviewed against nothing."""
    empty = tmp_path / "empty.xlsx"
    book = openpyxl.Workbook()
    book.active["A1"] = "A heading, and nothing else"
    book.save(empty)
    with pytest.raises(RubricImportError, match="anything shaped like a criterion"):
        import_workbook(empty)


def test_a_workbook_no_reader_can_open_raises_rather_than_importing_nothing(
    tmp_path: Path,
) -> None:
    """Both modes fail, so the import fails. The message names both errors and points at the CSV
    fallback, which is the only other thing there is to try."""
    broken = tmp_path / "broken.xlsx"
    broken.write_bytes(b"this is not a spreadsheet")
    with pytest.raises(RubricImportError, match="cannot be read as a spreadsheet") as caught:
        import_workbook(broken)
    assert "read-only mode raised" in str(caught.value)
    assert "import_csv" in str(caught.value)


def test_a_missing_file_raises_with_its_name(tmp_path: Path) -> None:
    with pytest.raises(RubricImportError, match="nowhere.xlsx does not exist"):
        import_workbook(tmp_path / "nowhere.xlsx")


# -- the CSV fallback ----------------------------------------------------------------------------


def test_the_reference_csv_reads_the_same_rows_as_the_spreadsheet() -> None:
    result = import_csv(REFERENCE_CSV, pack_id="probe", name="Probe", version="3.1.3")
    assert len(result.pack.criteria) == REFERENCE_TOTAL
    assert dict(Counter(c.section for c in result.pack.criteria)) == REFERENCE_CATEGORIES
    assert [c.code for c in result.pack.criteria if c.section == "ASFE"] == [
        "ASFE 1",
        "ASFE 2",
        "ASFE 3",
        "ASFE 4",
        "ASFE 5",
        "ASFE 7",
    ]


def test_the_rsi_csv_reads_eighteen_rows_from_a_completely_different_column_set() -> None:
    result = import_csv(RSI_CSV, pack_id="probe", name="Probe", version="3.1.3")
    assert len(result.pack.criteria) == RSI_TOTAL
    assert result.report.columns.category is None


def test_a_csv_import_says_it_is_a_lossy_fallback_every_time() -> None:
    for path in (REFERENCE_CSV, RSI_CSV):
        notes = " ".join(import_csv(path, pack_id="probe", name="Probe").report.notes)
        assert "lossy fallback" in notes
        assert "a spreadsheet export is authoritative" in notes


def test_the_rsi_csv_has_already_lost_characters_and_the_import_reports_it() -> None:
    """The two exports differ in encoding. The reference sheet's is clean UTF-8 with its curly
    quotes intact; this one has replacement characters where they were, and nothing downstream can
    recover a character the export dropped — so the import says so rather than serving the damage
    as the criterion text."""
    result = import_csv(RSI_CSV, pack_id="probe", name="Probe")
    notes = " ".join(result.report.notes)
    assert "U+FFFD" in notes
    assert "cannot be recovered" in notes
    assert "�" in result.pack.criterion("8").text


def test_the_reference_csv_kept_its_curly_quotes_and_raises_no_encoding_note() -> None:
    result = import_csv(REFERENCE_CSV, pack_id="probe", name="Probe")
    assert "“" in result.pack.criterion("ACD 5").text
    assert "�" not in result.pack.criterion("ACD 5").text
    assert "U+FFFD" not in " ".join(result.report.notes)


def test_a_csv_with_no_rows_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(RubricImportError, match="holds no rows"):
        import_csv(empty)


# -- the column map ------------------------------------------------------------------------------


def test_the_column_map_counts_only_the_identifiers_it_found() -> None:
    """Used to choose between sheets, so the count has to exclude the text column itself."""
    assert ColumnMap(text=3).identifier_count == 0
    assert ColumnMap(text=3, index=2, category=1).identifier_count == 2
