"""D21–D30, each against a clean page and against a page with the defect planted in it.

Both halves are required and the clean half is the one that catches more. A detector that fires on
everything passes every defective fixture, and it is the detector a faculty member dismisses the
whole tool over. So every detector here is asserted to find *nothing* on markup that is correct,
and to say how many artifacts it opened either way.

Nothing in this file names a rubric criterion, and neither does anything it tests. Detectors emit
facts about artifacts; a rubric pack projects them onto its own rows (SPEC §04).
"""

from __future__ import annotations

import pytest

from detector_fixtures import (
    codes,
    course,
    course_file,
    docx,
    one_page,
    page,
    pdf,
    pptx,
)
from sightline.detect import (
    CaptionTrack,
    ColourAsSoleCarrier,
    ContrastRatio,
    Detector,
    DetectorResult,
    DocumentAccessibility,
    HeadingStructure,
    ImageAltText,
    LinkText,
    MappingFileSource,
    Severity,
    TableSemantics,
    TextRenderedAsImage,
    Tier,
    TypeSizeAndFamily,
)


def run(detector: Detector, document: object, files: object = None) -> DetectorResult:
    return detector.run(document, files=files)  # type: ignore[arg-type]


# -- D21 image alt text --------------------------------------------------------------------------

CLEAN_IMAGES = """
<p><img src="/courses/4410/files/5006/preview" alt="Three overlapping circles"></p>
<p><img src="/courses/4410/files/5011/preview" alt="" role="presentation"></p>
<p><img src="/courses/4410/files/5012/preview" alt="" width="1" height="1"></p>
"""

DEFECTIVE_IMAGES = """
<p><img src="/courses/4410/files/5005/preview" width="640"></p>
<p><img src="/courses/4410/files/5007/preview" alt="IMG_2043.jpg"></p>
<p><img src="/courses/4410/files/5008/preview" alt=""></p>
<p><img src="/courses/4410/files/5009/preview" alt="LONGALT"></p>
<p><img src="/courses/4410/files/5013/preview" alt="A diagram" role="presentation"></p>
"""


def test_d21_finds_nothing_when_every_image_is_labelled_or_correctly_decorative() -> None:
    result = run(ImageAltText(), one_page(CLEAN_IMAGES))
    assert result.clean is True
    assert result.examined == 1
    assert result.subjects == 3


def test_d21_names_each_defect_separately() -> None:
    body = DEFECTIVE_IMAGES.replace("LONGALT", "A photograph of a whiteboard. " * 10)
    result = run(ImageAltText(), one_page(body))
    assert codes(result) == [
        "alt_empty_and_not_marked_decorative",
        "alt_is_a_filename",
        "alt_missing",
        "alt_over_long",
        "decorative_image_with_alt_text",
    ]
    assert result.examined == 1
    assert result.subjects == 5
    assert all(finding.examined == 1 for finding in result.findings)
    assert {finding.tier for finding in result.findings} == {Tier.A}


def test_d21_treats_alt_matching_the_filename_as_a_filename() -> None:
    body = '<img src="/files/graph-of-results.png" alt="graph-of-results">'
    assert codes(run(ImageAltText(), one_page(body))) == ["alt_is_a_filename"]


def test_d21_quotes_the_markup_as_it_was_written() -> None:
    result = run(ImageAltText(), one_page('<img  src="/a.png"   width="10">'))
    finding = result.findings[0]
    assert finding.evidence == '<img  src="/a.png"   width="10">'
    assert finding.severity is Severity.BARRIER
    assert finding.location.within == "image 1"


def test_d21_does_not_judge_whether_alt_text_is_good() -> None:
    """The scope boundary (SPEC §11). Alt text that exists, is not a filename and is not
    over-long is accepted whatever it says; judging its quality is not what the rubric asks and is
    not what this tool does."""
    body = '<img src="/a.png" alt="image">'
    assert codes(run(ImageAltText(), one_page(body))) == []


# -- D22 heading structure -----------------------------------------------------------------------

CLEAN_HEADINGS = """
<h2>Module 1 Overview</h2>
<p>This module introduces the vocabulary used for the rest of the course.</p>
<h3>What you will do</h3>
<ul><li>Read the chapter.</li></ul>
<h3>Figure</h3>
<p>Text that follows a heading, with <strong>bold</strong> used inside a sentence.</p>
<table><tr><td><strong>Week</strong></td><td>Topic</td></tr></table>
"""

DEFECTIVE_HEADINGS = """
<h3>Start Here</h3>
<p>Welcome.</p>
<h5>What to do first</h5>
<p><strong>Supplementary material</strong></p>
<h4></h4>
"""


def test_d22_accepts_headings_in_order_and_bold_used_inside_a_sentence() -> None:
    result = run(HeadingStructure(), one_page(CLEAN_HEADINGS))
    assert result.clean is True
    assert result.examined == 1
    assert result.subjects == 3


def test_d22_names_each_defect_separately() -> None:
    result = run(HeadingStructure(), one_page(DEFECTIVE_HEADINGS))
    assert codes(result) == [
        "bold_text_used_as_a_heading",
        "heading_empty",
        "heading_level_skipped",
    ]
    assert result.examined == 1
    assert result.subjects == 3


def test_d22_reports_a_long_page_with_no_headings_at_all() -> None:
    long_page = "<p>" + ("word " * 200) + "</p>"
    assert codes(run(HeadingStructure(), one_page(long_page))) == ["headings_absent"]


def test_d22_leaves_a_short_page_with_no_headings_alone() -> None:
    assert codes(run(HeadingStructure(), one_page("<p>A short note.</p>"))) == []


def test_d22_notices_a_second_first_level_heading_in_the_body() -> None:
    result = run(HeadingStructure(), one_page("<h1>Overview</h1><p>Text.</p>"))
    assert codes(result) == ["heading_h1_inside_body"]
    assert result.findings[0].severity is Severity.ADVISORY


def test_d22_says_which_levels_were_skipped() -> None:
    result = run(HeadingStructure(), one_page("<h2>A</h2><h5>B</h5>"))
    assert result.findings[0].context["skipped"] == ["h3", "h4"]


# -- D23 link text -------------------------------------------------------------------------------

CLEAN_LINKS = """
<p>Read the <a href="/courses/4410/assignments/syllabus">course syllabus</a> first, then the
<a href="/courses/4410/pages/course-schedule">weekly schedule</a>.</p>
<p><a href="/courses/4410/files/5002">week2-reading.docx</a></p>
<p><a href="/courses/4410/files/5100/download"><img src="/i.png" alt="Orientation video"></a></p>
"""

DEFECTIVE_LINKS = """
<p>To read the syllabus, <a href="/courses/4410/assignments/syllabus">click here</a>.
For the schedule, <a href="/courses/4410/pages/course-schedule">click here</a>.
The help desk is at https://help.example.edu/students.</p>
<p><a href="https://library.example.edu">https://library.example.edu</a></p>
<p><a href="/courses/4410/files/1"></a></p>
"""


def test_d23_accepts_descriptive_labels_a_filename_and_an_image_with_alt_text() -> None:
    result = run(LinkText(), one_page(CLEAN_LINKS))
    assert result.clean is True
    assert result.examined == 1
    assert result.subjects == 4


def test_d23_names_each_defect_separately() -> None:
    result = run(LinkText(), one_page(DEFECTIVE_LINKS))
    assert codes(result) == [
        "link_text_is_a_web_address",
        "link_text_says_nothing",
        "link_text_says_nothing",
        "link_without_text",
        "same_label_different_destinations",
        "web_address_written_as_prose",
    ]
    assert result.examined == 1


def test_d23_leaves_the_same_label_alone_when_it_goes_to_the_same_place() -> None:
    body = '<a href="/x">Syllabus</a> and <a href="/x">Syllabus</a>'
    assert codes(run(LinkText(), one_page(body))) == []


def test_d23_lists_the_destinations_a_repeated_label_leads_to() -> None:
    body = '<a href="/a">Slides</a> and <a href="/b">Slides</a>'
    result = run(LinkText(), one_page(body))
    assert result.findings[0].context["destinations"] == ["/a", "/b"]


# -- D24 table semantics -------------------------------------------------------------------------

CLEAN_TABLE = """
<table>
  <caption>Weekly schedule</caption>
  <tr><th scope="col">Week</th><th scope="col">Topic</th></tr>
  <tr><th scope="row">1</th><td>Foundations</td></tr>
  <tr><th scope="row">2</th><td>Methods</td></tr>
</table>
"""

DATA_TABLE_WITHOUT_HEADERS = """
<table width="900" border="1">
  <tr><td><strong>Week</strong></td><td><strong>Topic</strong></td></tr>
  <tr><td>1</td><td>Foundations</td></tr>
  <tr><td>2</td><td>Methods</td></tr>
</table>
"""

LAYOUT_TABLE = """
<table cellpadding="0" cellspacing="0" border="0">
  <tr><td><img src="/i.png" alt=""></td><td>Layout only.</td></tr>
</table>
"""


def test_d24_accepts_a_table_with_headers_scope_and_a_caption() -> None:
    result = run(TableSemantics(), one_page(CLEAN_TABLE))
    assert result.clean is True
    assert result.examined == 1
    assert result.subjects == 1


def test_d24_reports_a_table_of_data_with_no_header_cells() -> None:
    result = run(TableSemantics(), one_page(DATA_TABLE_WITHOUT_HEADERS))
    assert codes(result) == ["table_without_caption", "table_without_header_cells"]
    missing = next(f for f in result.findings if f.code == "table_without_header_cells")
    assert missing.observed == "3 row(s) × 2 column(s), no <th>"


def test_d24_tells_a_layout_table_apart_from_a_table_of_data() -> None:
    result = run(TableSemantics(), one_page(LAYOUT_TABLE))
    assert codes(result) == ["table_positions_content"]
    assert result.findings[0].context["reason"] == "the table has a single row"


def test_d24_reports_header_cells_with_no_scope() -> None:
    body = """
    <table><caption>A</caption>
      <tr><th>Week</th><th>Topic</th></tr>
      <tr><td>1</td><td>Foundations</td></tr>
    </table>
    """
    assert codes(run(TableSemantics(), one_page(body))) == ["header_cells_without_scope"]


def test_d24_reports_the_obsolete_summary_attribute() -> None:
    body = '<table summary="Weeks and topics"><caption>A</caption>' + (
        '<tr><th scope="col">W</th><th scope="col">T</th></tr>'
        "<tr><td>1</td><td>F</td></tr></table>"
    )
    assert codes(run(TableSemantics(), one_page(body))) == ["table_uses_summary_attribute"]


# -- D25 contrast --------------------------------------------------------------------------------


def test_d25_accepts_colour_that_reaches_the_ratio() -> None:
    body = '<p><span style="color: #cc0000;">Required work</span> is marked.</p>'
    result = run(ContrastRatio(), one_page(body))
    assert result.clean is True
    assert result.examined == 1
    assert result.subjects == 1


def test_d25_reports_the_ratio_and_the_background_it_measured_against() -> None:
    body = '<p><span style="color: #bbbbbb;">Office hours may change.</span></p>'
    result = run(ContrastRatio(), one_page(body))
    finding = result.findings[0]
    assert finding.code == "contrast_below_threshold"
    assert finding.observed == "1.92:1 for #bbbbbb on #ffffff, 4.50:1 needed"
    assert finding.context["background_assumed"] is True
    assert "#ffffff" in finding.context["note"]
    assert finding.severity is Severity.BARRIER


def test_d25_uses_a_declared_background_rather_than_the_assumption() -> None:
    body = '<div style="background-color: #000000;"><p style="color: #bbbbbb;">Text</p></div>'
    result = run(ContrastRatio(), one_page(body))
    assert result.clean is True


def test_d25_applies_the_larger_text_threshold() -> None:
    small = '<p style="color: #949494; font-size: 12pt;">Text</p>'
    large = '<p style="color: #949494; font-size: 18pt;">Text</p>'
    assert codes(run(ContrastRatio(), one_page(small))) == ["contrast_below_threshold"]
    assert codes(run(ContrastRatio(), one_page(large))) == []


def test_d25_counts_repeats_rather_than_reporting_each_one() -> None:
    body = "".join(f'<p style="color: #bbbbbb;">Line {index}</p>' for index in range(5))
    result = run(ContrastRatio(), one_page(body))
    assert len(result.findings) == 1
    assert result.findings[0].context["occurrences"] == 5
    assert result.subjects == 5


def test_d25_reports_the_rules_it_could_not_resolve_instead_of_ignoring_them() -> None:
    body = "<style>div p span { color: #bbbbbb }</style><div><p><span>Text</span></p></div>"
    result = run(ContrastRatio(), one_page(body))
    assert result.findings == ()
    assert result.undetermined and "div p span" in result.undetermined[0].reason
    assert result.clean is False


def test_d25_reads_colour_out_of_a_flat_style_block() -> None:
    body = '<style>.note { color: #bbbbbb; }</style><p class="note">Office hours.</p>'
    assert codes(run(ContrastRatio(), one_page(body))) == ["contrast_below_threshold"]


# -- D26 colour as the only cue (tier B) -----------------------------------------------------------


def test_d26_leaves_alone_a_page_where_colour_is_not_doing_the_work() -> None:
    body = (
        "<p>Required work is marked with an asterisk and listed in the schedule.</p>"
        '<p><span style="color: #cc0000;"><strong>Due Friday</strong></span></p>'
    )
    result = run(ColourAsSoleCarrier(), one_page(body))
    assert result.clean is True
    assert result.examined == 1


def test_d26_quotes_the_sentence_that_tells_a_learner_to_look_for_a_colour() -> None:
    body = "<p>Assignments shown in red are required. Everything else is optional.</p>"
    result = run(ColourAsSoleCarrier(), one_page(body))
    assert codes(result) == ["instructions_refer_to_a_colour"]
    finding = result.findings[0]
    assert finding.tier is Tier.B
    assert finding.evidence == "Assignments shown in red are required."
    assert "reviewer" in finding.context["note"]


def test_d26_reports_a_coloured_fragment_carrying_no_second_cue() -> None:
    body = '<p>Assignments <span style="color: #cc0000;">like this one</span> are required.</p>'
    result = run(ColourAsSoleCarrier(), one_page(body))
    assert codes(result) == ["coloured_text_carries_no_second_cue"]


def test_d26_leaves_a_whole_paragraph_in_one_colour_alone() -> None:
    body = '<p style="color: #444444;">A styled paragraph, not an instruction.</p>'
    assert codes(run(ColourAsSoleCarrier(), one_page(body))) == []


def test_d26_never_pre_rates_anything_it_quotes() -> None:
    body = "<p>The red items are required.</p>"
    result = run(ColourAsSoleCarrier(), one_page(body))
    assert all(finding.tier is Tier.B for finding in result.findings)
    assert all(finding.evidence for finding in result.findings)


# -- D27 captions --------------------------------------------------------------------------------

CAPTIONED = """
<video width="640" controls>
  <source src="/courses/4410/files/5100/download">
  <track kind="captions" srclang="en" src="/courses/4410/files/5101/download">
</video>
"""

UNCAPTIONED = '<video width="640" controls><source src="/courses/4410/files/5100/download"></video>'
HOSTED_PLAYER = '<iframe src="https://media.example.edu/embed/synthetic0001"></iframe>'


def test_d27_accepts_a_video_with_a_caption_track() -> None:
    result = run(CaptionTrack(), one_page(CAPTIONED))
    assert result.clean is True
    assert result.examined == 1
    assert result.subjects == 1


def test_d27_reports_a_video_with_no_caption_track() -> None:
    result = run(CaptionTrack(), one_page(UNCAPTIONED))
    assert codes(result) == ["media_without_caption_track"]
    assert result.findings[0].severity is Severity.BARRIER


def test_d27_says_a_hosted_player_is_unevidenced_rather_than_uncaptioned() -> None:
    """The honest answer. Nothing on the page records whether a third-party player carries
    captions, and reporting it as uncaptioned would be a fact nobody checked."""
    result = run(CaptionTrack(), one_page(HOSTED_PLAYER))
    assert codes(result) == ["player_captions_not_evidenced"]
    finding = result.findings[0]
    assert finding.severity is Severity.FRICTION
    assert "Open it and check" in finding.context["note"]


def test_d27_names_the_page_a_reviewer_opens_rather_than_the_embed() -> None:
    document = course([page("week-3", HOSTED_PLAYER)])
    finding = run(CaptionTrack(), document).findings[0]
    assert finding.location.artifact_id == "page:week-3"
    assert finding.location.within == "iframe embed"


def test_d27_reports_a_media_file_with_no_caption_file_beside_it() -> None:
    document = course(
        [page("one", "<p>See the recording.</p>")],
        files=[course_file("5100", "lecture-one.mp4"), course_file("5102", "lecture-two.mp4")],
    )
    result = run(CaptionTrack(), document)
    assert codes(result) == [
        "media_file_without_a_caption_file",
        "media_file_without_a_caption_file",
    ]
    assert result.examined == 2


def test_d27_accepts_a_media_file_with_a_caption_file_of_the_same_name() -> None:
    document = course(
        [page("one", "<p>See the recording.</p>")],
        files=[course_file("5100", "lecture-one.mp4"), course_file("5101", "lecture-one.vtt")],
    )
    assert codes(run(CaptionTrack(), document)) == []


# -- D28 type ------------------------------------------------------------------------------------


def test_d28_accepts_sans_serif_type_at_the_minimum_size() -> None:
    body = '<p style="font-family: Arial, sans-serif; font-size: 12pt;">Text</p>'
    result = run(TypeSizeAndFamily(), one_page(body))
    assert result.clean is True
    assert result.examined == 1


def test_d28_reports_a_decorative_face_at_a_size_below_the_minimum() -> None:
    body = '<p style="font-family: Papyrus, fantasy; font-size: 9pt;">Text</p>'
    result = run(TypeSizeAndFamily(), one_page(body))
    assert codes(result) == ["type_smaller_than_the_minimum", "typeface_is_not_sans_serif"]
    sized = result.findings[0]
    assert sized.observed == "font-size: 9pt, which resolves to 9pt"


def test_d28_reads_the_legacy_font_element() -> None:
    body = '<p><font face="Georgia" size="2">Text</font></p>'
    assert codes(run(TypeSizeAndFamily(), one_page(body))) == [
        "type_smaller_than_the_minimum",
        "typeface_is_not_sans_serif",
    ]


def test_d28_reads_the_default_style_of_an_attached_word_document() -> None:
    item = course_file("5002", "week2-reading.docx")
    document = course([page("one", "<p>See the document.</p>")], files=[item])
    source = MappingFileSource({item.id: docx(face="Georgia", half_points=18)})
    result = run(TypeSizeAndFamily(), document, source)
    assert codes(result) == [
        "document_type_smaller_than_the_minimum",
        "document_typeface_is_not_sans_serif",
    ]
    assert result.findings[0].location.artifact_id == item.id


def test_d28_records_a_document_whose_bytes_were_never_acquired() -> None:
    item = course_file("5002", "week2-reading.docx")
    document = course([page("one", "<p>See the document.</p>")], files=[item])
    result = run(TypeSizeAndFamily(), document)
    assert result.findings == ()
    assert result.undetermined and "bytes were not acquired" in result.undetermined[0].reason
    assert result.clean is False


# -- D29 text rendered as an image (tier B) --------------------------------------------------------


def test_d29_leaves_an_ordinary_figure_on_a_page_of_prose_alone() -> None:
    item = course_file("5006", "overlapping-circles.png")
    body = (
        "<p>" + ("word " * 60) + "</p>"
        '<p><img src="/courses/4410/files/5006/preview" alt="Circles"></p>'
    )
    document = course([page("one", body)], files=[item])
    result = run(TextRenderedAsImage(), document)
    assert result.clean is True
    assert result.examined == 2


def test_d29_quotes_an_image_whose_filename_names_a_document() -> None:
    item = course_file("5005", "schedule-table.png")
    document = course(
        [page("start-here", '<p><img src="/courses/4410/files/5005/preview" width="640"></p>')],
        files=[item],
    )
    result = run(TextRenderedAsImage(), document)
    assert codes(result) == ["image_named_like_a_document"]
    finding = result.findings[0]
    assert finding.tier is Tier.B
    assert finding.location.artifact_id == "file:5005"
    assert finding.context["words_matched"] == ["schedule", "table"]
    assert finding.context["appears_on"] == "/courses/4410/pages/start-here"


def test_d29_reports_a_page_carrying_more_picture_than_prose() -> None:
    body = "<p>Notes.</p>" + "".join(
        f'<img src="/courses/4410/files/60{index:02d}/preview" alt="A figure">'
        for index in range(3)
    )
    result = run(TextRenderedAsImage(), one_page(body))
    assert codes(result) == ["page_carries_more_picture_than_prose"]
    assert result.findings[0].context == {"images": 3, "words": 1}


def test_d29_sends_no_image_to_a_model_and_says_so() -> None:
    """The scope boundary again. D29 reads filenames and counts words; it never looks inside a
    picture, and the finding says that in as many words so a reviewer knows what to open."""
    item = course_file("5005", "syllabus-scan.png")
    document = course(
        [page("one", '<p><img src="/courses/4410/files/5005/preview" alt="Syllabus"></p>')],
        files=[item],
    )
    finding = run(TextRenderedAsImage(), document).findings[0]
    assert "does not look inside images" in finding.context["note"]


# -- D30 document accessibility --------------------------------------------------------------------


def _with_documents(**contents: bytes) -> tuple[object, MappingFileSource]:
    files = [course_file(str(5000 + index), name) for index, name in enumerate(contents, start=1)]
    document = course([page("one", "<p>See the attached documents.</p>")], files=files)
    source = MappingFileSource(dict(zip(contents, contents.values(), strict=True)))
    return document, source


def test_d30_accepts_documents_built_properly() -> None:
    document, source = _with_documents()
    item_pdf = course_file("5001", "syllabus.pdf")
    item_docx = course_file("5002", "reading.docx")
    item_pptx = course_file("5003", "slides.pptx")
    document = course(
        [page("one", "<p>See the attached documents.</p>")],
        files=[item_pdf, item_docx, item_pptx],
    )
    source = MappingFileSource(
        {
            item_pdf.id: pdf(tagged=True, text_layer=True),
            item_docx.id: docx(),
            item_pptx.id: pptx([{"title": "Observations"}, {"title": "Grouping"}]),
        }
    )
    result = run(DocumentAccessibility(), document, source)
    assert result.clean is True
    assert result.examined == 3
    assert result.subjects == 3


def test_d30_reports_a_scanned_untagged_pdf() -> None:
    item = course_file("5001", "syllabus.pdf")
    document = course([page("one", "<p>Syllabus.</p>")], files=[item])
    source = MappingFileSource(
        {item.id: pdf(tagged=False, text_layer=False, language=False, pages=4)}
    )
    result = run(DocumentAccessibility(), document, source)
    assert codes(result) == [
        "pdf_without_a_declared_language",
        "pdf_without_a_tag_tree",
        "pdf_without_a_text_layer",
    ]
    assert "4 page(s)" in result.findings[1].observed


def test_d30_reports_a_word_document_formatted_by_hand() -> None:
    item = course_file("5002", "reading.docx")
    document = course([page("one", "<p>Reading.</p>")], files=[item])
    source = MappingFileSource(
        {item.id: docx(heading_styles=False, table_header=False, image_alt=False)}
    )
    assert codes(run(DocumentAccessibility(), document, source)) == [
        "document_image_without_alt_text",
        "document_table_without_a_repeating_header_row",
        "document_without_heading_styles",
    ]


def test_d30_reports_a_deck_with_missing_repeated_and_timed_slides() -> None:
    item = course_file("5003", "slides.pptx")
    document = course([page("one", "<p>Slides.</p>")], files=[item])
    source = MappingFileSource(
        {
            item.id: pptx(
                [
                    {"title": "Observations"},
                    {"title": "Observations", "auto_advance": True},
                    {"title": None},
                    {"title": "Drawn by hand", "placeholders": False},
                ]
            )
        }
    )
    assert codes(run(DocumentAccessibility(), document, source)) == [
        "slide_advances_on_a_timer",
        "slide_built_outside_a_layout",
        "slide_titles_repeat",
        "slide_without_a_title",
    ]


def test_d30_records_an_encrypted_pdf_as_unread_rather_than_untagged() -> None:
    from detector_fixtures import encrypted_pdf

    item = course_file("5001", "locked.pdf")
    document = course([page("one", "<p>Locked.</p>")], files=[item])
    result = run(DocumentAccessibility(), document, MappingFileSource({item.id: encrypted_pdf()}))
    assert result.findings == ()
    assert result.undetermined and "encrypted" in result.undetermined[0].reason
    assert result.clean is False


def test_d30_records_a_legacy_binary_office_file_without_opening_it() -> None:
    item = course_file("5004", "legacy-handout.doc")
    document = course([page("one", "<p>Handout.</p>")], files=[item])
    result = run(DocumentAccessibility(), document, MappingFileSource({item.id: b"anything"}))
    assert result.findings == ()
    assert result.undetermined and "pre-2007" in result.undetermined[0].reason
    assert result.undetermined[0].remedy is not None


def test_d30_counts_every_file_it_considered_not_only_the_documents() -> None:
    document = course(
        [page("one", "<p>Files.</p>")],
        files=[
            course_file("5001", "syllabus.pdf"),
            course_file("5005", "schedule-table.png"),
            course_file("5100", "lecture.mp4"),
        ],
    )
    result = run(DocumentAccessibility(), document)
    assert result.examined == 3
    assert result.subjects == 1


@pytest.mark.parametrize(
    "detector",
    [
        ImageAltText(),
        HeadingStructure(),
        LinkText(),
        TableSemantics(),
        ContrastRatio(),
        ColourAsSoleCarrier(),
        CaptionTrack(),
        TypeSizeAndFamily(),
        TextRenderedAsImage(),
        DocumentAccessibility(),
    ],
)
def test_every_detector_reports_what_it_examined(detector: Detector) -> None:
    """The invariant, asserted for each of the ten in turn: a number greater than zero, and a
    finding that carries it."""
    document = course(
        [page("one", CLEAN_IMAGES + CLEAN_HEADINGS + CLEAN_LINKS + CLEAN_TABLE + CAPTIONED)],
        files=[course_file("5001", "syllabus.pdf"), course_file("5005", "schedule-table.png")],
    )
    result = detector.run(document)
    assert result.examined > 0
    assert all(finding.examined == result.examined for finding in result.findings)
