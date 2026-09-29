"""Item 13: the XLSX workbook — exhaustive, CSV-clean, and in the voice layer's words.

The headline test is the CSV round-trip. SPEC §08 requires the workbook to survive one without loss
"which the current instrument does not", and a round-trip exercised only on short ASCII strings
proves nothing — so the fixture plants course text with line breaks, commas, quotes, non-ASCII, a
leading ``=`` and XML-illegal control characters, and a self-test fails if it ever stops doing so.
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import getpass
import io
import re
import socket
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import openpyxl
import pytest
import yaml

from sightline.batch import ExportGate, ReviewNotOpenedError, create_batch_run
from sightline.detect import DetectionReport, Tier
from sightline.extract.runner import RowExtraction
from sightline.model import CourseDocument
from sightline.paths import DataPaths
from sightline.rubric import Coverage, Projection, Rating, project, shipped_packs
from sightline.store import RunStore
from sightline.voice import PhraseBankError, Voice, default_bank, default_register, render_review
from sightline.voice.bank import LABELS, parse_bank
from sightline.voice.render import Piece, verbatim
from sightline.workbook import (
    CELL_LIMIT,
    REPLACEMENT,
    WORKBOOK_FILENAME,
    Cell,
    Sheet,
    WorkbookError,
    WorkbookModel,
    build_workbook,
    clean_text,
    export_workbook,
    to_xlsx,
)
from voice_fixtures import sample_document, sample_projection, stand_in_extractions

REFERENCE = "uccs-oscqr-3-1-3"
RSI = "uccs-oscqr-rsi"
PACKS = (REFERENCE, RSI)
CREATED = datetime(2026, 9, 1, 12, 30, tzinfo=UTC)
BANK = Path(__file__).resolve().parents[1] / "src" / "sightline" / "voice" / "phrases.yaml"

#: Planted into one detector's findings. Every one of these is a way a spreadsheet loses text.
HOSTILE_TITLE = 'Week 1, "Intro" — Café naïve 日本語 😀'
HOSTILE_OBSERVED = '=HYPERLINK("x", "y")'
HOSTILE_EVIDENCE = 'Line one,\nline two with "quotes", commas\r\nand ünïcödé\x0b end  '


# -- fixtures -------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def document() -> CourseDocument:
    return sample_document(Path(__file__).resolve().parents[1])


@pytest.fixture(scope="module")
def reference(document: CourseDocument) -> tuple[Projection, DetectionReport]:
    return sample_projection(document, REFERENCE)


@pytest.fixture(scope="module")
def extractions(reference, document: CourseDocument) -> dict[str, RowExtraction]:
    projection, _ = reference
    return asyncio.run(stand_in_extractions(projection, document))


def _build(projection, report, document, extractions=None, **kwargs) -> WorkbookModel:
    return build_workbook(
        projection,
        shipped_packs()[projection.pack_id],
        base_url=document.base_url,
        created=CREATED,
        report=report,
        extractions=extractions,
        course_id=document.course.id,
        **kwargs,
    )


@pytest.fixture(scope="module")
def model(reference, extractions, document) -> WorkbookModel:
    projection, report = reference
    return _build(projection, report, document, extractions)


@pytest.fixture(scope="module")
def hostile(reference, extractions, document) -> tuple[Projection, WorkbookModel]:
    """The reference pack with one Tier A detector's findings rewritten into hostile text."""
    projection, report = reference
    tier_a = {tag for row in projection.rows if row.tier is Tier.A for tag in row.resolved_tags}
    target = next(r for r in report.results if r.findings and r.capability_tag in tier_a)
    planted = tuple(
        dataclasses.replace(
            finding,
            location=dataclasses.replace(finding.location, title=HOSTILE_TITLE),
            observed=HOSTILE_OBSERVED,
            evidence=HOSTILE_EVIDENCE,
        )
        for finding in target.findings
    )
    results = tuple(
        dataclasses.replace(r, findings=planted) if r is target else r for r in report.results
    )
    bent = DetectionReport(results=results)
    reprojected = project(shipped_packs()[REFERENCE], bent)
    return reprojected, _build(reprojected, bent, document, extractions)


def _read(data: bytes) -> openpyxl.Workbook:
    return openpyxl.load_workbook(io.BytesIO(data))


def _sheet_values(book: openpyxl.Workbook) -> list[tuple[str, list[list[str]]]]:
    return [
        (ws.title, [[cell.value for cell in row] for row in ws.iter_rows()])
        for ws in book.worksheets
    ]


def _via_csv(book: openpyxl.Workbook, where: Path) -> list[tuple[str, list[list[str]]]]:
    """Export every sheet to a CSV file the way a person would, then read the files back."""
    out = []
    for index, ws in enumerate(book.worksheets):
        path = where / f"sheet{index}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            for row in ws.iter_rows(values_only=True):
                writer.writerow(["" if value is None else value for value in row])
        with path.open(encoding="utf-8", newline="") as handle:
            out.append((ws.title, [list(row) for row in csv.reader(handle)]))
    return out


def _columns(model: WorkbookModel, sheet: int) -> tuple[list[str], list[dict[str, str]]]:
    header, *rows = model.sheets[sheet].values()
    return header, [dict(zip(header, row, strict=True)) for row in rows]


def _label(name: str) -> str:
    return default_bank().label(name)[1]


# -- THE HEADLINE TEST: CSV round-trip, cell for cell ----------------------------------------------


def test_the_hostile_fixture_really_is_hostile(hostile) -> None:
    """The round-trip below proves nothing if the text it carries is tame. Checked, not assumed."""
    _, model = hostile
    text = "\n".join(cell.text for _, cell in model.cells())
    for needle in (
        "\n",
        ",",
        '"',
        "Café naïve 日本語 😀",
        HOSTILE_OBSERVED,
        "ünïcödé",
        REPLACEMENT,
        "end  ",
    ):
        assert needle in text, needle
    # And the carriage return and the vertical tab were normalised where the test can see it.
    assert "\r" not in text and "\x0b" not in text
    assert "Line one,\nline two with \"quotes\", commas\nand ünïcödé� end  " in text


def test_the_workbook_round_trips_through_csv_cell_for_cell(hostile, tmp_path: Path) -> None:
    _, model = hostile
    book = _read(to_xlsx(model))
    expected = model.values()

    assert _sheet_values(book) == expected
    assert _via_csv(book, tmp_path) == expected


def test_a_cell_that_looks_like_a_formula_stays_text(hostile) -> None:
    _, model = hostile
    book = _read(to_xlsx(model))
    seen = 0
    for ws in book.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                assert cell.data_type == "s", (ws.title, cell.coordinate, cell.data_type)
                if isinstance(cell.value, str) and HOSTILE_OBSERVED in cell.value:
                    seen += 1
    assert seen > 0


def test_a_cell_that_starts_like_a_formula_is_written_as_text() -> None:
    """Course text can begin with anything. ``=`` at the start of a cell must never be evaluated."""
    values = ["=SUM(A1:A2)", "+1", "-1", "@cmd", '=HYPERLINK("https://canvas.example.edu")']
    sheet = Sheet(
        name=_cell_of("S"),
        columns=("col_code",),
        header=(_cell_of("Code"),),
        rows=tuple((_cell_of(value),) for value in values),
    )
    data = to_xlsx(WorkbookModel(sheets=(sheet,), created=CREATED))
    ws = _read(data).worksheets[0]
    cells = [row[0] for row in ws.iter_rows(min_row=2)]
    assert [cell.value for cell in cells] == values
    assert {cell.data_type for cell in cells} == {"s"}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "<f>" not in archive.read("xl/worksheets/sheet1.xml").decode()


def _cell_of(text: str) -> Cell:
    return Cell(parts=(("A", verbatim(text)),))


def test_clean_text_moves_verbatim_spans_with_the_text() -> None:
    piece = Piece("a\r\n", ()) + verbatim("b\x0bc") + Piece("\rd")
    cleaned = clean_text(piece)
    assert cleaned.text == f"a\nb{REPLACEMENT}c\nd"
    (start, end), = cleaned.spans
    assert cleaned.text[start:end] == f"b{REPLACEMENT}c"


# -- shape: every row, in order, and nothing the instrument's file gets wrong ----------------------


@pytest.mark.parametrize("pack_id", PACKS)
def test_every_row_of_the_pack_appears_in_instrument_order(pack_id: str, document) -> None:
    projection, report = sample_projection(document, pack_id)
    pack = shipped_packs()[pack_id]
    model = _build(projection, report, document)
    _, rows = _columns(model, 0)

    assert [row[_label("col_code")] for row in rows] == [c.code for c in pack.rendered_criteria()]
    titles = {section.code: section.title for section in pack.sections}
    assert [row[_label("col_category")] for row in rows] == [
        titles[c.section] for c in pack.rendered_criteria()
    ]
    assert [row[_label("col_criterion")] for row in rows] == [
        c.text for c in pack.rendered_criteria()
    ]


def test_the_emitted_file_has_no_merged_cells_macros_hidden_sheets_or_second_header(
    model: WorkbookModel,
) -> None:
    data = to_xlsx(model)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        types = archive.read("[Content_Types].xml").decode()
    assert not [name for name in names if "vba" in name.lower()]
    assert "macroEnabled" not in types
    assert "mergeCell" not in "".join(
        zipfile.ZipFile(io.BytesIO(data)).read(n).decode() for n in names if n.endswith(".xml")
    )

    book = _read(data)
    assert book.sheetnames == [_label("sheet_criteria"), _label("sheet_action_plan")]
    headers = {_label(name) for name in LABELS if name.startswith("col_")}
    for ws, sheet in zip(book.worksheets, model.sheets, strict=True):
        assert ws.sheet_state == "visible"
        assert not ws.merged_cells.ranges
        first, second = list(ws.iter_rows(min_row=1, max_row=2, values_only=True))
        assert list(first) == [cell.text for cell in sheet.header]
        assert set(first) <= headers
        assert not set(second) & headers, "a second header row"


def test_no_cell_anywhere_is_empty(model: WorkbookModel) -> None:
    book = _read(to_xlsx(model))
    for ws in book.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                assert isinstance(cell.value, str) and cell.value.strip(), (
                    ws.title,
                    cell.coordinate,
                )


# -- the three coverage states ---------------------------------------------------------------------


def test_the_three_coverage_states_render_distinguishably_and_none_is_blank(
    reference, model: WorkbookModel, tmp_path: Path
) -> None:
    projection, _ = reference

    def first(predicate) -> str:
        return next(row.code for row in projection.rows if predicate(row))

    codes = {
        "not_yet_implemented": first(lambda r: r.coverage is Coverage.NOT_YET_IMPLEMENTED),
        "no_deterministic_coverage": first(
            lambda r: r.coverage is Coverage.NO_DETERMINISTIC_COVERAGE and r.tier is Tier.B
        ),
        "sufficiently_present": first(lambda r: r.rating is Rating.SUFFICIENTLY_PRESENT),
        # The no-findings case that is not clean: checks ran, found nothing, and do not settle it.
        "no_findings": first(
            lambda r: r.coverage is Coverage.DETERMINISTIC
            and r.rating is Rating.NO_RESPONSE
            and not r.findings
            and r.tier is Tier.B
        ),
    }
    # Read back through CSV: what a person sees after the round-trip, not what the model holds.
    (_, criteria), _ = _via_csv(_read(to_xlsx(model)), tmp_path)
    header, *rows = criteria
    by_code = {row[header.index(_label("col_code"))]: dict(zip(header, row, strict=True)) for row in rows}
    shown = {
        state: (
            by_code[code][_label("col_coverage")],
            by_code[code][_label("col_rating")],
            by_code[code][_label("col_action_plan")],
        )
        for state, code in codes.items()
    }
    for state, cells in shown.items():
        assert all(value.strip() for value in cells), state
    assert len({cells[:2] for cells in shown.values()}) == 4, shown
    assert shown["not_yet_implemented"][0] != shown["no_deterministic_coverage"][0]
    assert shown["sufficiently_present"][1] == "Sufficiently Present"
    for state in ("not_yet_implemented", "no_deterministic_coverage", "no_findings"):
        assert shown[state][1] != "Sufficiently Present", state
    clean_words = re.compile(r"sufficiently present|found none|no issues|satisfied", re.I)
    for state in ("not_yet_implemented", "no_deterministic_coverage"):
        assert not clean_words.search(shown[state][0]), shown[state]


# -- tier is a column, not a colour ----------------------------------------------------------------


def test_tier_a_and_tier_c_are_separated_by_a_column_that_survives_csv(
    reference, model: WorkbookModel, tmp_path: Path
) -> None:
    projection, _ = reference
    (_, criteria), (_, action) = _via_csv(_read(to_xlsx(model)), tmp_path)
    tiers = {row.code: row.tier for row in projection.rows}
    for sheet in (criteria, action):
        header, *rows = sheet
        code = header.index(_label("col_code"))
        column = header.index(_label("col_determinacy"))
        rating = header.index(_label("col_rating"))
        seen = {}
        for row in rows:
            tier = tiers[row[code]]
            assert row[column] == _label(f"tier_{tier.value}")
            seen[tier] = row[column]
            if tier is Tier.C:
                assert row[rating] == "No Response"
        assert seen[Tier.A] != seen[Tier.C]
    # No fill or font carries tier: the only styles are the header's bold and wrapping.
    book = _read(to_xlsx(model))
    for ws in book.worksheets:
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                assert cell.fill.fill_type is None
                assert not cell.font.bold


# -- the action plan ---------------------------------------------------------------------------------


def _plan_codes(model: WorkbookModel) -> list[str]:
    _, rows = _columns(model, 1)
    seen: list[str] = []
    for row in rows:
        code = row[_label("col_code")]
        if not seen or seen[-1] != code:
            assert code not in seen, f"{code} is split across the action plan"
            seen.append(code)
    return seen


@pytest.mark.parametrize("pack_id", PACKS)
def test_the_action_plan_is_ordered_per_spec_and_drops_no_null_priority_row(
    pack_id: str, document
) -> None:
    projection, report = sample_projection(document, pack_id)
    model = _build(projection, report, document)
    codes = _plan_codes(model)

    assert codes == [row.code for row in projection.action_plan_order()]
    assert sorted(codes) == sorted(row.code for row in projection.rows)
    nulls = [row.code for row in projection.rows if row.priority is None]
    assert nulls, "the fixture must carry null-priority rows"
    if pack_id == REFERENCE:
        # The nine MOB rows carry no flag; they come after every flagged row, all of them.
        assert sorted(nulls) == sorted(f"MOB {n}" for n in range(1, 10))
        assert codes[-len(nulls) :] == [c for c in codes if c in nulls]
    else:
        # The whole RSI pack has no priority column: the order is band, then the instrument's.
        assert len(nulls) == len(projection.rows) == 18
    severity = {"major": 0, "moderate": 1, "minor": 2, "no_response": 3}
    severity.update(sufficiently_present=4, not_applicable=5)
    ratings = {row.code: row.rating.value for row in projection.rows}
    for group in ([c for c in codes if c in nulls],):
        assert [severity[ratings[c]] for c in group] == sorted(severity[ratings[c]] for c in group)


def test_every_located_item_carries_an_item_level_canvas_link(
    reference, extractions, model: WorkbookModel, document
) -> None:
    projection, report = reference
    review = render_review(
        projection, report=report, extractions=extractions, base_url=document.base_url
    )
    located = [s for row in review.rows for s in row.sentences if s.role in ("finding", "quote")]
    assert located, "the fixture must locate something"
    _, rows = _columns(model, 1)
    links = [row[_label("col_link")] for row in rows if row[_label("col_link")].startswith("http")]
    assert len(links) == len(located)
    # The action plan reorders rows (SPEC §07), so compare what is linked, not the order.
    assert sorted(links) == sorted(s.link for s in located)
    for link in links:
        assert link.startswith("https://canvas.example.edu/courses/")
        assert "/edit" not in link
    # The hyperlink behind the cell is the URL the cell shows, and nothing else.
    book = _read(to_xlsx(model))
    for row in book[_label("sheet_action_plan")].iter_rows(min_row=2):
        for cell in row:
            if cell.hyperlink is not None:
                assert cell.hyperlink.target == cell.value


def test_a_workbook_without_a_base_url_is_refused(reference, document) -> None:
    projection, report = reference
    with pytest.raises(WorkbookError, match="base URL"):
        build_workbook(
            projection, shipped_packs()[REFERENCE], base_url="", created=CREATED, report=report
        )


def test_a_row_too_long_for_one_cell_says_where_the_rest_is(reference, document) -> None:
    projection, report = reference
    target = next(r for r in report.results if r.findings)
    many = tuple(
        dataclasses.replace(f, observed=f"{f.observed} #{i:04d} " + "x" * 200)
        for i in range(400)
        for f in target.findings[:1]
    )
    results = tuple(
        dataclasses.replace(r, findings=many) if r is target else r for r in report.results
    )
    bent = DetectionReport(results=results)
    reprojected = project(shipped_packs()[REFERENCE], bent)
    model = _build(reprojected, bent, document)
    _, criteria = _columns(model, 0)
    _, plan = _columns(model, 1)
    row = next(r for r in reprojected.rows if r.contributions and len(r.findings) >= 400)
    cell = next(c for c in criteria if c[_label("col_code")] == row.code)[_label("col_action_plan")]
    assert len(cell) <= CELL_LIMIT
    assert "further lines for" in cell and "Action Plan sheet" in cell
    lines = [p for p in plan if p[_label("col_code")] == row.code]
    assert len(lines) >= 400
    assert all(f"#{i:04d}" in "\n".join(p[_label("col_action_plan")] for p in lines) for i in (0, 399))


# -- the near-boundary marker ------------------------------------------------------------------------


def test_the_near_boundary_marker_has_its_own_column(reference, model: WorkbookModel) -> None:
    projection, _ = reference
    _, rows = _columns(model, 0)
    by_code = {row[_label("col_code")]: row for row in rows}
    near = [row for row in projection.rows if row.near_boundary is not None]
    far = [row for row in projection.rows if row.rating.is_band and row.near_boundary is None]
    assert near and far
    for row in near:
        cells = by_code[row.code]
        marker = cells[_label("col_near_boundary")]
        assert f"{row.near_boundary.threshold_hours:g} h" in marker
        assert "threshold" in marker
        # Not folded into the rating, and not turned into a hedge in the action plan.
        assert cells[_label("col_rating")] == row.label
        assert "threshold" not in cells[_label("col_action_plan")]
    for row in far:
        assert by_code[row.code][_label("col_near_boundary")] == _label("near_none")
    unbanded = next(row for row in projection.rows if not row.rating.is_band)
    assert by_code[unbanded.code][_label("col_near_boundary")] == _label("near_not_banded")


def test_every_effort_figure_is_labelled_an_estimate(reference, model: WorkbookModel) -> None:
    projection, _ = reference
    _, rows = _columns(model, 0)
    by_code = {row[_label("col_code")]: row for row in rows}
    for row in projection.rows:
        cell = by_code[row.code][_label("col_effort")]
        if row.rating.is_band:
            assert cell.startswith(f"{row.hours:g} h, an estimate")
            assert "uncalibrated" in cell
        else:
            assert cell == _label("effort_none")
    assert all(row.rating is Rating.NO_RESPONSE for row in projection.rows if row.tier is Tier.C)


# -- no aggregate, and every word through the lint ---------------------------------------------------


FORBIDDEN_HEADERS = re.compile(
    r"score|grade|points|overall|total|average|mean|sum|percent|%|compliance", re.I
)


def test_no_aggregate_score_total_average_or_percentage_anywhere(
    reference, model: WorkbookModel
) -> None:
    projection, _ = reference
    data = to_xlsx(model)
    book = _read(data)
    for ws in book.worksheets:
        header = [cell.value for cell in ws[1]]
        assert not [h for h in header if FORBIDDEN_HEADERS.search(h)], header
        for row in ws.iter_rows():
            for cell in row:
                # No formula, no number: nothing to =AVERAGE() and no VALUE-style rating encoding.
                assert cell.data_type == "s"
                assert not isinstance(cell.value, int | float)
    # No summary row under the criteria: exactly one row per criterion after the header.
    assert book.worksheets[0].max_row == len(projection.rows) + 1
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "<f>" not in "".join(
            archive.read(n).decode() for n in archive.namelist() if "worksheets" in n
        )
    # And Sightline's own words carry no aggregate phrasing (verbatim course text is masked).
    assert [v for v in model.violations() if v.rule == "aggregate"] == []


def test_every_emitted_string_passes_the_register_lint_headers_included(
    model: WorkbookModel, hostile
) -> None:
    assert model.violations() == []
    assert hostile[1].violations() == []
    headers = [cell for sheet in model.sheets for cell in (sheet.name, *sheet.header)]
    for cell in headers:
        # Headers are Sightline's words in full: nothing in them is masked as verbatim.
        assert all(piece.spans == () for _, piece in cell.parts)


def test_a_planted_header_is_refused_by_the_bank_and_by_the_writer(
    reference, document, monkeypatch
) -> None:
    payload = yaml.safe_load(BANK.read_text(encoding="utf-8"))
    payload["labels"]["col_rating"]["text"] = "Overall score"
    with pytest.raises(PhraseBankError, match="labels.col_rating"):
        parse_bank(payload)

    # And a label that reaches the writer some other way is refused before a byte is written.
    projection, report = reference
    original = Voice.label

    def planted(self, name):
        register, piece = original(self, name)
        return (register, Piece("Points")) if name == "col_rating" else (register, piece)

    monkeypatch.setattr(Voice, "label", planted)
    with pytest.raises(WorkbookError, match="grading"):
        _build(projection, report, document)


def test_the_bank_refuses_a_missing_label() -> None:
    payload = yaml.safe_load(BANK.read_text(encoding="utf-8"))
    del payload["labels"]["coverage_not_yet_implemented"]
    with pytest.raises(PhraseBankError, match="coverage_not_yet_implemented is missing"):
        parse_bank(payload)
    assert default_register() is not None


# -- document properties and determinism -------------------------------------------------------------


def test_document_properties_are_ours_and_name_nobody(model: WorkbookModel) -> None:
    data = to_xlsx(model)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        core = archive.read("docProps/core.xml").decode()
        app = archive.read("docProps/app.xml").decode()
        everything = b"".join(archive.read(name) for name in archive.namelist()).decode()
    for element in ("creator", "lastModifiedBy", "Company", "Manager", "Application", "title"):
        assert element not in core + app, element
    assert core.count("2026-09-01T12:30:00Z") == 2  # created and modified, both the caller's
    for identity in {getpass.getuser(), socket.gethostname()}:
        if len(identity) >= 4:
            assert identity not in everything, identity
    assert "openpyxl" not in everything.lower()


def test_the_timestamp_is_the_callers_in_utc_and_never_naive(reference, document) -> None:
    projection, report = reference
    later = datetime(2026, 9, 1, 8, 30, tzinfo=timezone(timedelta(hours=-4)))
    model = build_workbook(
        projection, shipped_packs()[REFERENCE], base_url=document.base_url, created=later
    )
    assert model.created == CREATED
    with pytest.raises(WorkbookError, match="timezone-aware"):
        build_workbook(
            projection,
            shipped_packs()[REFERENCE],
            base_url=document.base_url,
            created=datetime(2026, 9, 1),
        )


def test_two_runs_produce_the_same_workbook_cell_for_cell(reference, extractions, document) -> None:
    projection, report = reference
    first = _build(projection, report, document, extractions)
    second = _build(projection, report, document, extractions)
    assert first.values() == second.values()
    # Read back from the files: same sheets in the same order, same columns, same cells.
    assert _sheet_values(_read(to_xlsx(first))) == _sheet_values(_read(to_xlsx(second)))


def test_a_different_pack_is_refused(reference, document) -> None:
    projection, report = reference
    with pytest.raises(WorkbookError, match="pack"):
        build_workbook(
            projection, shipped_packs()[RSI], base_url=document.base_url, created=CREATED
        )


# -- export goes through the gate ---------------------------------------------------------------------


@pytest.fixture
def store(paths: DataPaths) -> Iterator[RunStore]:
    opened = RunStore.open(paths)
    yield opened
    opened.dispose()


def test_the_workbook_is_exported_only_through_the_gate(
    store: RunStore, paths: DataPaths, model: WorkbookModel
) -> None:
    run_id = create_batch_run(store, ["one"], rubric_pack=REFERENCE)
    run = store.get_run(run_id)
    assert run is not None
    course_id = run.courses[0].id
    gate = ExportGate(store, paths)
    data = to_xlsx(model)

    with pytest.raises(ReviewNotOpenedError):
        export_workbook(gate, course_id, data)
    assert list(paths.root.rglob(WORKBOOK_FILENAME)) == []

    gate.open_review(course_id)
    written = export_workbook(gate, course_id, data)
    assert written == gate.destination(course_id, WORKBOOK_FILENAME)
    assert _sheet_values(_read(written.read_bytes())) == model.values()
