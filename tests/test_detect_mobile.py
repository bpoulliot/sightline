"""D39–D45, the mobile cluster, against clean and defective fixture courses.

Both directions for every detector, for the reason the accessibility and structure suites give: a
detector that fires on everything passes every defective case and is the one a faculty member
dismisses the tool over, and a detector that fires on nothing passes every clean case and is the
silent under-detection SPEC §11 is about.

Three things here are specific to this cluster.

**The boundary with D24 is asserted, not described.** ``NestedIndentLevels`` and ``TableSemantics``
declare the same capability tag on purpose, so a rubric row bound to it receives from both and
their effort sums. That makes any overlap a double charge rather than a duplicate line, and the
test that matters is the one running both over the same layout table and checking they do not name
the same element.

**The optional-section flag is exercised through the projection**, because that is where it does
its work. Item 04 built the flag and item 05 shipped the reference pack with its mobile block
flagged optional and switched on; what this item owes is the demonstration that the block *renders*
with real detector output behind it when the flag is on, and is reported as omitted when it is off.

**No course here is real.** Every fixture is assembled in code, every host is under
``example.edu``, and nothing traces to a course or a person.
"""

from __future__ import annotations

import pytest

from detector_fixtures import (
    COURSE_ID,
    assignment,
    codes,
    course,
    course_file,
    module,
    module_item,
    page,
    refused,
)
from sightline.detect.accessibility import TableSemantics
from sightline.detect.base import NothingExamined, ResultState, Severity, Tier
from sightline.detect.integrity import empty_document
from sightline.detect.mobile import (
    APP_FORMATS,
    INDENT_LIMIT,
    NARROW_VIEWPORT_PX,
    SUPERSEDED_FORMATS,
    AttachmentFormats,
    EmbedFallbackLink,
    FixedPixelWidths,
    LegacyEmbeds,
    NestedIndentLevels,
    RequiredAppAvailability,
    TextFloatedBesideImages,
)
from sightline.detect.runner import run_detectors
from sightline.rubric.project import project
from sightline.rubric.shipped import shipped_pack
from sightline.rubric.states import Coverage, Rating

COURSE_ARTIFACT = f"course:{COURSE_ID}"

#: The two instruments Sightline ships. One has a mobile block and one does not, which is what
#: makes the pair the right pair to project the same detector output onto.
REFERENCE_ID = "uccs-oscqr-3-1-3"
RSI_ID = "uccs-oscqr-rsi"


def reasons(result: object) -> list[str]:
    return [entry.reason for entry in getattr(result, "undetermined", ())]


# ---------------------------------------------------------------------------------------------
# D39 — an embed with a plain link beside it
# ---------------------------------------------------------------------------------------------


def test_d39_an_embed_with_a_link_to_the_same_video_beside_it_is_clean() -> None:
    document = course(
        [
            page(
                "lecture",
                '<p><iframe src="https://media.example.edu/embed/lecture-one"></iframe></p>'
                '<p><a href="https://media.example.edu/watch/lecture-one">Watch the lecture</a>'
                "</p>",
            )
        ]
    )
    result = EmbedFallbackLink().run(document)
    assert result.clean
    assert result.examined == 1
    assert result.subjects == 1


def test_d39_an_embed_with_no_link_anywhere_on_the_page_is_reported() -> None:
    document = course(
        [page("lecture", '<p><iframe src="https://media.example.edu/embed/lecture-two"></iframe>')]
    )
    result = EmbedFallbackLink().run(document)
    assert codes(result) == ["embed_without_an_accompanying_link"]
    finding = result.findings[0]
    assert finding.severity is Severity.FRICTION
    assert finding.evidence == "https://media.example.edu/embed/lecture-two"
    assert finding.location.artifact_id == "page:lecture"
    assert finding.location.within == "iframe embed"


def test_d39_a_link_to_a_different_resource_is_not_an_accompanying_link() -> None:
    """The failure a whole-URL comparison would hide: a page that links *something* on the same
    host reads as accompanied, and every embed on it goes quiet."""
    document = course(
        [
            page(
                "lecture",
                '<p><iframe src="https://media.example.edu/embed/lecture-three"></iframe></p>'
                '<p><a href="https://media.example.edu/embed/unrelated-recording">Other</a></p>',
            )
        ]
    )
    assert codes(EmbedFallbackLink().run(document)) == ["embed_without_an_accompanying_link"]


def test_d39_a_course_with_no_embeds_at_all_is_not_a_broken_detector() -> None:
    document = course([page("text-only", "<p>Nothing is embedded here.</p>")])
    result = EmbedFallbackLink().run(document)
    assert result.state is ResultState.NOT_EXAMINED
    assert result.clean is False
    assert "embeds" in (result.not_examined_because or "")


def test_d39_refuses_a_document_with_nothing_in_it() -> None:
    with pytest.raises(NothingExamined):
        EmbedFallbackLink().run(empty_document())


# ---------------------------------------------------------------------------------------------
# D40 — levels of indentation
# ---------------------------------------------------------------------------------------------


def test_d40_a_flat_module_and_an_ordinary_list_are_clean() -> None:
    document = course(
        [page("week-1", "<ul><li>Read<ul><li>chapter one</li></ul></li></ul>")],
        modules=[
            module(
                "3001",
                "Module 1",
                module_item("31001", "Overview", module_id="module:3001"),
                module_item("31002", "Reading", module_id="module:3001", position=2),
            )
        ],
    )
    result = NestedIndentLevels().run(document)
    assert result.clean
    assert result.subjects == 2 + 2 + 0  # two module items, two lists, no tables


def test_d40_a_module_item_indented_past_the_limit_is_reported_on_the_item() -> None:
    document = course(
        [page("week-1", "<p>Flat.</p>")],
        modules=[
            module(
                "3001",
                "Module 1",
                module_item("31001", "Overview", module_id="module:3001"),
                module_item("31002", "Buried", module_id="module:3001", position=2, indent=3),
            )
        ],
    )
    result = NestedIndentLevels().run(document)
    assert codes(result) == ["module_item_indented_past_the_limit"]
    finding = result.findings[0]
    assert finding.location.artifact_id == "module_item:31002"
    assert finding.context["indent"] == 3
    assert finding.context["limit"] == INDENT_LIMIT
    assert "3 level(s)" in finding.observed


def test_d40_an_item_indented_exactly_to_the_limit_is_left_alone() -> None:
    """The boundary, stated in the fixture rather than trusted. Canvas's indent field is what a
    designer sets by hand, and a detector firing one level early reports every sub-item in every
    course."""
    document = course(
        [page("week-1", "<p>Flat.</p>")],
        modules=[
            module(
                "3001",
                "Module 1",
                module_item("31001", "At the limit", module_id="module:3001", indent=INDENT_LIMIT),
            )
        ],
    )
    assert NestedIndentLevels().run(document).clean


def test_d40_reports_one_finding_per_over_deep_branch_not_one_per_element() -> None:
    """Six nested list elements are one mistake and one edit. Reporting each would multiply the
    estimated effort by however deep somebody nested it."""
    document = course(
        [
            page(
                "deep",
                "<ul><li>one<ul><li>two<ul><li>three<ul><li>four</li></ul></li></ul></li></ul>"
                "</li></ul>",
            )
        ],
        modules=[module("3001", "Module 1", module_item("31001", "Page", module_id="module:3001"))],
    )
    result = NestedIndentLevels().run(document)
    assert codes(result) == ["content_indented_past_the_limit"]
    assert result.findings[0].context["depth"] == INDENT_LIMIT + 1


def test_d40_reports_an_inner_table_and_d24_reports_the_outer_one() -> None:
    """The overlap that would be a double charge. Both detectors declare ``table_semantics``, so a
    row bound to it sums their effort — and the guarantee that makes that safe is that they never
    name the same element."""
    document = course(
        [
            page(
                "nested",
                '<table border="0"><tr><td>'
                "<table><tr><th>Week</th></tr><tr><td>1</td></tr></table>"
                "</td><td>Beside it</td></tr></table>",
            )
        ],
        modules=[module("3001", "Module 1", module_item("31001", "Page", module_id="module:3001"))],
    )
    mobile = NestedIndentLevels().run(document)
    tables = TableSemantics().run(document)

    assert "table_nested_inside_a_table" in codes(mobile)
    assert "table_positions_content" in codes(tables)
    inner = [
        finding for finding in mobile.findings if finding.code == "table_nested_inside_a_table"
    ]
    outer = [finding for finding in tables.findings if finding.code == "table_positions_content"]
    assert [finding.location.within for finding in inner] == ["table 2"]
    assert [finding.location.within for finding in outer] == ["table 1"]


def test_d40_refuses_a_document_with_nothing_in_it() -> None:
    with pytest.raises(NothingExamined):
        NestedIndentLevels().run(empty_document())


# ---------------------------------------------------------------------------------------------
# D41 — text floated beside an image
# ---------------------------------------------------------------------------------------------


def test_d41_an_image_on_its_own_line_is_clean() -> None:
    document = course(
        [page("figure", '<p><img src="/courses/4410/files/1/preview" alt="A diagram"></p>')]
    )
    result = TextFloatedBesideImages().run(document)
    assert result.clean
    assert result.subjects == 1


def test_d41_a_floated_image_with_nothing_beside_it_is_clean() -> None:
    """A float with no text to wrap does nothing on any screen. Reporting it would be reporting a
    declaration rather than an effect a reviewer can see."""
    document = course(
        [page("figure", '<p><img style="float: right" src="/f/1" alt="A diagram"></p>')]
    )
    assert TextFloatedBesideImages().run(document).clean


def test_d41_a_floated_image_with_text_in_the_same_block_is_reported() -> None:
    document = course(
        [
            page(
                "figure",
                '<p><img style="float: left; margin-right: 8px" src="/f/1" alt="A diagram">'
                "Read the attached document before the quiz.</p>",
            )
        ]
    )
    result = TextFloatedBesideImages().run(document)
    assert codes(result) == ["text_floated_beside_an_image"]
    finding = result.findings[0]
    assert finding.context["side"] == "left"
    assert finding.context["words_beside"] == 7
    assert finding.location.within == "image 1"
    assert 'style="float: left' in (finding.evidence or "")


def test_d41_reads_a_float_declared_in_a_style_block() -> None:
    document = course(
        [
            page(
                "figure",
                "<style>.wrap { float: right; }</style>"
                '<p><img class="wrap" src="/f/1" alt="A diagram">Text beside it.</p>',
            )
        ]
    )
    assert codes(TextFloatedBesideImages().run(document)) == ["text_floated_beside_an_image"]


def test_d41_reads_the_legacy_align_attribute() -> None:
    """Courses imported from an older system are full of ``align="left"``, which floats exactly as
    the CSS does. A detector that only read ``float`` would report those courses clean."""
    document = course(
        [page("figure", '<p><img align="left" src="/f/1" alt="A diagram">Text beside it.</p>')]
    )
    assert codes(TextFloatedBesideImages().run(document)) == ["text_floated_beside_an_image"]


def test_d41_reads_a_float_on_a_wrapper_that_holds_only_the_image() -> None:
    """The form every WYSIWYG editor writes: the float is on the container, not the picture."""
    document = course(
        [
            page(
                "figure",
                '<div><div style="float: left"><img src="/f/1" alt="A diagram"></div>'
                "Text that wraps around it.</div>",
            )
        ]
    )
    assert codes(TextFloatedBesideImages().run(document)) == ["text_floated_beside_an_image"]


def test_d41_refuses_a_document_with_nothing_in_it() -> None:
    with pytest.raises(NothingExamined):
        TextFloatedBesideImages().run(empty_document())


# ---------------------------------------------------------------------------------------------
# D42 — widths declared in pixels
# ---------------------------------------------------------------------------------------------


def test_d42_widths_declared_as_percentages_are_clean_and_still_counted() -> None:
    """Counted, which is the half a "found nothing" result cannot show on its own: the page
    declared three widths and all three were fine."""
    document = course(
        [
            page(
                "layout",
                '<div style="width: 100%"><table width="100%"><tr><td>x</td></tr></table>'
                '<img src="/f/1" alt="A diagram" style="width: 50%"></div>',
            )
        ]
    )
    result = FixedPixelWidths().run(document)
    assert result.clean
    assert result.subjects == 3


def test_d42_a_pixel_width_wider_than_a_phone_is_reported_as_friction() -> None:
    document = course([page("layout", '<table width="900"><tr><td>x</td></tr></table>')])
    result = FixedPixelWidths().run(document)
    assert codes(result) == ["width_fixed_in_pixels"]
    finding = result.findings[0]
    assert finding.severity is Severity.FRICTION
    assert finding.context["pixels"] == 900
    assert finding.context["viewport_px"] == NARROW_VIEWPORT_PX
    assert 'width="900"' in finding.observed


def test_d42_a_pixel_width_that_still_fits_is_reported_as_advisory() -> None:
    """The criterion asks for percentages either way, so it is reported either way — but a 200px
    image is not the thing that pushes a page off the side of a phone, and the severity says so."""
    document = course([page("layout", '<img src="/f/1" alt="A diagram" width="200">')])
    result = FixedPixelWidths().run(document)
    assert codes(result) == ["width_fixed_in_pixels"]
    assert result.findings[0].severity is Severity.ADVISORY


def test_d42_reads_css_units_other_than_pixels_and_ignores_the_ones_that_scale() -> None:
    document = course(
        [
            page(
                "layout",
                '<div style="width: 8in">Wide</div>'
                '<div style="width: 40em">Scales with the text</div>'
                '<div style="width: 90vw">Scales with the screen</div>',
            )
        ]
    )
    result = FixedPixelWidths().run(document)
    assert codes(result) == ["width_fixed_in_pixels"]
    assert result.findings[0].context["pixels"] == pytest.approx(768.0)


def test_d42_a_max_width_is_the_responsive_idiom_and_is_not_reported() -> None:
    """``max-width: 780px`` with a percentage width is the fix, not the defect. Reporting it would
    be reporting somebody for having done the thing the criterion asks for."""
    document = course([page("layout", '<div style="width: 100%; max-width: 780px">x</div>')])
    assert FixedPixelWidths().run(document).clean


def test_d42_one_element_declaring_two_fixed_widths_is_one_finding() -> None:
    """One element is one edit. Two findings would price the same fix twice."""
    document = course([page("layout", '<div style="width: 780px; min-width: 600px">x</div>')])
    result = FixedPixelWidths().run(document)
    assert codes(result) == ["width_fixed_in_pixels"]
    assert result.findings[0].context["pixels"] == 780


def test_d42_refuses_a_document_with_nothing_in_it() -> None:
    with pytest.raises(NothingExamined):
        FixedPixelWidths().run(empty_document())


# ---------------------------------------------------------------------------------------------
# D43 — embeds that need a plugin
# ---------------------------------------------------------------------------------------------


def test_d43_a_modern_embed_is_clean_and_the_count_says_what_was_opened() -> None:
    document = course(
        [
            page(
                "media",
                '<p><object data="/courses/4410/files/1/download" type="video/mp4"></object>',
            )
        ]
    )
    result = LegacyEmbeds().run(document)
    assert result.clean
    assert result.subjects == 1


def test_d43_a_flash_object_is_reported_as_a_barrier_with_the_type_quoted() -> None:
    document = course(
        [
            page(
                "media",
                '<object data="/courses/4410/files/1/download" '
                'type="application/x-shockwave-flash" width="640"></object>',
            )
        ]
    )
    result = LegacyEmbeds().run(document)
    assert codes(result) == ["embed_needs_a_browser_plugin"]
    finding = result.findings[0]
    assert finding.severity is Severity.BARRIER
    assert finding.context["technology"] == "Flash"
    assert finding.context["named_in"] == "type attribute"
    assert "x-shockwave-flash" in (finding.evidence or "")


def test_d43_reads_a_class_identifier_a_file_extension_and_a_movie_parameter() -> None:
    """Three forms an older editor writes, none of which declares a type. Reading only ``type``
    would report every one of them clean."""
    document = course(
        [
            page(
                "media",
                '<object classid="clsid:D27CDB6E-AE6D-11cf-96B8-444553540000">'
                '<param name="movie" value="/f/animation.swf"></object>'
                '<embed src="/f/other.swf"></embed>'
                '<applet code="Demo.class"></applet>',
            )
        ]
    )
    result = LegacyEmbeds().run(document)
    assert codes(result) == ["embed_needs_a_browser_plugin"] * 3
    assert sorted(finding.context["named_in"] for finding in result.findings) == [
        "class identifier",
        "file extension",
        "tag name",
    ]


def test_d43_a_player_specific_format_degrades_rather_than_closes_the_route() -> None:
    document = course([page("media", '<embed src="/courses/4410/files/1/lecture.wmv"></embed>')])
    result = LegacyEmbeds().run(document)
    assert result.findings[0].severity is Severity.FRICTION
    assert result.findings[0].context["technology"] == "Windows Media Player"


def test_d43_refuses_a_document_with_nothing_in_it() -> None:
    with pytest.raises(NothingExamined):
        LegacyEmbeds().run(empty_document())


# ---------------------------------------------------------------------------------------------
# D44 — what formats the course attaches
# ---------------------------------------------------------------------------------------------


def test_d44_a_course_attaching_pdfs_and_pictures_is_clean() -> None:
    document = course(
        [page("week-1", "<p>Read the notes.</p>")],
        files=[
            course_file("5001", "notes.pdf", content_type="application/pdf"),
            course_file("5002", "diagram.png", content_type="image/png"),
        ],
    )
    result = AttachmentFormats().run(document)
    assert result.clean
    assert result.subjects == 2


def test_d44_a_superseded_format_is_named_and_counted_against_the_census() -> None:
    document = course(
        [page("week-1", "<p>Read the notes.</p>")],
        files=[
            course_file("5001", "notes.pdf", content_type="application/pdf"),
            course_file("5002", "handout.doc", content_type="application/msword"),
        ],
    )
    result = AttachmentFormats().run(document)
    assert codes(result) == ["attachment_in_a_superseded_format"]
    finding = result.findings[0]
    assert finding.severity is Severity.FRICTION
    assert finding.context["census"] == {".doc": 1, ".pdf": 1}
    assert "1 of 2 attachment(s)" in finding.observed
    assert SUPERSEDED_FORMATS[".doc"] in finding.observed


def test_d44_a_current_office_format_is_advisory_and_an_archive_is_friction() -> None:
    document = course(
        [page("week-1", "<p>Read the notes.</p>")],
        files=[
            course_file("5001", "slides.pptx"),
            course_file("5002", "materials.zip"),
        ],
    )
    result = AttachmentFormats().run(document)
    assert codes(result) == ["attachment_is_an_archive", "attachment_needs_another_application"]
    by_code = {finding.code: finding for finding in result.findings}
    assert by_code["attachment_needs_another_application"].severity is Severity.ADVISORY
    assert by_code["attachment_is_an_archive"].severity is Severity.FRICTION
    assert APP_FORMATS[".pptx"] in by_code["attachment_needs_another_application"].observed


def test_d44_a_course_that_attaches_nothing_is_not_a_broken_detector() -> None:
    document = course([page("week-1", "<p>Everything is on the page.</p>")])
    result = AttachmentFormats().run(document)
    assert result.state is ResultState.NOT_EXAMINED
    assert result.clean is False


def test_d44_a_refused_file_listing_is_reported_as_refused_rather_than_as_no_attachments() -> None:
    document = course([page("week-1", "<p>x</p>")], examinations=[refused("files")])
    result = AttachmentFormats().run(document)
    assert result.state is ResultState.NOT_EXAMINED
    assert "refused" in (result.not_examined_because or "")


def test_d44_refuses_a_document_with_nothing_in_it() -> None:
    with pytest.raises(NothingExamined):
        AttachmentFormats().run(empty_document())


# ---------------------------------------------------------------------------------------------
# D45 — apps the course requires (tier C)
# ---------------------------------------------------------------------------------------------


def test_d45_a_course_that_sends_nobody_anywhere_raises_no_question() -> None:
    document = course(
        [page("week-1", "<p>Everything for this course is in Canvas.</p>")],
        modules=[module("3001", "Module 1", module_item("31001", "Page", module_id="module:3001"))],
    )
    result = RequiredAppAvailability().run(document)
    assert result.findings == ()
    assert result.undetermined == ()
    assert result.clean


def test_d45_an_external_tool_launch_is_recorded_as_a_question_not_as_a_finding() -> None:
    """Tier C: the context and the question, with no rating pre-selected. The answer is on two app
    stores and in no course artifact, so it is recorded as something that could not be read."""
    document = course(
        [page("week-1", "<p>Launch the courseware from the module.</p>")],
        modules=[
            module(
                "3001",
                "Module 1",
                module_item(
                    "31001",
                    "Synthetic Courseware",
                    module_id="module:3001",
                    item_type="ExternalTool",
                    external_url="https://courseware.example.edu/lti/launch",
                ),
            )
        ],
    )
    result = RequiredAppAvailability().run(document)
    assert result.findings == ()
    assert result.clean is False
    entry = result.undetermined[0]
    assert entry.location.artifact_id == "module_item:31001"
    assert "Synthetic Courseware" in entry.reason
    assert "Android and iOS" in entry.reason
    assert entry.remedy


def test_d45_an_external_tool_assignment_is_recorded_too() -> None:
    document = course(
        [page("week-1", "<p>x</p>")],
        assignments=[assignment("9001", "Publisher homework", submission_types=("external_tool",))],
        modules=[module("3001", "Module 1", module_item("31001", "Page", module_id="module:3001"))],
    )
    result = RequiredAppAvailability().run(document)
    assert [entry.location.artifact_id for entry in result.undetermined] == ["assignment:9001"]


def test_d45_a_page_asking_for_an_app_is_recorded_with_the_sentence_quoted() -> None:
    document = course(
        [page("week-1", "<p>Download the Synthetic Reader app before the first quiz.</p>")],
        modules=[module("3001", "Module 1", module_item("31001", "Page", module_id="module:3001"))],
    )
    result = RequiredAppAvailability().run(document)
    assert len(result.undetermined) == 1
    assert "Download the Synthetic Reader app" in result.undetermined[0].reason


def test_d45_the_word_app_inside_another_word_raises_no_question() -> None:
    """A Tier C question raised against a false match is worse than one not raised: it sends a
    reviewer to a page that says nothing about an app."""
    document = course(
        [
            page(
                "week-1",
                "<p>Use the apparatus in the lab. Applications are due in week three and the "
                "appendix explains why.</p>",
            )
        ],
        modules=[module("3001", "Module 1", module_item("31001", "Page", module_id="module:3001"))],
    )
    assert RequiredAppAvailability().run(document).undetermined == ()


def test_d45_is_tier_c_and_says_so_on_the_result() -> None:
    document = course(
        [page("week-1", "<p>x</p>")],
        modules=[module("3001", "Module 1", module_item("31001", "Page", module_id="module:3001"))],
    )
    assert RequiredAppAvailability().run(document).tier is Tier.C


def test_d45_refuses_a_document_with_nothing_in_it() -> None:
    with pytest.raises(NothingExamined):
        RequiredAppAvailability().run(empty_document())


# ---------------------------------------------------------------------------------------------
# One walk, not seven
# ---------------------------------------------------------------------------------------------

MOBILE = (
    EmbedFallbackLink,
    NestedIndentLevels,
    TextFloatedBesideImages,
    FixedPixelWidths,
    LegacyEmbeds,
    AttachmentFormats,
    RequiredAppAvailability,
)


def test_no_detector_here_parses_a_body_with_anything_but_the_shared_scanner() -> None:
    """The acceptance condition, checked structurally rather than by reading the module. Every
    markup-reading detector in this cluster goes through ``HtmlDetector.pages``, which is the one
    parse D21–D30 already perform; a second ``HTMLParser`` subclass here would mean two parsers
    that can disagree about the same page, which is how one detector reports three images and
    another four."""
    import inspect

    from sightline.detect import mobile

    source = inspect.getsource(mobile)
    assert "HTMLParser" not in source
    assert "from .html_scan import" in source


def test_every_mobile_detector_declares_a_capability_and_never_names_a_criterion() -> None:
    import re

    pattern = re.compile(r"\b(?:COAL|GSOV|OREP|ACD|ENIN|ASFE|MOB)\s*\d+\b")
    for cls in MOBILE:
        detector = cls()
        assert detector.capability_tag
        assert detector.capability_description
        assert not pattern.search(detector.title)
        assert not pattern.search(detector.capability_description)


# ---------------------------------------------------------------------------------------------
# The optional-section flag, demonstrated end to end
# ---------------------------------------------------------------------------------------------


def mobile_course():
    """One synthetic course carrying a defect for each of the deterministic mobile detectors."""
    return course(
        [
            page(
                "week-1",
                '<div style="width: 780px">'
                '<p><img style="float: left" src="/f/1" alt="A diagram">Text beside it.</p>'
                '<iframe src="https://media.example.edu/embed/lecture-four"></iframe>'
                '<object data="/f/animation.swf"></object>'
                "</div>",
            )
        ],
        files=[course_file("5001", "handout.doc", content_type="application/msword")],
        modules=[
            module(
                "3001",
                "Module 1",
                module_item("31001", "Overview", module_id="module:3001"),
                module_item("31002", "Buried", module_id="module:3001", position=2, indent=4),
            )
        ],
    )


def mobile_report():
    return run_detectors(mobile_course(), only=[cls() for cls in MOBILE])


def test_the_mobile_block_renders_with_detector_output_behind_it_when_the_flag_is_on() -> None:
    projection = project(shipped_pack(REFERENCE_ID), mobile_report())
    mobile_rows = [row for row in projection.rows if row.section == "MOB"]
    assert len(mobile_rows) == 9
    assert projection.omitted == ()

    covered = [row for row in mobile_rows if row.coverage is Coverage.DETERMINISTIC]
    assert covered, "no mobile row received anything from a detector"
    assert all(row.contributions for row in covered)
    assert any(row.findings for row in covered)


def test_switching_the_mobile_section_off_hides_every_mobile_row_and_says_which() -> None:
    """Hidden from the review, named in the report. A block that vanished silently would be a
    course reported clean on criteria nobody looked at."""
    pack = shipped_pack(REFERENCE_ID).with_section_enabled("MOB", False)
    projection = project(pack, mobile_report())

    assert [row for row in projection.rows if row.section == "MOB"] == []
    assert set(projection.omitted) == {f"MOB {number}" for number in range(1, 10)}
    assert "9 omitted with a switched-off section" in projection.statement()


def test_switching_the_section_off_changes_what_is_rendered_and_not_what_was_examined() -> None:
    """The flag is a projection, not a filter on the detectors. The same report is projected both
    ways here, and the non-mobile rows come out identical."""
    report = mobile_report()
    on = project(shipped_pack(REFERENCE_ID), report)
    off = project(shipped_pack(REFERENCE_ID).with_section_enabled("MOB", False), report)

    def outside_mobile(projection):
        return {row.code: (row.coverage, row.rating, row.hours) for row in projection.rows}

    elsewhere = {
        code: value for code, value in outside_mobile(on).items() if not code.startswith("MOB")
    }
    assert elsewhere == outside_mobile(off)


def test_the_row_that_no_detector_can_reach_stays_not_applicable_with_its_note() -> None:
    """Tier D, and deliberately detectorless: whether a course was opened on a handset is not in
    the course. The pack carries the note and the projection leaves the rating for a human."""
    projection = project(shipped_pack(REFERENCE_ID), mobile_report())
    row = projection.row("MOB 5")
    assert row is not None
    assert row.tier is Tier.D
    assert row.rating is Rating.NOT_APPLICABLE
    assert row.bindings == ()
    assert any("not determinable" in note.lower() for note in row.notes)


def test_the_tier_c_row_gathers_its_context_and_proposes_no_rating() -> None:
    projection = project(shipped_pack(REFERENCE_ID), mobile_report())
    row = projection.row("MOB 6")
    assert row is not None
    assert row.tier is Tier.C
    assert row.rating is Rating.NO_RESPONSE
    assert row.coverage is Coverage.DETERMINISTIC


def test_a_pack_with_no_mobile_block_is_unaffected_by_any_of_this() -> None:
    """The other shipped instrument has no mobile section at all. Its rows must not acquire one
    because the detectors ran — a rubric is a projection, and these facts have nowhere to go."""
    projection = project(shipped_pack(RSI_ID), mobile_report())
    assert all(row.section != "MOB" for row in projection.rows)
    assert projection.omitted == ()


def test_the_detectors_run_the_same_way_whichever_pack_is_projected() -> None:
    report = mobile_report()
    assert report.not_examined == ()
    assert {result.detector for result in report.results} == {cls.id for cls in MOBILE}
    for result in report.results:
        assert result.examined > 0
