"""D01–D20, the Canvas structure cluster, against clean and defective fixture courses.

Every detector here gets both directions. A detector that fires on everything passes every
defective case and is the one a faculty member dismisses the tool over; a detector that fires on
nothing passes every clean case and is the silent under-detection SPEC §11 is about. Neither half
is optional and neither is enough.

**Examined counts are asserted, not incidental.** A finding carries the number of artifacts the
detector opened to find it, and that number is what makes "0 of 5 graded items carry a due date"
checkable. A detector reporting the right code over the wrong population is wrong in the way that
is hardest to see, so the counts are read back in the assertions rather than left to the framework.

Every course here is synthetic and assembled in code; nothing traces to a real course or person.
"""

from __future__ import annotations

import pytest

from detector_fixtures import (
    COURSE_ID,
    announcement,
    assignment,
    codes,
    course,
    course_file,
    discussion,
    examined,
    group,
    module,
    module_item,
    outcome,
    page,
    quiz,
    refused,
    rubric,
    syllabus,
    tab,
)
from sightline.detect.base import NothingExamined, ResultState, Severity, Tier
from sightline.detect.html_scan import scan
from sightline.detect.links import LinkCheck, LinkState
from sightline.detect.sources import MappingFileSource
from sightline.detect.structure import (
    ActivityInstructions,
    AssessmentScaffolding,
    AssignmentRubric,
    CourseFeedbackMechanism,
    CourseSchedule,
    DiscussionInventory,
    DueDateCoverage,
    ExternalToolInventory,
    FormativeOpportunities,
    GradebookStructure,
    InstructorPresence,
    ModuleOverview,
    NavigationPruning,
    OutcomesDefined,
    PolicyLinks,
    SupportResourceLinks,
    SyllabusPresence,
    SyllabusPrintability,
    TimedAssessmentDisclosure,
    WelcomeAnnouncement,
    count_dates,
    external,
    links_in,
    plain_text,
)

COURSE_ARTIFACT = f"course:{COURSE_ID}"


class StubResolver:
    """A link resolver that answers from a table and records what it was asked.

    Not a network. The point of a resolver interface is that D13 can be driven through every
    branch — resolved, gone, and the one that matters, *could not tell* — without a socket.
    """

    def __init__(self, answers: dict[str, LinkCheck]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def check(self, url: str) -> LinkCheck:
        self.asked.append(url)
        return self.answers.get(
            url, LinkCheck(url=url, state=LinkState.RESOLVED, status=200, detail="200")
        )


def gone(url: str) -> LinkCheck:
    return LinkCheck(url=url, state=LinkState.GONE, status=404, detail="The server answered 404.")


def unknown(url: str) -> LinkCheck:
    return LinkCheck(url=url, state=LinkState.UNKNOWN, detail="The address could not be reached.")


# ---------------------------------------------------------------------------------------------
# Shared reading
# ---------------------------------------------------------------------------------------------


def test_links_are_read_from_anchors_and_from_addresses_written_as_prose() -> None:
    """Canvas linkifies a typed address when it renders, so a detector that only read ``<a href>``
    would report a syllabus that types the help desk's address as linking nothing."""
    found = links_in(
        page(
            "links",
            '<p><a href="https://policies.example.edu/x">policy</a> and also '
            "https://help.example.edu/students for help.</p>",
        )
    )
    assert [entry.url for entry in found] == [
        "https://policies.example.edu/x",
        "https://help.example.edu/students",
    ]


def test_a_trailing_full_stop_is_not_part_of_a_typed_address() -> None:
    found = links_in(page("links", "<p>See https://policies.example.edu/conduct.</p>"))
    assert [entry.url for entry in found] == ["https://policies.example.edu/conduct"]


def test_a_course_relative_link_is_not_external() -> None:
    assert external("https://policies.example.edu/x") is True
    assert external("/courses/4410/pages/start-here") is False
    assert external("mailto:nobody@example.edu") is False


#: Bodies where the seam rule decides the answer. Both helpers are asserted against the same list,
#: because the rule is meant to be one rule with two implementations rather than two rules.
SEAM_CASES = (
    ("<h2>Course schedule</h2><p>Week 1</p>", "Course schedule Week 1"),
    ("<em>un</em>likely", "unlikely"),
    ("<span>Sch</span><span>edule</span>", "Schedule"),
    ("<table><tr><td>Week 1</td><td>Reading</td></tr></table>", "Week 1 Reading"),
    ("a<br>b", "a b"),
    ("<p>Due <strong>Friday</strong>.</p>", "Due Friday."),
    ("a<!--an editor's leftover-->b", "ab"),
    ("<style>p{color:red}</style><p>Text</p>", "Text"),
    ("<p>Caf&eacute;</p>", "Café"),
    ("", ""),
)


def test_prose_is_read_the_way_the_page_renders_it() -> None:
    """The trap this helper exists for, in both directions. A course whose schedule heading sits
    directly against its first row read as ``Course scheduleWeek 1`` and reported as having no
    schedule at all; a space at *every* tag instead would read ``<em>un</em>likely`` as two words
    and match ``\\bun\\b``. Every vocabulary match in this module is written with word boundaries,
    so both halves are wrong output with no error."""
    for html, expected in SEAM_CASES:
        assert plain_text(html) == expected, html
    assert plain_text(None) == ""


def test_the_seam_rule_is_one_rule_and_the_scanner_agrees_with_it() -> None:
    """``plain_text`` reads the string and ``scan`` reads the tree, and they are separate code
    until item 31 decides whether the structure detectors move to the run's shared parse (item 30
    made that parse free to reuse). They are not allowed to be
    separate *rules*: a detector's answer must not depend on which helper it happened to call."""
    for html, expected in SEAM_CASES:
        assert scan(html).text == expected, html


def test_a_week_number_is_not_a_date_and_a_month_and_day_is() -> None:
    """The distinction D04 turns on. A schedule numbered by week is not a dated schedule."""
    assert count_dates("Week 1 Week 2 Week 3") == 0
    assert count_dates("Reflection due January 23") == 1
    assert count_dates("Quiz on 1/30 and the report on 2026-02-06") == 2


# ---------------------------------------------------------------------------------------------
# D01 — syllabus presence
# ---------------------------------------------------------------------------------------------


def test_d01_a_course_with_a_written_syllabus_is_clean() -> None:
    document = course(
        [page("start-here", "<p>Welcome.</p>")],
        syllabus=syllabus(
            "<h2>Syllabus</h2><p>" + "This course runs for fifteen weeks online. " * 6 + "</p>"
        ),
    )
    result = SyllabusPresence().run(document)
    assert result.clean
    assert result.examined == 2  # the syllabus and the one page
    assert result.subjects == 1


def test_d01_a_course_with_no_syllabus_says_so_on_the_course() -> None:
    document = course([page("start-here", "<p>Welcome.</p>")])
    result = SyllabusPresence().run(document)
    assert codes(result) == ["syllabus_absent"]
    finding = result.findings[0]
    assert finding.location.artifact_id == COURSE_ARTIFACT
    assert finding.location.canvas_path == f"/courses/{COURSE_ID}"
    assert finding.severity is Severity.BARRIER
    assert result.examined == 1


def test_d01_counts_the_pages_named_like_a_syllabus_when_there_is_no_syllabus() -> None:
    document = course([page("course-syllabus", "<p>Everything is here instead.</p>")])
    result = SyllabusPresence().run(document)
    assert "1 page(s) in the course are named like a syllabus" in result.findings[0].observed


def test_d01_an_empty_syllabus_body_is_a_different_finding_from_an_absent_one() -> None:
    document = course([page("start-here", "<p>.</p>")], syllabus=syllabus(""))
    result = SyllabusPresence().run(document)
    assert codes(result) == ["syllabus_empty"]
    assert result.findings[0].location.artifact_id == f"syllabus:{COURSE_ID}"


def test_d01_a_syllabus_of_a_dozen_words_is_reported_with_the_length_measured() -> None:
    document = course([], syllabus=syllabus("<p>See the PDF in Files.</p>"))
    result = SyllabusPresence().run(document)
    assert codes(result) == ["syllabus_shorter_than_a_syllabus"]
    finding = result.findings[0]
    assert finding.context["words"] == 5
    assert "5 word(s)" in finding.observed
    assert finding.evidence == "See the PDF in Files."


def test_d01_refuses_a_course_with_nothing_in_it() -> None:
    from sightline.model import CourseDocument, CourseInfo

    empty = CourseDocument(base_url="", course=CourseInfo(id="0", name="empty"))
    with pytest.raises(NothingExamined):
        SyllabusPresence().run(empty)


def test_d01_reports_a_refused_syllabus_rather_than_a_missing_one() -> None:
    """A collection acquisition could not read is not a course without one (SPEC §11)."""
    document = course(examinations=[refused("syllabus"), examined("pages", 0)])
    result = SyllabusPresence().run(document)
    assert result.state is ResultState.NOT_EXAMINED
    assert result.clean is False
    assert "not readable" in (result.not_examined_because or "")


# ---------------------------------------------------------------------------------------------
# D02 — syllabus printability and sectioning
# ---------------------------------------------------------------------------------------------


def test_d02_a_syllabus_with_headings_and_no_attachment_is_clean() -> None:
    document = course(syllabus=syllabus("<h2>Syllabus</h2><h3>Grading</h3><p>Stated.</p>"))
    result = SyllabusPrintability().run(document)
    assert result.clean
    assert result.examined == 1


def test_d02_a_syllabus_with_no_heading_at_all_is_reported() -> None:
    document = course(syllabus=syllabus("<p>Everything runs together in one paragraph.</p>"))
    result = SyllabusPrintability().run(document)
    assert codes(result) == ["syllabus_without_section_headings"]
    assert "no heading element" in result.findings[0].observed


def test_d02_an_attached_pdf_with_no_bytes_is_recorded_as_unread_not_as_clean() -> None:
    """The case the sample course is built to hold open: metadata without bytes."""
    attachment = course_file("5001", "syllabus.pdf", content_type="application/pdf")
    document = course(
        files=[attachment],
        syllabus=syllabus('<h2>S</h2><p><a href="/courses/4410/files/5001">syllabus.pdf</a></p>'),
    )
    result = SyllabusPrintability().run(document)
    assert result.findings == ()
    assert result.clean is False
    assert [entry.location.artifact_id for entry in result.undetermined] == ["file:5001"]
    assert "bytes were not acquired" in result.undetermined[0].reason


def test_d02_an_attached_pdf_with_neither_tags_nor_an_outline_is_reported() -> None:
    from detector_fixtures import pdf

    attachment = course_file("5001", "syllabus.pdf", content_type="application/pdf")
    document = course(files=[attachment], syllabus=syllabus("<h2>S</h2><p>Sections.</p>"))
    source = MappingFileSource({"syllabus.pdf": pdf(tagged=False, text_layer=True)})
    result = SyllabusPrintability().run(document, files=source)
    assert codes(result) == ["syllabus_attachment_without_sections"]
    assert result.findings[0].context["tagged"] is False


def test_d02_a_tagged_attachment_is_clean() -> None:
    from detector_fixtures import pdf

    attachment = course_file("5001", "syllabus.pdf", content_type="application/pdf")
    document = course(files=[attachment], syllabus=syllabus("<h2>S</h2><p>Sections.</p>"))
    source = MappingFileSource({"syllabus.pdf": pdf(tagged=True, text_layer=True)})
    result = SyllabusPrintability().run(document, files=source)
    assert result.clean
    assert result.subjects == 1


def test_d02_ignores_a_pdf_that_is_neither_linked_nor_named_like_a_syllabus() -> None:
    other = course_file("5002", "week2-reading.pdf", content_type="application/pdf")
    document = course(files=[other], syllabus=syllabus("<h2>S</h2><p>Sections.</p>"))
    result = SyllabusPrintability().run(document)
    assert result.clean
    assert result.examined == 2  # the syllabus and the file it considered and passed over


# ---------------------------------------------------------------------------------------------
# D03 — module overviews
# ---------------------------------------------------------------------------------------------


def test_d03_a_module_opening_with_an_overview_is_clean() -> None:
    document = course(
        modules=[
            module(
                "3001",
                "Module 1",
                module_item("31001", "Module 1 Overview", module_id="module:3001"),
                module_item("31002", "Reading", module_id="module:3001", position=2),
            )
        ]
    )
    result = ModuleOverview().run(document)
    assert result.clean
    assert result.examined == 1
    assert result.subjects == 2


def test_d03_a_module_with_no_overview_names_what_it_opens_with() -> None:
    document = course(
        modules=[
            module(
                "3003",
                "Module 3",
                module_item("33001", "slides.pptx", module_id="module:3003", item_type="File"),
            )
        ]
    )
    result = ModuleOverview().run(document)
    assert codes(result) == ["module_without_an_overview_item"]
    finding = result.findings[0]
    assert finding.location.artifact_id == "module:3003"
    assert "'slides.pptx'" in finding.observed
    assert finding.context["items"] == 1


def test_d03_a_sub_heading_is_a_label_and_is_not_counted_as_an_item() -> None:
    document = course(
        modules=[
            module(
                "3004",
                "Module 4",
                module_item("34001", "Overview", module_id="module:3004", item_type="SubHeader"),
                module_item("34002", "Reading", module_id="module:3004", position=2),
            )
        ]
    )
    result = ModuleOverview().run(document)
    # The sub-heading says "Overview" and is not an item, so this module has none.
    assert codes(result) == ["module_without_an_overview_item"]
    assert result.findings[0].context["items"] == 1


def test_d03_reports_each_module_separately() -> None:
    document = course(
        modules=[
            module("3001", "One", module_item("1", "Introduction", module_id="module:3001")),
            module("3002", "Two", module_item("2", "Reading", module_id="module:3002")),
            module("3003", "Three", module_item("3", "Reading", module_id="module:3003")),
        ]
    )
    result = ModuleOverview().run(document)
    assert len(result.findings) == 2
    assert {finding.location.artifact_id for finding in result.findings} == {
        "module:3002",
        "module:3003",
    }
    assert result.examined == 3


# ---------------------------------------------------------------------------------------------
# D04 — the course schedule
# ---------------------------------------------------------------------------------------------


def test_d04_a_dated_schedule_page_is_clean() -> None:
    document = course([page("course-schedule", "<p>Week 1 — January 23. Week 2 — January 30.</p>")])
    result = CourseSchedule().run(document)
    assert result.clean
    assert result.subjects == 1


def test_d04_a_schedule_with_no_dates_is_reported_with_the_word_count() -> None:
    document = course([page("course-schedule", "<p>Week 1, Week 2. Dates announced in class.</p>")])
    result = CourseSchedule().run(document)
    assert codes(result) == ["schedule_carries_no_dates"]
    finding = result.findings[0]
    assert finding.location.artifact_id == "page:course-schedule"
    assert "0 calendar dates" in finding.observed


def test_d04_a_course_with_no_schedule_at_all_says_so_on_the_course() -> None:
    document = course([page("start-here", "<p>Welcome.</p>")])
    result = CourseSchedule().run(document)
    assert codes(result) == ["no_course_schedule_found"]
    assert result.findings[0].location.artifact_id == COURSE_ARTIFACT
    assert result.examined == 1


def test_d04_reads_a_schedule_written_into_the_syllabus() -> None:
    document = course(syllabus=syllabus("<h2>Course schedule</h2><p>Week 1 — March 3.</p>"))
    result = CourseSchedule().run(document)
    assert result.clean
    assert result.subjects == 1


# ---------------------------------------------------------------------------------------------
# D05 — the welcome announcement
# ---------------------------------------------------------------------------------------------


def test_d05_a_welcome_announcement_is_clean() -> None:
    document = course(announcements=[announcement("6001", "Welcome to the course")])
    result = WelcomeAnnouncement().run(document)
    assert result.clean
    assert result.examined == 1


def test_d05_a_reminder_is_not_a_welcome_and_the_finding_quotes_the_earliest() -> None:
    document = course(
        announcements=[
            announcement("6001", "Reminder: quiz Friday", body="<p>It closes on Friday.</p>"),
            announcement(
                "6002", "Second reminder", posted_at="2026-02-02T08:00:00Z", body="<p>Again.</p>"
            ),
        ]
    )
    result = WelcomeAnnouncement().run(document)
    assert codes(result) == ["no_welcome_announcement"]
    finding = result.findings[0]
    assert finding.location.artifact_id == "announcement:6001"
    assert "2 announcement(s)" in finding.observed
    assert finding.evidence == "It closes on Friday."


def test_d05_an_announcement_from_a_previous_offering_is_not_this_terms_welcome() -> None:
    """The trap: a course copied forward carries last year's welcome, which nobody enrolled saw."""
    document = course(
        announcements=[
            announcement("6002", "Welcome to Spring 2024", posted_at="2024-01-08T08:00:00Z"),
            announcement("6001", "Reminder: quiz Friday", posted_at="2026-01-27T08:00:00Z"),
        ]
    )
    result = WelcomeAnnouncement().run(document)
    assert codes(result) == ["no_welcome_announcement"]
    assert result.findings[0].location.artifact_id == "announcement:6001"
    assert "1 announcement(s)" in result.findings[0].observed
    assert result.examined == 2  # both were read; only one counted


def test_d05_a_course_whose_only_announcements_predate_it_says_that_instead() -> None:
    document = course(
        announcements=[announcement("6002", "Old news", posted_at="2024-01-08T08:00:00Z")]
    )
    result = WelcomeAnnouncement().run(document)
    assert codes(result) == ["no_announcement_posted_for_this_offering"]
    assert result.findings[0].location.artifact_id == COURSE_ARTIFACT


def test_d05_a_course_with_no_announcements_is_not_examined_rather_than_clean() -> None:
    document = course([page("start-here", "<p>Welcome.</p>")])
    result = WelcomeAnnouncement().run(document)
    assert result.state is ResultState.NOT_EXAMINED
    assert result.clean is False


# ---------------------------------------------------------------------------------------------
# D06 — navigation pruning
# ---------------------------------------------------------------------------------------------


def _navigation_course(**kwargs: object) -> object:
    return course(
        navigation=[
            tab("home", "Home", position=1),
            tab("modules", "Modules", position=2),
            tab("quizzes", "Quizzes", position=3),
            tab("collaborations", "Collaborations", position=4),
            tab("grades", "Grades", position=5),
        ],
        **kwargs,  # type: ignore[arg-type]
    )


def test_d06_a_menu_whose_entries_all_have_content_reports_only_the_unevidenced_tool() -> None:
    document = _navigation_course(
        modules=[module("3001", "One", module_item("1", "Overview", module_id="module:3001"))],
        quizzes=[quiz("8001")],
    )
    result = NavigationPruning().run(document)
    assert codes(result) == ["navigation_entry_not_evidenced"]
    assert result.findings[0].context["tab"] == "collaborations"
    assert result.examined == 5
    assert result.subjects == 5


def test_d06_an_entry_with_an_empty_collection_behind_it_is_reported_with_the_count() -> None:
    document = _navigation_course(
        modules=[module("3001", "One", module_item("1", "Overview", module_id="module:3001"))]
    )
    result = NavigationPruning().run(document)
    assert sorted(codes(result)) == [
        "navigation_entry_not_evidenced",
        "navigation_entry_with_nothing_behind_it",
    ]
    empty = next(f for f in result.findings if f.code == "navigation_entry_with_nothing_behind_it")
    assert "0 item(s) in quizzes" in empty.observed
    assert empty.location.artifact_id == COURSE_ARTIFACT


def test_d06_a_hidden_entry_is_not_reported_however_empty_it_is() -> None:
    """Hidden is the fix. Reporting a hidden entry would report the remediation as the defect."""
    document = course(navigation=[tab("quizzes", "Quizzes", hidden=True)])
    result = NavigationPruning().run(document)
    assert result.clean
    assert result.subjects == 0


def test_d06_an_external_tool_in_the_menu_is_not_extraneous() -> None:
    document = course(
        navigation=[tab("context_external_tool_7700", "Synthetic Courseware", type="external")]
    )
    result = NavigationPruning().run(document)
    assert result.clean


def test_d06_the_entries_every_course_has_are_never_reported() -> None:
    document = course(
        navigation=[tab(name) for name in ("home", "grades", "people", "settings", "profile")]
    )
    result = NavigationPruning().run(document)
    assert result.clean
    assert result.examined == 5


# ---------------------------------------------------------------------------------------------
# D07 — gradebook structure
# ---------------------------------------------------------------------------------------------


def test_d07_weights_totalling_one_hundred_are_clean() -> None:
    document = course(
        assignment_groups=[
            group("4001", "Assignments", weight=60.0),
            group("4002", "Quizzes", weight=40.0, position=2),
        ]
    )
    result = GradebookStructure().run(document)
    assert result.clean
    assert result.examined == 2


def test_d07_weights_totalling_ninety_are_reported_with_each_weight_named() -> None:
    document = course(
        assignment_groups=[
            group("4001", "Assignments", weight=30.0),
            group("4002", "Quizzes", weight=40.0, position=2),
            group("4003", "Discussions", weight=20.0, position=3),
        ]
    )
    result = GradebookStructure().run(document)
    assert codes(result) == ["group_weights_do_not_total_100"]
    finding = result.findings[0]
    assert finding.context["total"] == 90.0
    assert "Assignments 30, Quizzes 40, Discussions 20" in finding.observed


def test_d07_a_course_that_does_not_weight_its_groups_is_clean() -> None:
    """Weighting off is a supported configuration, not a defect."""
    document = course(
        assignment_groups=[group("4001", "Assignments"), group("4002", "Quizzes", position=2)]
    )
    result = GradebookStructure().run(document)
    assert result.clean


def test_d07_weighting_only_some_groups_is_reported_separately() -> None:
    document = course(
        assignment_groups=[
            group("4001", "Assignments", weight=100.0),
            group("4002", "Quizzes", position=2),
        ]
    )
    result = GradebookStructure().run(document)
    assert codes(result) == ["assignment_group_weighting_is_partial"]
    assert "1 do not (Quizzes)" in result.findings[0].observed


def test_d07_floating_point_noise_does_not_become_a_finding() -> None:
    document = course(
        assignment_groups=[
            group("4001", "A", weight=33.33),
            group("4002", "B", weight=33.33, position=2),
            group("4003", "C", weight=33.34, position=3),
        ]
    )
    result = GradebookStructure().run(document)
    assert result.clean


# ---------------------------------------------------------------------------------------------
# D08 — a rubric on a graded assignment
# ---------------------------------------------------------------------------------------------


def test_d08_a_graded_assignment_with_its_rubric_attached_is_clean() -> None:
    document = course(
        assignments=[assignment("9001", has_rubric=True, rubric_title="Reflection Rubric")],
        rubrics=[rubric("2001", "Reflection Rubric")],
    )
    result = AssignmentRubric().run(document)
    assert result.clean
    assert result.examined == 2
    assert result.subjects == 2


def test_d08_a_graded_assignment_with_no_rubric_is_reported_with_its_points() -> None:
    document = course(assignments=[assignment("9002", "Final Project", points=100.0)])
    result = AssignmentRubric().run(document)
    assert codes(result) == ["graded_assignment_without_a_rubric"]
    finding = result.findings[0]
    assert finding.location.artifact_id == "assignment:9002"
    assert finding.context["points"] == 100.0


def test_d08_an_ungraded_assignment_is_not_expected_to_carry_a_rubric() -> None:
    document = course(
        assignments=[assignment("9004", "Practice", points=0.0, grading_type="not_graded")]
    )
    result = AssignmentRubric().run(document)
    assert result.clean


def test_d08_a_rubric_no_assignment_names_is_reported_as_an_advisory() -> None:
    document = course(
        assignments=[assignment("9001", has_rubric=True, rubric_title="Reflection Rubric")],
        rubrics=[rubric("2001", "Reflection Rubric"), rubric("2002", "Orphaned Rubric")],
    )
    result = AssignmentRubric().run(document)
    assert codes(result) == ["rubric_attached_to_nothing"]
    finding = result.findings[0]
    assert finding.severity is Severity.ADVISORY
    assert "'Orphaned Rubric'" in finding.observed


# ---------------------------------------------------------------------------------------------
# D09 — activity instructions
# ---------------------------------------------------------------------------------------------


def test_d09_activities_that_all_say_what_to_do_are_clean() -> None:
    document = course(
        assignments=[assignment("9001")],
        quizzes=[quiz("8001")],
        discussions=[discussion("7001")],
    )
    result = ActivityInstructions().run(document)
    assert result.clean
    assert result.examined == 3
    assert result.subjects == 3


def test_d09_an_empty_description_is_a_barrier_and_names_the_kind_of_activity() -> None:
    document = course(quizzes=[quiz("8001", body="")])
    result = ActivityInstructions().run(document)
    assert codes(result) == ["activity_without_a_description"]
    finding = result.findings[0]
    assert finding.severity is Severity.BARRIER
    assert finding.context["activity"] == "quiz"


def test_d09_a_graded_assignment_with_nothing_to_submit_is_reported() -> None:
    document = course(assignments=[assignment("9002", submission_types=("none",))])
    result = ActivityInstructions().run(document)
    assert codes(result) == ["graded_assignment_with_no_way_to_submit"]
    assert "submission types: none" in result.findings[0].observed


def test_d09_an_external_tool_submission_is_a_real_way_to_submit() -> None:
    """Counting it as none would report every LTI-submitted assignment as unsubmittable."""
    document = course(assignments=[assignment("9003", submission_types=("external_tool",))])
    result = ActivityInstructions().run(document)
    assert result.clean


def test_d09_an_ungraded_assignment_needs_no_submission_route() -> None:
    document = course(
        assignments=[assignment("9004", grading_type="not_graded", submission_types=(), points=0.0)]
    )
    result = ActivityInstructions().run(document)
    assert result.clean


# ---------------------------------------------------------------------------------------------
# D10 — due-date coverage
# ---------------------------------------------------------------------------------------------


def test_d10_every_graded_item_dated_is_clean() -> None:
    document = course(
        assignments=[assignment("9001")],
        quizzes=[quiz("8001")],
        discussions=[discussion("7002", graded=True, due_at="2026-02-13T23:59:00Z")],
    )
    result = DueDateCoverage().run(document)
    assert result.clean
    assert result.subjects == 3


def test_d10_an_undated_graded_item_carries_the_coverage_count_with_it() -> None:
    document = course(
        assignments=[assignment("9001"), assignment("9002", "Final Project", due_at=None)],
        quizzes=[quiz("8001")],
    )
    result = DueDateCoverage().run(document)
    assert codes(result) == ["graded_item_without_a_due_date"]
    finding = result.findings[0]
    assert finding.location.artifact_id == "assignment:9002"
    assert finding.context == {"graded_items": 3, "dated_items": 2}
    assert "2 of 3 graded item(s)" in finding.observed


def test_d10_a_graded_discussion_reads_the_date_from_the_assignment_behind_it() -> None:
    """Canvas keeps it there and nowhere else. Reading the topic would report every graded
    discussion as undated."""
    document = course(discussions=[discussion("7002", graded=True, due_at=None)])
    result = DueDateCoverage().run(document)
    assert codes(result) == ["graded_item_without_a_due_date"]
    assert "a graded discussion with no due date" in result.findings[0].observed


def test_d10_an_ungraded_discussion_is_not_a_graded_item() -> None:
    document = course(discussions=[discussion("7001", graded=False)])
    result = DueDateCoverage().run(document)
    assert result.state is ResultState.NOT_EXAMINED or result.subjects == 0


def test_d10_a_zero_point_assignment_is_not_counted_as_graded_work() -> None:
    document = course(assignments=[assignment("9001"), assignment("9005", points=0.0, due_at=None)])
    result = DueDateCoverage().run(document)
    assert result.clean
    assert result.subjects == 1


# ---------------------------------------------------------------------------------------------
# D11 — outcomes
# ---------------------------------------------------------------------------------------------


def test_d11_outcomes_with_descriptions_are_clean() -> None:
    document = course(
        modules=[module("3001", "One", module_item("1", "Overview", module_id="module:3001"))],
        outcomes=[outcome("7101", "Describe the sequence"), outcome("7102", "Apply the method")],
    )
    result = OutcomesDefined().run(document)
    assert result.clean
    assert result.examined == 3  # one module plus two outcomes
    assert result.subjects == 2


def test_d11_a_course_with_modules_and_no_outcomes_says_so_on_the_course() -> None:
    document = course(
        modules=[
            module("3001", "One", module_item("1", "Overview", module_id="module:3001")),
            module("3002", "Two", module_item("2", "Overview", module_id="module:3002")),
        ]
    )
    result = OutcomesDefined().run(document)
    assert codes(result) == ["no_outcomes_recorded"]
    finding = result.findings[0]
    assert finding.location.artifact_id == COURSE_ARTIFACT
    assert finding.context["modules"] == 2
    assert "0 learning outcomes across 2 module(s)" in finding.observed


def test_d11_an_outcome_with_an_empty_description_is_reported_on_the_outcome() -> None:
    document = course(
        modules=[module("3001", "One", module_item("1", "Overview", module_id="module:3001"))],
        outcomes=[outcome("7102", "Apply the method", description="")],
    )
    result = OutcomesDefined().run(document)
    assert codes(result) == ["outcome_without_a_description"]
    finding = result.findings[0]
    assert finding.location.artifact_id == "outcome:7102"
    assert finding.location.canvas_path.endswith("/outcomes#outcome_7102")
    assert finding.context["group"] == "Course Outcomes"


# ---------------------------------------------------------------------------------------------
# D12 — external tools
# ---------------------------------------------------------------------------------------------


def _tool_module() -> object:
    return module(
        "3003",
        "Module 3",
        module_item(
            "33003",
            "Synthetic Courseware",
            module_id="module:3003",
            item_type="ExternalTool",
            external_url="https://courseware.example.edu/lti/launch",
        ),
    )


def test_d12_a_tool_the_course_writes_about_is_clean() -> None:
    document = course(
        [page("tools", "<p>We use Synthetic Courseware for the weekly practice sets.</p>")],
        modules=[_tool_module()],  # type: ignore[list-item]
    )
    result = ExternalToolInventory().run(document)
    assert result.clean
    assert result.subjects == 1


def test_d12_a_bare_launch_nothing_mentions_is_reported_with_its_host() -> None:
    document = course(
        [page("start-here", "<p>Work through the modules.</p>")],
        modules=[_tool_module()],  # type: ignore[list-item]
    )
    result = ExternalToolInventory().run(document)
    assert codes(result) == ["external_tool_launch_not_described"]
    finding = result.findings[0]
    assert finding.location.artifact_id == "module_item:33003"
    assert finding.context["host"] == "courseware.example.edu"
    assert "courseware.example.edu" in finding.observed


def test_d12_a_course_with_no_external_tools_is_clean_and_still_examined_the_modules() -> None:
    document = course(
        modules=[module("3001", "One", module_item("1", "Overview", module_id="module:3001"))],
        assignments=[assignment("9001")],
    )
    result = ExternalToolInventory().run(document)
    assert result.clean
    assert result.examined == 2
    assert result.subjects == 0


def test_d12_an_assignment_submitted_through_a_tool_counts_as_a_launch() -> None:
    document = course(assignments=[assignment("9003", "Lab", submission_types=("external_tool",))])
    result = ExternalToolInventory().run(document)
    assert codes(result) == ["external_tool_launch_not_described"]


# ---------------------------------------------------------------------------------------------
# D13 — policy links
# ---------------------------------------------------------------------------------------------

_POLICY_SYLLABUS = (
    "<h2>S</h2><p>Campus policy on academic integrity: "
    '<a href="https://policies.example.edu/academic-integrity">the policy site</a>.</p>'
)


def test_d13_a_syllabus_carrying_a_policy_link_is_clean_with_resolution_off() -> None:
    """Off is the default. Nothing is requested, so nothing is claimed about where the link goes —
    and the presence the criterion asks for is still established."""
    document = course(syllabus=syllabus(_POLICY_SYLLABUS))
    result = PolicyLinks().run(document)
    assert result.clean
    assert result.subjects == 1
    assert result.undetermined == ()


def test_d13_a_course_linking_no_policy_at_all_is_reported_on_the_syllabus() -> None:
    document = course(syllabus=syllabus("<h2>S</h2><p>Work hard.</p>"))
    result = PolicyLinks().run(document)
    assert codes(result) == ["no_policy_links_found"]
    assert result.findings[0].location.artifact_id == f"syllabus:{COURSE_ID}"


def test_d13_with_no_syllabus_the_absence_is_reported_on_the_course() -> None:
    document = course([page("start-here", "<p>Nothing here.</p>")])
    result = PolicyLinks().run(document)
    assert codes(result) == ["no_policy_links_found"]
    assert result.findings[0].location.artifact_id == COURSE_ARTIFACT


def test_d13_a_link_the_far_end_says_is_gone_is_a_finding() -> None:
    url = "https://policies.example.edu/academic-integrity"
    document = course(syllabus=syllabus(_POLICY_SYLLABUS))
    resolver = StubResolver({url: gone(url)})
    result = PolicyLinks().run(document, links=resolver)
    assert codes(result) == ["policy_link_is_gone"]
    assert result.findings[0].context["status"] == 404
    assert result.findings[0].evidence == url
    assert resolver.asked == [url]


def test_d13_a_link_that_could_not_be_reached_is_unread_and_never_a_broken_link() -> None:
    """The finding this detector exists to avoid. A DNS timeout is not a dead policy page."""
    url = "https://policies.example.edu/academic-integrity"
    document = course(syllabus=syllabus(_POLICY_SYLLABUS))
    result = PolicyLinks().run(document, links=StubResolver({url: unknown(url)}))
    assert result.findings == ()
    assert len(result.undetermined) == 1
    assert "could not be checked" in result.undetermined[0].reason
    assert result.clean is False


def test_d13_a_link_that_resolves_is_clean() -> None:
    document = course(syllabus=syllabus(_POLICY_SYLLABUS))
    result = PolicyLinks().run(document, links=StubResolver({}))
    assert result.clean


def test_d13_does_not_request_a_course_relative_link() -> None:
    body = '<h2>S</h2><p><a href="/courses/4410/pages/policy">the course policy page</a></p>'
    document = course(syllabus=syllabus(body))
    resolver = StubResolver({})
    result = PolicyLinks().run(document, links=resolver)
    assert resolver.asked == []
    assert result.clean


# ---------------------------------------------------------------------------------------------
# D14 — learner support links
# ---------------------------------------------------------------------------------------------


def test_d14_a_syllabus_linking_support_services_is_clean() -> None:
    body = '<h2>S</h2><p><a href="https://library.example.edu">the library</a></p>'
    document = course(syllabus=syllabus(body))
    result = SupportResourceLinks().run(document)
    assert result.clean
    assert result.subjects == 1


def test_d14_a_course_linking_none_anywhere_is_reported_with_what_was_looked_for() -> None:
    document = course([page("start-here", "<p>Nothing.</p>")], syllabus=syllabus("<p>Nothing.</p>"))
    result = SupportResourceLinks().run(document)
    assert codes(result) == ["no_support_resource_links_found"]
    assert "tutoring, the library, advising" in result.findings[0].observed


def test_d14_support_links_elsewhere_but_not_in_the_syllabus_is_its_own_advisory() -> None:
    document = course(
        [page("start-here", '<p><a href="https://tutoring.example.edu">tutoring</a></p>')],
        syllabus=syllabus("<h2>S</h2><p>Work hard.</p>"),
    )
    result = SupportResourceLinks().run(document)
    assert codes(result) == ["syllabus_links_no_support_resources"]
    finding = result.findings[0]
    assert finding.severity is Severity.ADVISORY
    assert finding.location.artifact_id == f"syllabus:{COURSE_ID}"
    assert finding.context["elsewhere"] == 1
    assert "tutoring.example.edu" in finding.observed


def test_d14_a_course_with_no_syllabus_at_all_reports_only_the_absence() -> None:
    document = course([page("start-here", '<p><a href="https://library.example.edu">lib</a></p>')])
    result = SupportResourceLinks().run(document)
    assert result.clean


# ---------------------------------------------------------------------------------------------
# D15 — timed assessment disclosure
# ---------------------------------------------------------------------------------------------


def test_d15_a_quiz_whose_description_mentions_the_limit_is_clean() -> None:
    document = course(
        quizzes=[quiz("8001", time_limit=45, body="<p>You have 45 minutes once you begin.</p>")]
    )
    result = TimedAssessmentDisclosure().run(document)
    assert result.clean
    assert result.subjects == 1


def test_d15_an_undisclosed_limit_quotes_the_description_and_says_what_was_read() -> None:
    document = course(quizzes=[quiz("8001", time_limit=45, body="<p>Ten questions.</p>")])
    result = TimedAssessmentDisclosure().run(document)
    assert codes(result) == ["time_limit_not_mentioned_in_the_description"]
    finding = result.findings[0]
    assert finding.tier is Tier.B
    assert finding.context["time_limit_minutes"] == 45
    assert finding.evidence == "Ten questions."
    assert "Only this assessment's own description was read." in finding.observed


def test_d15_a_quiz_with_no_time_limit_is_not_its_business() -> None:
    document = course(quizzes=[quiz("8001", time_limit=None, body="<p>Ten questions.</p>")])
    result = TimedAssessmentDisclosure().run(document)
    assert result.clean
    assert result.examined == 1
    assert result.subjects == 0


def test_d15_an_empty_description_says_so_rather_than_quoting_nothing() -> None:
    document = course(quizzes=[quiz("8001", time_limit=30, body="")])
    result = TimedAssessmentDisclosure().run(document)
    assert result.findings[0].evidence == "(the description holds no text)"


# ---------------------------------------------------------------------------------------------
# D16 — low-stakes practice
# ---------------------------------------------------------------------------------------------


def test_d16_a_practice_quiz_makes_the_course_clean() -> None:
    document = course(quizzes=[quiz("8001"), quiz("8002", quiz_type="practice_quiz", points=0.0)])
    result = FormativeOpportunities().run(document)
    assert result.clean
    assert result.subjects == 2


def test_d16_a_course_where_everything_counts_states_the_rule_it_applied() -> None:
    document = course(quizzes=[quiz("8001")], assignments=[assignment("9001")])
    result = FormativeOpportunities().run(document)
    assert codes(result) == ["no_low_stakes_assessment_found"]
    finding = result.findings[0]
    assert finding.location.artifact_id == COURSE_ARTIFACT
    assert finding.context["assessments"] == 2
    assert "practice quiz, a survey, or an assignment worth zero points" in finding.observed


def test_d16_a_zero_point_assignment_counts_as_low_stakes() -> None:
    document = course(assignments=[assignment("9001"), assignment("9005", points=0.0)])
    result = FormativeOpportunities().run(document)
    assert result.clean


# ---------------------------------------------------------------------------------------------
# D17 — the discussion inventory
# ---------------------------------------------------------------------------------------------


def test_d17_discussions_with_prompts_are_clean() -> None:
    document = course(discussions=[discussion("7001"), discussion("7002", graded=True)])
    result = DiscussionInventory().run(document)
    assert result.clean
    assert result.examined == 2


def test_d17_a_discussion_with_no_prompt_carries_the_inventory_in_the_finding() -> None:
    document = course(discussions=[discussion("7001", body=""), discussion("7002", graded=True)])
    result = DiscussionInventory().run(document)
    assert codes(result) == ["discussion_without_a_prompt"]
    assert "2 discussion(s), 1 of them graded" in result.findings[0].observed


def test_d17_a_course_with_no_discussions_is_not_examined_rather_than_clean() -> None:
    document = course([page("start-here", "<p>.</p>")])
    result = DiscussionInventory().run(document)
    assert result.state is ResultState.NOT_EXAMINED
    assert result.clean is False


# ---------------------------------------------------------------------------------------------
# D18 — instructor presence
# ---------------------------------------------------------------------------------------------


def test_d18_a_page_titled_for_the_instructor_is_clean() -> None:
    document = course([page("about-your-instructor", "<p>Fifteen years of synthetic studies.</p>")])
    result = InstructorPresence().run(document)
    assert result.clean
    assert result.examined == 1


def test_d18_an_introduction_written_into_a_body_is_found_without_a_matching_title() -> None:
    document = course([page("start-here", "<p>My name is the synthetic instructor.</p>")])
    result = InstructorPresence().run(document)
    assert result.clean


def test_d18_a_course_with_nothing_of_the_kind_says_how_much_it_read() -> None:
    document = course(
        [page("start-here", "<p>Work through the modules.</p>")],
        syllabus=syllabus("<h2>S</h2><p>Grades are posted in the gradebook.</p>"),
        announcements=[announcement("6001")],
        discussions=[discussion("7001")],
    )
    result = InstructorPresence().run(document)
    assert codes(result) == ["no_instructor_introduction_found"]
    finding = result.findings[0]
    assert finding.tier is Tier.B
    assert finding.context["artifacts"] == 4
    assert "4 artifact(s) read" in finding.observed


def test_d18_office_hours_mentioned_in_passing_are_not_an_introduction() -> None:
    document = course([page("start-here", "<p>Office hours are listed on the Modules page.</p>")])
    result = InstructorPresence().run(document)
    assert codes(result) == ["no_instructor_introduction_found"]


# ---------------------------------------------------------------------------------------------
# D19 — scaffolding on a long assessment
# ---------------------------------------------------------------------------------------------


def test_d19_a_large_assessment_with_a_draft_beside_it_is_clean() -> None:
    document = course(
        assignments=[
            assignment("9002", "Final Project", points=100.0),
            assignment("9006", "Final Project Draft", points=10.0),
        ]
    )
    result = AssessmentScaffolding().run(document)
    assert result.clean
    assert result.subjects == 2


def test_d19_a_large_assessment_with_no_staged_parts_reports_the_share_it_carries() -> None:
    document = course(
        assignments=[assignment("9002", "Final Project", points=100.0, body="<p>Hand it in.</p>")],
        quizzes=[quiz("8001", points=20.0)],
    )
    result = AssessmentScaffolding().run(document)
    assert codes(result) == ["high_stakes_assessment_without_staged_parts"]
    finding = result.findings[0]
    assert finding.tier is Tier.B
    assert finding.context["points"] == 100.0
    assert finding.context["share"] == pytest.approx(100 / 120, rel=1e-3)
    assert "83% of the course" in finding.observed
    assert finding.evidence == "Hand it in."


def test_d19_an_assessment_below_a_quarter_of_the_grade_is_not_prolonged() -> None:
    document = course(
        assignments=[
            assignment("9001", "One", points=20.0),
            assignment("9002", "Two", points=20.0),
            assignment("9003", "Three", points=20.0),
            assignment("9004", "Four", points=20.0),
            assignment("9005", "Five", points=20.0),
        ]
    )
    result = AssessmentScaffolding().run(document)
    assert result.clean


def test_d19_staging_described_in_the_instructions_counts() -> None:
    document = course(
        assignments=[
            assignment(
                "9002",
                "Final Project",
                points=100.0,
                body="<p>Submit a proposal in week 4 and a draft in week 8.</p>",
            )
        ]
    )
    result = AssessmentScaffolding().run(document)
    assert result.clean


def test_d19_a_course_with_no_graded_points_reports_nothing() -> None:
    document = course(assignments=[assignment("9004", points=0.0, grading_type="not_graded")])
    result = AssessmentScaffolding().run(document)
    assert result.clean


# ---------------------------------------------------------------------------------------------
# D20 — a route for course feedback
# ---------------------------------------------------------------------------------------------


def test_d20_an_ungraded_survey_is_a_feedback_route() -> None:
    document = course(quizzes=[quiz("8003", "Mid-term check-in", quiz_type="ungraded_survey")])
    result = CourseFeedbackMechanism().run(document)
    assert result.clean
    assert result.examined == 1


def test_d20_a_discussion_named_for_feedback_is_a_feedback_route() -> None:
    document = course(quizzes=[quiz("8001")], discussions=[discussion("7003", "Course feedback")])
    result = CourseFeedbackMechanism().run(document)
    assert result.clean


def test_d20_a_course_with_none_of_them_says_what_it_read() -> None:
    document = course(
        [page("start-here", "<p>Work through the modules.</p>")],
        quizzes=[quiz("8001")],
        discussions=[discussion("7001")],
        announcements=[announcement("6001")],
    )
    result = CourseFeedbackMechanism().run(document)
    assert codes(result) == ["no_course_feedback_mechanism_found"]
    finding = result.findings[0]
    assert finding.location.artifact_id == COURSE_ARTIFACT
    assert finding.context["artifacts"] == 4
    assert "quizzes, discussions, pages and announcements" in finding.observed


def test_d20_a_graded_quiz_is_not_a_feedback_route() -> None:
    document = course(quizzes=[quiz("8001", "Module 2 Quiz")])
    result = CourseFeedbackMechanism().run(document)
    assert codes(result) == ["no_course_feedback_mechanism_found"]
