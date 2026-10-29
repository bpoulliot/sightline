"""The review screen as data: what a designer sees for a course, in which channel (item 16).

Pure and testable, in the shape of item 14's report model (:mod:`sightline.report.model`), whose
pieces it reuses. It takes the course as :mod:`sightline.report.loader` reads it back — the review
file the run saved; nothing is re-run — and the latest override record per row, and arranges them.
It writes no sentence of its own: the sentences are :func:`sightline.voice.render_review`'s, the
fixed words are phrase-bank labels and formats, and the values somebody else wrote (a criterion, a
course title, a rating word, a reviewer's name or note) are verbatim spans. Every piece is linted by
item 15's register lint before a template sees it.

THE DECISIONS THIS IS BUILT TO (roadmap item 16; SPEC §02, §09)
---------------------------------------------------------------
- **Grouped by rubric category, in the instrument's own order** (decided 2026-09-08). Severity is a
  *sort* and a *filter* inside a category, never a second grouping. A view is its query parameters,
  so it is linkable.
- **Tier is visible on every row**, as the ink-density chip from ``docs/tokens.css``. A row's
  sentences are set in one block per register (:func:`sightline.report.model.blocks_of`), so a Tier
  A count and a Tier C question never share a channel.
- **A Tier C row opens with nothing selected**, its evidence shown, and stays *unresolved* until a
  rating is recorded for it. Tier A, B and D rows open with the proposal selected, so recording one
  unchanged — with a note, say — records a confirmation, which item 12a counts as calibration data.
- **There is no bulk-accept of any kind.** Every rating is recorded by one form for one row.
- **The reviewer's rating never replaces the proposal.** Both are shown, each labelled.
- **No aggregate.** Counts by category and reading, never added up; a count of unresolved questions
  is work remaining. No progress bar, no percentage, no course score, no run count.

HOW OFTEN HUMAN REVIEWERS AGREE, AND THE NEAR-BOUNDARY MARKER (roadmap item 24)
-------------------------------------------------------------------------------
Every row closes with :func:`sightline.voice.agreement.brief` — how often human reviewers rating the
same course independently chose the same rating, with its sample size and limits — collapsed under
its own heading, after the rating form rather than inside it, so it is available where a rating is
decided without being read as describing the rating a reviewer recorded. A row whose proposed effort
sits near a band threshold carries item 12a's persisted marker as a field after the proposal's
*Estimated effort*, rendered from the review file and never recomputed, and that row's collapsed
statement adds :func:`sightline.voice.agreement.near_boundary_legend`. Neither is a new grouping, a
filter or a sort: the rows, their order and their categories are exactly as before.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from urllib.parse import quote, urlencode

from ..detect.base import Tier
from ..report.loader import LoadedReview
from ..report.model import Block, Field, Text, anchor_for, blocks_of, near_boundary_piece
from ..rubric.project import RowResult
from ..rubric.states import Coverage, Rating
from ..voice import agreement
from ..voice.lint import Register, Violation, default_register
from ..voice.render import Piece, RenderedRow, Sentence, Voice, plain, verbatim

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..models import RatingOverride

#: The two sorts inside a category: the instrument's own order, and SPEC §07's action-plan order
#: (priority crossed with band; a null priority sorts after every labelled one, never dropped).
SORTS: tuple[str, ...] = ("instrument", "severity")

#: The filter key a Tier C row reads under. It proposes no rating, so it is never filed under the
#: No Response its row carries.
QUESTION = "question"

#: The value a filter takes when it filters nothing.
ANY = "any"

#: The longest note stored with a recorded rating. A note is the reviewer's own words; this bounds
#: a pasted document, not a sentence.
NOTE_MAX = 4000

_TIER_LABEL = {Tier.A: "tier_A", Tier.B: "tier_B", Tier.C: "tier_C", Tier.D: "tier_D"}
_COVERAGE_LABEL = {
    Coverage.DETERMINISTIC: "coverage_deterministic",
    Coverage.NOT_YET_IMPLEMENTED: "coverage_not_yet_implemented",
    Coverage.NO_DETERMINISTIC_COVERAGE: "coverage_no_deterministic_coverage",
}

#: Every fixed label a review template prints, so each one is linted with the rest.
_LABELS: tuple[str, ...] = (
    "review_lede",
    "review_index_heading",
    "review_index_intro",
    "review_col_unresolved",
    "review_export_heading",
    "review_export_intro",
    "review_export_button",
    "review_exported",
    "review_export_files",
    "review_sort",
    "review_sort_instrument",
    "review_sort_severity",
    "review_filter_reading",
    "review_filter_tier",
    "review_filter_any",
    "review_filter_unresolved",
    "review_apply",
    "review_rows_none",
    "review_back",
    "review_form_rating",
    "review_form_note",
    "review_form_submit",
    "review_settings_link",
    "col_category",
    "col_reviewer_rating",
    "report_summary_question",
    "link_search",
    "agreement_heading",
)


# ---------------------------------------------------------------------------------------------
# The view: sort and filter, as query parameters
# ---------------------------------------------------------------------------------------------


def reading_key(row: RowResult) -> str:
    """What a row is filtered and counted under: its proposed rating, or "question" for Tier C."""
    return QUESTION if row.tier is Tier.C else row.rating.value


@dataclass(frozen=True, slots=True)
class View:
    """One way of looking at a category. Every field is a query parameter, so a view is a link."""

    sort: str = "instrument"
    reading: str = ANY
    tier: str = ANY
    unresolved: bool = False

    @classmethod
    def parse(
        cls,
        *,
        sort: str | None = None,
        reading: str | None = None,
        tier: str | None = None,
        only: str | None = None,
    ) -> View:
        """A view from request parameters. A value it does not know reads as the default."""
        readings = {rating.value for rating in Rating} | {QUESTION}
        return cls(
            sort=sort if sort in SORTS else "instrument",
            reading=reading if reading in readings else ANY,
            tier=tier if tier in {member.value for member in Tier} else ANY,
            unresolved=only == "unresolved",
        )

    def params(self) -> dict[str, str]:
        """The non-default parameters, in a fixed order, so equal views give equal links."""
        out: dict[str, str] = {}
        if self.sort != "instrument":
            out["sort"] = self.sort
        if self.reading != ANY:
            out["reading"] = self.reading
        if self.tier != ANY:
            out["tier"] = self.tier
        if self.unresolved:
            out["only"] = "unresolved"
        return out

    def query(self) -> str:
        params = self.params()
        return "?" + urlencode(params) if params else ""

    def keeps(self, row: RowResult, *, unresolved: bool) -> bool:
        if self.reading != ANY and reading_key(row) != self.reading:
            return False
        if self.tier != ANY and row.tier.value != self.tier:
            return False
        return not (self.unresolved and not unresolved)


def index_href(course_id: str) -> str:
    return f"/review/{quote(course_id, safe='')}"


def category_href(course_id: str, section: str, view: View | None = None) -> str:
    base = f"{index_href(course_id)}/category/{quote(section, safe='')}"
    return base + (view.query() if view else "")


# ---------------------------------------------------------------------------------------------
# The pieces
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Option:
    """One choice in a sort or filter control."""

    value: str
    label: Text
    selected: bool


@dataclass(frozen=True, slots=True)
class Choice:
    """One rating a reviewer can record. ``checked`` is never true on a Tier C row."""

    value: str
    label: Text
    checked: bool


@dataclass(frozen=True, slots=True)
class Recorded:
    """The latest rating recorded for a row: whose, when, and the note with it."""

    rating: Rating
    line: Text
    note: Text | None


@dataclass(frozen=True, slots=True)
class ReviewRow:
    """One criterion on a category page."""

    code: str
    anchor: str
    tier: str
    #: The coverage state's value, for a ``data-`` attribute a test or a stylesheet can read.
    coverage: str
    criterion: Text
    fields: tuple[Field, ...]
    blocks: tuple[Block, ...]
    recorded: Recorded | None
    #: Set on a Tier C row nobody has recorded a rating for — work remaining, not a judgement.
    unresolved: Text | None
    choices: tuple[Choice, ...]
    hint: Text
    #: Whether the proposal carries a near-boundary marker, so the row's legend is shown.
    near: bool = False

    @property
    def question(self) -> bool:
        return self.tier == Tier.C.value


@dataclass(frozen=True, slots=True)
class CategoryLine:
    """One category on the index: its counts by proposed reading, and its unresolved questions."""

    code: str
    title: Text
    href: str
    counts: tuple[str, ...]
    unresolved: int


@dataclass(frozen=True, slots=True)
class Nav:
    label: Text
    href: str


@dataclass(frozen=True, slots=True)
class Header:
    """What every review page opens with: the course, the instrument, when the review was opened."""

    course_id: str
    title: Text
    instrument: Text
    opened: Text
    lede: Text
    #: Who a recorded rating is recorded as, or the refusal that names Settings.
    reviewer: Text
    reviewer_set: bool


@dataclass(frozen=True, slots=True)
class ExportFile:
    name: str
    href: str


@dataclass(frozen=True, slots=True)
class _Page:
    header: Header
    labels: Mapping[str, Text]

    def _base_texts(self) -> Iterator[tuple[str, Text]]:
        yield "title", self.header.title
        yield "instrument", self.header.instrument
        yield "opened", self.header.opened
        yield "lede", self.header.lede
        yield "reviewer", self.header.reviewer
        for name, label in self.labels.items():
            yield f"label {name}", label

    def texts(self) -> Iterator[tuple[str, Text]]:  # pragma: no cover - overridden
        yield from self._base_texts()

    def sentences(self) -> Iterator[tuple[str, Sentence]]:
        return iter(())

    def violations(self, register: Register | None = None) -> list[Violation]:
        rules = register or default_register()
        found = [v for where, text in self.texts() for v in text.lint(rules, where=where)]
        found.extend(v for where, s in self.sentences() for v in s.lint(rules, where=where))
        return found

    def verbatim(self) -> list[str]:
        """Every verbatim value the page prints: what the lint masks, and nothing else."""
        values = [
            text.text[start:end]
            for _, text in self.texts()
            for start, end in text.piece.spans
            if end > start
        ]
        values.extend(value for _, s in self.sentences() for value in s.verbatim)
        return values


@dataclass(frozen=True, slots=True)
class ReviewIndex(_Page):
    """The index: categories in the instrument's order, with counts, and the export control."""

    columns: tuple[Text, ...] = ()
    categories: tuple[CategoryLine, ...] = ()
    jump: Text | None = None
    jump_href: str | None = None
    exported: bool = False
    files: tuple[ExportFile, ...] = ()

    def texts(self) -> Iterator[tuple[str, Text]]:
        yield from self._base_texts()
        for index, column in enumerate(self.columns):
            yield f"column {index + 1}", column
        for line in self.categories:
            yield f"category {line.code}", line.title
        if self.jump is not None:
            yield "jump", self.jump


@dataclass(frozen=True, slots=True)
class ReviewCategory(_Page):
    """One category's page: its rows in the chosen view, and a form per row."""

    section: str = ""
    #: The category's own address, with no view: where the sort and filter form submits.
    href: str = ""
    category: Text | None = None
    view: View = field(default_factory=View)
    sorts: tuple[Option, ...] = ()
    readings: tuple[Option, ...] = ()
    tiers: tuple[Option, ...] = ()
    rows: tuple[ReviewRow, ...] = ()
    shown: Text | None = None
    back: Nav | None = None
    previous: Nav | None = None
    next: Nav | None = None
    #: The banner after a rating was recorded, or the reason one was not.
    saved: Text | None = None
    refused: Text | None = None
    #: How often human reviewers choose the same rating: :func:`sightline.voice.agreement.brief`.
    agreement: tuple[Sentence, ...] = ()
    #: The near-boundary marker's explanation, shown on the rows that carry one.
    legend: Sentence | None = None

    def texts(self) -> Iterator[tuple[str, Text]]:
        yield from self._base_texts()
        if self.category is not None:
            yield "category", self.category
        for group, options in (("sort", self.sorts), ("reading", self.readings)):
            for option in options:
                yield f"{group} {option.value}", option.label
        for option in self.tiers:
            yield f"tier {option.value}", option.label
        if self.shown is not None:
            yield "shown", self.shown
        for nav in (self.back, self.previous, self.next):
            if nav is not None:
                yield f"nav {nav.href}", nav.label
        for name, banner in (("saved", self.saved), ("refused", self.refused)):
            if banner is not None:
                yield name, banner
        for row in self.rows:
            yield f"{row.code} criterion", row.criterion
            for item in row.fields:
                yield f"{row.code} field name", item.name
                yield f"{row.code} field {item.name.text}", item.value
            for block in row.blocks:
                yield f"{row.code} block {block.register}", block.heading
            if row.recorded is not None:
                yield f"{row.code} recorded", row.recorded.line
                if row.recorded.note is not None:
                    yield f"{row.code} note", row.recorded.note
            if row.unresolved is not None:
                yield f"{row.code} unresolved", row.unresolved
            for choice in row.choices:
                yield f"{row.code} choice {choice.value}", choice.label
            yield f"{row.code} hint", row.hint

    def sentences(self) -> Iterator[tuple[str, Sentence]]:
        for sentence in self.agreement:
            yield f"agreement {sentence.shape}", sentence
        if self.legend is not None:
            yield f"legend {self.legend.shape}", self.legend
        for row in self.rows:
            for block in row.blocks:
                for sentence in block.sentences:
                    yield f"{row.code} {sentence.shape}", sentence


class ReviewScreenError(ValueError):
    """A review page breaks its contract: a lint violation, an empty piece of text."""


# ---------------------------------------------------------------------------------------------
# Building it
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReviewScreen:
    """Everything the pages are built from, for one course: read once per request."""

    course_id: str
    review: LoadedReview
    opened: datetime
    records: Mapping[str, RatingOverride]
    reviewer: str | None
    voice: Voice = field(default_factory=Voice)

    # -- the shared pieces ---------------------------------------------------------------------

    def label(self, name: str) -> Text:
        register, piece = self.voice.label(name)
        return Text(register, piece)

    def fragment(self, name: str, **values: Piece | str | int) -> Text:
        register, _ = self.voice.bank.fmt(name)
        return Text(register, self.voice.fragment(name, **values))

    @property
    def rows(self) -> tuple[RowResult, ...]:
        """The projection's rows. A switched-off optional section's rows are not among them
        (``Projection.omitted``), so they are neither shown nor counted as unresolved."""
        return self.review.projection.rows

    def sections(self) -> list[tuple[str, str]]:
        """``(code, title)`` of every category with a row, in the instrument's own order."""
        present = {row.section for row in self.rows}
        ordered = [(s.code, s.title) for s in self.review.pack.sections if s.code in present]
        known = {code for code, _ in ordered}
        for row in self.rows:
            if row.section not in known:
                known.add(row.section)
                ordered.append((row.section, row.section))
        return ordered

    def rating_words(self) -> list[tuple[str, str]]:
        """``(rating, the instrument's word)`` in the instrument's order: clean, bands, defaults."""
        scale = self.review.projection.rating_scale or self.review.pack.rating_scale
        words = [(Rating.SUFFICIENTLY_PRESENT.value, scale.sufficiently_present)]
        words.extend((band.rating, band.label) for band in scale.bands)
        words.append((Rating.NO_RESPONSE.value, scale.no_response))
        words.append((Rating.NOT_APPLICABLE.value, scale.not_applicable))
        return words

    def word(self, rating: Rating) -> str:
        return dict(self.rating_words())[rating.value]

    def unresolved(self, row: RowResult) -> bool:
        """A Tier C row nobody has recorded a rating for."""
        return row.tier is Tier.C and row.code not in self.records

    def header(self) -> Header:
        course = self.review.course_name or self.review.course_id or ""
        instrument = verbatim(self.review.pack.name)
        stamp = self.opened.astimezone(UTC)
        if self.reviewer:
            reviewer = self.fragment("review_records_as", reviewer=verbatim(self.reviewer))
        else:
            reviewer = self.label("review_needs_reviewer")
        return Header(
            course_id=self.course_id,
            title=self.fragment("report_title", course=verbatim(course), instrument=instrument),
            instrument=self.fragment(
                "report_instrument",
                instrument=instrument,
                version=verbatim(self.review.pack.version),
            ),
            opened=self.fragment("report_opened", date=plain(f"{stamp:%Y-%m-%d %H:%M} UTC")),
            lede=self.label("review_lede"),
            reviewer=reviewer,
            reviewer_set=bool(self.reviewer),
        )

    def labels(self) -> dict[str, Text]:
        return {name: self.label(name) for name in _LABELS}

    # -- the index -----------------------------------------------------------------------------

    def index(self, *, exported: bool = False, files: Sequence[ExportFile] = ()) -> ReviewIndex:
        keys = [rating for rating, _ in self.rating_words()] + [QUESTION]
        columns = [Text("A", verbatim(word)) for _, word in self.rating_words()]
        columns.append(self.label("report_summary_question"))
        lines = []
        for code, title in self.sections():
            members = [row for row in self.rows if row.section == code]
            counts = dict.fromkeys(keys, 0)
            for row in members:
                counts[reading_key(row)] += 1
            lines.append(
                CategoryLine(
                    code=code,
                    title=Text("A", verbatim(title)),
                    href=category_href(self.course_id, code),
                    counts=tuple(str(counts[key]) for key in keys),
                    unresolved=sum(self.unresolved(row) for row in members),
                )
            )
        first = self._first_unresolved()
        if first is None:
            jump, jump_href = self.label("review_jump_none"), None
        else:
            titles = dict(self.sections())
            jump = self.fragment(
                "review_jump",
                code=verbatim(first.code),
                title=verbatim(titles.get(first.section, first.section)),
            )
            jump_href = category_href(self.course_id, first.section) + "#" + anchor_for(first.code)
        page = ReviewIndex(
            header=self.header(),
            labels=self.labels(),
            columns=tuple(columns),
            categories=tuple(lines),
            jump=jump,
            jump_href=jump_href,
            exported=exported,
            files=tuple(files),
        )
        _check(page)
        return page

    def _first_unresolved(self) -> RowResult | None:
        """The first unresolved question, in the instrument's category order then row order."""
        order = {code: position for position, (code, _) in enumerate(self.sections())}
        pending = [row for row in self.rows if self.unresolved(row)]
        pending.sort(key=lambda row: order.get(row.section, len(order)))
        return pending[0] if pending else None

    # -- a category ----------------------------------------------------------------------------

    def category(
        self,
        section: str,
        view: View,
        *,
        saved: Text | None = None,
        refused: Text | None = None,
    ) -> ReviewCategory:
        sections = self.sections()
        codes = [code for code, _ in sections]
        if section not in codes:
            raise KeyError(section)
        position = codes.index(section)
        titles = dict(sections)
        members = self._ordered(section, view)
        rendered = {row.code: row for row in self.review.render(self.voice).rows}
        rows = tuple(self._row(row, rendered[row.code]) for row in members)

        def nav(offset: int, name: str) -> Nav | None:
            target = position + offset
            if not 0 <= target < len(codes):
                return None
            code = codes[target]
            return Nav(
                label=self.fragment(name, title=verbatim(titles[code])),
                href=category_href(self.course_id, code),
            )

        page = ReviewCategory(
            header=self.header(),
            labels=self.labels(),
            section=section,
            href=category_href(self.course_id, section),
            category=Text("A", verbatim(titles[section])),
            view=view,
            sorts=tuple(
                Option(name, self.label(f"review_sort_{name}"), view.sort == name) for name in SORTS
            ),
            readings=(
                Option(ANY, self.label("review_filter_any"), view.reading == ANY),
                *(
                    Option(value, Text("A", verbatim(word)), view.reading == value)
                    for value, word in self.rating_words()
                ),
                Option(QUESTION, self.label("report_summary_question"), view.reading == QUESTION),
            ),
            tiers=(
                Option(ANY, self.label("review_filter_any"), view.tier == ANY),
                *(
                    Option(tier.value, self.label(_TIER_LABEL[tier]), view.tier == tier.value)
                    for tier in Tier
                ),
            ),
            rows=rows,
            shown=self.fragment("review_shown", rows=self.voice.count(len(rows), "row")),
            back=Nav(self.label("review_back"), index_href(self.course_id)),
            previous=nav(-1, "review_previous"),
            next=nav(1, "review_next"),
            saved=saved,
            refused=refused,
            agreement=agreement.brief(voice=self.voice),
            legend=(
                agreement.near_boundary_legend(self.review.projection.effort, voice=self.voice)
                if any(row.near for row in rows)
                else None
            ),
        )
        _check(page)
        return page

    def _ordered(self, section: str, view: View) -> list[RowResult]:
        """The category's rows in the view's order, filtered. Nothing is dropped for a null."""
        if view.sort == "severity":
            source: Sequence[RowResult] = self.review.projection.action_plan_order()
        else:
            source = self.rows
        return [
            row
            for row in source
            if row.section == section and view.keeps(row, unresolved=self.unresolved(row))
        ]

    def _row(self, row: RowResult, rendered: RenderedRow) -> ReviewRow:
        fields = [
            Field(self.label("col_priority"), self._priority(row)),
            Field(self.label("col_determinacy"), self.label(_TIER_LABEL[row.tier])),
            Field(self.label("col_coverage"), self.label(_COVERAGE_LABEL[row.coverage])),
            Field(self.label("col_rating"), self._reading(row)),
        ]
        if row.rating.is_band and row.hours is not None:
            name = (
                "effort_calibrated"
                if self.review.projection.effort.calibrated
                else "effort_placeholder"
            )
            fields.append(
                Field(
                    self.label("col_effort"), self.fragment(name, hours=plain(f"{row.hours:g} h"))
                )
            )
        if row.near_boundary is not None:
            words = dict(self.rating_words())
            fields.append(
                Field(
                    self.label("col_near_boundary"),
                    Text("A", near_boundary_piece(self.voice, row.near_boundary, words)),
                )
            )
        record = self.records.get(row.code)
        recorded = self._recorded(record) if record is not None else None
        question = row.tier is Tier.C
        if question:
            # Nothing is pre-selected on a question, recorded or not: the record is shown above
            # the form, and a second rating is chosen afresh.
            selected = None
        elif recorded is not None:
            selected = recorded.rating.value
        else:
            selected = row.rating.value
        return ReviewRow(
            code=row.code,
            anchor=anchor_for(row.code),
            tier=row.tier.value,
            coverage=row.coverage.value,
            criterion=Text("A", verbatim(row.text)),
            fields=tuple(fields),
            blocks=blocks_of(rendered.sentences, self.voice),
            recorded=recorded,
            unresolved=self.label("review_unresolved") if self.unresolved(row) else None,
            choices=tuple(
                Choice(value, Text("A", verbatim(word)), value == selected)
                for value, word in self.rating_words()
            ),
            hint=self.label(
                "review_form_hint_question" if question else "review_form_hint_proposal"
            ),
            near=row.near_boundary is not None,
        )

    def _priority(self, row: RowResult) -> Text:
        if row.priority is None:
            return self.fragment("no_priority")
        return Text("A", verbatim(row.priority))

    def _reading(self, row: RowResult) -> Text:
        """A Tier C row proposes nothing, so it never shows its default as a reading."""
        if row.tier is Tier.C:
            return self.label("report_reading_question")
        return Text("A", verbatim(row.label))

    def _recorded(self, record: RatingOverride) -> Recorded:
        rating = Rating(record.human_rating)
        when = record.created_at.astimezone(UTC) if record.created_at else None
        line = self.fragment(
            "review_recorded",
            rating=verbatim(self.word(rating)),
            reviewer=verbatim(record.reviewer),
            date=plain(f"{when:%Y-%m-%d %H:%M} UTC" if when else ""),
        )
        note = (
            self.fragment("review_note", note=verbatim(record.note))
            if record.note and record.note.strip()
            else None
        )
        return Recorded(rating=rating, line=line, note=note)

    # -- banners -------------------------------------------------------------------------------

    def saved(self, code: str) -> Text | None:
        record = self.records.get(code)
        if record is None:
            return None
        return self.fragment(
            "review_saved",
            code=verbatim(code),
            rating=verbatim(self.word(Rating(record.human_rating))),
        )

    def choose_question(self, code: str) -> Text:
        return self.fragment("review_choose_question", code=verbatim(code))


def _check(page: _Page) -> None:
    problems = [violation.line() for violation in page.violations()]
    for where, text in page.texts():
        if not text.text.strip():
            problems.append(f"{where} is empty")
    if isinstance(page, ReviewCategory):
        for row in page.rows:
            if row.question and any(choice.checked for choice in row.choices):
                problems.append(f"{row.code}: a Tier C row ships a pre-selected rating")
            for block in row.blocks:
                registers = {sentence.register for sentence in block.sentences}
                if registers != {block.register}:
                    problems.append(
                        f"{row.code}: a block for register {block.register} holds "
                        f"{sorted(registers)}"
                    )
    if problems:
        raise ReviewScreenError("the review page breaks its contract:\n  " + "\n  ".join(problems))


__all__ = [
    "ANY",
    "NOTE_MAX",
    "QUESTION",
    "SORTS",
    "CategoryLine",
    "Choice",
    "ExportFile",
    "Header",
    "Nav",
    "Option",
    "Recorded",
    "ReviewCategory",
    "ReviewIndex",
    "ReviewRow",
    "ReviewScreen",
    "ReviewScreenError",
    "View",
    "category_href",
    "index_href",
    "reading_key",
]
