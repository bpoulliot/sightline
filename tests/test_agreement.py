"""Item 24: how often independent human reviewers choose the same rating, and the guard around it.

The headline test is the first one. These figures describe PEOPLE AGREEING WITH EACH OTHER; the one
harmful misreading is a sentence that attributes them, or any accuracy figure, to Sightline. The
``self_assessment`` rule is what refuses it, and a guard that has never fired is indistinguishable
from one that cannot — so it is fired on the forbidden constructions, planted deliberately, before
anything else here trusts it (item 21's discipline for the detector guard, item 15's for the lint).
Every planted phrase has a control beside it that must pass, so a rule that refused everything
would fail this file too.
"""

from __future__ import annotations

import dataclasses
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from sightline.rubric import shipped_packs
from sightline.rubric.effort import Margins, default_effort_model
from sightline.voice import PhraseBankError, Sentence, default_register
from sightline.voice.agreement import (
    AGREEMENT_FILENAME,
    REQUIRED_LIMITS,
    AgreementError,
    brief,
    default_agreement,
    disagreements,
    full,
    markdown,
    near_boundary_legend,
    parse_agreement,
)
from sightline.voice.bank import parse_bank
from sightline.voice.lint import REGISTERS
from sightline.workbook import build_workbook
from voice_fixtures import sample_document, sample_projection

ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = ROOT / "src" / "sightline" / "voice"
DATA = VOICE_DIR / AGREEMENT_FILENAME
README = ROOT / "README.md"
BEGIN = "<!-- agreement:begin"
END = "<!-- agreement:end -->"


def _raw() -> dict:
    return yaml.safe_load(DATA.read_text(encoding="utf-8"))


def _sentence(text: str, register: str = "A", *, figures: tuple[str, ...] = ()) -> Sentence:
    """``text`` as the renderer would hand it over, with each of ``figures`` inserted as a span."""
    spans = []
    for figure in figures:
        start = text.index(figure)
        spans.append((start, start + len(figure)))
    return Sentence(
        role="planted", shape="planted", register=register, text=text, spans=tuple(sorted(spans))
    )


def _rules(sentence: Sentence) -> set[str]:
    return {violation.rule for violation in sentence.lint(default_register())}


# -- the guard, fired first -----------------------------------------------------------------------

#: The constructions the item names as fabrications, each worded as a surface might plant it. The
#: figures are inserted as spans, the way the renderer inserts every figure — the case that matters,
#: because a masked figure is exactly what the aggregate rule cannot see.
PLANTED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Sightline agrees with human reviewers 91% of the time.", ("91%",)),
    ("Sightline agrees with reviewers 91% of the time.", ("91%",)),
    ("accuracy: 82%", ("82%",)),
    ("confidence: high", ()),
    ("reliability score", ()),
    # Sightline and a figure in one claim, with none of the vocabulary: the construction alone.
    ("Sightline's proposed ratings matched the reviewer on 91% of the criteria.", ("91%",)),
    ("This review chose the reviewer's rating on 78% of criteria.", ("78%",)),
    # The near-boundary marker smuggled in as self-confidence through a friendly affordance.
    ("Minor Revision (unsure)", ()),
    ("Near a band threshold: 80% confident", ("80%",)),
)

#: The same facts worded correctly. Each must pass, or the rule refuses everything.
CONTROLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Human reviewers rating the same course independently chose the same rating on 72% to "
        "91% of the criteria both rated.",
        ("72%", "91%"),
    ),
    ("That is a measure of how hard the task is, not of how often Sightline is right.", ()),
    ("Sightline examined 8 artifacts with 1 check and found none of the conditions checked.", ()),
)


@pytest.mark.parametrize(("text", "figures"), PLANTED)
@pytest.mark.parametrize("register", REGISTERS)
def test_the_guard_fires_on_every_planted_attribution(text, figures, register) -> None:
    assert "self_assessment" in _rules(_sentence(text, register, figures=figures)), text


@pytest.mark.parametrize(("text", "figures"), CONTROLS)
def test_the_guard_stays_quiet_on_the_same_facts_worded_correctly(text, figures) -> None:
    assert _rules(_sentence(text, figures=figures)) == set(), text


def test_a_figure_beside_sightline_is_refused_even_as_a_span() -> None:
    """The construction reads the whole sentence. Masking the figure does not excuse it, and
    without the name the same masked figure passes — so it is the pairing that is refused."""
    named = _sentence("Sightline chose the same rating on 91% of them.", figures=("91%",))
    assert [v.token for v in named.lint()] == ["a figure beside \\bsightline\\b"]
    unnamed = _sentence("Two reviewers chose the same rating on 91% of them.", figures=("91%",))
    assert unnamed.lint() == []


def test_the_vocabulary_is_read_in_sightlines_words_only() -> None:
    """A syllabus quoted saying "confidence" is quoted, not written; the same word written by
    Sightline is refused."""
    quote = "This course builds confidence in statistical reasoning."
    text = f"GSOV 3: the syllabus reads “{quote}”"
    assert _sentence(text, "B", figures=(quote,)).lint() == []
    assert _rules(_sentence(text, "B")) == {"self_assessment"}


def test_a_planted_attribution_in_the_real_bank_is_refused_at_load() -> None:
    shipped = yaml.safe_load((VOICE_DIR / "phrases.yaml").read_text(encoding="utf-8"))
    parse_bank(shipped)  # the control: the bank as shipped loads

    attributed = yaml.safe_load((VOICE_DIR / "phrases.yaml").read_text(encoding="utf-8"))
    attributed["formats"]["agreement_scope"]["text"] += " Sightline is accurate."
    with pytest.raises(PhraseBankError) as caught:
        parse_bank(attributed)
    assert any("self_assessment rule" in problem for problem in caught.value.problems)

    confident = yaml.safe_load((VOICE_DIR / "phrases.yaml").read_text(encoding="utf-8"))
    confident["A"]["clean"][0] += " Confidence: high."
    with pytest.raises(PhraseBankError) as caught:
        parse_bank(confident)
    assert any("'confidence'" in problem for problem in caught.value.problems)


# -- one source -----------------------------------------------------------------------------------


def test_the_figures_carry_their_sample_scope_provenance_and_limits() -> None:
    data = default_agreement()
    assert data.courses > 0 and data.rated_instances > 0
    assert data.measured and data.instrument and data.scope and data.source
    assert data.pairs and all(pair.rated_by_both > 0 for pair in data.pairs)
    assert set(data.limits) == set(REQUIRED_LIMITS)
    assert data.disagreements.total == data.disagreements.one_band


@pytest.mark.parametrize("limit", REQUIRED_LIMITS)
def test_a_data_file_that_drops_a_limit_is_refused(limit) -> None:
    raw = _raw()
    raw["limits"].remove(limit)
    with pytest.raises(AgreementError, match=limit):
        parse_agreement(raw)


@pytest.mark.parametrize(
    ("where", "key"),
    [(None, "rating_distribution"), ("corpus", "minor_ratings"), ("pairs", "ratings")],
)
def test_a_figure_nobody_decided_to_publish_cannot_be_added(where, key) -> None:
    """Only the agreement figures are published. A new key is refused, not rendered or ignored."""
    raw = _raw()
    target = raw if where is None else raw[where][0] if where == "pairs" else raw[where]
    target[key] = 1
    with pytest.raises(AgreementError, match="not published"):
        parse_agreement(raw)


def test_the_loader_refuses_inconsistent_figures() -> None:
    raw = _raw()
    raw["pairs"][0]["identical_percent"] = 101
    with pytest.raises(AgreementError, match="over 100"):
        parse_agreement(raw)
    raw = _raw()
    raw["disagreements"]["one_band"] -= 1
    with pytest.raises(AgreementError, match="must equal total"):
        parse_agreement(raw)


def test_every_surface_renders_from_the_data_and_nothing_else() -> None:
    """Change a figure in the data and every sentence carrying it changes with it."""
    data = default_agreement()
    shifted = dataclasses.replace(
        data,
        pairs=tuple(
            dataclasses.replace(pair, identical_percent=pair.identical_percent - 10)
            for pair in data.pairs
        ),
        courses=data.courses + 7,
    )
    before = markdown(data)
    after = markdown(shifted)
    for pair in data.pairs:
        assert f"{pair.identical_percent - 10}%" in after
    assert f"{data.courses + 7} courses" in after
    assert f"{data.courses + 7} courses" not in before
    assert brief(shifted)[0].text != brief(data)[0].text


def test_the_readme_block_is_the_rendering() -> None:
    text = README.read_text(encoding="utf-8")
    start = text.index("-->\n", text.index(BEGIN)) + len("-->\n")
    block = text[start : text.index(END)]
    assert block.strip("\n") + "\n" == markdown(), (
        "README's agreement block differs from the data. Regenerate it from "
        "sightline.voice.agreement.markdown() rather than editing it by hand."
    )


def _typed_figures() -> list[re.Pattern[str]]:
    data = default_agreement()
    patterns = [re.compile(rf"(?<![\d.]){p}\s*%") for p in data.percentages()]
    patterns.append(re.compile(rf"(?<![\d.]){data.low}\s*[–-]\s*{data.high}\s*%"))
    return patterns


def _hits(text: str) -> list[str]:
    return [m.group(0) for pattern in _typed_figures() for m in pattern.finditer(text)]


#: Text files a surface is built from. Tests are not scanned: they plant the figures on purpose.
_SCANNED = {".py", ".yaml", ".yml", ".json", ".html", ".j2", ".css", ".js", ".md", ".txt"}


def test_no_surface_hardcodes_an_agreement_figure() -> None:
    # The scanner finds a figure where one is typed — the positive control. (The data file stores
    # bare integers, so it is the one file the scan skips by name rather than by luck.)
    assert _hits(markdown()), "the scanner does not recognise a rendered figure"
    assert _hits("independent passes agreed 72–91% of the time")

    offenders: list[str] = []
    for path in sorted((ROOT / "src" / "sightline").rglob("*")):
        if path.is_file() and path.suffix in _SCANNED and path != DATA:
            offenders.extend(f"{path.relative_to(ROOT)}: {hit}" for hit in _hits(path.read_text()))
    for path in (ROOT / "docs" / "SPEC.md", ROOT / "docs" / "RUBRIC-PACKS.md"):
        offenders.extend(f"{path.relative_to(ROOT)}: {hit}" for hit in _hits(path.read_text()))
    for path in sorted((ROOT / "examples").rglob("*")):
        if path.is_file() and path.suffix in _SCANNED:
            offenders.extend(f"{path.relative_to(ROOT)}: {hit}" for hit in _hits(path.read_text()))
    readme = README.read_text(encoding="utf-8")
    outside = readme[: readme.index(BEGIN)] + readme[readme.index(END) :]
    offenders.extend(f"README.md: {hit}" for hit in _hits(outside))
    assert offenders == [], offenders


# -- the words ------------------------------------------------------------------------------------


def test_every_agreement_sentence_passes_the_register_lint() -> None:
    sentences = (*full(), near_boundary_legend())
    assert [v.line() for s in sentences for v in s.lint(default_register())] == []


def test_the_short_form_states_the_limits_in_the_same_breath() -> None:
    first, second = (sentence.text for sentence in brief())
    data = default_agreement()
    assert f"{data.low}%" in first and f"{data.high}%" in first
    assert f"{len(data.pairs)} pairings" in first and "one band" in first
    assert f"{data.courses} courses is a small sample" in second
    assert f"{data.major_ratings} Major Revision ratings among {data.rated_instances}" in second
    assert "not of how often Sightline is right" in second
    # The figures and Sightline's name never share a sentence.
    assert "Sightline" not in first and "%" not in second


def test_the_long_form_states_every_pair_and_every_limit() -> None:
    text = markdown()
    data = default_agreement()
    for pair in data.pairs:
        assert f"{pair.passes}: {pair.rated_by_both} criteria rated by both" in text
        assert f"{pair.identical_percent}%" in text
    assert "not a survey" in text
    assert "cannot characterise the Major Revision boundary" in text
    assert "not one statistic" in text
    assert "not part of this repository" in text


def test_a_sentence_the_data_no_longer_supports_is_refused_rather_than_rendered() -> None:
    data = default_agreement()
    two_band = dataclasses.replace(
        data,
        disagreements=dataclasses.replace(
            data.disagreements,
            one_band=data.disagreements.one_band - 1,
            two_bands_or_more=1,
        ),
    )
    with pytest.raises(AgreementError, match="needs a person"):
        brief(two_band)
    crossed = dataclasses.replace(
        data,
        disagreements=dataclasses.replace(data.disagreements, major_vs_sufficiently_present=1),
    )
    with pytest.raises(AgreementError, match="needs a person"):
        disagreements(crossed)


def test_the_readme_section_passes_the_register_lint() -> None:
    """Every line added to the README about the figures, not only the rendered block."""
    text = README.read_text(encoding="utf-8")
    start = text.index("## How often two reviewers choose the same rating")
    section = text[start : text.index("\n## ", start + 1)]
    block = section[section.index(BEGIN) : section.index(END)]
    prose = section.replace(block, "")
    rules = default_register()
    assert rules.lint(prose, "A", where="README prose", sentence=False) == []
    # The control: read as unmasked prose, the block's figures do trip the aggregate rule, so the
    # read above is a real one. The block's sentences are linted with their spans elsewhere.
    assert "aggregate" in {v.rule for v in rules.lint(block, "A", where="block", sentence=False)}


# -- the near-boundary marker ---------------------------------------------------------------------


def test_the_legend_is_a_distance_from_a_threshold_not_a_confidence() -> None:
    legend = near_boundary_legend()
    model = default_effort_model()
    assert f"{model.margins.lowest:g} h" in legend.text
    assert f"{model.margins.between_bands:g} h" in legend.text
    assert "%" not in legend.text
    assert legend.lint() == []
    wider = dataclasses.replace(model, margins=Margins(lowest=0.5, between_bands=0.2))
    assert "0.5 h" in near_boundary_legend(wider).text


# -- not in the workbook --------------------------------------------------------------------------


def test_the_workbook_carries_no_agreement_statement() -> None:
    document = sample_document(ROOT)
    projection, report = sample_projection(document, "uccs-oscqr-3-1-3")
    model = build_workbook(
        projection,
        shipped_packs()[projection.pack_id],
        base_url=document.base_url,
        created=datetime(2026, 9, 1, tzinfo=UTC),
        report=report,
        course_id=document.course.id,
    )
    cells = "\n".join(cell.text for _, cell in model.cells())
    assert cells  # the control: there is a workbook to read
    for sentence in full():
        assert sentence.text not in cells
    assert _hits(cells) == []
    assert "workbook deliberately does not" in (ROOT / "src" / "sightline" / "workbook.py").read_text()
