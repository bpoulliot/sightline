"""Item 14: the PDF course report — read back from the review file, printed through the gate.

The course is the synthetic sample, with located evidence for a handful of rows from the suite's
scripted stand-in (``tests/voice_fixtures.py``), serialised exactly as the batch runner saves it and
read back through :mod:`sightline.report.loader`. Nothing here calls a model or Canvas.

The PDF is read back with ``tests/pdf_reader.py``, which is checked first, in both directions,
against a document whose text is known.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

from pdf_reader import PdfReadError, read_pdf
from sightline.batch import (
    BatchRunner,
    CourseWork,
    ExportGate,
    Stage,
    StageOutcome,
    create_batch_run,
    pipeline_factory,
)
from sightline.batch.pipeline import BundleSource, ReviewPipeline
from sightline.detect import Tier
from sightline.paths import DataPaths
from sightline.report import (
    REPORT_FILENAME,
    ReportError,
    ReviewFileError,
    build_report,
    load_review,
    parse_review,
    to_html,
    to_pdf,
)
from sightline.report.__main__ import main as report_main
from sightline.report.model import FULL_SECTIONS, LOCATED_ROLES, Block, anchor_for, blocks_of
from sightline.rubric import Coverage, Rating, shipped_packs
from sightline.store import RunStore
from sightline.voice import Voice, agreement, default_register, render_review
from sightline.voice.lint import mask
from sightline.voice.render import Sentence
from sightline.workbook import WORKBOOK_FILENAME
from voice_fixtures import sample_document, sample_projection, stand_in_extractions

REFERENCE = "uccs-oscqr-3-1-3"
OPENED = datetime(2026, 9, 1, 12, 30, tzinfo=UTC)
REPO = Path(__file__).resolve().parents[1]


# -- fixtures -------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def live() -> dict[str, Any]:
    document = sample_document(REPO)
    projection, report = sample_projection(document, REFERENCE)
    extractions = asyncio.run(stand_in_extractions(projection, document))
    return {
        "document": document,
        "projection": projection,
        "report": report,
        "extractions": extractions,
    }


def review_payload(live: Mapping[str, Any]) -> dict[str, Any]:
    """What the batch runner saves for this course: the real pipeline's own review shape."""
    work = CourseWork(
        reference="examples/sample-course",
        course_id="run-store-course",
        ordinal=0,
        document=live["document"],
        detection=live["report"],
        projection=live["projection"],
        rows=tuple(live["extractions"].values()),
    )
    pipeline = ReviewPipeline(source=BundleSource(), pack=shipped_packs()[REFERENCE])
    return pipeline.review(work)


def saved(payload: Mapping[str, Any]) -> str:
    """Serialised exactly as ``BatchRunner._save_review`` does."""
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


@pytest.fixture(scope="module")
def review_text(live) -> str:
    return saved(review_payload(live))


@pytest.fixture(scope="module")
def loaded(review_text):
    return parse_review(review_text)


@pytest.fixture(scope="module")
def model(loaded):
    return build_report(loaded, created=OPENED)


@pytest.fixture(scope="module")
def pdf(model) -> bytes:
    return to_pdf(model)


@pytest.fixture(scope="module")
def pages(pdf):
    return read_pdf(pdf)


@pytest.fixture(scope="module")
def pdf_text(pages) -> str:
    return "\n".join(page.text for page in pages)


# -- the reader is checked before it is trusted --------------------------------------------------


def _known_pdf(body: str, **options: Any) -> bytes:
    from weasyprint import CSS, HTML
    from weasyprint.text.fonts import FontConfiguration

    from sightline.assets import resolve_fonts_dir
    from sightline.report.pdf import font_faces

    fonts = resolve_fonts_dir()
    config = FontConfiguration()
    sheet = CSS(string=font_faces(fonts), base_url=fonts.as_uri() + "/", font_config=config)
    style = CSS(string='body { font-family: "IBM Plex Sans"; }', font_config=config)
    html = f'<html lang="en"><body>{body}</body></html>'
    return HTML(string=html).write_pdf(stylesheets=[sheet, style], font_config=config, **options)


def test_the_pdf_reader_recovers_known_text_and_links_and_nothing_else() -> None:
    data = _known_pdf(
        '<p>Alpha “quoted” 4.5:1 café</p><p><a href="https://canvas.example.edu/courses/1/'
        'pages/x">Linked words</a></p><p style="break-before: page">Second page</p>'
    )
    pages = read_pdf(data)
    assert [page.text for page in pages] == [
        "Alpha “quoted” 4.5:1 café\nLinked words",
        "Second page",
    ]
    assert pages[0].links == ["https://canvas.example.edu/courses/1/pages/x"]
    assert pages[1].links == []
    # ...and does not find what is not there.
    assert "Omega" not in pages[0].text


def test_the_pdf_reader_refuses_what_it_cannot_read() -> None:
    with pytest.raises(PdfReadError):
        read_pdf(b"not a pdf at all")
    # A font whose ToUnicode map has lost its entries: the reader raises rather than dropping text.
    data = _known_pdf("<p>Alpha</p>", uncompressed_pdf=True)
    gutted = re.sub(rb"beginbfchar.*?endbfchar", b"beginbfchar\nendbfchar", data, flags=re.S)
    assert gutted != data, "the self-test found no ToUnicode map to remove"
    assert read_pdf(data)[0].text == "Alpha"
    with pytest.raises(PdfReadError, match="ToUnicode"):
        read_pdf(gutted)


# -- the review file reads back as the live run ---------------------------------------------------


def test_render_review_over_the_loaded_file_equals_the_live_run(live, loaded) -> None:
    document = live["document"]
    rendered_live = render_review(
        live["projection"],
        report=live["report"],
        extractions=live["extractions"],
        base_url=document.base_url,
        course_id=document.course.id,
    )
    rendered_file = loaded.render()
    assert [s.to_dict() for s in rendered_file.sentences()] == [
        s.to_dict() for s in rendered_live.sentences()
    ]
    assert rendered_file.to_dict() == rendered_live.to_dict()
    assert len(rendered_live.sentences()) > 100


def test_every_persisted_shape_round_trips(live, loaded) -> None:
    assert loaded.projection.to_dict() == json.loads(saved(live["projection"].to_dict()))
    assert loaded.report.to_dict() == json.loads(saved(live["report"].to_dict()))
    for code, extraction in live["extractions"].items():
        assert loaded.extractions[code].to_dict() == json.loads(saved(extraction.to_dict()))
    assert loaded.projection.priority_order == shipped_packs()[REFERENCE].priority_order
    assert loaded.projection.rating_scale == shipped_packs()[REFERENCE].rating_scale
    assert [row.code for row in loaded.projection.action_plan_order()] == [
        row.code for row in live["projection"].action_plan_order()
    ]
    assert loaded.base_url == "https://canvas.example.edu"


@pytest.mark.parametrize("key", ["course", "pack", "detection", "projection", "extraction"])
def test_a_review_file_missing_a_key_is_refused_by_name(review_text, key) -> None:
    payload = json.loads(review_text)
    del payload[key]
    with pytest.raises(ReviewFileError, match=repr(key)):
        load_review(payload)


def test_a_review_made_against_another_pack_version_is_refused(review_text) -> None:
    payload = json.loads(review_text)
    payload["pack"]["version"] = "0.0.1"
    with pytest.raises(ReviewFileError, match="version"):
        load_review(payload)


def test_a_projection_missing_a_field_the_renderer_needs_is_refused(review_text) -> None:
    payload = json.loads(review_text)
    del payload["projection"]["effort_calibrated"]
    with pytest.raises(ReviewFileError, match="effort_calibrated"):
        load_review(payload)


# -- the report's structure -----------------------------------------------------------------------


def test_the_sections_come_in_the_decided_order(model, pdf_text) -> None:
    labels = model.labels
    order = [
        labels["report_about_heading"].text,
        labels["report_summary_heading"].text,
        labels["report_plan_heading"].text,
        *(section.title.text for section in model.full),
        labels["report_compact_heading"].text,
        labels["report_narrative_heading"].text,
    ]
    flat = _flat(pdf_text)
    positions = [flat.find(_flat(heading)) for heading in order]
    assert all(position >= 0 for position in positions), list(zip(order, positions, strict=True))
    assert positions == sorted(positions)
    assert [section.code for section in model.full] == list(FULL_SECTIONS)


def test_accessibility_and_interaction_are_in_full_and_the_rest_compact(model, loaded) -> None:
    full = {detail.code for section in model.full for detail in section.details}
    compact = {line.code for section in model.compact for line in section.lines}
    rows = {row.code: row for row in loaded.projection.rows}
    assert full == {code for code, row in rows.items() if row.section in FULL_SECTIONS}
    assert compact == set(rows) - full
    # In full means every sentence the voice layer gives the row.
    rendered = {row.code: row for row in loaded.render().rows}
    for section in model.full:
        for detail in section.details:
            printed = [s for block in detail.blocks for s in block.sentences]
            assert sorted(s.to_dict()["text"] for s in printed) == sorted(
                s.text for s in rendered[detail.code].sentences
            )


def test_no_block_holds_both_tier_a_and_tier_c_lines(model) -> None:
    blocks = [block for s in model.full for d in s.details for block in d.blocks]
    assert {block.register for block in blocks} >= {"A", "B", "C", "D"}
    for block in blocks:
        assert {sentence.register for sentence in block.sentences} == {block.register}


def test_the_printed_blocks_keep_the_registers_apart(pdf, model) -> None:
    html = to_html(model)
    blocks = _html_blocks(html)
    assert len(blocks) >= sum(len(d.blocks) for s in model.full for d in s.details)
    for register, lines in blocks:
        assert lines, "a block with no lines"
        assert set(lines) == {register}, (register, lines)
        assert not ({"A", "C"} <= set(lines))


def test_a_mixed_block_is_refused_rather_than_printed(model) -> None:
    """The contract check fires: a hand-built block mixing a count with a question is refused."""
    voice = Voice()
    count = Sentence(role="check", shape="A.check", register="A", text="x examined 3 artifacts.")
    question = Sentence(
        role="question", shape="C.question", register="C", text="Whether it fits is your call."
    )
    # blocks_of never mixes them...
    assert [b.register for b in blocks_of([count, question], voice)] == ["A", "C"]
    # ...and a model that did is refused at build time.
    section = model.full[0]
    detail = section.details[0]
    bad = Block(register="A", heading=detail.blocks[0].heading, sentences=(count, question))
    broken = copy.copy(model)
    object.__setattr__(
        broken,
        "full",
        (
            type(section)(
                code=section.code,
                anchor=section.anchor,
                title=section.title,
                full=True,
                details=(type(detail)(**{**_fields(detail), "blocks": (bad,)}),),
            ),
        ),
    )
    from sightline.report.model import _check

    with pytest.raises(ReportError, match="register A holds"):
        _check(broken, default_register())


def test_every_rendered_finding_carries_an_item_level_canvas_link(model, pages) -> None:
    located = [
        sentence
        for section in model.full
        for detail in section.details
        for block in detail.blocks
        for sentence in block.sentences
        if sentence.role in LOCATED_ROLES
    ]
    assert len(located) > 30, "the sample renders too few findings to prove anything"
    printed = {link for page in pages for link in page.links}
    for sentence in located:
        assert sentence.link, sentence.text
        # The artifact's own page — the course home for a finding about the course itself.
        assert re.match(r"^https://canvas\.example\.edu/courses/4410(/\S+)?$", sentence.link)
        # Item-level: the page or item, never the editor.
        assert not sentence.link.endswith("/edit")
        assert sentence.link in printed, sentence.link
    for section in model.compact:
        for line in section.lines:
            for link in line.links:
                assert link.url in printed
    # Every URI in the file is a Canvas item under the configured base URL.
    assert printed and all(link.startswith("https://canvas.example.edu/") for link in printed)


def test_the_three_coverage_states_read_differently_in_a_compact_line(model, loaded) -> None:
    lines = {line.code: line for section in model.compact for line in section.lines}
    rows = {row.code: row for row in loaded.projection.rows}

    def first(predicate) -> str:
        return next(code for code, row in rows.items() if code in lines and predicate(row))

    picked = {
        "clean": first(lambda r: r.rating is Rating.SUFFICIENTLY_PRESENT),
        "not yet implemented": first(lambda r: r.coverage is Coverage.NOT_YET_IMPLEMENTED),
        "no check assigned": first(
            lambda r: r.coverage is Coverage.NO_DETERMINISTIC_COVERAGE and r.tier is Tier.B
        ),
        "tier B at No Response": first(
            lambda r: r.tier is Tier.B
            and r.rating is Rating.NO_RESPONSE
            and r.coverage is Coverage.DETERMINISTIC
        ),
        "tier C": first(lambda r: r.tier is Tier.C),
    }
    readings = {
        name: (lines[code].reading.text, lines[code].coverage.text, lines[code].located.text)
        for name, code in picked.items()
    }
    assert len(set(readings.values())) == len(readings), readings
    clean_label = loaded.pack.rating_scale.sufficiently_present
    for name in ("not yet implemented", "no check assigned", "tier B at No Response", "tier C"):
        assert clean_label not in " ".join(readings[name]), name
    assert readings["clean"][0] == clean_label
    assert "no check ran" in readings["not yet implemented"][2]
    assert readings["tier C"][0] != loaded.pack.rating_scale.no_response


def test_mobile_rows_carry_no_priority_and_sort_last(model, loaded) -> None:
    plan = [entry.code for entry in model.plan]
    mobile = [code for code in plan if code.startswith("MOB")]
    assert mobile and plan[-len(mobile) :] == mobile
    no_flag = Voice().fragment("no_priority").text
    assert all(e.priority.text == no_flag for e in model.plan if e.code.startswith("MOB"))
    order = [row.code for row in loaded.projection.action_plan_order()]
    assert plan == [code for code in order if code in plan]


def test_codes_are_the_instruments_own_and_never_renumbered(model, loaded) -> None:
    printed = [line.code for s in model.compact for line in s.lines] + [
        d.code for s in model.full for d in s.details
    ]
    assert sorted(printed) == sorted(row.code for row in loaded.projection.rows)
    assert "ASFE 7" in printed and "ASFE 6" not in printed


# -- the words ------------------------------------------------------------------------------------


def test_every_piece_of_the_report_passes_the_register_lint(model) -> None:
    assert model.violations() == []


def test_the_pdf_prints_no_word_the_model_did_not_supply(model, pdf_text) -> None:
    """The template writes nothing of its own: every word on the page is a linted piece."""
    supplied = " ".join(
        [text.text for _, text in model.texts()]
        + [sentence.text for _, sentence in model.sentences()]
        + [link for _, link in model.links()]
        + [line.code for s in model.compact for line in s.lines]
        + [d.code for s in model.full for d in s.details]
        + [str(n) for n in range(0, 200)]
        + ["A", "B", "C", "D"]
        + [count for row in model.summary.rows for count in row.counts]
    )
    # Case-insensitively: the stylesheet sets the block headings in capitals.
    known = {word.lower() for word in _words(supplied)}
    # A line broken after a hyphen reads back as two lines; rejoin it.
    printed = re.sub(r"-\n", "-", pdf_text)
    stray = sorted({word.lower() for word in _words(printed)} - known)
    assert stray == [], stray


def _sentences_of(flat: str, spans: tuple[tuple[int, int], ...]):
    """``flat`` cut into sentences, each with the spans that fall inside it, shifted to it.

    ``self_assessment`` refuses a figure in a *sentence* that names Sightline. Read as one blob, the
    human-agreement figures in the *what this document is* block would sit "beside" every mention
    of Sightline in the document; read sentence by sentence, each is held to what it says.
    """
    start = 0
    for end in [m.start() for m in re.finditer(r"(?<=[.!?])\s+", flat)] + [len(flat)]:
        inside = tuple(
            (max(a, start) - start, min(b, end) - start) for a, b in spans if a < end and b > start
        )
        yield flat[start:end], inside
        start = end + 1


def test_item_15s_lint_passes_over_the_pdfs_extracted_text(model, pdf_text) -> None:
    """Read in the register every tier shares — grading, aggregate, self-assessment — with the
    verbatim spans (course text, criterion text, instrument words) masked, as the lint does.
    Sentence by sentence, as a sentence is linted (see :func:`_sentences_of`)."""
    flat = _flat(pdf_text)
    spans: list[tuple[int, int]] = []
    missing: list[str] = []
    for value in sorted(_verbatim(model), key=len, reverse=True):
        needle = _flat(value)
        if len(needle) < 2:
            continue
        found = [(m.start(), m.end()) for m in re.finditer(_wrapped(needle), flat)]
        if not found:
            missing.append(value)
        spans.extend(found)
    assert missing == [], f"verbatim values not found in the PDF text: {missing[:5]}"
    merged = _merge(spans)
    rules = default_register()
    violations = [
        violation
        for text, inside in _sentences_of(flat, merged)
        for violation in rules.lint(text, "B", where="the PDF's text", sentence=False, spans=inside)
    ]
    assert violations == [], [v.line() for v in violations]
    assert "Human reviewers" in flat and "Sightline" in flat, "the reading below needs both"
    # The same reading fires on a planted aggregate, and on a human-agreement figure claimed for
    # Sightline (item 24), so the pass above is not a reading of nothing.
    for plant, expected in (
        (" The overall score is 80%.", {"grading", "aggregate"}),
        (" Sightline agrees with reviewers 91% of the time.", {"self_assessment"}),
    ):
        planted = [
            violation
            for text, inside in _sentences_of(flat + plant, merged)
            for violation in rules.lint(text, "B", where="planted", sentence=False, spans=inside)
        ]
        assert {violation.rule for violation in planted} >= expected, plant
    # The grep the acceptance asks for, over Sightline's own words.
    own = mask(flat, merged)
    assert not re.search(r"\d\s*%|\bper ?cent", own)
    assert not re.search(r"\b(total|overall|score|grade|grading|average)\b", own, re.I)
    assert not re.search(r"\b\d+\s+(?:of|out of)\s+\d+\b", own)


def test_no_run_count_is_printed(pdf_text) -> None:
    assert not re.search(r"\b\d+ (?:of \d+ )?runs?\b", pdf_text)


# -- item 24: how often human reviewers agree, and the near-boundary marker ------------------------


class _About(HTMLParser):
    """The paragraphs of the *what this document is* block, in order, with their classes."""

    def __init__(self) -> None:
        super().__init__()
        self.inside = False
        self.paragraph: list[str] | None = None
        self.paragraphs: list[tuple[str, str]] = []
        self.cls = ""

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        if tag == "section" and "about" in (attributes.get("class") or "").split():
            self.inside = True
        elif self.inside and tag == "p":
            self.paragraph, self.cls = [], attributes.get("class") or ""

    def handle_endtag(self, tag) -> None:
        if tag == "section":
            self.inside = False
        elif tag == "p" and self.paragraph is not None:
            self.paragraphs.append((self.cls, " ".join("".join(self.paragraph).split())))
            self.paragraph = None

    def handle_data(self, data) -> None:
        if self.paragraph is not None:
            self.paragraph.append(data)


def _about(model) -> list[tuple[str, str]]:
    parser = _About()
    parser.feed(to_html(model))
    return parser.paragraphs


def test_the_about_block_states_how_often_human_reviewers_agree(model, loaded, pdf_text) -> None:
    """``brief()`` after the about paragraph, then the marker's one legend — and, in a report that
    carries a reviewer's rating, both come before the paragraph about it, so neither is read as
    describing the rating a reviewer recorded."""
    brief = " ".join(sentence.text for sentence in agreement.brief())
    legend = agreement.near_boundary_legend(loaded.projection.effort).text
    assert model.agreement == agreement.brief()
    assert _about(model) == [("", model.about.text), ("agreement", brief), ("near-legend", legend)]
    flat = _flat(pdf_text)
    assert brief in flat and legend in flat
    assert flat.count("Human reviewers rating the same course") == 1, "said once, not per row"


def test_the_agreement_statement_comes_before_the_reviewers_rating_paragraph(loaded) -> None:
    some_row = next(row.code for row in loaded.projection.rows if row.tier is not Tier.C)
    reviewed = build_report(loaded, created=OPENED, overrides={some_row: Rating.MAJOR})
    classes = [cls for cls, _ in _about(reviewed)]
    assert classes == ["", "agreement", "near-legend", ""]
    assert _about(reviewed)[-1][1] == reviewed.labels["report_reviewer_intro"].text


def test_every_marked_row_carries_12as_marker_on_the_proposals_effort(model, loaded) -> None:
    rows = {row.code: row for row in loaded.projection.rows}
    marked = {code for code, row in rows.items() if row.near_boundary is not None}
    assert marked and len(marked) < len(rows), "the fixture needs marked and unmarked rows"
    details = {d.code: d for s in model.full for d in s.details}
    lines = {line.code: line for s in model.compact for line in s.lines}
    assert set(details) | set(lines) == set(rows)
    for code, detail in details.items():
        names = [field.name.text for field in detail.fields]
        if code in marked:
            assert names[names.index("Estimated effort") + 1] == "Near a band threshold"
            value = dict(zip(names, (f.value.text for f in detail.fields), strict=True))
            assert value["Near a band threshold"].startswith(f"{rows[code].hours:g} h is ")
        else:
            assert "Near a band threshold" not in names
    for code, line in lines.items():
        if code in marked:
            assert line.near is not None
            assert line.near.text.startswith(f"Near a band threshold: {rows[code].hours:g} h is ")
        else:
            assert line.near is None
    for text in [line.near.text for line in lines.values() if line.near] + [
        f.value.text for d in details.values() for f in d.fields if f.name.text.startswith("Near")
    ]:
        assert not re.search(r"%|★|☆|\b(?:unsure|likely|confiden\w*|probab\w*)\b", text)


def test_the_compact_marker_sits_with_the_proposal_not_the_reviewers_rating(loaded) -> None:
    code = next(
        row.code
        for row in loaded.projection.rows
        if row.near_boundary is not None and row.section not in FULL_SECTIONS
    )
    reviewed = build_report(loaded, created=OPENED, overrides={code: Rating.MAJOR})
    html = to_html(reviewed)
    line = html[html.index(f'id="{anchor_for(code)}"') :]
    line = line[: line.index("</div>")]
    reading, near, reviewer = (
        line.index("Proposed: "),
        line.index("Near a band threshold: "),
        line.index("Reviewer&#39;s rating: "),
    )
    assert reading < near < reviewer


def test_the_marker_is_rendered_from_the_review_file_never_recomputed(review_text) -> None:
    payload = json.loads(review_text)
    rows = payload["projection"]["rows"]
    (target,) = [row for row in rows if row["code"] == "ACD 1"]
    assert target["near_boundary"], "the fixture's ACD 1 carries a marker"
    target["near_boundary"]["distance_hours"] = 0.07
    model = build_report(parse_review(saved(payload)), created=OPENED)
    (detail,) = [d for s in model.full for d in s.details if d.code == "ACD 1"]
    (field,) = [f for f in detail.fields if f.name.text == "Near a band threshold"]
    assert " is 0.07 h from the " in field.value.text


def test_a_report_with_no_marker_prints_no_legend_and_no_marker(review_text) -> None:
    payload = json.loads(review_text)
    for row in payload["projection"]["rows"]:
        row["near_boundary"] = None
    model = build_report(parse_review(saved(payload)), created=OPENED)
    assert model.legend is None
    assert [cls for cls, _ in _about(model)] == ["", "agreement"]
    text = " ".join(page.text for page in read_pdf(to_pdf(model)))
    assert "near a band threshold" not in text.lower()
    assert "Human reviewers rating the same course" in _flat(text)


# -- bytes ----------------------------------------------------------------------------------------


def test_the_pdf_is_byte_identical_on_a_rerun(model, pdf) -> None:
    assert to_pdf(model) == pdf


def test_the_clock_and_source_date_epoch_do_not_reach_the_bytes(model, pdf, monkeypatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1")
    assert to_pdf(model) == pdf
    assert os.environ["SOURCE_DATE_EPOCH"] == "1", "the render must put the variable back"
    monkeypatch.delenv("SOURCE_DATE_EPOCH")
    assert to_pdf(model) == pdf
    assert "SOURCE_DATE_EPOCH" not in os.environ


def test_the_metadata_carries_the_opened_at_moment_and_nothing_personal(pdf) -> None:
    import zlib

    text = pdf.decode("latin-1")
    streams = []
    for match in re.finditer(rb"stream\r?\n", pdf):
        try:
            streams.append(zlib.decompressobj().decompress(pdf[match.end() :]).decode("latin-1"))
        except zlib.error:
            continue
    everything = text + "".join(streams)
    assert "/CreationDate (D:20260901123000" in everything
    assert "/ModDate (D:20260901123000" in everything
    assert "/Author" not in everything
    # No file identifier in the trailer. (Tagged-PDF structure elements carry an /ID of their own.)
    assert not re.search(r"/ID\s*\[", everything)


def test_a_different_opening_changes_only_the_dates(loaded, pdf) -> None:
    later = to_pdf(build_report(loaded, created=datetime(2026, 9, 2, 8, 0, tzinfo=UTC)))
    assert later != pdf
    before = [page.text for page in read_pdf(pdf)]
    after = [page.text for page in read_pdf(later)]
    changed = [
        (old, new)
        for a, b in zip(before, after, strict=True)
        for old, new in zip(a.splitlines(), b.splitlines(), strict=True)
        if old != new
    ]
    assert changed == [("Review opened 2026-09-01 12:30 UTC", "Review opened 2026-09-02 08:00 UTC")]


def test_a_report_without_a_base_url_is_refused(review_text) -> None:
    payload = json.loads(review_text)
    payload["course"]["base_url"] = None
    with pytest.raises(ReportError, match="base URL"):
        build_report(load_review(payload), created=OPENED)


# -- the export command and the gate --------------------------------------------------------------


class SavedReviewPipeline:
    """Five stages that succeed, and a review that is the sample course's real one."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        self.payload = payload

    async def run(self, stage: Stage, work: CourseWork) -> StageOutcome:
        return work.record(
            StageOutcome(stage=stage, examined=1, statement=f"{stage.value}: examined 1.")
        )

    def review(self, work: CourseWork) -> Mapping[str, Any]:
        return self.payload


@pytest.fixture
def finished(live, paths: DataPaths) -> Iterator[tuple[RunStore, str]]:
    store = RunStore.open(paths)
    run_id = create_batch_run(store, ["examples/sample-course"], rubric_pack=REFERENCE)
    pipeline = SavedReviewPipeline(review_payload(live))
    asyncio.run(BatchRunner(store, paths, pipeline_factory(pipeline)).execute(run_id))
    run = store.get_run(run_id)
    assert run is not None
    yield store, run.courses[0].id
    store.dispose()


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.name.startswith("sightline.sqlite")
    }


def test_the_export_command_is_refused_for_an_unopened_course(
    finished, paths, monkeypatch, capsys
) -> None:
    _store, course_id = finished
    monkeypatch.setenv("SIGHTLINE_DATA_DIR", str(paths.root))
    before = _files(paths.root)
    assert report_main(["export", course_id]) == 1
    err = capsys.readouterr().err
    assert "Nobody has opened the review" in err
    assert "does not generate a report nobody has read" in err
    assert _files(paths.root) == before
    assert list(paths.resolve("reports").rglob("*")) == []


def test_the_export_writes_only_through_the_gate(finished, paths, monkeypatch, capsys) -> None:
    store, course_id = finished
    monkeypatch.setenv("SIGHTLINE_DATA_DIR", str(paths.root))
    opened = ExportGate(store, paths).open_review(course_id)
    before = _files(paths.root)

    assert report_main(["export", course_id]) == 0
    out = [line for line in capsys.readouterr().out.splitlines() if line.startswith("reports/")]
    after = _files(paths.root)
    added = sorted(set(after) - set(before))
    assert added == sorted(
        [f"reports/{course_id}/{REPORT_FILENAME}", f"reports/{course_id}/{WORKBOOK_FILENAME}"]
    )
    assert {name: after[name] for name in before} == before, "the export changed another file"
    assert sorted(out) == added
    # The same bytes a direct render gives, dated when the review was opened.
    text = paths.root / f"runs/{store.get_course(course_id).run_id}/courses/{course_id}/"
    loaded = parse_review((text / "review.json").read_text(encoding="utf-8"))
    assert after[added[0]] == to_pdf(build_report(loaded, created=opened))
    # And a second export writes the same bytes again.
    assert report_main(["export", course_id]) == 0
    assert _files(paths.root)[added[0]] == after[added[0]]


def test_the_export_command_names_a_course_it_does_not_know(paths, monkeypatch, capsys) -> None:
    monkeypatch.setenv("SIGHTLINE_DATA_DIR", str(paths.root))
    assert report_main(["export", "no-such-course"]) == 2
    assert "No course" in capsys.readouterr().err


# -- helpers --------------------------------------------------------------------------------------


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _wrapped(needle: str) -> str:
    """A pattern for ``needle`` that also matches it broken across lines mid-word.

    The stylesheet lets a long unbroken value — a URL, a line of markup — wrap anywhere, and a
    line break read back is a space.
    """
    parts: list[str] = []
    for index, char in enumerate(needle):
        if char == " ":
            parts.append(r"\s")
            continue
        parts.append(re.escape(char))
        if index + 1 < len(needle) and needle[index + 1] != " ":
            parts.append(r"\s?")
    return "".join(parts)


def _merge(spans: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Overlapping spans as one ordered, disjoint set — the shape the lint takes."""
    out: list[list[int]] = []
    for start, end in sorted(spans):
        if out and start <= out[-1][1]:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return tuple((start, end) for start, end in out)


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z'’-]*", text)


def _verbatim(model) -> set[str]:
    values: set[str] = set()
    for _, text in model.texts():
        values.update(text.text[a:b] for a, b in text.piece.spans)
    for _, sentence in model.sentences():
        values.update(sentence.verbatim)
    return {value for value in values if value.strip()}


def _fields(detail) -> dict[str, Any]:
    return {name: getattr(detail, name) for name in detail.__slots__}


class _Blocks(HTMLParser):
    """Each ``section.block`` and the registers of the lines inside it."""

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[tuple[str, list[str]]] = []
        self.depth = 0
        self.open: tuple[str, list[str], int] | None = None

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        if tag == "section":
            self.depth += 1
            if "block" in (attributes.get("class") or "").split():
                self.open = (attributes["data-register"] or "", [], self.depth)
        if tag == "p" and self.open and "line" in (attributes.get("class") or "").split():
            self.open[1].append(attributes["data-register"] or "")

    def handle_endtag(self, tag) -> None:
        if tag == "section":
            if self.open and self.open[2] == self.depth:
                self.blocks.append((self.open[0], self.open[1]))
                self.open = None
            self.depth -= 1


def _html_blocks(html: str) -> list[tuple[str, list[str]]]:
    parser = _Blocks()
    parser.feed(html)
    return parser.blocks
