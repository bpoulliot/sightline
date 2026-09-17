"""Importing a rubric workbook into a usable pack.

**What comes out is usable immediately, with no bindings.** That is the whole shape of this module.
An earlier answer to item 04 was "import to a skeleton, then hand-author every binding", and it was
rejected: it makes *upload Quality Matters* mean *write forty YAML bindings first*, which is a
roadblock an open-source tool cannot justify. A pack with zero bindings loads, runs and produces a
review — its rows carry ``NO_DETERMINISTIC_COVERAGE`` and route to the extraction layer — so the
import is finished when the rows are in, not when somebody has mapped them.

**It does not assume the reference instrument's layout.** The two committed workbooks are
structurally unalike, deliberately: one has ten sheets, category codes in a column, a composite key,
an OPEN SUNY number and a priority flag; the other has one sheet, seven columns, section headings
sitting in the index column, and none of those identifiers. So the columns are *found* rather than
assumed, and what was found is reported back in :class:`ImportReport` — which column held the
criterion text, which held the index, how many rows were read — so a person can falsify the import
in seconds instead of trusting it. That is the same rule the rest of the product follows: a
checkable artifact, never a verdict.

TRAPS, ALL OF THEM OBSERVED IN THE REAL FILES
---------------------------------------------

**One workbook does not open in openpyxl's default mode at all.** ``UCCS OSCQR RSI Review.xlsm``
raises ``TypeError: expected <class 'int'>``: it carries 19 merged-cell records and five of them are
whole-row references with no column letters (``ref="8:8"``), which Excel accepts and openpyxl's
``CellRange`` refuses. The file is not corrupt. ``read_only=True`` loads it cleanly, because
read-only mode never binds merged cells. So the fallback is taken *explicitly and reported*, never
silently, and if it fails too the import raises. **It never returns a zero-row pack.**

**An import yielding zero rows is an error.** :class:`RubricImportError`, every time. A pack with
zero *bindings* is not an error and the two are not the same thing.

**One category skips an index.** The reference instrument's assessment block runs 1, 2, 3, 4, 5, 7.
Row codes are built from the identifiers on the row, never from a position, and
``tests/test_rubric_importer.py`` asserts that sequence exactly.

**The index column mixes float and int inside one sheet.** 62 cells float, 6 int on the canonical
sheet, so ``COAL 1.0`` and ``COAL 1`` both occur in the raw data. Indices are normalized through
:func:`_index_text`, which renders an integral float without its decimal point.

**A read-only sheet reports a max_row of 1,048,561.** The last criterion is on row 42. The scan
stops after a run of empty rows rather than walking the sheet's declared extent.

**The mobile block carries no priority flag.** Null, not "Important". Nothing here fills it in.

**The crosswalk columns are not a binding source.** The 3.1.3 sheet's OPEN SUNY column maps UCCS
numbering to another OSCQR variant's numbering. It says nothing whatever about detectors, and
neither workbook has a column naming one — which is why no import can produce bindings and why it
is not supposed to.

**CSV is a lossy fallback and the two exports do not even agree on encoding.** The canonical sheet's
export is clean UTF-8 with its curly quotes intact; the RSI export has already lost them to U+FFFD.
Spreadsheets are authoritative; :func:`import_csv` exists for a file no spreadsheet reader can open
and says so in the report it returns.
"""

from __future__ import annotations

import csv
import datetime as dt
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.worksheet.worksheet import Worksheet

from ..detect.base import Tier
from .pack import Criterion, PackSource, RubricPack, Section, default_rating_scale

#: A cell has to hold at least this much text to be a criterion rather than a label. Both
#: instruments' shortest criterion is well over it and every column heading on both is well under.
MIN_CRITERION_CHARS = 40

#: Stop scanning after this many consecutive rows with nothing in them. Needed because a
#: read-only worksheet reports the sheet's declared extent, which on one of these files is
#: 1,048,561 rows against a last criterion on row 42.
EMPTY_RUN_LIMIT = 40

#: How far to look for the columns. Both instruments' widest is 30.
MAX_COLUMNS = 40

#: Rows to consider at all. Generous, and bounded, for the reason above.
MAX_ROWS = 2000

#: Priority labels the reference instrument uses, most important first. Used to *recognise* a
#: priority column, not to fill one in: a workbook whose flags are words nobody here listed gets a
#: null priority and a note saying so, which is honest, rather than a guess.
KNOWN_PRIORITIES = ("Essential", "Important")

#: Instruction and boilerplate lines that sit in the same column as section headings. Matched
#: case-insensitively on the opening words, because both files carry one per section.
_BOILERPLATE = re.compile(
    r"^\s*(click on the appropriate|this space is for|additional narrative|"
    r"estimated time needed|rubric criteria)",
    re.I,
)

#: A section heading as both instruments write one: an ordinal, then a name in capitals.
_SECTION_HEADING = re.compile(r"^\s*(\d+)\s*[.)]\s*(.+?)\s*$")


class RubricImportError(RuntimeError):
    """An import could not produce a pack. Always raised rather than returning an empty one."""


# ---------------------------------------------------------------------------------------------
# What the importer decided, reported back
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class ColumnMap:
    """Which column of the sheet held what. 1-based, as the spreadsheet numbers them.

    Only ``text`` is required. Everything else is an identifier the source instrument may or may
    not carry, and an instrument that carries none of them still imports.
    """

    text: int
    #: Within-category index, where the instrument has one. The `7` of `ASFE 7`.
    index: int | None = None
    #: Category code, where the instrument has one. The `ASFE` of `ASFE 7`.
    category: int | None = None
    priority: int | None = None
    #: A column holding a distinct value on every criterion row — the instrument's own row key.
    key: int | None = None
    letter: int | None = None
    #: A column of two or three repeated flags, which is how an instrument marks provenance.
    origin: int | None = None
    #: Column the section headings sit in. Often the same column as ``index``.
    section: int | None = None
    #: Other populated columns left of the text. Recorded by column letter rather than given a
    #: name: the importer does not know that some column holds another instrument's numbering, and
    #: calling it `open_suny` would be an invention rather than a reading.
    extra: tuple[int, ...] = ()

    def named(self) -> Mapping[str, int]:
        return {
            name: value
            for name, value in (
                ("text", self.text),
                ("index", self.index),
                ("category", self.category),
                ("priority", self.priority),
                ("key", self.key),
                ("letter", self.letter),
                ("origin", self.origin),
                ("section", self.section),
            )
            if value is not None
        }

    @property
    def identifier_count(self) -> int:
        """How many identifying columns were found beside the text. Used to choose a sheet."""
        return len(self.named()) - 1

    def statement(self) -> str:
        found = ", ".join(
            f"{name}={_column_letter(column)}" for name, column in self.named().items()
        )
        if self.extra:
            found += f"; also carried verbatim: {', '.join(map(_column_letter, self.extra))}"
        return found


@dataclass(frozen=True, kw_only=True, slots=True)
class ImportReport:
    """What the importer read, and what it had to decide to read it.

    Returned beside the pack rather than logged, because "which column did you think held the
    criterion?" is exactly the question a person needs answered before trusting 68 imported rows.
    """

    workbook: str
    sheet: str
    columns: ColumnMap
    rows_read: int
    sections_found: int
    first_row: int
    last_row: int
    #: Anything worth stating about how the file had to be opened or what it lacked.
    notes: tuple[str, ...] = ()
    #: What the row codes were built from. ``positional_index`` is itself a warning.
    key_source: str = "authored"

    def statement(self) -> str:
        lines = [
            f"{self.workbook} [{self.sheet}]: {self.rows_read} criterion row(s) read from rows "
            f"{self.first_row}-{self.last_row}, in {self.sections_found} section(s).",
            f"  columns: {self.columns.statement()}",
            f"  row codes built from: {self.key_source}",
        ]
        lines.extend(f"  {note}" for note in self.notes)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workbook": self.workbook,
            "sheet": self.sheet,
            "columns": dict(self.columns.named()),
            "rows_read": self.rows_read,
            "sections_found": self.sections_found,
            "first_row": self.first_row,
            "last_row": self.last_row,
            "notes": list(self.notes),
            "key_source": self.key_source,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class Import:
    """The pack, and the report about how it was produced."""

    pack: RubricPack
    report: ImportReport


# ---------------------------------------------------------------------------------------------
# Reading the sheet
# ---------------------------------------------------------------------------------------------


@dataclass
class _Grid:
    """A rectangle of cell values, 1-indexed by (row, column) as a spreadsheet is."""

    cells: dict[tuple[int, int], Any] = field(default_factory=dict)
    max_row: int = 0

    def at(self, row: int, column: int) -> Any:
        return self.cells.get((row, column))

    def rows(self) -> Iterator[int]:
        return iter(range(1, self.max_row + 1))


def _load_workbook(path: Path) -> tuple[openpyxl.Workbook, tuple[str, ...]]:
    """Open a workbook, taking the read-only fallback explicitly and saying so.

    The failure this handles is real and specific: one of the committed instruments raises on a
    malformed merged-cell range. Catching that and returning nothing would be the silent
    under-detection the whole project is built against, so the exception is caught, the *stated*
    recovery is attempted, and a failure of that recovery raises.
    """
    notes: list[str] = []
    try:
        return openpyxl.load_workbook(path, data_only=True), ()
    except Exception as first:  # noqa: BLE001 - reported, then retried explicitly
        notes.append(
            f"This workbook does not open in openpyxl's default mode: {type(first).__name__}: "
            f"{first}. It was re-opened read-only, which does not bind merged cells; the "
            "criterion text is unaffected. Nothing here suppresses the error — an import that "
            "cannot read the file raises rather than returning an empty pack."
        )
        try:
            return openpyxl.load_workbook(path, data_only=True, read_only=True), tuple(notes)
        except Exception as second:  # noqa: BLE001 - now it is fatal
            raise RubricImportError(
                f"{path.name} cannot be read as a spreadsheet. Default mode raised "
                f"{type(first).__name__}: {first}; read-only mode raised "
                f"{type(second).__name__}: {second}. If a CSV export of the same sheet exists, "
                "import_csv() reads one explicitly — and note that a CSV export is lossy."
            ) from second


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One sheet, scored for whether it is the criterion grid."""

    name: str
    visible: bool
    order: int
    rows: int
    identifiers: int

    def line(self) -> str:
        state = "visible" if self.visible else "hidden"
        return (
            f"{self.name!r} ({state}, {self.rows} row(s), "
            f"{self.identifiers} identifier column(s))"
        )


def _candidates(book: openpyxl.Workbook) -> tuple[_Candidate, ...]:
    found: list[_Candidate] = []
    for order, name in enumerate(book.sheetnames):
        sheet = book[name]
        grid = _read_grid(sheet)
        if not grid.max_row:
            continue
        try:
            layout = _find_layout(grid)
        except RubricImportError:
            continue
        found.append(
            _Candidate(
                name=name,
                visible=getattr(sheet, "sheet_state", "visible") == "visible",
                order=order,
                rows=len(layout.rows),
                identifiers=layout.columns.identifier_count,
            )
        )
    return tuple(found)


def _pick_sheet(book: openpyxl.Workbook, wanted: str | None) -> tuple[str, tuple[str, ...]]:
    """The sheet to import, and what was considered on the way to choosing it.

    Named explicitly, this is a lookup. Left to the importer it is a *scored* choice, and the score
    is the number of identifier columns beside the criterion text — because that is what separates
    a criterion grid from an aggregate sheet built out of one, generically, without knowing either
    committed instrument. The reference workbook is a live demonstration: it holds ten sheets, of
    which a blank metadata template, an action-plan aggregate of 459 rows and a change log all
    carry criterion-shaped text, and only the criterion grids carry the identifiers.

    Visible sheets are preferred over hidden ones, and among equals the first in workbook order
    wins — four of the reference workbook's sheets are byte-for-byte the same grid, so there is no
    further fact to choose on. Every candidate is returned as a note either way, so the choice is
    checkable rather than trusted.
    """
    if wanted is not None:
        if wanted not in book.sheetnames:
            raise RubricImportError(
                f"no sheet named {wanted!r}. This workbook holds: {', '.join(book.sheetnames)}."
            )
        return wanted, ()
    if len(book.sheetnames) == 1:
        return book.sheetnames[0], ()

    found = _candidates(book)
    if not found:
        raise RubricImportError(
            "no sheet in this workbook holds anything shaped like a criterion. Name the sheet "
            f"explicitly if one does: {', '.join(book.sheetnames)}."
        )
    best = max(found, key=lambda entry: (entry.identifiers, entry.visible, -entry.order))
    others = tuple(entry for entry in found if entry.name != best.name)
    notes = [
        f"No sheet was named, so {best.name!r} was chosen: it carries {best.identifiers} "
        f"identifier column(s) beside {best.rows} criterion row(s), more than any other sheet "
        "here. Name the sheet explicitly to override this."
    ]
    if others:
        notes.append("Also considered: " + "; ".join(entry.line() for entry in others) + ".")
    return best.name, tuple(notes)


def _read_grid(sheet: Worksheet) -> _Grid:
    """Read the sheet into a bounded grid, stopping after a run of empty rows."""
    grid = _Grid()
    empty_run = 0
    row_number = 0
    for row_number, row in enumerate(
        sheet.iter_rows(min_row=1, max_row=MAX_ROWS, max_col=MAX_COLUMNS, values_only=True),
        start=1,
    ):
        present = False
        for offset, value in enumerate(row, start=1):
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            present = True
            grid.cells[(row_number, offset)] = value
        if present:
            empty_run = 0
            grid.max_row = row_number
        else:
            empty_run += 1
            if empty_run >= EMPTY_RUN_LIMIT and grid.max_row:
                break
    if not grid.max_row and row_number:
        grid.max_row = 0
    return grid


# ---------------------------------------------------------------------------------------------
# Finding the columns
# ---------------------------------------------------------------------------------------------


def _is_criterion_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    return len(text) >= MIN_CRITERION_CHARS and not _BOILERPLATE.match(text)


#: How much of the candidate rows an identifier column has to cover before rows it leaves blank
#: are treated as something other than criteria. High, because the thing being caught is a
#: handful of prose lines among dozens of numbered rows, not a sparsely-filled column.
ANCHOR_COVERAGE = 0.8


@dataclass(frozen=True, slots=True)
class _Layout:
    """Which columns hold what, and which rows are criteria.

    Read together, because the second answer changes the first: a prose line mistaken for a
    criterion puts a blank in the middle of the index column, and the index column then stops
    looking like one.
    """

    columns: ColumnMap
    rows: tuple[int, ...]


def _find_layout(grid: _Grid) -> _Layout:
    """Locate the criterion text column, the rows that are criteria, and the identifiers beside.

    Two passes, and the second one is why the RSI instrument imports 18 rows rather than 19. The
    first pass finds every cell holding a sentence of prose, which on that sheet catches its own
    printed title alongside its criteria. The second looks for the column the instrument
    *numbered* its rows in — one populated on nearly every candidate — and drops the candidates it
    leaves blank: a title, a narrative block and an instruction line are all unnumbered, and a
    criterion is not. An instrument that numbers nothing at all keeps every candidate.
    """
    tally: dict[int, int] = {}
    for (_, column), value in grid.cells.items():
        if _is_criterion_text(value):
            tally[column] = tally.get(column, 0) + 1
    if not tally:
        raise RubricImportError(
            "no column in this sheet holds anything shaped like a criterion: nothing has "
            f"{MIN_CRITERION_CHARS}+ characters of text in it. An import that produced zero rows "
            "would be reported as an empty rubric, which is why this raises instead."
        )
    text_column = max(tally.items(), key=lambda item: (item[1], -item[0]))[0]
    candidates = _criterion_rows(grid, text_column)
    if not candidates:
        raise RubricImportError(
            f"column {_column_letter(text_column)} holds criterion text but no row of it could be "
            "read. Zero rows is an import failure, never an empty rubric."
        )
    body = _anchored(grid, candidates, text_column)

    found = ColumnMap(
        text=text_column,
        index=_numeric_column(grid, body, text_column),
        category=_category_column(grid, body, text_column),
        priority=_priority_column(grid, body, text_column),
        key=_key_column(grid, body, text_column),
        letter=_letter_column(grid, body, text_column),
        origin=_origin_column(grid, body, text_column),
        section=_section_column(grid, text_column),
    )
    claimed = set(found.named().values())
    columns = ColumnMap(
        text=found.text,
        index=found.index,
        category=found.category,
        priority=found.priority,
        key=found.key,
        letter=found.letter,
        origin=found.origin,
        section=found.section,
        extra=tuple(
            column
            for column in _left_of(text_column)
            if column not in claimed and all(grid.at(row, column) is not None for row in body)
        ),
    )
    return _Layout(columns=columns, rows=body)


def _anchored(grid: _Grid, candidates: Sequence[int], text_column: int) -> tuple[int, ...]:
    """The candidate rows the instrument numbered, or all of them if it numbered none."""
    if len(candidates) < 2:
        return tuple(candidates)
    best: tuple[int, int] | None = None
    for column in _left_of(text_column):
        filled = sum(1 for row in candidates if grid.at(row, column) is not None)
        if best is None or filled > best[0]:
            best = (filled, column)
    if best is None:
        return tuple(candidates)
    filled, column = best
    if filled == len(candidates) or filled < ANCHOR_COVERAGE * len(candidates):
        return tuple(candidates)
    return tuple(row for row in candidates if grid.at(row, column) is not None)


def _criterion_rows(grid: _Grid, text_column: int) -> tuple[int, ...]:
    return tuple(row for row in grid.rows() if _is_criterion_text(grid.at(row, text_column)))


def _left_of(text_column: int) -> range:
    return range(1, text_column)


def _numeric_column(grid: _Grid, body: Sequence[int], text_column: int) -> int | None:
    """The rightmost column left of the text that holds a small number on every criterion row.

    Rightmost because both instruments put the within-category index immediately beside the text
    and the running sequence number further out; the index is the one a code is built from.
    """
    for column in reversed(_left_of(text_column)):
        values = [grid.at(row, column) for row in body]
        if all(isinstance(value, int | float) and 0 < value < 1000 for value in values):
            return column
    return None


def _category_column(grid: _Grid, body: Sequence[int], text_column: int) -> int | None:
    """A column of short codes that repeat in runs — a category code, if the sheet has one."""
    for column in reversed(_left_of(text_column)):
        values = [grid.at(row, column) for row in body]
        if not all(isinstance(value, str) and 2 <= len(value.strip()) <= 8 for value in values):
            continue
        codes = [str(value).strip() for value in values]
        if not all(code.replace("-", "").replace("_", "").isalnum() for code in codes):
            continue
        # A category repeats; a composite key does not. Requiring fewer distinct values than rows
        # is what tells the two apart without knowing either instrument.
        if 1 < len(set(codes)) < len(codes):
            return column
    return None


def _priority_column(grid: _Grid, body: Sequence[int], text_column: int) -> int | None:
    """A column whose non-empty values are all recognised priority labels.

    Deliberately *not* "any column of short repeated strings": the mobile block of the reference
    instrument leaves this blank on nine rows, so the test is over the values that are present.
    """
    wanted = {label.casefold() for label in KNOWN_PRIORITIES}
    for column in _left_of(text_column):
        present = [
            str(grid.at(row, column)).strip()
            for row in body
            if isinstance(grid.at(row, column), str)
        ]
        if present and all(value.casefold() in wanted for value in present):
            return column
    return None


def _key_column(grid: _Grid, body: Sequence[int], text_column: int) -> int | None:
    """A column holding a distinct value on every criterion row — the instrument's own row key.

    Rightmost such column, because an instrument that carries both a running number and a
    composite built from it puts the composite nearer the text, and the composite is the one that
    survives a category being reordered.
    """
    for column in reversed(_left_of(text_column)):
        values = [grid.at(row, column) for row in body]
        if any(value is None for value in values):
            continue
        rendered = [_scalar_text(value) for value in values]
        if len(set(rendered)) == len(rendered):
            return column
    return None


def _letter_column(grid: _Grid, body: Sequence[int], text_column: int) -> int | None:
    """A column of single letters, which is how both the sequence letter and the mobile block
    are written on the reference instrument."""
    for column in _left_of(text_column):
        values = [grid.at(row, column) for row in body]
        if all(isinstance(value, str) and len(value.strip()) == 1 for value in values):
            return column
    return None


def _origin_column(grid: _Grid, body: Sequence[int], text_column: int) -> int | None:
    """A column of two or three repeated uppercase flags — a provenance marker."""
    for column in _left_of(text_column):
        values = [grid.at(row, column) for row in body]
        if not all(isinstance(value, str) and 2 <= len(value.strip()) <= 6 for value in values):
            continue
        codes = {str(value).strip() for value in values}
        if 1 <= len(codes) <= 3 and all(code.isupper() for code in codes):
            return column
    return None


def _section_column(grid: _Grid, text_column: int) -> int | None:
    """The column section headings sit in.

    Both instruments write a heading the same way — an ordinal, a dot, a name in capitals — and
    put it in a column *other* than the criterion text, which is what makes it findable without
    knowing either file. One puts it in the index column; the other puts it two columns over.
    """
    tally: dict[int, int] = {}
    for (row, column), value in grid.cells.items():
        if column == text_column or not isinstance(value, str):
            continue
        if grid.at(row, text_column) is not None:
            continue
        text = value.strip()
        if _SECTION_HEADING.match(text) and not _BOILERPLATE.match(text):
            tally[column] = tally.get(column, 0) + 1
    if not tally:
        return None
    return max(tally.items(), key=lambda item: (item[1], -item[0]))[0]


# ---------------------------------------------------------------------------------------------
# Building the pack
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Heading:
    """A section heading found in the sheet: its row, its ordinal, and its name."""

    row: int
    code: str
    title: str


def _headings(grid: _Grid, columns: ColumnMap) -> tuple[_Heading, ...]:
    if columns.section is None:
        return ()
    found: list[_Heading] = []
    for row in grid.rows():
        value = grid.at(row, columns.section)
        if not isinstance(value, str) or grid.at(row, columns.text) is not None:
            continue
        text = value.strip()
        match = _SECTION_HEADING.match(text)
        if not match or _BOILERPLATE.match(text):
            continue
        found.append(
            _Heading(row=row, code=match.group(1), title=_title_case(match.group(2).strip()))
        )
    return tuple(found)


def _section_for(row: int, headings: Sequence[_Heading]) -> _Heading | None:
    current: _Heading | None = None
    for heading in headings:
        if heading.row < row:
            current = heading
        else:
            break
    return current


def build_pack(
    grid: _Grid,
    layout: _Layout,
    *,
    pack_id: str,
    name: str,
    version: str,
    workbook: str,
    sheet: str,
    source_title: str | None = None,
    notes: Sequence[str] = (),
    imported_at: dt.date | None = None,
) -> Import:
    """Turn a read grid into a pack with empty bindings, plus the report about the reading."""
    columns = layout.columns
    body = layout.rows
    if not body:
        raise RubricImportError(
            f"{workbook} [{sheet}]: no criterion rows were found. An import that yields zero rows "
            "is an error, not an empty rubric."
        )

    headings = _headings(grid, columns)
    extra = list(notes)
    criteria: list[Criterion] = []
    sections: dict[str, Section] = {}
    positional = 0
    key_source = "authored"

    for row in body:
        heading = _section_for(row, headings)
        category = _text(grid.at(row, columns.category)) if columns.category else None
        index = _index_text(grid.at(row, columns.index)) if columns.index else None

        if category and index:
            code = f"{category} {index}"
            key_source = "category_and_index"
            section_code = category
        elif row_key := (_scalar_text(grid.at(row, columns.key)) if columns.key else None):
            code = row_key
            key_source = "composite_code"
            section_code = heading.code if heading else "1"
        else:
            positional += 1
            code = index or str(positional)
            key_source = "positional_index"
            section_code = heading.code if heading else "1"

        if section_code not in sections:
            sections[section_code] = Section(
                code=section_code,
                title=(
                    heading.title
                    if heading and (not category or heading.code == section_code)
                    else _title_case(category or section_code)
                ),
            )
            if heading and heading.code != section_code and category:
                sections[section_code] = Section(code=section_code, title=heading.title)

        criteria.append(
            Criterion(
                code=code,
                section=section_code,
                text=_text(grid.at(row, columns.text)) or "",
                # Everything imports at tier B. The importer has read a sentence of prose; it has
                # not established that a parser can answer it, and defaulting to tier A would
                # promise arithmetic on 68 rows nothing is bound to. Authoring a pack sets tiers.
                tier=Tier.B,
                priority=_text(grid.at(row, columns.priority)) if columns.priority else None,
                bindings=(),
                source_ids=_source_ids(grid, row, columns),
            )
        )

    if key_source == "positional_index" or _is_sequential(
        [criterion.code for criterion in criteria]
    ):
        extra.append(
            "The row codes are this instrument's own sequential numbering, which is positional in "
            "substance: a revised edition that inserts a row renumbers everything after it, and a "
            "re-import will not diff cleanly against this one (SPEC §11). There is nothing to be "
            "done about that here — the instrument carries no stabler identifier — so it is stated "
            "rather than discovered later."
        )
    if columns.priority is None:
        extra.append(
            "No priority column was found, so every row's priority is null. Action-plan ordering "
            "tolerates that; it is not filled in with a default."
        )
    else:
        blank = sum(1 for row in body if not isinstance(grid.at(row, columns.priority), str))
        if blank:
            extra.append(
                f"{blank} row(s) carry no priority flag. Null, not a default — the flag is absent "
                "on the instrument."
            )

    pack = RubricPack(
        schema_version=1,
        id=pack_id,
        name=name,
        version=version,
        source=PackSource(
            title=source_title,
            workbook=workbook,
            sheet=sheet,
            imported_at=imported_at or dt.date.today(),
            key_source=key_source,
            notes=tuple(extra),
        ),
        priority_order=tuple(
            label
            for label in KNOWN_PRIORITIES
            if any(criterion.priority == label for criterion in criteria)
        ),
        rating_scale=default_rating_scale(),
        sections=tuple(sections[code] for code in sections),
        criteria=tuple(criteria),
    )
    report = ImportReport(
        workbook=workbook,
        sheet=sheet,
        columns=columns,
        rows_read=len(criteria),
        sections_found=len(sections),
        first_row=body[0],
        last_row=body[-1],
        notes=tuple(extra),
        key_source=key_source,
    )
    return Import(pack=pack, report=report)


def import_workbook(
    path: str | Path,
    *,
    sheet: str | None = None,
    pack_id: str | None = None,
    name: str | None = None,
    version: str = "imported",
    source_title: str | None = None,
    imported_at: dt.date | None = None,
) -> Import:
    """Import a rubric workbook. Raises rather than returning an empty pack.

    ``sheet`` names the sheet when the workbook has several; without it, :func:`_pick_sheet`
    chooses and says what it chose between.

    ``source_title`` is the instrument's printed title, and it is a *parameter* rather than
    something read off the sheet. Both committed workbooks do print one, in different places, and
    a rule general enough to find both picks up a rating name off one of them. Guessing a title is
    the sort of small invention this project does not make: a caller who knows it says it, and
    otherwise the field stays empty.
    """
    target = Path(path)
    if not target.exists():
        raise RubricImportError(f"{target.name} does not exist")
    book, open_notes = _load_workbook(target)
    try:
        chosen, choice_notes = _pick_sheet(book, sheet)
        grid = _read_grid(book[chosen])
    finally:
        book.close()
    if not grid.max_row:
        raise RubricImportError(f"{target.name} [{sheet or '?'}] holds no data at all")
    layout = _find_layout(grid)
    return build_pack(
        grid,
        layout,
        pack_id=pack_id or _slug(target.stem),
        name=name or target.stem,
        version=version,
        workbook=target.name,
        sheet=chosen,
        source_title=source_title,
        notes=(*open_notes, *choice_notes),
        imported_at=imported_at,
    )


def import_csv(
    path: str | Path,
    *,
    pack_id: str | None = None,
    name: str | None = None,
    version: str = "imported",
    encoding: str = "utf-8",
    source_title: str | None = None,
    imported_at: dt.date | None = None,
) -> Import:
    """Import from a CSV export. **A lossy fallback, taken explicitly.**

    Use this only when no spreadsheet reader can open the file. The two committed exports do not
    even agree on encoding: one is clean UTF-8 with its curly quotes intact and the other has
    already lost them to U+FFFD, and nothing downstream can recover a character the export dropped.
    The report says so, every time.
    """
    target = Path(path)
    if not target.exists():
        raise RubricImportError(f"{target.name} does not exist")
    grid = _Grid()
    with target.open(newline="", encoding=encoding, errors="replace") as handle:
        for row_number, fields in enumerate(csv.reader(handle), start=1):
            if row_number > MAX_ROWS:
                break
            for offset, value in enumerate(fields[:MAX_COLUMNS], start=1):
                text = value.strip()
                if text:
                    grid.cells[(row_number, offset)] = _coerce(text)
                    grid.max_row = row_number
    if not grid.max_row:
        raise RubricImportError(f"{target.name} holds no rows")
    layout = _find_layout(grid)
    lossy = _replacement_characters(grid)
    notes = [
        "Imported from a CSV export, which is a lossy fallback: a spreadsheet export is "
        "authoritative and was not used here."
    ]
    if lossy:
        notes.append(
            f"{lossy} cell(s) contain U+FFFD, the replacement character. The export lost those "
            "characters before Sightline saw the file and they cannot be recovered from it; "
            "re-import from the spreadsheet to get the criterion text as written."
        )
    return build_pack(
        grid,
        layout,
        pack_id=pack_id or _slug(target.stem),
        name=name or target.stem,
        version=version,
        workbook=target.name,
        sheet="(csv)",
        source_title=source_title,
        notes=notes,
        imported_at=imported_at,
    )


# ---------------------------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------------------------


def _source_ids(grid: _Grid, row: int, columns: ColumnMap) -> dict[str, str | float | int | None]:
    """The instrument's own identifiers for one row, verbatim.

    Kept so a re-import of a revised instrument diffs against something stable rather than
    silently renumbering criteria (SPEC §11). The spreadsheet row is in here for the same reason:
    it is what a person opens the file to.
    """
    payload: dict[str, str | float | int | None] = {"row": row}
    for field_name in ("key", "letter", "category", "index", "origin"):
        column = getattr(columns, field_name)
        if column is None:
            continue
        value = grid.at(row, column)
        if value is not None:
            payload[field_name] = _scalar_text(value)
    for column in columns.extra:
        value = grid.at(row, column)
        if value is not None:
            payload[f"column_{_column_letter(column)}"] = _scalar_text(value)
    return payload


def _replacement_characters(grid: _Grid) -> int:
    return sum(1 for value in grid.cells.values() if isinstance(value, str) and "�" in value)


def _coerce(text: str) -> Any:
    """A CSV field as the nearest scalar. Everything in a CSV arrives as a string."""
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _index_text(value: Any) -> str | None:
    """A within-category index as a code fragment.

    The reference instrument's index column mixes float and int **inside one sheet** — 62 float
    and 6 int on the canonical one — so ``1.0`` and ``1`` both occur and both have to render as
    ``1`` or the same criterion gets two different codes depending on the cell.
    """
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int | float):
        return str(value)
    text = str(value).strip()
    return text or None


def _scalar_text(value: Any) -> str:
    rendered = _index_text(value)
    return rendered if rendered is not None else ""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _is_sequential(codes: Sequence[str]) -> bool:
    """Whether these codes are exactly ``1..N``, in order and with no gaps.

    A code that is a position in disguise renumbers when a row is inserted, and an instrument
    whose only identifier is that is worth saying so about. The reference instrument fails this
    test twice over — its categories are prefixed and one of them skips an index — which is why
    the check is a measurement of the codes rather than an assumption about them.
    """
    return bool(codes) and [code.strip() for code in codes] == [
        str(number) for number in range(1, len(codes) + 1)
    ]


def _title_case(text: str) -> str:
    """A heading written in capitals, rendered as a title. Small words stay lower case."""
    small = {"and", "or", "of", "the", "a", "an", "for", "to", "in", "on", "with"}
    words = text.strip().split()
    out = []
    for position, word in enumerate(words):
        lowered = word.lower()
        if position and lowered in small:
            out.append(lowered)
        elif word.isupper() and len(word) <= 4 and not word.isalpha():
            out.append(word)
        else:
            out.append(lowered.capitalize())
    return " ".join(out)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return slug or "imported-rubric"


def _column_letter(column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


__all__ = [
    "EMPTY_RUN_LIMIT",
    "KNOWN_PRIORITIES",
    "MIN_CRITERION_CHARS",
    "ColumnMap",
    "Import",
    "ImportReport",
    "RubricImportError",
    "import_csv",
    "import_workbook",
]
