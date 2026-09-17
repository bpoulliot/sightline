"""The shared HTML scan — the parse every content detector reads.

Two of these are about traps rather than features. Course HTML is malformed in every interesting
way, and the failures that follow from that are silent: a parser that loses the second half of a
page reports fewer images, not an error.
"""

from __future__ import annotations

from sightline.detect.html_scan import excerpt, heading_level, normalize_space, scan, tag_counts


def test_an_unclosed_iframe_does_not_swallow_the_rest_of_the_page() -> None:
    """``HTMLParser`` treats ``iframe`` as CDATA by default, and an unclosed one eats everything
    after it. Acquisition hit this; the scanner inherits the fix, and this is the proof it did."""
    page = scan(
        '<p>Before</p><iframe src="https://media.example.edu/embed/one">'
        '<img src="/courses/4410/files/1/preview" alt="After">'
    )
    assert [element.tag for element in page.elements] == ["p", "iframe", "img"]
    assert page.of("img")[0].attr("alt") == "After"


def test_script_and_style_are_still_cdata() -> None:
    page = scan("<style>.a { color: red; }</style><script>if (1 < 2) {}</script><p>Text</p>")
    assert page.stylesheets == (".a { color: red; }",)
    assert [element.tag for element in page.elements] == ["style", "script", "p"]
    assert page.text == "Text"


def test_an_unmatched_end_tag_does_not_unwind_the_document() -> None:
    page = scan("<div><p>One</p></span><p>Two</p></div>")
    paragraphs = page.of("p")
    assert [element.text for element in paragraphs] == ["One", "Two"]
    assert all(element.ancestor_tags == ("div",) for element in paragraphs)


def test_an_element_carries_its_own_text_and_its_descendants_text() -> None:
    page = scan("<p>Before <strong>bold</strong> after</p>")
    paragraph = page.of("p")[0]
    assert paragraph.text == "Before bold after"
    assert paragraph.own_text == "Before after"
    assert page.children(paragraph)[0].text == "bold"


def test_an_element_quotes_the_markup_as_written_and_the_line_it_was_on() -> None:
    page = scan("<p>One</p>\n<p>Two</p>\n<img src='/x.png'   alt='A picture'>")
    image = page.of("img")[0]
    assert image.raw == "<img src='/x.png'   alt='A picture'>"
    assert image.line == 3


def test_a_missing_attribute_and_an_empty_one_are_different_facts() -> None:
    page = scan('<img src="/a.png"><img src="/b.png" alt="">')
    first, second = page.of("img")
    assert first.attr("alt") is None and not first.has("alt")
    assert second.attr("alt") == "" and second.has("alt")


def test_void_elements_do_not_nest_what_follows_them() -> None:
    page = scan("<div><br><p>After</p></div>")
    assert page.of("p")[0].ancestor_tags == ("div",)


def test_text_inside_an_anchor_is_kept_out_of_the_unlinked_runs() -> None:
    page = scan(
        "<p>Visit https://help.example.edu now, or "
        '<a href="/x">https://other.example.edu</a></p>'
    )
    joined = " ".join(run for _, run in page.unlinked_runs)
    assert "https://help.example.edu" in joined
    assert "other.example.edu" not in joined


def test_index_counts_within_a_tag_and_ordinal_across_the_document() -> None:
    page = scan("<p>a</p><img src='1'><p>b</p><img src='2'>")
    images = page.of("img")
    assert [element.index for element in images] == [1, 2]
    assert [element.ordinal for element in images] == [1, 3]


def test_helpers() -> None:
    assert heading_level("h3") == 3
    assert heading_level("p") is None
    assert normalize_space("  a\n  b  ") == "a b"
    assert excerpt("abcdef", 4) == "abc…"
    assert excerpt("abc", 4) == "abc"
    assert tag_counts(scan("<p>a</p><p>b</p>"), ["p", "img"]) == {"img": 0, "p": 2}


def test_an_empty_body_scans_to_nothing_rather_than_raising() -> None:
    page = scan("")
    assert page.elements == ()
    assert page.words == 0
