"""Course references: URL, bare id, CSV — and everything that is none of those.

The acceptance criterion for this item is that all three input forms resolve to the same course
id. That is asserted here at the parser, and again in ``tests/test_canvas_client.py`` through a
real client against recorded fixtures, because the parser agreeing with itself is not the claim.
"""

from __future__ import annotations

import pytest

from sightline.canvas.refs import (
    CourseRefError,
    normalize_base_url,
    parse_course_ref,
    parse_course_ref_csv,
    parse_course_refs,
)

HOST = "canvas.example.edu"
BASE = f"https://{HOST}"

#: Every spelling of "course 4410" that has to work. The trailing-path cases are the ones people
#: actually paste: nobody copies the course home page URL, they copy whatever tab they were on.
SAME_COURSE = [
    "4410",
    " 4410 ",
    f"{BASE}/courses/4410",
    f"{BASE}/courses/4410/",
    f"{BASE}/courses/4410/pages/welcome",
    f"{BASE}/courses/4410/assignments/9912",
    f"{BASE}/courses/4410/modules",
    f"{BASE}/courses/4410/modules#module_4",
    f"{BASE}/courses/4410/quizzes/12?module_item_id=88",
    f"{BASE}/courses/4410/pages/a%20page%20with%20spaces",
    f"{HOST}/courses/4410",
    "/courses/4410",
    f"<{BASE}/courses/4410>",
    f'"{BASE}/courses/4410"',
]


@pytest.mark.parametrize("text", SAME_COURSE)
def test_every_form_resolves_to_the_same_course_id(text: str) -> None:
    assert parse_course_ref(text).course_id == "4410"


def test_the_form_is_reported_so_the_screen_can_say_what_it_read() -> None:
    assert parse_course_ref("4410").form == "numeric-id"
    assert parse_course_ref(f"{BASE}/courses/4410").form == "course-url"
    assert parse_course_ref("sis_course_id:SAMPLE-4410").form == "sis-id"


def test_sis_ids_survive_both_spellings() -> None:
    bare = parse_course_ref("sis_course_id:SAMPLE-4410")
    from_url = parse_course_ref(f"{BASE}/courses/sis_course_id:SAMPLE-4410")
    assert bare.course_id == from_url.course_id == "sis_course_id:SAMPLE-4410"
    assert from_url.api_segment == "sis_course_id:SAMPLE-4410"


@pytest.mark.parametrize(
    ("text", "expected_in_reason"),
    [
        ("", "Empty"),
        ("not a course", "neither a course id nor"),
        (f"{BASE}/courses", "neither a course id nor"),
        (f"{BASE}/accounts/1/terms", "neither a course id nor"),
        ("ftp://canvas.example.edu/courses/4410", "not a scheme Sightline follows"),
    ],
)
def test_a_rejection_says_what_was_wrong(text: str, expected_in_reason: str) -> None:
    with pytest.raises(CourseRefError) as caught:
        parse_course_ref(text)
    assert expected_in_reason.lower() in str(caught.value).lower()
    # Every rejection carries a next move, not only a complaint.
    assert caught.value.remedy


def test_a_url_from_another_instance_is_refused_by_name() -> None:
    """Two Canvas instances both have a course 4410. Reviewing the wrong one looks like success."""
    with pytest.raises(CourseRefError) as caught:
        parse_course_ref("https://canvas.other.example/courses/4410", expected_host=HOST)
    message = str(caught.value)
    assert "canvas.other.example" in message
    assert HOST in message


def test_a_url_from_the_configured_instance_is_accepted() -> None:
    assert parse_course_ref(f"{BASE}/courses/4410", expected_host=HOST).course_id == "4410"


# -- base URL ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        BASE,
        f"{BASE}/",
        f"{HOST}",
        f"{BASE}/courses/4410",
        f"{BASE}/courses/4410/pages/welcome",
        f"  {BASE}  ",
    ],
)
def test_a_base_url_is_trimmed_back_to_its_root(text: str) -> None:
    """The commonest configuration mistake there is, and it 404s every call with no clue why."""
    assert normalize_base_url(text) == BASE


def test_a_base_url_keeps_a_non_default_port() -> None:
    assert normalize_base_url("http://canvas.example.edu:8900/courses/1") == (
        "http://canvas.example.edu:8900"
    )


@pytest.mark.parametrize("text", ["", "   ", "ftp://canvas.example.edu", "https://"])
def test_an_unusable_base_url_is_refused_with_a_remedy(text: str) -> None:
    with pytest.raises(CourseRefError) as caught:
        normalize_base_url(text)
    assert caught.value.remedy


# -- batches ----------------------------------------------------------------------------------


def test_a_pasted_block_reports_every_outcome() -> None:
    batch = parse_course_refs(
        f"""4410
        {BASE}/courses/4417/pages/welcome
        4410
        not a course
        """,
        expected_host=HOST,
    )
    assert batch.course_ids == ["4410", "4417"]
    assert [item.line for item in batch.duplicates] == [3]
    assert [item.line for item in batch.rejected] == [4]
    assert batch.examined == 4
    assert not batch.ok


def test_commas_and_tabs_separate_as_well_as_newlines() -> None:
    batch = parse_course_refs("4410, 4417\t4424")
    assert batch.course_ids == ["4410", "4417", "4424"]


def test_a_space_separates_only_when_every_piece_is_a_reference() -> None:
    """Splitting on spaces unconditionally turns one bad line into three rejections quoting words
    the user never meant as references — a report that misdescribes its own input."""
    assert parse_course_refs("4410 4417").course_ids == ["4410", "4417"]

    batch = parse_course_refs("not a course")
    assert batch.examined == 1
    assert [item.raw for item in batch.rejected] == ["not a course"]


def test_an_empty_paste_reports_nothing_examined() -> None:
    batch = parse_course_refs("   \n  \n")
    assert batch.examined == 0
    assert batch.accepted == []
    assert "0 examined" in batch.summary()


# -- CSV --------------------------------------------------------------------------------------

CSV_WITH_HEADER = (
    "Course Name,Course URL,Instructor\n"
    f"Sample Course 4410,{BASE}/courses/4410,Reviewer A\n"
    f"Sample Course 4417,{BASE}/courses/4417/pages/welcome,Reviewer B\n"
)

CSV_WITHOUT_HEADER = f"Sample Course 4410,{BASE}/courses/4410\nSample Course 4417,4417\n"

CSV_IDS = "course_id,term\n4410,Spring\n4417,Spring\n"


def test_a_named_header_column_is_used_and_named_back() -> None:
    batch = parse_course_ref_csv(CSV_WITH_HEADER, expected_host=HOST, filename="courses.csv")
    assert batch.course_ids == ["4410", "4417"]
    assert batch.column_name == "Course URL"
    assert "Course URL" in batch.column_choice


def test_a_bare_id_column_resolves_to_the_same_ids() -> None:
    assert parse_course_ref_csv(CSV_IDS).course_ids == ["4410", "4417"]


def test_without_a_header_the_chosen_column_is_explained() -> None:
    batch = parse_course_ref_csv(CSV_WITHOUT_HEADER, expected_host=HOST)
    assert batch.course_ids == ["4410", "4417"]
    assert batch.column_index == 1
    assert "parsed 2 of 2 rows" in batch.column_choice


def test_a_csv_with_no_usable_column_says_which_headers_it_saw() -> None:
    batch = parse_course_ref_csv("Name,Term\nSample Course,Spring\n", filename="wrong.csv")
    assert batch.accepted == []
    assert batch.rejected
    assert "Name, Term" in batch.rejected[0].remedy


def test_a_row_with_an_empty_reference_cell_is_reported_rather_than_skipped() -> None:
    """SPEC §11: silent under-detection is the failure mode. A skipped row is a small one."""
    batch = parse_course_ref_csv("course_id,term\n4410,Spring\n,Fall\n4417,Spring\n")
    assert batch.course_ids == ["4410", "4417"]
    assert len(batch.rejected) == 1
    assert batch.rejected[0].line == 3
    assert batch.examined == 3


def test_a_wholly_blank_row_is_formatting_not_a_lost_course() -> None:
    """The distinction the rule above turns on: a row with nothing in it carries no reference."""
    batch = parse_course_ref_csv("course_id\n4410\n\n\n4417\n")
    assert batch.course_ids == ["4410", "4417"]
    assert batch.rejected == []
    assert batch.examined == 2


def test_a_utf8_bom_does_not_hide_the_header() -> None:
    """Excel writes one, and a BOM on 'course_id' makes the header match nothing."""
    batch = parse_course_ref_csv(("﻿" + CSV_IDS).encode("utf-8"))
    assert batch.course_ids == ["4410", "4417"]
    assert batch.column_name == "course_id"


def test_a_semicolon_delimited_csv_is_read() -> None:
    batch = parse_course_ref_csv("course_id;term\n4410;Spring\n4417;Spring\n")
    assert batch.course_ids == ["4410", "4417"]


def test_an_empty_upload_is_reported_not_silently_accepted() -> None:
    batch = parse_course_ref_csv(b"", filename="empty.csv")
    assert batch.accepted == []
    assert batch.rejected[0].remedy


def test_all_three_input_forms_agree() -> None:
    """The acceptance criterion, in one place: URL, bare id and CSV give the same course id."""
    from_url = parse_course_ref(f"{BASE}/courses/4410/pages/welcome").course_id
    from_id = parse_course_ref("4410").course_id
    from_csv = parse_course_ref_csv(CSV_IDS).course_ids[0]
    assert from_url == from_id == from_csv == "4410"
