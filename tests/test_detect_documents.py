"""Reading structure out of PDF, DOCX and PPTX bytes.

Every fixture here is assembled by hand rather than produced by a library, and deliberately: a
fixture written by the same library the probe reads with proves the two agree, not that either is
right. What these assert is that a file carrying a tag tree is reported as carrying one, a file
carrying none is reported as carrying none, and a file the probe cannot read is reported as
unreadable rather than as either.
"""

from __future__ import annotations

from detector_fixtures import docx, encrypted_pdf, pdf, pptx
from sightline.detect.documents import inspect_docx, inspect_pdf, inspect_pptx


def test_a_tagged_pdf_with_text_is_read_as_one() -> None:
    report = inspect_pdf(pdf(tagged=True, text_layer=True))
    assert report.readable
    assert report.tagged and report.marked and report.has_text_layer and report.has_language
    assert report.pages == 1


def test_a_scanned_pdf_is_read_as_pages_with_no_text_in_them() -> None:
    report = inspect_pdf(pdf(tagged=False, text_layer=False, language=False, pages=3))
    assert report.readable
    assert not report.tagged
    assert not report.has_text_layer
    assert not report.has_language
    assert report.pages == 3
    assert report.images == 1


def test_an_encrypted_pdf_is_unreadable_rather_than_untagged() -> None:
    """The distinction is the point. Reporting an encrypted file as untagged would be inventing a
    fact about a document nobody looked inside."""
    report = inspect_pdf(encrypted_pdf())
    assert not report.readable
    assert report.encrypted
    assert not report.tagged
    assert report.reason is not None and "encrypted" in report.reason


def test_bytes_that_are_not_a_pdf_are_reported_as_such() -> None:
    assert inspect_pdf(b"").reason == "the file is empty"
    report = inspect_pdf(b"PK\x03\x04 this is a zip")
    assert not report.readable
    assert report.reason is not None and "PDF header" in report.reason


def test_a_word_document_built_properly_reads_as_built_properly() -> None:
    report = inspect_docx(docx())
    assert report.readable
    assert report.uses_heading_styles and report.heading_styles == ("Heading1",)
    assert report.tables == 1 and report.tables_with_header_rows == 1
    assert report.images == 1 and report.images_without_alt == 0
    assert report.default_face == "Calibri"
    assert report.default_points == 12.0


def test_a_word_document_built_with_direct_formatting_reads_as_that() -> None:
    report = inspect_docx(
        docx(
            heading_styles=False,
            table_header=False,
            image_alt=False,
            face="Georgia",
            half_points=18,
        )
    )
    assert report.readable
    assert not report.uses_heading_styles
    assert report.tables_with_header_rows == 0
    assert report.images_without_alt == 1
    assert report.default_face == "Georgia"
    # Word stores a size in half-points. Reading it as points is the single easiest way to be
    # wrong here by a factor of two, so the number is asserted rather than the comparison.
    assert report.default_points == 9.0


def test_a_legacy_binary_office_file_is_unreadable_and_says_why() -> None:
    report = inspect_docx(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1 old compound file")
    assert not report.readable
    assert report.reason is not None and "zip container" in report.reason


def test_a_zip_that_is_not_an_office_file_is_unreadable() -> None:
    from io import BytesIO
    from zipfile import ZipFile

    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "hi")
    report = inspect_docx(buffer.getvalue())
    assert not report.readable
    assert report.reason is not None and "word/document.xml" in report.reason


def test_a_deck_with_unique_titles_and_click_advance_reads_as_clean() -> None:
    report = inspect_pptx(pptx([{"title": "Observations"}, {"title": "Grouping"}]))
    assert report.readable
    assert report.slides == 2
    assert report.slides_without_title == ()
    assert report.duplicate_titles == ()
    assert report.slides_advancing_automatically == ()
    assert report.slides_off_layout == ()


def test_a_deck_with_every_planted_defect_reports_each_of_them() -> None:
    report = inspect_pptx(
        pptx(
            [
                {"title": "Observations"},
                {"title": "Observations", "auto_advance": True},
                {"title": None},
                {"title": "Drawn by hand", "placeholders": False},
            ]
        )
    )
    assert report.slides == 4
    assert report.slides_without_title == (3, 4)
    assert report.duplicate_titles == ("observations",)
    assert report.slides_advancing_automatically == (2,)
    assert report.slides_off_layout == (4,)
