"""The PDF course report as data: what is said, in what order, in which visual channel.

Pure and testable. It takes what :func:`sightline.voice.render_review` produced and arranges it; it
writes no sentence of its own. Headings and fixed words are phrase-bank labels and formats, values
somebody else wrote (a criterion's text, a course title, a rating word, a URL) are verbatim spans,
and every one of them is linted by item 15's register lint before :mod:`sightline.report.pdf`
turns the model into a page.

THE STRUCTURE (decided 2026-09-08, not to be re-opened here)
------------------------------------------------------------
One document, addressed to the instructional designer and forwardable to the instructor unchanged,
in this order: (a) *what this document is*; (b) counts by category and reading, with no aggregate
of any shape; (c) the action plan in :meth:`~sightline.rubric.project.Projection.action_plan_order`;
(d) the accessibility and interaction categories **in full** — every sentence the voice layer gives
those rows, with quotes, counts, locations and links; (e) every other row as **one compact line**,
with the statement that the workbook holds everything; (f) the closing narrative.

The action plan lists *criteria*, one entry each, with what was located for them and a link to the
entry below where those items are. It does not repeat the items themselves: section (d) carries
them for its two categories, and the workbook carries them for every criterion (item 13's Action
Plan sheet is the one-line-per-item form). What an entry holds is the located items, never a
remediation step — nothing supplies remediation wording, and SPEC §01 forbids composing it (roadmap
item 35).

TIER A AND TIER C NEVER SHARE A VISUAL CHANNEL (SPEC §02)
---------------------------------------------------------
A row in full is split into :class:`Block` s by the ``register`` each :class:`Sentence` carries —
never by reading its text. One block per register, each with its own heading from the bank and its
own treatment in the stylesheet, so a count and a question are never set in one paragraph.

THE REVIEWER'S RATING SITS BESIDE THE PROPOSAL, NEVER IN ITS PLACE
------------------------------------------------------------------
Operator decision Q2 (2026-10-29, roadmap item 16). Where a reviewer recorded a rating, the report
carries their latest one, labelled as the reviewer's: a field after *Proposed rating* in a row in
full, a fact in a compact line, a column in the action plan. In those places the proposal is labelled
as proposed, and it keeps its band, its effort estimate and its place in the action plan — nothing is
re-banded or back-computed from the human rating. A report for a course nobody overrode is the
report item 14 printed, byte for byte: every addition is conditional on a rating having been
recorded.

THE COVERAGE STATES STAY DISTINGUISHABLE IN ONE LINE
----------------------------------------------------
A compact line carries the row's coverage in the bank's words and a *located* cell that says what
ran: a count of observations only where a check ran, "no check ran" where none did, and whether
located evidence was gathered at all. A Tier C row reads "no rating proposed", never its default.
So a clean Tier A row, a row no check examines yet and a row no check is assigned to, and a Tier B
row left at No Response and a Tier C row, each read differently.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ..detect.base import Tier
from ..rubric.project import RowResult
from ..rubric.states import Coverage, Rating
from ..voice.lint import Register, Violation, default_register
from ..voice.render import Piece, RenderedRow, Sentence, Voice, plain, verbatim
from ..workbook import WORKBOOK_FILENAME
from .loader import LoadedReview

#: The categories rendered in full, by section code. Decided 2026-09-08 for the reference
#: instrument: accessibility (ACD) and interaction (ENIN). A pack with neither renders every row
#: compactly, and says the workbook holds everything.
FULL_SECTIONS: tuple[str, ...] = ("ACD", "ENIN")

#: The order the four registers' blocks take inside a row: counts, then located evidence, then the
#: question, then the limits of the pass.
REGISTER_ORDER: tuple[str, ...] = ("A", "B", "C", "D")

_TIER_LABEL = {Tier.A: "tier_A", Tier.B: "tier_B", Tier.C: "tier_C", Tier.D: "tier_D"}
_COVERAGE_LABEL = {
    Coverage.DETERMINISTIC: "coverage_deterministic",
    Coverage.NOT_YET_IMPLEMENTED: "coverage_not_yet_implemented",
    Coverage.NO_DETERMINISTIC_COVERAGE: "coverage_no_deterministic_coverage",
}
#: The roles that locate something in the course. Only these carry a Canvas link.
LOCATED_ROLES = frozenset({"finding", "quote"})


class ReportError(ValueError):
    """The report cannot be built as promised: no base URL, a lint violation, a broken contract."""


# ---------------------------------------------------------------------------------------------
# The pieces
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Text:
    """A piece of text and the register it is read in."""

    register: str
    piece: Piece

    @property
    def text(self) -> str:
        return self.piece.text

    def lint(self, rules: Register, *, where: str) -> list[Violation]:
        return rules.lint(
            self.piece.text, self.register, where=where, sentence=False, spans=self.piece.spans
        )


@dataclass(frozen=True, slots=True)
class Link:
    """A Canvas link, the words it is shown with, and whether it finds the item by id."""

    url: str
    label: Text
    exact: bool = True


@dataclass(frozen=True, slots=True)
class Block:
    """One visual channel inside a row: every sentence of one register, under its own heading."""

    register: str
    heading: Text
    sentences: tuple[Sentence, ...]


@dataclass(frozen=True, slots=True)
class Field:
    """A labelled value in a row's header — ``Priority: Essential``."""

    name: Text
    value: Text


@dataclass(frozen=True, slots=True)
class RowDetail:
    """One criterion in full."""

    code: str
    anchor: str
    tier: str
    criterion: Text
    fields: tuple[Field, ...]
    blocks: tuple[Block, ...]


@dataclass(frozen=True, slots=True)
class CompactLine:
    """One criterion in one line: code, criterion, reading, coverage, what was located, where."""

    code: str
    anchor: str
    tier: str
    criterion: Text
    determinacy: Text
    coverage: Text
    reading: Text
    located: Text
    links: tuple[Link, ...]
    #: Shown when there is no link, so the cell is never blank.
    no_link: Text
    #: The reviewer's recorded rating, labelled as theirs; ``None`` when none was recorded.
    reviewer: Text | None = None


@dataclass(frozen=True, slots=True)
class Section:
    """One category of the instrument, rendered in full or compactly."""

    code: str
    anchor: str
    title: Text
    full: bool
    details: tuple[RowDetail, ...] = ()
    lines: tuple[CompactLine, ...] = ()


@dataclass(frozen=True, slots=True)
class PlanEntry:
    code: str
    #: The anchor of the criterion's entry below — in full or compact.
    target: str
    tier: str
    priority: Text
    criterion: Text
    reading: Text
    located: Text
    #: The reviewer's rating, or "none recorded"; ``None`` when nobody recorded any rating.
    reviewer: Text | None = None


@dataclass(frozen=True, slots=True)
class SummaryRow:
    category: Text
    counts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Summary:
    columns: tuple[Text, ...]
    rows: tuple[SummaryRow, ...]


@dataclass(frozen=True, slots=True)
class NarrativeSlot:
    register: str
    heading: Sentence
    sentences: tuple[Sentence, ...]


@dataclass(frozen=True, slots=True)
class ReportModel:
    """The whole report. :func:`sightline.report.pdf.to_pdf` is the only thing that prints it."""

    created: datetime
    title: Text
    course: Text
    course_link: str
    instrument: Text
    opened: Text
    running: Text
    labels: Mapping[str, Text]
    about: Text
    summary: Summary
    plan: tuple[PlanEntry, ...]
    full: tuple[Section, ...]
    compact: tuple[Section, ...]
    compact_intro: Text
    narrative: tuple[NarrativeSlot, ...]
    #: Whether any reviewer's rating is carried. Every Q2 addition to the page is gated on it.
    reviewed: bool = False

    # -- reading it back ------------------------------------------------------------------------

    def texts(self) -> Iterator[tuple[str, Text]]:
        """Every fixed or verbatim piece of text with a location, for the lint."""
        yield "title", self.title
        yield "course", self.course
        yield "instrument", self.instrument
        yield "opened", self.opened
        yield "running", self.running
        yield "about", self.about
        yield "compact intro", self.compact_intro
        for name, label in self.labels.items():
            yield f"label {name}", label
        for index, column in enumerate(self.summary.columns):
            yield f"summary column {index + 1}", column
        for row in self.summary.rows:
            yield f"summary row {row.category.text}", row.category
        for entry in self.plan:
            for name in ("priority", "criterion", "reading", "located"):
                yield f"plan {entry.code} {name}", getattr(entry, name)
            if entry.reviewer is not None:
                yield f"plan {entry.code} reviewer", entry.reviewer
        for section in (*self.full, *self.compact):
            yield f"section {section.code} title", section.title
            for detail in section.details:
                yield f"{detail.code} criterion", detail.criterion
                for field in detail.fields:
                    yield f"{detail.code} field name", field.name
                    yield f"{detail.code} field {field.name.text}", field.value
                for block in detail.blocks:
                    yield f"{detail.code} block {block.register}", block.heading
            for line in section.lines:
                for name in ("criterion", "determinacy", "coverage", "reading", "located"):
                    yield f"{line.code} {name}", getattr(line, name)
                yield f"{line.code} no link", line.no_link
                if line.reviewer is not None:
                    yield f"{line.code} reviewer", line.reviewer
                for link in line.links:
                    yield f"{line.code} link", link.label

    def sentences(self) -> Iterator[tuple[str, Sentence]]:
        """Every rendered sentence the report prints, with a location."""
        for section in self.full:
            for detail in section.details:
                for block in detail.blocks:
                    for sentence in block.sentences:
                        yield f"{detail.code} {sentence.shape}", sentence
        for slot in self.narrative:
            yield f"narrative {slot.heading.shape}", slot.heading
            for sentence in slot.sentences:
                yield f"narrative {sentence.shape}", sentence

    def violations(self, register: Register | None = None) -> list[Violation]:
        rules = register or default_register()
        found = [v for where, text in self.texts() for v in text.lint(rules, where=where)]
        found.extend(v for where, s in self.sentences() for v in s.lint(rules, where=where))
        return found

    def links(self) -> Iterator[tuple[str, str]]:
        """Every Canvas link the report carries, with where it is."""
        yield "course", self.course_link
        for section in self.full:
            for detail in section.details:
                for block in detail.blocks:
                    for sentence in block.sentences:
                        if sentence.link:
                            yield detail.code, sentence.link
        for section in self.compact:
            for line in section.lines:
                for link in line.links:
                    yield line.code, link.url


# ---------------------------------------------------------------------------------------------
# Building it
# ---------------------------------------------------------------------------------------------


def anchor_for(code: str) -> str:
    """An element id for a criterion code. ``ASFE 7`` -> ``row-ASFE-7``; codes are unique."""
    return "row-" + re.sub(r"[^A-Za-z0-9]+", "-", code).strip("-")


def build_report(
    review: LoadedReview,
    *,
    created: datetime,
    overrides: Mapping[str, Rating] | None = None,
    voice: Voice | None = None,
    register: Register | None = None,
) -> ReportModel:
    """The report model for one loaded review, linted, or :class:`ReportError`.

    ``created`` is the one timestamp the document carries — the moment a person opened the review
    (:meth:`sightline.batch.gate.ExportGate.require_open`), never the clock. ``overrides`` is the
    reviewer's latest rating per row code (:func:`sightline.rubric.overrides.reviewer_ratings`);
    empty or ``None`` prints the report exactly as it was before item 16.
    """
    if not review.base_url:
        raise ReportError(
            "The review file records no Canvas base URL, and every located item in the report "
            "carries a link built from it."
        )
    if created.tzinfo is None:
        raise ReportError("created must be timezone-aware; the report records UTC")
    voice = voice or Voice()
    rules = register or default_register()
    projection = review.projection
    rendered = review.render(voice)
    if [row.code for row in projection.rows] != [row.code for row in rendered.rows]:
        raise ReportError("the rendered review does not follow the projection's row order")
    by_code = {row.code: row for row in rendered.rows}
    reviewed = dict(overrides or {})
    unknown = sorted(set(reviewed) - set(by_code))
    if unknown:
        raise ReportError(
            f"a reviewer's rating was given for {unknown}, which this review has no row for"
        )
    ctx = _Builder(voice=voice, review=review, overrides=reviewed)

    titles = {section.code: section.title for section in review.pack.sections}
    order = [section.code for section in review.pack.sections]
    rows_by_section: dict[str, list[RowResult]] = {}
    for row in projection.rows:
        rows_by_section.setdefault(row.section, []).append(row)
    order.extend(code for code in rows_by_section if code not in order)

    full: list[Section] = []
    compact: list[Section] = []
    for code in order:
        members = rows_by_section.get(code)
        if not members:
            continue
        title = Text("A", verbatim(titles.get(code, code)))
        if code in FULL_SECTIONS:
            full.append(
                Section(
                    code=code,
                    anchor=f"section-{anchor_for(code)}",
                    title=ctx.fragment("report_full_heading", title=verbatim(title.text)),
                    full=True,
                    details=tuple(ctx.detail(row, by_code[row.code]) for row in members),
                )
            )
        else:
            compact.append(
                Section(
                    code=code,
                    anchor=f"section-{anchor_for(code)}",
                    title=title,
                    full=False,
                    lines=tuple(ctx.compact(row, by_code[row.code]) for row in members),
                )
            )

    plan = tuple(
        ctx.plan_entry(row, by_code[row.code])
        for row in projection.action_plan_order()
        if _located(by_code[row.code])
    )

    course_name = review.course_name or review.course_id or ""
    instrument = verbatim(review.pack.name)
    stamp = created.astimezone(UTC)
    model = ReportModel(
        created=stamp,
        title=ctx.fragment("report_title", course=verbatim(course_name), instrument=instrument),
        course=Text("A", verbatim(course_name)),
        course_link=(
            f"{review.base_url.rstrip('/')}/courses/{review.course_id}"
            if review.course_id
            else review.base_url
        ),
        instrument=ctx.fragment(
            "report_instrument", instrument=instrument, version=verbatim(review.pack.version)
        ),
        opened=ctx.fragment("report_opened", date=plain(f"{stamp:%Y-%m-%d %H:%M} UTC")),
        running=ctx.fragment("report_running", course=verbatim(course_name), instrument=instrument),
        labels={name: ctx.label(name) for name in _LABELS},
        about=ctx.fragment("report_about", instrument=instrument),
        summary=ctx.summary(),
        plan=plan,
        full=tuple(full),
        compact=tuple(compact),
        compact_intro=ctx.fragment("report_compact_intro", workbook=verbatim(WORKBOOK_FILENAME)),
        narrative=tuple(
            NarrativeSlot(
                register=slot.heading.register, heading=slot.heading, sentences=slot.sentences
            )
            for slot in rendered.narrative.slots
        ),
        reviewed=bool(reviewed),
    )
    _check(model, rules)
    return model


#: The labels a template prints as headings or column names.
_LABELS: tuple[str, ...] = (
    "report_about_heading",
    "report_summary_heading",
    "report_summary_intro",
    "report_plan_heading",
    "report_plan_intro",
    "report_plan_none",
    "report_compact_heading",
    "report_narrative_heading",
    "report_col_located",
    "report_col_where",
    "col_code",
    "col_criterion",
    "col_priority",
    "col_rating",
    "col_determinacy",
    "col_coverage",
    "link_search",
    "col_reviewer_rating",
    "reviewer_none",
    "report_reviewer_intro",
)


def _located(rendered: RenderedRow) -> bool:
    return any(s.role in LOCATED_ROLES or s.role == "question" for s in rendered.sentences)


@dataclass(frozen=True, slots=True)
class _Builder:
    voice: Voice
    review: LoadedReview
    overrides: Mapping[str, Rating]

    def label(self, name: str) -> Text:
        register, piece = self.voice.label(name)
        return Text(register, piece)

    def fragment(self, name: str, **values: Piece | str | int) -> Text:
        register, _ = self.voice.bank.fmt(name)
        return Text(register, self.voice.fragment(name, **values))

    # -- shared cells ---------------------------------------------------------------------------

    def reading(self, row: RowResult) -> Text:
        """A Tier C row proposes nothing, so it never shows the default as its reading."""
        if row.tier is Tier.C:
            return self.label("report_reading_question")
        return Text("A", verbatim(row.label))

    def labelled_reading(self, row: RowResult) -> Text:
        """The proposal where it stands unlabelled beside a reviewer's rating: called proposed."""
        if row.code not in self.overrides or row.tier is Tier.C:
            return self.reading(row)
        return self.fragment("report_proposed_reading", label=verbatim(row.label))

    def human_label(self, row: RowResult) -> Text | None:
        """The instrument's word for the reviewer's rating on this row, or ``None``."""
        human = self.overrides.get(row.code)
        if human is None:
            return None
        return Text("A", verbatim(_rating_word(self.review, human)))

    def reviewer_fact(self, row: RowResult) -> Text | None:
        """The reviewer's rating as a labelled fact, for a compact line. ``None`` if none."""
        human = self.overrides.get(row.code)
        if human is None:
            return None
        return self.fragment(
            "report_reviewer_reading", label=verbatim(_rating_word(self.review, human))
        )

    def reviewer_cell(self, row: RowResult) -> Text | None:
        """The action plan's reviewer column: their rating, "none recorded", or no column at all."""
        if not self.overrides:
            return None
        return self.human_label(row) or self.label("reviewer_none")

    def priority(self, row: RowResult) -> Text:
        if row.priority is None:
            return self.fragment("no_priority")
        return Text("A", verbatim(row.priority))

    def located(self, row: RowResult, rendered: RenderedRow) -> Text:
        """What ran and what it located. Never a bare zero where nothing ran."""
        parts: list[tuple[str, Piece]] = []
        if row.contributions:
            parts.append(("A", self.voice.count(len(row.findings), "observation")))
        else:
            parts.append(self.voice.label("report_no_check_ran"))
        if row.needs_extraction:
            if row.code in self.review.extractions:
                quotes = len(rendered.by_role("quote"))
                parts.append(("B", self.voice.count(quotes, "quote")))
            else:
                parts.append(self.voice.label("report_not_gathered"))
        questions = len(rendered.by_role("question"))
        if questions:
            parts.append(("C", self.voice.count(questions, "question")))
        # The cell is read in its weakest register: a C part makes the whole cell C, so the lint
        # holds it to the stricter list rather than excusing the question inside an A cell.
        register = max((register for register, _ in parts), key=REGISTER_ORDER.index)
        return Text(register, self.voice.listing([piece for _, piece in parts]))

    # -- the three row forms --------------------------------------------------------------------

    def detail(self, row: RowResult, rendered: RenderedRow) -> RowDetail:
        fields = [
            Field(self.label("col_priority"), self.priority(row)),
            Field(self.label("col_determinacy"), self.label(_TIER_LABEL[row.tier])),
            Field(self.label("col_coverage"), self.label(_COVERAGE_LABEL[row.coverage])),
            Field(self.label("col_rating"), self.reading(row)),
        ]
        human = self.human_label(row)
        if human is not None:
            fields.append(Field(self.label("col_reviewer_rating"), human))
        if row.rating.is_band and row.hours is not None:
            name = (
                "effort_calibrated"
                if self.review.projection.effort.calibrated
                else "effort_placeholder"
            )
            fields.append(
                Field(
                    self.label("col_effort"),
                    self.fragment(name, hours=plain(f"{row.hours:g} h")),
                )
            )
        return RowDetail(
            code=row.code,
            anchor=anchor_for(row.code),
            tier=row.tier.value,
            criterion=Text("A", verbatim(row.text)),
            fields=tuple(fields),
            blocks=blocks_of(rendered.sentences, self.voice),
        )

    def compact(self, row: RowResult, rendered: RenderedRow) -> CompactLine:
        return CompactLine(
            code=row.code,
            anchor=anchor_for(row.code),
            tier=row.tier.value,
            criterion=Text("A", verbatim(row.text)),
            determinacy=self.label(_TIER_LABEL[row.tier]),
            coverage=self.label(_COVERAGE_LABEL[row.coverage]),
            reading=self.labelled_reading(row),
            located=self.located(row, rendered),
            links=_artifact_links(rendered, self.voice),
            no_link=self.label("report_nothing_to_link"),
            reviewer=self.reviewer_fact(row),
        )

    def plan_entry(self, row: RowResult, rendered: RenderedRow) -> PlanEntry:
        return PlanEntry(
            code=row.code,
            target=anchor_for(row.code),
            tier=row.tier.value,
            priority=self.priority(row),
            criterion=Text("A", verbatim(row.text)),
            reading=self.reading(row),
            located=self.located(row, rendered),
            reviewer=self.reviewer_cell(row),
        )

    # -- the summary ----------------------------------------------------------------------------

    def summary(self) -> Summary:
        """Counts of rows per category and reading. Counts only: nothing is added up."""
        projection = self.review.projection
        scale = projection.rating_scale or self.review.pack.rating_scale
        columns: list[tuple[str, Text]] = [
            (Rating.SUFFICIENTLY_PRESENT.value, Text("A", verbatim(scale.sufficiently_present)))
        ]
        columns.extend((band.rating, Text("A", verbatim(band.label))) for band in scale.bands)
        columns.append((Rating.NO_RESPONSE.value, Text("A", verbatim(scale.no_response))))
        columns.append((Rating.NOT_APPLICABLE.value, Text("A", verbatim(scale.not_applicable))))
        columns.append(("question", self.label("report_summary_question")))
        keys = [key for key, _ in columns]

        titles = {section.code: section.title for section in self.review.pack.sections}
        seen: dict[str, dict[str, int]] = {}
        for row in projection.rows:
            key = "question" if row.tier is Tier.C else row.rating.value
            seen.setdefault(row.section, dict.fromkeys(keys, 0))[key] += 1
        return Summary(
            columns=tuple(text for _, text in columns),
            rows=tuple(
                SummaryRow(
                    category=Text("A", verbatim(titles.get(section, section))),
                    counts=tuple(str(counts[key]) for key in keys),
                )
                for section, counts in seen.items()
            ),
        )


def _rating_word(review: LoadedReview, rating: Rating) -> str:
    """The instrument's own word for ``rating``, from the scale the review was made with."""
    scale = review.projection.rating_scale or review.pack.rating_scale
    words = {
        Rating.SUFFICIENTLY_PRESENT: scale.sufficiently_present,
        Rating.NO_RESPONSE: scale.no_response,
        Rating.NOT_APPLICABLE: scale.not_applicable,
    }
    words.update({Rating(band.rating): band.label for band in scale.bands})
    return words[rating]


def blocks_of(sentences: Sequence[Sentence], voice: Voice) -> tuple[Block, ...]:
    """A row's sentences split into one block per register, in :data:`REGISTER_ORDER`.

    Chosen from ``Sentence.register`` alone. Reading order inside a block is the renderer's.
    """
    blocks = []
    for register in REGISTER_ORDER:
        members = tuple(sentence for sentence in sentences if sentence.register == register)
        if members:
            label_register, piece = voice.label(f"report_block_{register}")
            blocks.append(
                Block(register=register, heading=Text(label_register, piece), sentences=members)
            )
    return tuple(blocks)


def _artifact_links(rendered: RenderedRow, voice: Voice) -> tuple[Link, ...]:
    """One link per distinct Canvas item something was located in, in first-seen order."""
    seen: dict[str, Link] = {}
    for sentence in rendered.sentences:
        if sentence.role not in LOCATED_ROLES or not sentence.link or sentence.link in seen:
            continue
        title = _place_title(sentence)
        seen[sentence.link] = Link(
            url=sentence.link, label=Text("A", verbatim(title)), exact=sentence.link_exact
        )
    return tuple(seen.values())


def _place_title(sentence: Sentence) -> str:
    """The artifact title the sentence names: its first verbatim span, which is the place."""
    return sentence.verbatim[0] if sentence.verbatim else sentence.link or ""


def _check(model: ReportModel, rules: Register) -> None:
    problems = [violation.line() for violation in model.violations(rules)]
    for where, text in model.texts():
        if not text.text.strip():
            problems.append(f"{where} is empty")
    for section in model.full:
        for detail in section.details:
            for block in detail.blocks:
                registers = {sentence.register for sentence in block.sentences}
                if registers != {block.register}:
                    problems.append(
                        f"{detail.code}: a block for register {block.register} holds "
                        f"{sorted(registers)}"
                    )
                problems.extend(
                    f"{detail.code}: {sentence.shape} locates something and has no Canvas link"
                    for sentence in block.sentences
                    if sentence.role in LOCATED_ROLES and not sentence.link
                )
    if problems:
        raise ReportError("the report breaks its contract:\n  " + "\n  ".join(problems))


__all__ = [
    "FULL_SECTIONS",
    "LOCATED_ROLES",
    "REGISTER_ORDER",
    "Block",
    "CompactLine",
    "Field",
    "Link",
    "NarrativeSlot",
    "PlanEntry",
    "ReportError",
    "ReportModel",
    "RowDetail",
    "Section",
    "Summary",
    "SummaryRow",
    "Text",
    "anchor_for",
    "blocks_of",
    "build_report",
]
