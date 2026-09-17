"""Colour, contrast and type, read out of declared styles.

The arithmetic here is the whole of D25's credibility: a contrast ratio a reviewer cannot
reproduce is a number they have to take on trust, and this tool does not ask for trust. So the
known values are checked against the ones the WCAG formula produces for colours anyone can look up.
"""

from __future__ import annotations

import pytest

from sightline.detect.styles import (
    ASSUMED_BACKGROUND,
    MINIMUM_POINTS,
    StyleContext,
    Stylesheet,
    contrast_ratio,
    declarations,
    font_points,
    legacy_font_declarations,
    parse_colour,
    required_ratio,
    summarise_ratio,
    typeface,
)


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("#fff", "#ffffff"),
        ("#FFFFFF", "#ffffff"),
        ("white", "#ffffff"),
        ("  #Cc0000 ", "#cc0000"),
        ("rgb(204, 0, 0)", "#cc0000"),
        ("rgba(204, 0, 0, 0.5)", "#cc0000"),
        ("rgb(80%, 0%, 0%)", "#cc0000"),
    ],
)
def test_colours_this_module_recognises(written: str, expected: str) -> None:
    colour = parse_colour(written)
    assert colour is not None
    assert colour.hex == expected
    assert colour.written == written.strip()


@pytest.mark.parametrize(
    "written", ["", None, "transparent", "inherit", "var(--brand)", "lab(50% 20 30)", "#12345"]
)
def test_a_colour_this_module_does_not_recognise_is_none_rather_than_a_guess(
    written: str | None,
) -> None:
    assert parse_colour(written) is None


def test_contrast_against_the_published_values() -> None:
    black = parse_colour("#000000")
    white = parse_colour("#ffffff")
    assert black is not None and white is not None
    assert contrast_ratio(black, white) == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio(white, white) == pytest.approx(1.0, abs=0.001)

    grey = parse_colour("#767676")  # the darkest grey that still passes 4.5:1 on white
    assert grey is not None
    assert contrast_ratio(grey, white) == pytest.approx(4.54, abs=0.02)

    failing = parse_colour("#bbbbbb")
    assert failing is not None
    assert contrast_ratio(failing, white) == pytest.approx(1.92, abs=0.02)


def test_the_threshold_depends_on_how_large_the_text_is() -> None:
    assert required_ratio(None, bold=False) == 4.5
    assert required_ratio(11.0, bold=False) == 4.5
    assert required_ratio(18.0, bold=False) == 3.0
    assert required_ratio(14.0, bold=True) == 3.0
    assert required_ratio(14.0, bold=False) == 4.5


def test_summarise_ratio_reads_the_way_a_reviewer_writes_it() -> None:
    assert summarise_ratio(4.4999) == "4.50:1"


def test_declarations_survive_comments_and_missing_semicolons() -> None:
    assert declarations("color: red; /* a note */ font-size: 9pt") == {
        "color": "red",
        "font-size": "9pt",
    }
    assert declarations("COLOR:#FFF") == {"color": "#FFF"}
    assert declarations(None) == {}


@pytest.mark.parametrize(
    ("written", "points"),
    [("12pt", 12.0), ("16px", 12.0), ("1em", 12.0), ("100%", 12.0), ("9pt", 9.0), ("1in", 72.0)],
)
def test_lengths_resolve_to_points(written: str, points: float) -> None:
    assert font_points(written) == pytest.approx(points)


def test_a_length_in_a_unit_this_module_cannot_resolve_is_none() -> None:
    assert font_points("calc(1em + 2px)") is None
    assert font_points("2vw") is None
    assert font_points(None) is None


@pytest.mark.parametrize(
    ("family", "kind"),
    [
        ("Arial, sans-serif", "sans"),
        ("Georgia, serif", "serif"),
        ("'Times New Roman'", "serif"),
        ("Papyrus, fantasy", "decorative"),
        ("Comic Sans MS", "decorative"),
        ("Brandon Grotesque", "unrecognised"),
    ],
)
def test_typefaces_are_classified_by_the_first_face_recognised(family: str, kind: str) -> None:
    classified = typeface(family)
    assert classified is not None
    assert classified.kind == kind


def test_a_stylesheet_applies_flat_rules_and_counts_what_it_cannot() -> None:
    sheet = Stylesheet.parse(
        [
            ".note { color: #bbbbbb; } p { font-size: 9pt; }",
            "#lead { color: red }",
            "div p span { color: blue } .a:hover { color: green }",
        ]
    )
    assert sheet.matching("p", {}) == {"font-size": "9pt"}
    assert sheet.matching("span", {"class": "note"}) == {"color": "#bbbbbb"}
    assert sheet.matching("p", {"id": "lead"}) == {"color": "red", "font-size": "9pt"}
    assert sorted(sheet.unsupported) == [".a:hover", "div p span"]


def test_a_later_rule_wins_the_way_css_says_it_does() -> None:
    sheet = Stylesheet.parse(["p { color: red } p { color: blue }"])
    assert sheet.matching("p", {}) == {"color": "blue"}


def test_a_style_context_inherits_and_then_overrides() -> None:
    outer = StyleContext().merged_with({"color": "#cc0000", "font-size": "9pt"})
    inner = outer.merged_with({"color": "#000000"})
    assert outer.colour is not None and outer.colour.hex == "#cc0000"
    assert inner.colour is not None and inner.colour.hex == "#000000"
    assert inner.points == 9.0  # inherited


def test_a_style_context_keeps_what_it_could_not_resolve() -> None:
    context = StyleContext().merged_with({"color": "var(--brand)", "font-size": "2vw"})
    assert context.colour is None
    assert context.points is None
    assert context.unresolved == ("color: var(--brand)", "font-size: 2vw")


def test_bold_comes_from_the_tag_or_from_the_weight() -> None:
    assert StyleContext().merged_with({}, bold_tag=True).bold is True
    assert StyleContext().merged_with({"font-weight": "700"}).bold is True
    assert StyleContext().merged_with({"font-weight": "400"}, bold_tag=True).bold is False


def test_the_legacy_font_element_is_translated_rather_than_understood_everywhere() -> None:
    assert legacy_font_declarations({"face": "Papyrus", "size": "2", "color": "red"}) == {
        "font-family": "Papyrus",
        "font-size": "10pt",
        "color": "red",
    }
    assert legacy_font_declarations({"size": "99"}) == {}


def test_the_constants_say_what_the_rubric_asks_for() -> None:
    assert MINIMUM_POINTS == 12.0
    assert ASSUMED_BACKGROUND == "#ffffff"
