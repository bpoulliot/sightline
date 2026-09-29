"""Item 15: the voice contract, the phrase bank, and the closing narrative.

The headline test is the first one. The register lint is the item's real deliverable, and a lint
that has never failed is indistinguishable from a lint that cannot — so it is fired on planted
phrases before anything else here trusts it, the discipline item 21 applies to the detector guard.
"""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import re
from pathlib import Path

import pytest
import yaml

from sightline.detect import DetectionReport, Tier
from sightline.detect.base import ResultState
from sightline.extract.base import ExtractorResult
from sightline.extract.runner import RowExtraction
from sightline.model import CourseDocument
from sightline.rubric import Coverage, Projection, Rating, project, shipped_packs
from sightline.rubric.effort import EffortModel
from sightline.voice import (
    PhraseBankError,
    RenderedReview,
    Sentence,
    default_bank,
    default_register,
    lint_bank,
    render_review,
    render_row,
)
from sightline.voice.__main__ import main, plain_text
from sightline.voice.bank import SHAPES, choose, parse_bank
from sightline.voice.lint import mask
from sightline.voice.narrative import SLOTS
from voice_fixtures import STAND_IN, sample_document, sample_projection, stand_in_extractions

VOICE_DIR = Path(__file__).resolve().parents[1] / "src" / "sightline" / "voice"
PLANTED = Path(__file__).resolve().parent / "fixtures" / "voice" / "planted.yaml"
REFERENCE = "uccs-oscqr-3-1-3"
PACKS = tuple(sorted(shipped_packs()))


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


@pytest.fixture(scope="module")
def rendered(reference, extractions, document: CourseDocument) -> RenderedReview:
    projection, report = reference
    return render_review(
        projection,
        report=report,
        extractions=extractions,
        base_url=document.base_url,
        course_id=document.course.id,
    )


def every_sample_rendering(document: CourseDocument, extractions) -> list[RenderedReview]:
    """Both shipped packs, with located evidence and without — every sentence the sample yields."""
    out = []
    for pack_id in PACKS:
        projection, report = sample_projection(document, pack_id)
        out.append(render_review(projection, report=report, course_id=document.course.id))
        if pack_id == REFERENCE:
            out.append(
                render_review(
                    projection,
                    report=report,
                    extractions=extractions,
                    base_url=document.base_url,
                    course_id=document.course.id,
                )
            )
    return out


# -- THE HEADLINE TEST: the lint fires -------------------------------------------------------------


def test_the_lint_fires_on_a_planted_hedged_tier_a_phrase_and_a_verdict_tier_c_phrase() -> None:
    planted = yaml.safe_load(PLANTED.read_text(encoding="utf-8"))
    violations = lint_bank(planted, origin="planted")
    by_shape: dict[str, set[str]] = {}
    for violation in violations:
        shape = violation.where.split()[1].split("[")[0]
        by_shape.setdefault(shape, set()).add(violation.rule)

    # Fires, for the right rule, on each planted phrase.
    assert by_shape["A.planted_hedge"] == {"hedge"}
    assert by_shape["C.planted_verdict"] == {"verdict"}
    assert by_shape["C.planted_no_handoff"] == {"handoff"}
    assert by_shape["B.planted_grading"] == {"grading"}
    assert by_shape["D.planted_aggregate"] == {"aggregate"}
    assert by_shape["D.planted_percentage"] == {"aggregate"}
    # And stays quiet on the same facts worded correctly — the other direction.
    assert not any(shape.endswith(".control") for shape in by_shape), by_shape
    tokens = {v.token for v in violations if v.where.startswith("planted C.planted_verdict")}
    assert {"insufficient", "should be"} <= tokens


def test_a_planted_phrase_in_the_real_bank_is_refused_at_load() -> None:
    """Not only a lint the suite runs: the loader refuses a bank that breaks the register."""
    shipped = yaml.safe_load((VOICE_DIR / "phrases.yaml").read_text(encoding="utf-8"))
    parse_bank(shipped)  # the control: the bank as shipped loads

    hedged = yaml.safe_load((VOICE_DIR / "phrases.yaml").read_text(encoding="utf-8"))
    hedged["A"]["clean"][0] = hedged["A"]["clean"][0].replace(
        " examined {", " appear to have examined {", 1
    )
    with pytest.raises(PhraseBankError) as caught:
        parse_bank(hedged)
    assert any(
        "hedge rule" in problem and "'appear'" in problem for problem in caught.value.problems
    )

    verdict = yaml.safe_load((VOICE_DIR / "phrases.yaml").read_text(encoding="utf-8"))
    verdict["C"]["question"][0] += " The coverage is inadequate."
    with pytest.raises(PhraseBankError) as caught:
        parse_bank(verdict)
    assert any("verdict rule" in problem for problem in caught.value.problems)


def test_the_lint_reads_exactly_the_words_sightline_wrote() -> None:
    """Masking is by position. A quoted "Grades" passes; the same word in Sightline's own words
    fails; and a span over one occurrence never hides another."""
    rules = default_register()
    quote = "Grades are posted in the Canvas gradebook."
    text = f"GSOV 10: the syllabus reads “{quote}”"
    start = text.index(quote)
    quoted = Sentence(
        role="quote", shape="B.quote", register="B", text=text, spans=((start, start + len(quote)),)
    )
    assert quoted.lint(rules) == []

    unmasked = dataclasses.replace(quoted, spans=())
    assert [v.rule for v in unmasked.lint(rules)] == ["grading"]

    twice = f"{quote} Grades"
    elsewhere = Sentence(
        role="quote", shape="B.quote", register="B", text=twice, spans=((0, len(quote)),)
    )
    assert [v.rule for v in elsewhere.lint(rules)] == ["grading"]
    assert mask("abc def", ((4, 7),)) == "abc  … "


# -- the shipped bank ------------------------------------------------------------------------------


def test_every_string_in_the_shipped_bank_passes_the_register_lint() -> None:
    shipped = yaml.safe_load((VOICE_DIR / "phrases.yaml").read_text(encoding="utf-8"))
    assert lint_bank(shipped, origin="phrases.yaml") == []
    bank = default_bank()
    assert set(bank.shapes) == set(SHAPES)
    for shape, variants in bank.shapes.items():
        assert len(variants) >= 3, shape


def test_a_variant_that_drops_a_count_is_refused() -> None:
    """Variety is in the wording, never in which facts are stated."""
    payload = yaml.safe_load((VOICE_DIR / "phrases.yaml").read_text(encoding="utf-8"))
    payload["A"]["clean"][1] = "{code}: {checks} found none of the conditions checked."
    with pytest.raises(PhraseBankError) as caught:
        parse_bank(payload)
    assert any("missing: examined" in problem for problem in caught.value.problems)

    payload = yaml.safe_load((VOICE_DIR / "phrases.yaml").read_text(encoding="utf-8"))
    del payload["D"]["not_yet_implemented"]
    with pytest.raises(PhraseBankError) as caught:
        parse_bank(payload)
    assert any("D.not_yet_implemented is missing" in p for p in caught.value.problems)


# -- every rendered sentence -----------------------------------------------------------------------


def test_every_sentence_rendered_from_the_sample_course_passes_the_register_lint(
    document, extractions
) -> None:
    rules = default_register()
    checked = 0
    for review in every_sample_rendering(document, extractions):
        for sentence in review.sentences():
            assert sentence.lint(rules, where=f"{review.pack_id} {sentence.shape}") == []
            checked += 1
    assert checked > 500  # the sample yields hundreds; a handful would mean rendering broke


def test_no_grading_vocabulary_and_no_aggregate_score_anywhere_in_the_rendered_sample(
    document, extractions
) -> None:
    """A grep over the whole output, with only verbatim spans — course text, titles, measured
    values, instrument labels — masked. Sightline's own words carry no grade and no total."""
    rules = default_register()
    words = " \n".join(
        mask(sentence.text, sentence.spans)
        for review in every_sample_rendering(document, extractions)
        for sentence in review.sentences()
    )
    for token, pattern in (*rules.grading.patterns, *rules.aggregate.patterns):
        assert not pattern.search(words), token
    for literal in ("score", "grade", "points", "quality", "evaluation", "%", "overall"):
        assert literal not in words.lower(), literal


def test_rendering_the_sample_twice_is_byte_identical(document, extractions) -> None:
    first = [plain_text(review) for review in every_sample_rendering(document, extractions)]
    second = [plain_text(review) for review in every_sample_rendering(document, extractions)]
    assert first == second
    once = every_sample_rendering(document, extractions)[-1].to_dict()
    again = every_sample_rendering(document, extractions)[-1].to_dict()
    assert once == again


def test_a_subset_renders_the_rows_it_shares_with_the_full_run_identically(
    reference, extractions, document
) -> None:
    """The global-counter trap. Rendering fewer rows, or a report with fewer detectors in it, must
    not reword a row that did not change."""
    projection, report = reference
    full = render_review(projection, report=report, extractions=extractions)

    halved = dataclasses.replace(projection, rows=projection.rows[1::2])
    subset = render_review(halved, report=report, extractions=extractions)
    for row in subset.rows:
        assert row == full.row(row.code)

    # A different detection run: drop the accessibility cluster, re-project, and every row whose
    # own inputs did not change reads exactly as before.
    kept = tuple(r for r in report.results if not ("D21" <= r.detector <= "D30"))
    smaller = DetectionReport(results=kept)
    reprojected = project(shipped_packs()[REFERENCE], smaller)
    partial = render_review(reprojected, report=smaller, extractions=extractions)
    unchanged = 0
    for row in partial.rows:
        before = projection.row(row.code)
        after = reprojected.row(row.code)
        if before == after:
            assert row == full.row(row.code)
            unchanged += 1
    assert 20 < unchanged < len(projection.rows)


def test_rotation_is_keyed_and_actually_varies(rendered) -> None:
    assert choose(4, "A.finding", "ACD 8\x1fx") == choose(4, "A.finding", "ACD 8\x1fx")
    finding_variants = {
        s.text.split(" ")[0] for s in rendered.sentences() if s.shape == "A.finding_source"
    }
    assert len(finding_variants) > 1, "every finding line used one variant: rotation is inert"


def test_the_voice_package_has_no_source_of_randomness_or_time() -> None:
    """Random seeding is the global counter wearing a hat, and the clock is worse."""
    banned = {"random", "time", "datetime", "secrets", "uuid", "itertools"}
    for path in VOICE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module.split(".")[0]]
            assert not banned & set(names), f"{path.name} imports {banned & set(names)}"


# -- what the sentences say ------------------------------------------------------------------------


def test_a_clean_tier_a_row_renders_what_it_examined(rendered) -> None:
    row = rendered.row("GSOV 2")
    assert row is not None and row.rating is Rating.SUFFICIENTLY_PRESENT
    headline = row.by_role("headline")[0]
    assert headline.shape == "A.clean"
    assert "8 artifacts" in headline.text and "examined" in headline.text
    assert "no issues" not in headline.text.lower()
    check = row.by_role("check")[0]
    assert "8 artifacts" in check.text and "1 item of interest" in check.text


def test_the_three_coverage_states_render_as_three_distinct_sentences(rendered) -> None:
    pending = rendered.row("OREP 1")
    unbound = rendered.row("COAL 3")
    clean = rendered.row("GSOV 2")
    shapes = (
        pending.sentences[0].shape,
        unbound.sentences[0].shape,
        clean.by_role("headline")[0].shape,
    )
    assert shapes == ("D.not_yet_implemented", "D.no_deterministic_coverage", "A.clean")

    bank = default_bank()
    clean_variants = set(bank.shapes["A.clean"])
    rules = default_register()
    for shape in ("D.not_yet_implemented", "D.no_deterministic_coverage", "D.unresolved"):
        assert not clean_variants & set(bank.shapes[shape])
        for variant in bank.shapes[shape]:
            words = mask(variant, [m.span() for m in re.finditer(r"\{[a-z_]+\}", variant)])
            assert not any(p.search(words) for _, p in rules.clean), variant
    for variant in clean_variants:
        assert "found no" in variant


def test_neither_unexamined_state_can_produce_clean_wording(rendered, reference) -> None:
    projection, _ = reference
    label = projection.rating_scale.sufficiently_present
    for rendered_row in rendered.rows:
        source = projection.row(rendered_row.code)
        if source.coverage is Coverage.DETERMINISTIC:
            continue
        for sentence in rendered_row.sentences:
            assert sentence.shape not in ("A.clean", "A.reading_clean"), rendered_row.code
            assert label not in sentence.text, rendered_row.code

    # And a hand-built row claiming to be clean without having been examined is refused outright.
    row = projection.row("OREP 1")
    forged = dataclasses.replace(row, rating=Rating.SUFFICIENTLY_PRESENT, label=label)
    with pytest.raises(ValueError, match="only a Tier A row"):
        render_row(forged)
    unbound = dataclasses.replace(
        projection.row("COAL 3"), tier=Tier.A, rating=Rating.SUFFICIENTLY_PRESENT, label=label
    )
    with pytest.raises(ValueError):
        render_row(unbound)


def test_a_tier_c_sentence_presents_a_distribution_and_hands_the_call_over(rendered) -> None:
    question = rendered.row("ENIN 6").by_role("question")[0]
    assert question.shape == "C.question"
    assert "3 quotes" in question.text
    assert "1 quote from 1 assignment" in question.text
    assert "2 quotes from 2 discussions" in question.text
    assert any(p.search(question.text) for _, p in default_register().handoff.patterns)


def test_no_tier_c_sentence_carries_a_rating(rendered, reference) -> None:
    projection, _ = reference
    scale = projection.rating_scale
    labels = {
        scale.sufficiently_present,
        scale.not_applicable,
        scale.no_response,
        *(band.label for band in scale.bands),
    }
    tier_c = [row for row in rendered.rows if row.tier is Tier.C]
    assert len(tier_c) >= 8
    for row in tier_c:
        assert not row.by_role("reading"), row.code
        for sentence in row.sentences:
            assert not any(label in sentence.text for label in labels), sentence.text


def test_quotes_are_rendered_verbatim_with_their_location_and_a_link(rendered, document) -> None:
    row = rendered.row("GSOV 10")
    quotes = row.by_role("quote")
    assert [q.verbatim[-1] for q in quotes] == list(STAND_IN["GSOV 10"])
    for quote in quotes:
        assert "Syllabus" in quote.text and "line" in quote.text
        assert quote.link == f"{document.base_url}/courses/4410/assignments/syllabus"
    evidence = row.by_role("evidence")[0]
    assert "15 passages" in evidence.text and "2 quotes" in evidence.text


def test_the_extraction_outcomes_read_differently(reference) -> None:
    """Not gathered, searched-and-nothing, and could-not-be-read are three different facts."""
    projection, _ = reference
    row = projection.row("GSOV 3")

    def result(**overrides) -> RowExtraction:
        base = {
            "extractor": "ROW GSOV 3",
            "title": "t",
            "tier": Tier.B,
            "state": ResultState.EXAMINED,
            "examined": 15,
            "passages": 15,
            "answered": 15,
        }
        base.update(overrides)
        return RowExtraction(
            criterion="GSOV 3",
            section="GSOV",
            tier=Tier.B,
            reason="r",
            result=ExtractorResult(**base),
        )

    shapes = {
        "not run": render_row(row).by_role("evidence")[0].shape,
        "none": render_row(row, extraction=result()).by_role("evidence")[0].shape,
        "unread": render_row(row, extraction=result(answered=12)).by_role("evidence")[0].shape,
        "no text": render_row(row, extraction=result(passages=0, answered=0))
        .by_role("evidence")[0]
        .shape,
        "nothing": render_row(
            row, extraction=result(state=ResultState.NOT_EXAMINED, examined=0, passages=0)
        )
        .by_role("evidence")[0]
        .shape,
    }
    assert len(set(shapes.values())) == len(shapes), shapes
    unread = render_row(row, extraction=result(answered=12)).by_role("evidence")[0]
    assert "3 of 15 passages" in unread.text


def test_a_band_says_its_hours_come_from_a_placeholder(rendered, reference) -> None:
    readings = [s for row in rendered.rows for s in row.by_role("reading") if "h of" in s.text]
    assert readings
    assert all("placeholder" in s.text for s in readings)

    projection, report = reference
    fitted = dataclasses.replace(projection, effort=EffortModel(calibrated=True))
    calibrated = render_review(fitted, report=report)
    texts = [s.text for row in calibrated.rows for s in row.by_role("reading") if "h of" in s.text]
    assert texts and not any("placeholder" in t for t in texts)


def test_criterion_codes_are_never_renumbered(rendered, reference) -> None:
    projection, _ = reference
    assert [row.code for row in rendered.rows] == [row.code for row in projection.rows]
    asfe = [row.code for row in rendered.rows if row.section == "ASFE"]
    assert asfe == ["ASFE 1", "ASFE 2", "ASFE 3", "ASFE 4", "ASFE 5", "ASFE 7"]
    for row in rendered.rows:
        for sentence in row.sentences:
            assert "ASFE 6" not in sentence.text


def test_a_detector_that_crashed_is_named_and_never_reads_clean(reference) -> None:
    projection, report = reference
    crashed = tuple(
        (
            dataclasses.replace(
                r,
                state=ResultState.NOT_EXAMINED,
                examined=0,
                subjects=0,
                findings=(),
                undetermined=(),
                crashed="RuntimeError",
                not_examined_because="it raised",
            )
            if r.detector == "D13"
            else r
        )
        for r in report.results
    )
    broken = DetectionReport(results=crashed)
    reprojected = project(shipped_packs()[REFERENCE], broken)
    review = render_review(reprojected, report=broken)
    row = review.row("GSOV 2")
    assert row.rating is Rating.NO_RESPONSE
    assert row.by_role("headline")[0].shape == "A.not_examined"
    assert row.by_role("check")[0].shape == "A.check_crashed"
    limits = review.narrative.slot("limits")
    assert limits.sentences[0].shape == "D.narrative_crashed"
    assert "D13" in limits.sentences[0].text


# -- the closing narrative -------------------------------------------------------------------------


def test_the_narrative_has_four_slots_including_what_could_not_be_determined(rendered) -> None:
    narrative = rendered.narrative
    assert tuple(slot.key for slot in narrative.slots) == SLOTS
    assert all(slot.sentences for slot in narrative.slots)
    assert [slot.heading.text for slot in narrative.slots][-1] == (
        "What Sightline could not determine"
    )
    limits = {sentence.shape for sentence in narrative.slot("limits").sentences}
    assert {
        "D.narrative_not_yet_implemented",
        "D.narrative_unbound",
        "D.narrative_judgement",
        "D.narrative_out_of_scope",
    } <= limits
    strong = narrative.slot("strong").sentences[0]
    assert "GSOV 2" in strong.text and "8 artifacts examined" in strong.text


def test_the_limits_slot_is_never_empty(reference) -> None:
    projection, report = reference
    only_clean = dataclasses.replace(projection, rows=(projection.row("GSOV 2"),))
    review = render_review(only_clean, report=report)
    limits = review.narrative.slot("limits").sentences
    assert [s.shape for s in limits] == ["D.narrative_limits_none"]
    assert "course artifacts" in limits[0].text


def test_what_to_do_first_sorts_with_a_null_priority_and_drops_nothing(reference) -> None:
    projection, report = reference
    mobile = [row for row in projection.rows if row.section == "MOB"]
    assert all(row.priority is None for row in mobile)

    only_mobile = dataclasses.replace(projection, rows=tuple(mobile))
    first = render_review(only_mobile, report=report).narrative.slot("first").sentences[0]
    assert "MOB 1" in first.text and "no priority flag on the instrument" in first.text

    # Mixed: every labelled row sorts ahead of the unflagged ones, and none of them is lost.
    mixed = dataclasses.replace(
        projection,
        rows=tuple(r for r in projection.rows if r.section in ("MOB", "ACD")),
    )
    ordered = [r.code for r in mixed.action_plan_order() if r.rating.is_band]
    assert ordered[0].startswith("ACD") and ordered[-1].startswith("MOB")
    text = render_review(mixed, report=report).narrative.slot("first").sentences[0].text
    assert "MOB" not in text  # five ACD rows come first
    work = render_review(mixed, report=report).narrative.slot("work").sentences[0].text
    assert all(code in work for code in ordered)


def test_needs_work_lists_codes_and_never_a_total(rendered) -> None:
    work = rendered.narrative.slot("work").sentences[0].text
    assert "33 rows" in work and "Minor Revision: COAL 1" in work
    assert " h " not in work and "hours" not in work


# -- the demo --------------------------------------------------------------------------------------


def test_the_demo_prints_the_sample_course_the_same_way_twice(capsys) -> None:
    root = Path(__file__).resolve().parents[1]
    argv = ["--bundle", str(root / "examples" / "sample-course"), "--pack", REFERENCE]
    assert main(argv) == 0
    first = capsys.readouterr().out
    assert main(argv) == 0
    assert capsys.readouterr().out == first
    assert "What Sightline could not determine" in first
    assert "was not gathered in this run" in first or "gathered no" in first
    assert "canvas.example.edu" not in first  # links only on request


def test_the_demo_requires_a_pack(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["--bundle", "examples/sample-course"])
