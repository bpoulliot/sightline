"""The XLSX workbook: every row of the selected rubric, exhaustively (roadmap item 13, SPEC §08).

The PDF (item 14) is action-plan-led and says it is not exhaustive; this is the artifact it points
at. So everything a row carries is here — the proposed rating, the evidence, the action plan, and a
Canvas link for every located item.

A CLEAN WORKBOOK, NOT A COPY OF THE INSTRUMENT
----------------------------------------------
The reference instrument is a macro workbook with whole-row merged ranges, five hidden sheets and a
column of formulas; one of its two files will not open in the standard Python toolchain at all
(SPEC §03). The output of a review is none of that: ``.xlsx``, one header row per sheet, no merged
cells, no hidden sheets, no formulas, and **every cell text**. Text is what survives a round-trip
through CSV unchanged — a float does not, reliably — and a column of numbers is a column somebody
will ``=AVERAGE()``. The instrument's own ``timeToFix`` VALUE encoding (Major 1 … SP 4) exists to be
averaged; nothing here is a number, so nothing here is an aggregate waiting to happen (SPEC §11).

Two sheets, driven by the pack rather than by 3.1.3's layout:

``Criteria``
    One row per rendered criterion, in the instrument's own category order.
``Action Plan``
    Regenerated from the findings, never carried across (SPEC §03). One line per located item —
    an observation, a quote, a question — with its Canvas link, in SPEC §07's order: the priority
    flag crossed with the band, through ``Projection.action_plan_order``, where a null priority
    sorts after every labelled one and is never dropped. A row with nothing
    located still gets one line, so every criterion appears.

THE THREE COVERAGE STATES ARE THREE CELLS' WORTH OF DIFFERENCE
--------------------------------------------------------------
A narrow cell is the easiest place in the product to collapse ``NOT_YET_IMPLEMENTED``,
``NO_DETERMINISTIC_COVERAGE`` and ``SUFFICIENTLY_PRESENT`` into one reassuring blank. The Coverage
column states which of the first two a row is in, in words from the bank that are never clean
wording; only the Proposed rating column can say Sufficiently Present; and **no cell is ever
empty** — :func:`build_workbook` refuses to emit one.

TIER IS A COLUMN, NOT A COLOUR
------------------------------
SPEC §02: Tier A findings and Tier C questions never share a visual channel. In a spreadsheet the
channel that survives CSV, a screen reader and a forwarded copy is the column, so determinacy is
data in a column of its own. A fill would be lost in exactly the round-trip this file must survive.

EVERY WORD IS THE VOICE LAYER'S
-------------------------------
Sentences come from :func:`sightline.voice.render_review`; headers, sheet names and categorical
values are bank labels; values somebody else wrote — the criterion text, codes, the instrument's
rating words, a URL — are verbatim spans. Every cell is linted by item 15's register lint before a
byte is written, and a violation raises. The fix for a failing cell is the words in the bank, never
an exemption here.

WHAT IS AND IS NOT EMBEDDED
---------------------------
The document properties are written by this module, not left to the library: no creator, no
last-modified-by, no application name, no company, and one timestamp the caller supplies. The
workbook gets forwarded to faculty; it must not name the operator or the machine. It does carry the
configured Canvas base URL, in every link, because that is its job — which is also why no test
fixture or example may carry a real one.

DETERMINISM
-----------
The same projection and evidence produce the same cells, sheet order and column order. That — cell
by cell — is the bar, rather than byte-identity of the file: an ``.xlsx`` is a zip container whose
bytes depend on the zip and XML writers' versions, and a byte test would fail on a library upgrade
that changed nothing a reader sees. Zip entry timestamps are pinned anyway, so the bytes do not
drift with the clock.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font

from .detect.base import Tier
from .detect.runner import DetectionReport
from .extract.runner import RowExtraction
from .rubric.pack import RubricPack
from .rubric.project import Projection, RowResult
from .rubric.states import Coverage, Rating
from .voice.lint import Register, Violation, default_register
from .voice.render import Piece, RenderedRow, Sentence, Voice, plain, render_review, verbatim

if TYPE_CHECKING:
    from .batch.gate import ExportGate

#: The exported file's name inside the course's export directory.
WORKBOOK_FILENAME = "review.xlsx"

#: Excel refuses more characters than this in one cell. Nothing is truncated to fit: a Criteria
#: cell that would overflow says how many further lines are on the Action Plan sheet, which carries
#: every line.
CELL_LIMIT = 32_767

#: What an XML-illegal control character becomes. XML 1.0 cannot carry them, so no ``.xlsx`` can;
#: the replacement is visible rather than silent.
REPLACEMENT = "�"

#: The sentence roles that name something to act on or decide: a located observation, a quote, a
#: question. Everything else a row says is evidence of what was examined.
ACTION_ROLES = frozenset({"finding", "quote", "question"})

#: Roles left out of the Evidence cell. A detector can record hundreds of observations; they are
#: listed once each on the Action Plan sheet and in the Action plan column, not twice in a row.
_NOT_EVIDENCE = frozenset({"finding"})

_TIER_LABEL = {Tier.A: "tier_A", Tier.B: "tier_B", Tier.C: "tier_C", Tier.D: "tier_D"}
_COVERAGE_LABEL = {
    Coverage.DETERMINISTIC: "coverage_deterministic",
    Coverage.NOT_YET_IMPLEMENTED: "coverage_not_yet_implemented",
    Coverage.NO_DETERMINISTIC_COVERAGE: "coverage_no_deterministic_coverage",
}

_CRITERIA_COLUMNS = (
    "col_category",
    "col_code",
    "col_criterion",
    "col_priority",
    "col_determinacy",
    "col_coverage",
    "col_rating",
    "col_effort",
    "col_near_boundary",
    "col_evidence",
    "col_action_plan",
)
_ACTION_COLUMNS = (
    "col_category",
    "col_code",
    "col_priority",
    "col_rating",
    "col_near_boundary",
    "col_determinacy",
    "col_action_plan",
    "col_link",
    "col_link_kind",
)

#: Column widths, by header label. Presentation only; nothing here reaches a CSV.
_WIDTHS = {
    "col_category": 22,
    "col_code": 10,
    "col_criterion": 50,
    "col_priority": 14,
    "col_determinacy": 26,
    "col_coverage": 30,
    "col_rating": 18,
    "col_effort": 30,
    "col_near_boundary": 30,
    "col_evidence": 80,
    "col_action_plan": 80,
    "col_link": 45,
    "col_link_kind": 22,
}


class WorkbookError(ValueError):
    """The workbook cannot be built as promised: an empty cell, a lint violation, no base URL."""


# ---------------------------------------------------------------------------------------------
# The table model — what is written, before any library sees it
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Cell:
    """One cell: one or more pieces of text, one per line, each read in its own register.

    A row's Evidence cell can hold a Tier D coverage line above its own tier's lines, so a cell is
    linted piece by piece, each in the register its words were written in, never as one blob in a
    register chosen for it.
    """

    #: ``(register, piece)``, already made safe for a cell by :func:`clean_text`.
    parts: tuple[tuple[str, Piece], ...]
    #: A hyperlink target. Only ever the same URL the cell's text shows.
    link: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(piece.text for _, piece in self.parts)

    def lint(self, register: Register, *, where: str) -> list[Violation]:
        return [
            violation
            for tier, piece in self.parts
            for violation in register.lint(
                piece.text, tier, where=where, sentence=False, spans=piece.spans
            )
        ]


@dataclass(frozen=True, slots=True)
class Sheet:
    name: Cell
    #: The bank label names of the columns, in order.
    columns: tuple[str, ...]
    header: tuple[Cell, ...]
    rows: tuple[tuple[Cell, ...], ...]

    def values(self) -> list[list[str]]:
        """Header row, then data rows, as the strings a reader of the file will see."""
        header = [cell.text for cell in self.header]
        return [header, *([cell.text for cell in row] for row in self.rows)]


@dataclass(frozen=True, slots=True)
class WorkbookModel:
    """The whole workbook as text. :func:`to_xlsx` is the only thing that turns it into a file."""

    sheets: tuple[Sheet, ...]
    created: datetime

    def values(self) -> list[tuple[str, list[list[str]]]]:
        return [(sheet.name.text, sheet.values()) for sheet in self.sheets]

    def cells(self) -> list[tuple[str, Cell]]:
        """Every cell with a location, sheet names included."""
        out: list[tuple[str, Cell]] = []
        for sheet in self.sheets:
            name = sheet.name.text
            out.append((f"sheet name {name!r}", sheet.name))
            out.extend((f"{name} header {i + 1}", c) for i, c in enumerate(sheet.header))
            for r, row in enumerate(sheet.rows, start=2):
                out.extend((f"{name} row {r} column {i + 1}", c) for i, c in enumerate(row))
        return out

    def violations(self, register: Register | None = None) -> list[Violation]:
        rules = register or default_register()
        return [v for where, cell in self.cells() for v in cell.lint(rules, where=where)]


def clean_text(piece: Piece) -> Piece:
    """``piece`` as a cell can hold it, with its verbatim spans moved to match.

    Two changes and no others. A carriage return becomes a line feed, because XML parsing turns
    ``\\r\\n`` into ``\\n`` on the way back in, so a CR could never survive the file anyway — better
    it is normalised here, where the round-trip test can see it, than lost inside the zip. And a
    control character XML 1.0 cannot carry becomes :data:`REPLACEMENT`, visibly.
    """
    text = piece.text
    out: list[str] = []
    # new position of every old position, plus one for the end
    moved: list[int] = []
    index = 0
    while index < len(text):
        moved.append(len(out))
        char = text[index]
        if char == "\r":
            out.append("\n")
            if index + 1 < len(text) and text[index + 1] == "\n":
                index += 1
                moved.append(len(out) - 1)
        elif ILLEGAL_CHARACTERS_RE.match(char):
            out.append(REPLACEMENT)
        else:
            out.append(char)
        index += 1
    moved.append(len(out))
    spans = tuple((moved[start], moved[end]) for start, end in piece.spans)
    return Piece("".join(out), spans)


def _cell(register: str, piece: Piece, link: str | None = None) -> Cell:
    return Cell(parts=((register, clean_text(piece)),), link=link)


def _verbatim_cell(text: str, link: str | None = None) -> Cell:
    return _cell("A", verbatim(text), link)


def _sentences_cell(sentences: Sequence[Sentence], *more: tuple[str, Piece]) -> Cell:
    """Sentences one per line, each keeping its own register, then any further pieces."""
    parts = [
        (sentence.register, clean_text(Piece(sentence.text, sentence.spans)))
        for sentence in sentences
    ]
    parts.extend((register, clean_text(piece)) for register, piece in more)
    return Cell(parts=tuple(parts))


# ---------------------------------------------------------------------------------------------
# Building it
# ---------------------------------------------------------------------------------------------


def build_workbook(
    projection: Projection,
    pack: RubricPack,
    *,
    base_url: str,
    created: datetime,
    report: DetectionReport | None = None,
    extractions: Sequence[RowExtraction] | Mapping[str, RowExtraction] | None = None,
    course_id: str | None = None,
    voice: Voice | None = None,
    register: Register | None = None,
) -> WorkbookModel:
    """Every row of ``projection`` as a workbook model, linted, with no empty cell.

    ``pack`` supplies the category titles, and must be the pack the projection was made from.
    ``base_url`` is the configured Canvas URL every link is built from; without one there would be
    findings without links, so it is required. ``created`` is the timestamp the document properties
    carry — the caller's, never the clock's.
    """
    if not base_url:
        raise WorkbookError(
            "A Canvas base URL is required: every located item in the workbook carries a link."
        )
    if pack.id != projection.pack_id:
        raise WorkbookError(
            f"the projection is of pack {projection.pack_id!r} and the pack given is {pack.id!r}"
        )
    if created.tzinfo is None:
        raise WorkbookError("created must be timezone-aware; the workbook records UTC")
    voice = voice or Voice()
    rules = register or default_register()
    review = render_review(
        projection,
        report=report,
        extractions=extractions,
        base_url=base_url,
        course_id=course_id,
        voice=voice,
    )
    rendered = {row.code: row for row in review.rows}
    if [row.code for row in projection.rows] != [row.code for row in review.rows]:
        raise WorkbookError("the rendered review does not follow the projection's row order")

    problems = [
        violation.line()
        for row in review.rows
        for sentence in row.sentences
        for violation in sentence.lint(rules, where=f"{row.code} {sentence.shape}")
    ]
    if problems:
        raise WorkbookError("rendered sentences break the register:\n  " + "\n  ".join(problems))

    titles = {section.code: section.title for section in pack.sections}
    labels = _rating_labels(projection, pack)
    ctx = _Builder(voice=voice, titles=titles, labels=labels, projection=projection)

    criteria = Sheet(
        name=ctx.label("sheet_criteria"),
        columns=_CRITERIA_COLUMNS,
        header=tuple(ctx.label(name) for name in _CRITERIA_COLUMNS),
        rows=tuple(ctx.criteria_row(row, rendered[row.code]) for row in projection.rows),
    )
    action_rows: list[tuple[Cell, ...]] = []
    for row in projection.action_plan_order():
        action_rows.extend(ctx.action_lines(row, rendered[row.code]))
    action = Sheet(
        name=ctx.label("sheet_action_plan"),
        columns=_ACTION_COLUMNS,
        header=tuple(ctx.label(name) for name in _ACTION_COLUMNS),
        rows=tuple(action_rows),
    )
    model = WorkbookModel(sheets=(criteria, action), created=created.astimezone(UTC))
    _check(model, rules)
    return model


def _rating_labels(projection: Projection, pack: RubricPack) -> Mapping[str, str]:
    scale = projection.rating_scale or pack.rating_scale
    out = {
        Rating.SUFFICIENTLY_PRESENT.value: scale.sufficiently_present,
        Rating.NOT_APPLICABLE.value: scale.not_applicable,
        Rating.NO_RESPONSE.value: scale.no_response,
    }
    out.update({band.rating: band.label for band in scale.bands})
    return out


@dataclass(frozen=True, slots=True)
class _Builder:
    voice: Voice
    titles: Mapping[str, str]
    labels: Mapping[str, str]
    projection: Projection

    def label(self, name: str) -> Cell:
        register, piece = self.voice.label(name)
        return _cell(register, piece)

    def fragment(self, name: str, **values: Piece | str | int) -> Cell:
        register, _ = self.voice.bank.fmt(name)
        return _cell(register, self.voice.fragment(name, **values))

    # -- the shared columns -------------------------------------------------------------------

    def category(self, row: RowResult) -> Cell:
        return _verbatim_cell(self.titles.get(row.section, row.section))

    def priority(self, row: RowResult) -> Cell:
        if row.priority is None:
            return self.fragment("no_priority")
        return _verbatim_cell(row.priority)

    def determinacy(self, row: RowResult) -> Cell:
        return self.label(_TIER_LABEL[row.tier])

    def rating(self, row: RowResult) -> Cell:
        return _verbatim_cell(row.label)

    def near(self, row: RowResult) -> Cell:
        near = row.near_boundary
        if near is not None:
            return self.fragment(
                "near_boundary",
                hours=plain(f"{near.hours:g} h"),
                distance=plain(f"{near.distance_hours:g} h"),
                below=verbatim(self.labels.get(near.below, near.below)),
                above=verbatim(self.labels.get(near.above, near.above)),
                threshold=plain(f"{near.threshold_hours:g} h"),
                margin=plain(f"{near.margin_hours:g} h"),
            )
        if row.rating.is_band and row.hours is not None:
            return self.label("near_none")
        return self.label("near_not_banded")

    # -- Criteria -----------------------------------------------------------------------------

    def criteria_row(self, row: RowResult, rendered: RenderedRow) -> tuple[Cell, ...]:
        evidence = [s for s in rendered.sentences if s.role not in _NOT_EVIDENCE]
        return (
            self.category(row),
            _verbatim_cell(row.code),
            _verbatim_cell(row.text),
            self.priority(row),
            self.determinacy(row),
            self.label(_COVERAGE_LABEL[row.coverage]),
            self.rating(row),
            self.effort_cell(row),
            self.near(row),
            _sentences_cell(evidence),
            self.action_cell(row, rendered),
        )

    def effort_cell(self, row: RowResult) -> Cell:
        if not (row.rating.is_band and row.hours is not None):
            return self.label("effort_none")
        name = "effort_calibrated" if self.projection.effort.calibrated else "effort_placeholder"
        return self.fragment(name, hours=plain(f"{row.hours:g} h"))

    def action_cell(self, row: RowResult, rendered: RenderedRow) -> Cell:
        actions = [s for s in rendered.sentences if s.role in ACTION_ROLES]
        if not actions:
            return self.label("action_none")
        cell = _sentences_cell(actions)
        if len(cell.text) <= CELL_LIMIT:
            return cell
        # Keep whole sentences, then say how many more there are and where. Nothing is cut.
        kept: list[Sentence] = []
        for sentence in actions:
            trial = _sentences_cell([*kept, sentence])
            if len(trial.text) > CELL_LIMIT - 200:
                break
            kept.append(sentence)
        register, _ = self.voice.bank.fmt("action_plan_more")
        more = self.voice.fragment(
            "action_plan_more",
            lines=plain(str(len(actions) - len(kept))),
            code=verbatim(row.code),
        )
        return _sentences_cell(kept, (register, more))

    # -- Action Plan --------------------------------------------------------------------------

    def action_lines(self, row: RowResult, rendered: RenderedRow) -> list[tuple[Cell, ...]]:
        actions = [s for s in rendered.sentences if s.role in ACTION_ROLES]
        # A row with nothing located still gets a line — its own first sentence, which says what
        # it examined or why nothing did — so the sheet lists every criterion and none silently.
        lines = actions or list(rendered.sentences[:1])
        shared = (
            self.category(row),
            _verbatim_cell(row.code),
            self.priority(row),
            self.rating(row),
            self.near(row),
            self.determinacy(row),
        )
        out = []
        for sentence in lines:
            if sentence.link:
                link = _verbatim_cell(sentence.link, link=sentence.link)
                kind = self.label("link_item" if sentence.link_exact else "link_search")
            else:
                link = self.label("link_none")
                kind = self.label("link_none")
            out.append((*shared, _sentences_cell([sentence]), link, kind))
        return out


def _check(model: WorkbookModel, rules: Register) -> None:
    """Refuse the model rather than write a cell that breaks a promise."""
    problems: list[str] = []
    for where, cell in model.cells():
        if not cell.text.strip():
            problems.append(f"{where} is empty; no cell may be blank")
        if len(cell.text) > CELL_LIMIT:
            problems.append(f"{where} holds {len(cell.text)} characters; the limit is {CELL_LIMIT}")
    problems.extend(violation.line() for violation in model.violations(rules))
    for sheet in model.sheets:
        width = len(sheet.header)
        problems.extend(
            f"{sheet.name.text} row {index} has {len(row)} cells for {width} columns"
            for index, row in enumerate(sheet.rows, start=2)
            if len(row) != width
        )
    if problems:
        raise WorkbookError("the workbook breaks its contract:\n  " + "\n  ".join(problems))


# ---------------------------------------------------------------------------------------------
# Writing it
# ---------------------------------------------------------------------------------------------

#: Every zip entry carries this timestamp, so the container does not drift with the clock.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

_CORE = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
    'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    '<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
    '<dcterms:modified xsi:type="dcterms:W3CDTF">{created}</dcterms:modified>'
    "</cp:coreProperties>"
)
_APP = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"'
    "/>"
)


def to_xlsx(model: WorkbookModel) -> bytes:
    """The workbook as ``.xlsx`` bytes. Never a path: the export gate does the writing."""
    book = Workbook()
    book.remove(book.active)
    bold = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    for sheet in model.sheets:
        ws = book.create_sheet(title=sheet.name.text)
        for column, cell in enumerate(sheet.header, start=1):
            target = ws.cell(row=1, column=column)
            _set_text(target, cell.text)
            target.font = bold
        for r, row in enumerate(sheet.rows, start=2):
            for column, cell in enumerate(row, start=1):
                target = ws.cell(row=r, column=column)
                _set_text(target, cell.text)
                target.alignment = wrap
                if cell.link:
                    target.hyperlink = cell.link
        ws.freeze_panes = "A2"
        for column, name in enumerate(sheet.columns, start=1):
            letter = ws.cell(row=1, column=column).column_letter
            ws.column_dimensions[letter].width = _WIDTHS.get(name, 20)
    buffer = io.BytesIO()
    book.save(buffer)
    return _pin_properties(buffer.getvalue(), model.created)


def _set_text(target, text: str) -> None:  # type: ignore[no-untyped-def]
    # openpyxl reads a string starting with "=" as a formula. Course text can start with anything,
    # and a quote must never become a formula, so every cell is pinned to the string type.
    target.value = text
    target.data_type = "s"


def _pin_properties(data: bytes, created: datetime) -> bytes:
    """Replace the library's document properties with ours, and pin every entry's timestamp.

    openpyxl stamps ``modified`` with the clock on every save and names itself as the application;
    neither is a fact about the review. Rewriting the two parts is the only way to control both.
    """
    stamp = escape(created.strftime("%Y-%m-%dT%H:%M:%SZ"))
    replaced = {
        "docProps/core.xml": _CORE.replace("{created}", stamp).encode("utf-8"),
        "docProps/app.xml": _APP.encode("utf-8"),
    }
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            body = replaced.get(info.filename, source.read(info.filename))
            entry = zipfile.ZipInfo(info.filename, date_time=_ZIP_EPOCH)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            target.writestr(entry, body)
    return out.getvalue()


def export_workbook(gate: ExportGate, course_id: str, data: bytes) -> Path:
    """Write the workbook for one course through the export gate (SPEC §09).

    Refused, by the gate, for a course whose review nobody has opened.
    """
    return gate.save(course_id, WORKBOOK_FILENAME, data)


__all__ = [
    "ACTION_ROLES",
    "CELL_LIMIT",
    "REPLACEMENT",
    "WORKBOOK_FILENAME",
    "Cell",
    "Sheet",
    "WorkbookError",
    "WorkbookModel",
    "build_workbook",
    "clean_text",
    "export_workbook",
    "to_xlsx",
]
