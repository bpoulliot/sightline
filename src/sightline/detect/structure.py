"""D01–D20: the Canvas structure cluster.

These twenty read what the Canvas API knows about a course's *shape* — its syllabus, modules,
navigation menu, assignment groups, due dates, outcomes, external tools — rather than the markup
inside its pages, which is item 06's half. Between them they answer the questions a designer would
otherwise open twelve Canvas screens to answer.

**Three things about a structural detector differ from a content one, and all three are here on
purpose.**

*The interesting answer is often an absence.* No syllabus, no welcome announcement, no way for a
learner to give feedback. An absence still has to be a finding with a location a reviewer can open,
so a course-level finding is located on the course itself through
:meth:`~sightline.detect.base.Survey.course_location`. That builds a location and hands out no
artifact, so it cannot be used to fake an examined count: every detector here still reaches a real
collection, and every one of them is refused by the framework over a course holding nothing.

*What a detector cannot see, it says.* Canvas exposes no content behind Collaborations, Chat or
Attendance, so D06 reports those menu entries as unevidenced rather than as unused, in those words.
D11 reads outcomes and their groups because that is what Canvas records; it does not infer an
outcome's relationship to a module, because Canvas keeps none. D15 reads an assessment's own
description and says so, rather than claiming the time limit is disclosed nowhere in the course.

*A rule is stated in the finding.* "No low-stakes assessment" means something precise — no practice
quiz, no survey, no zero-point or ungraded assignment — and the finding says which of those it
looked for, so a reviewer who disagrees with the rule can see the rule rather than guess at it.

**Three of these are tier B** (D15, D18, D19). Each looks for a passage that would settle the
question and reports what it found or that it found none, worded as evidence. They are the same
contract as the model-backed extractors of SPEC §05 and a stricter form of it, because a parser
cannot invent a quote.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlsplit

from ..model import (
    Artifact,
    Assignment,
    CourseFile,
    Discussion,
    Module,
    ModuleItem,
    Syllabus,
)
from .base import Detector, Finding, Severity, Survey, Tier
from .documents import inspect_pdf
from .html_scan import HEADING_TAGS, INLINE_TAGS, Scan, excerpt, scan, verbatim
from .links import LinkState

# ---------------------------------------------------------------------------------------------
# Shared reading
# ---------------------------------------------------------------------------------------------

#: An address written as text rather than marked up. Canvas linkifies these when it renders, so a
#: policy link typed into a page is a policy link a learner can follow.
_BARE_URL = re.compile(r"https?://[^\s<>\"')]+")

#: Punctuation a bare URL commonly ends a sentence with, and which is not part of the address.
_URL_TAIL = ".,;:!?)]}’\"'"


@dataclass(frozen=True, slots=True)
class Link:
    """One address found in a body, and the text a learner sees for it."""

    url: str
    label: str
    line: int


#: A ``<script>`` or ``<style>`` block, whose contents are not prose whatever they look like.
_SCRIPTISH = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
#: Any tag, with its name captured where it has one. Replaced by :func:`_seam` rather than removed.
_ANY_TAG = re.compile(r"</?([a-zA-Z][^\s/>]*)?[^>]*>")


def _seam(match: re.Match[str]) -> str:
    """What one tag leaves behind: a space where the page breaks, nothing where it does not."""
    if match.group(0).startswith("<!"):
        # A comment or a doctype renders as nothing at all, so it separates nothing either.
        return ""
    name = match.group(1)
    return "" if name and name.lower() in INLINE_TAGS else " "


def plain_text(html: str | None) -> str:
    r"""The prose of a body, read the way the page renders it, without building a tree.

    The seam rule is :data:`~sightline.detect.html_scan.INLINE_TAGS` and it is imported rather than
    restated: a space goes where the rendered page breaks one run of text from the next, and
    nothing goes where a tag falls inside a word. So ``<h2>Course schedule</h2><p>Week 1</p>``
    reads ``Course schedule Week 1`` — every detector in this module matches vocabulary with word
    boundaries, and a ``\bschedule\b`` that fails at a block seam is a course reported as having no
    schedule when it has one — while the span soup a word processor pastes into Canvas,
    ``<span>Sch</span><span>edule</span>``, reads ``Schedule`` and matches the same regex. Both
    directions are the same defect (SPEC §11); item 29 fixed them together.

    **What this does that** :func:`~sightline.detect.html_scan.scan` **does not is no longer
    anything — it only does it without a parse.** The two now agree on every seam, on entities, and
    on dropping script and style contents, checked case by case against the scanner. Two things the
    scanner still reads better, both because this is a regex and not a parser: a ``>`` inside a
    quoted attribute value ends a tag here and does not there, and an *unclosed* ``<script>`` leaves
    its code in the prose here and is dropped there. Neither is a seam; both are why the scanner is
    the one to reach for when a detector needs the tree anyway.

    What kept this function was the parse it avoided, and since roadmap item 30 there is no such
    parse to avoid: a run parses each body once and shares it through ``Survey.parse``, so a
    detector here reading ``survey.parse(artifact).text`` costs no extra parse. Whether they
    should is item 31's change to make, not this docstring's.

    Script and style blocks go first, because their contents are not prose however much CSS looks
    like words when the tags come off.
    """
    if not html:
        return ""
    stripped = _ANY_TAG.sub(_seam, _SCRIPTISH.sub(" ", html))
    return " ".join(unescape(stripped).split())


def body_text(artifact: Artifact | None) -> str:
    """The prose of an artifact's body, whitespace-normalized. Empty for a body-less artifact."""
    if artifact is None:
        return ""
    return plain_text(artifact.body_html)


def links_in(artifact: Artifact | None, page: Scan | None = None) -> tuple[Link, ...]:
    """Every address in a body: marked-up anchors first, then addresses written as prose.

    Both halves are needed and the second is the one that gets missed. A syllabus that types the
    help desk's address into a paragraph has linked the help desk as far as a learner is concerned,
    and a detector that only read ``<a href>`` would report it as absent.

    ``page`` is the body's parse when the caller already holds it — a detector passes
    ``survey.parse(artifact)``, the run's shared parse — and is parsed here when omitted.
    """
    if artifact is None or not artifact.body_html:
        return ()
    if page is None:
        page = scan(artifact.body_html)
    found: list[Link] = []
    for anchor in page.of("a"):
        href = (anchor.attr("href") or "").strip()
        if href:
            found.append(Link(url=href, label=anchor.text or href, line=anchor.line))
    for line, run in page.unlinked_runs:
        for match in _BARE_URL.finditer(run):
            url = match.group(0).rstrip(_URL_TAIL)
            found.append(Link(url=url, label=url, line=line))
    return tuple(found)


def external(url: str) -> bool:
    """Whether an address leaves the Canvas instance.

    A course-relative path never does. A scheme-bearing address is treated as external, which is
    the safe direction: the Canvas host is configuration a detector is not given (SPEC §11), so a
    detector cannot tell an absolute link back into Canvas from one that leaves it, and treating
    an internal link as external costs a reviewer one glance while the reverse hides a dead link.
    """
    return bool(urlsplit(url).scheme) and urlsplit(url).scheme.lower() in {"http", "https"}


def matches(pattern: re.Pattern[str], *texts: str | None) -> bool:
    return any(pattern.search(text) for text in texts if text)


def has_heading(artifact: Artifact | None) -> bool:
    return bool(artifact and artifact.body_html and scan(artifact.body_html).of(*HEADING_TAGS))


def module_items(module: Module) -> tuple[ModuleItem, ...]:
    """A module's items with the sub-headings dropped — a heading is a label, not content."""
    return tuple(item for item in module.items if item.item_type != "SubHeader")


def graded_points(assignment: Assignment) -> float:
    return assignment.points_possible or 0.0


# ---------------------------------------------------------------------------------------------
# D01 — syllabus present and non-empty
# ---------------------------------------------------------------------------------------------

#: A page standing in for the syllabus. Courses do this often enough that a detector calling the
#: course syllabus-less because the Syllabus screen is blank would be wrong most of the time.
_SYLLABUS_PAGE = re.compile(r"\bsyllabus\b|\bcourse\s+information\b", re.IGNORECASE)

#: Below this many words, a syllabus body is a pointer rather than a syllabus. Reported as a
#: measured length, never as a judgement about the text.
_PLACEHOLDER_WORDS = 40


class SyllabusPresence(Detector):
    id = "D01"
    title = "Whether the course has a syllabus, and whether it says anything"
    tier = Tier.A
    collections = ("syllabus", "pages")
    capability_tag = "syllabus_presence"
    capability_description = (
        "Whether a syllabus exists on the course's Syllabus screen at all, whether its body is "
        "empty, and how many words it holds when it is not."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        syllabus = survey.syllabus()
        stand_ins = [
            page for page in survey.pages() if matches(_SYLLABUS_PAGE, page.title, page.slug)
        ]
        survey.counted(1 if syllabus is not None else 0)

        if syllabus is None:
            observed = (
                f"no syllabus body on the course's Syllabus screen; "
                f"{len(stand_ins)} page(s) in the course are named like a syllabus"
            )
            yield survey.finding(
                code="syllabus_absent",
                severity=Severity.BARRIER,
                location=survey.course_location(within="Syllabus"),
                observed=observed,
            )
            return

        text = body_text(syllabus)
        location = survey.location(syllabus)
        if not text:
            yield survey.finding(
                code="syllabus_empty",
                severity=Severity.BARRIER,
                location=location,
                observed="the Syllabus screen exists and its body holds no text",
            )
            return

        words = len(text.split())
        if words < _PLACEHOLDER_WORDS:
            yield survey.finding(
                code="syllabus_shorter_than_a_syllabus",
                severity=Severity.FRICTION,
                location=location,
                observed=f"{words} word(s), below the {_PLACEHOLDER_WORDS} this check reads as a "
                "pointer rather than a syllabus",
                evidence=verbatim(text),
                words=words,
            )


# ---------------------------------------------------------------------------------------------
# D02 — syllabus printable, and divided into sections
# ---------------------------------------------------------------------------------------------

#: Extensions a printable copy of a syllabus arrives as.
_PRINTABLE = (".pdf", ".doc", ".docx", ".odt", ".rtf")


class SyllabusPrintability(Detector):
    id = "D02"
    title = (
        "Whether the syllabus is divided into headed sections, and what an attached printable "
        "copy carries"
    )
    tier = Tier.A
    collections = ("syllabus", "files")
    capability_tag = "syllabus_printability"
    capability_description = (
        "Whether the syllabus body uses real headings to divide itself into sections, and — for "
        "an attached printable copy — whether that file carries a tag tree or an outline."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        syllabus = survey.syllabus()
        attachments = self._attached_copies(survey, syllabus)
        survey.counted(len(attachments))

        if syllabus is not None and syllabus.body_html:
            page = survey.parse(syllabus)
            headings = page.of(*HEADING_TAGS)
            if page.text and not headings:
                yield survey.finding(
                    code="syllabus_without_section_headings",
                    severity=Severity.FRICTION,
                    location=survey.location(syllabus),
                    observed=(
                        f"{page.words} word(s) of syllabus with no heading element, so a printed "
                        "or saved copy carries no section structure"
                    ),
                )

        for attachment in attachments:
            yield from self._attachment(survey, attachment)

    def _attached_copies(self, survey: Survey, syllabus: Syllabus | None) -> tuple[CourseFile, ...]:
        """Files the syllabus links, plus any file named like a syllabus.

        Both, because the two miss different courses: a syllabus that links its PDF is found by the
        first, and a course that uploads ``syllabus.pdf`` and links it from Modules by the second.
        """
        found: dict[str, CourseFile] = {}
        page = survey.parse(syllabus) if syllabus is not None and syllabus.body_html else None
        for link in links_in(syllabus, page):
            candidate = survey.find_file(link.url)
            if candidate is not None and candidate.extension in _PRINTABLE:
                found[candidate.id] = candidate
        for entry in survey.files(*_PRINTABLE):
            if "syllabus" in entry.filename.lower():
                found[entry.id] = entry
        return tuple(sorted(found.values(), key=lambda item: item.id))

    def _attachment(self, survey: Survey, attachment: CourseFile) -> Iterator[Finding]:
        location = survey.location(attachment)
        if attachment.extension != ".pdf":
            return
        data = survey.read(attachment)
        if data is None:
            survey.undetermined(
                location,
                reason=(
                    f"{attachment.filename} is the syllabus's printable copy and its bytes were "
                    "not acquired, so whether it carries sections could not be read."
                ),
                remedy=(
                    "Run the review with a content export so the file's own bytes are available."
                ),
            )
            return
        report = inspect_pdf(data)
        if not report.readable:
            survey.undetermined(
                location,
                reason=f"{attachment.filename} could not be read: {report.reason}.",
            )
            return
        if not report.tagged and not report.has_outline:
            yield survey.finding(
                code="syllabus_attachment_without_sections",
                severity=Severity.FRICTION,
                location=location,
                observed=(
                    f"{report.pages} page(s), no tag tree and no document outline, so the saved "
                    "copy carries no section structure"
                ),
                pages=report.pages,
                tagged=report.tagged,
                outline=report.has_outline,
            )


# ---------------------------------------------------------------------------------------------
# D03 — a module overview per module
# ---------------------------------------------------------------------------------------------

_OVERVIEW = re.compile(
    r"\boverview\b|\bintroduction\b|\bintro\b|\bstart\s+here\b|\bread\s+me\b|\bwelcome\b"
    r"|\bobjectives\b|\bwhat\s+you.{0,3}ll\s+learn\b|\bat\s+a\s+glance\b|\bchecklist\b",
    re.IGNORECASE,
)


class ModuleOverview(Detector):
    id = "D03"
    title = "Modules with no item that reads as an overview of the module"
    tier = Tier.A
    collections = ("modules",)
    capability_tag = "module_overview"
    capability_description = (
        "Whether each module holds an item named as an overview, introduction or objectives "
        "listing, and where in the module it sits."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for module in survey.modules():
            items = module_items(module)
            survey.counted(len(items))
            named = [item for item in items if matches(_OVERVIEW, item.title)]
            if named:
                continue
            first = items[0].title if items else "nothing"
            yield survey.finding(
                code="module_without_an_overview_item",
                severity=Severity.FRICTION,
                location=survey.location(module),
                observed=(
                    f"{len(items)} item(s), none named as an overview, introduction or "
                    f"objectives listing; the module opens with {first!r}"
                ),
                items=len(items),
            )


# ---------------------------------------------------------------------------------------------
# D04 — a course schedule carrying dates
# ---------------------------------------------------------------------------------------------

_SCHEDULE = re.compile(
    r"\bschedule\b|\bcalendar\b|\bcourse\s+outline\b|\bimportant\s+dates\b|\bdue\s+dates\b",
    re.IGNORECASE,
)

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
)
#: What counts as a date on a schedule. Deliberately three shapes and no more: a number in a table
#: cell is not a date, and a detector that counted one would call every week-numbered schedule
#: dated.
_DATE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"\b(?:{_MONTHS})\b\.?\s+\d{{1,2}}\b", re.IGNORECASE),
    re.compile(rf"\b\d{{1,2}}\s+(?:{_MONTHS})\b", re.IGNORECASE),
    re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
)


def count_dates(text: str) -> int:
    return sum(len(pattern.findall(text)) for pattern in _DATE_PATTERNS)


class CourseSchedule(Detector):
    id = "D04"
    title = "Whether the course has a schedule, and whether dates are written on it"
    tier = Tier.A
    collections = ("pages", "syllabus")
    capability_tag = "course_schedule"
    capability_description = (
        "Whether an artifact named as a schedule, calendar or course outline exists, and how many "
        "calendar dates are written on it."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        candidates: list[Artifact] = [
            page for page in survey.pages() if matches(_SCHEDULE, page.title, page.slug)
        ]
        syllabus = survey.syllabus()
        if syllabus is not None and matches(_SCHEDULE, body_text(syllabus)):
            candidates.append(syllabus)
        survey.counted(len(candidates))

        if not candidates:
            yield survey.finding(
                code="no_course_schedule_found",
                severity=Severity.FRICTION,
                location=survey.course_location(),
                observed=(
                    "no page or syllabus named as a schedule, calendar, course outline or "
                    "important dates"
                ),
            )
            return

        for candidate in candidates:
            text = body_text(candidate)
            dates = count_dates(text)
            if dates:
                continue
            yield survey.finding(
                code="schedule_carries_no_dates",
                severity=Severity.FRICTION,
                location=survey.location(candidate),
                observed=(
                    f"{len(text.split())} word(s) of schedule and 0 calendar dates "
                    "(a month and day, a d/m or m/d pair, or an ISO date)"
                ),
                evidence=excerpt(text),
            )


# ---------------------------------------------------------------------------------------------
# D05 — a welcome announcement
# ---------------------------------------------------------------------------------------------

_WELCOME = re.compile(
    r"\bwelcome\b|\bgetting\s+started\b|\bstart\s+here\b|\bintroduction\s+to\s+the\s+course\b"
    r"|\bcourse\s+opens?\b|\bfirst\s+week\b",
    re.IGNORECASE,
)


class WelcomeAnnouncement(Detector):
    id = "D05"
    title = "Whether an announcement posted for this offering welcomes learners"
    tier = Tier.A
    collections = ("announcements",)
    capability_tag = "welcome_announcement"
    capability_description = (
        "Whether any announcement posted on or after the course start date reads as a welcome, "
        "and which announcement came first if none does."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        start = (survey.course().start_at or "")[:10]
        current: list[Discussion] = []
        for announcement in survey.announcements():
            posted = (announcement.posted_at or announcement.updated_at or "")[:10]
            # An announcement predating the course start belongs to a previous offering. Counting
            # one as this term's welcome would close the row on a message nobody enrolled has seen.
            if start and posted and posted < start:
                continue
            current.append(announcement)
        survey.counted(len(current))

        if any(matches(_WELCOME, item.title, body_text(item)) for item in current):
            return

        if not current:
            yield survey.finding(
                code="no_announcement_posted_for_this_offering",
                severity=Severity.FRICTION,
                location=survey.course_location(within="Announcements"),
                observed=(
                    f"no announcement posted on or after the course start date "
                    f"({start or 'no start date recorded'})"
                ),
            )
            return

        earliest = min(current, key=lambda item: (item.posted_at or item.updated_at or ""))
        yield survey.finding(
            code="no_welcome_announcement",
            severity=Severity.FRICTION,
            location=survey.location(earliest),
            observed=(
                f"{len(current)} announcement(s) posted for this offering and none reads as a "
                f"welcome; the earliest is {earliest.title!r}"
            ),
            evidence=excerpt(body_text(earliest)),
        )


# ---------------------------------------------------------------------------------------------
# D06 — navigation pruned of unused tools
# ---------------------------------------------------------------------------------------------

#: Menu entry -> the collection whose emptiness means nothing sits behind it.
_TAB_COLLECTIONS = {
    "announcements": "announcements",
    "assignments": "assignments",
    "discussions": "discussions",
    "files": "files",
    "modules": "modules",
    "outcomes": "outcomes",
    "pages": "pages",
    "quizzes": "quizzes",
    "rubrics": "rubrics",
    "syllabus": "syllabus",
}

#: Menu entries Canvas exposes no course content for. Sightline cannot tell a used one from an
#: unused one and says exactly that rather than guessing in either direction.
_TAB_UNEVIDENCED = {
    "attendance",
    "chat",
    "collaborations",
    "conferences",
}

#: Menu entries every course has and no course should remove. Not reported at all.
_TAB_STRUCTURAL = {"home", "grades", "people", "settings", "profile", "search"}


class NavigationPruning(Detector):
    id = "D06"
    title = "Course navigation entries visible to learners with nothing behind them"
    tier = Tier.A
    collections = ("tabs",)
    capability_tag = "navigation_pruning"
    capability_description = (
        "Which navigation entries a learner sees, which of them have no course content behind "
        "them, and which are tools whose contents Canvas does not expose."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        shown = 0
        for tab in survey.navigation():
            if tab.hidden:
                continue
            shown += 1
            identifier = tab.id.lower()
            if identifier in _TAB_STRUCTURAL:
                continue
            # An external tool in the menu was installed deliberately by somebody. Reporting it as
            # extraneous would turn every LTI integration into a finding.
            if (tab.type or "").lower() == "external" or identifier.startswith(
                "context_external_tool"
            ):
                continue
            location = survey.course_location(within=f"navigation — {tab.label}")
            collection = _TAB_COLLECTIONS.get(identifier)
            if collection is not None:
                held = survey.population(collection)
                if held == 0:
                    yield survey.finding(
                        code="navigation_entry_with_nothing_behind_it",
                        severity=Severity.ADVISORY,
                        location=location,
                        observed=(
                            f"{tab.label!r} is visible to learners and the course holds 0 "
                            f"item(s) in {collection}"
                        ),
                        tab=tab.id,
                    )
                continue
            if identifier in _TAB_UNEVIDENCED:
                yield survey.finding(
                    code="navigation_entry_not_evidenced",
                    severity=Severity.ADVISORY,
                    location=location,
                    observed=(
                        f"{tab.label!r} is visible to learners and Canvas exposes no content for "
                        "this tool, so whether the course uses it was not established"
                    ),
                    tab=tab.id,
                )
        survey.counted(shown)


# ---------------------------------------------------------------------------------------------
# D07 — gradebook structure and group weights
# ---------------------------------------------------------------------------------------------

#: Weights are floats out of a Canvas form. A total of 99.999999 is 100.
_WEIGHT_TOLERANCE = 0.01


class GradebookStructure(Detector):
    id = "D07"
    title = (
        "Assignment group weights that do not total 100, and weighting applied to only some "
        "groups"
    )
    tier = Tier.A
    collections = ("assignment_groups",)
    capability_tag = "gradebook_structure"
    capability_description = (
        "Whether the course weights its assignment groups, whether every group carries a weight "
        "when any does, and what the weights total."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        groups = list(survey.assignment_groups())
        survey.counted(len(groups))
        if not groups:
            yield survey.finding(
                code="no_assignment_groups",
                severity=Severity.ADVISORY,
                location=survey.course_location(within="Assignments"),
                observed="the course defines 0 assignment groups, so the gradebook has no sections",
            )
            return

        weighted = [group for group in groups if group.group_weight is not None]
        if not weighted:
            # Weighting switched off is an ordinary, supported configuration. Nothing to report.
            return

        unweighted = [group for group in groups if group.group_weight is None]
        if unweighted:
            names = ", ".join(sorted(group.name for group in unweighted))
            yield survey.finding(
                code="assignment_group_weighting_is_partial",
                severity=Severity.FRICTION,
                location=survey.course_location(within="Assignments"),
                observed=(
                    f"{len(weighted)} of {len(groups)} group(s) carry a weight; "
                    f"{len(unweighted)} do not ({names})"
                ),
            )

        total = round(sum(group.group_weight or 0.0 for group in weighted), 4)
        if abs(total - 100.0) > _WEIGHT_TOLERANCE:
            detail = ", ".join(
                f"{group.name} {group.group_weight:g}"
                for group in sorted(weighted, key=lambda item: item.position)
            )
            yield survey.finding(
                code="group_weights_do_not_total_100",
                severity=Severity.FRICTION,
                location=survey.course_location(within="Assignments"),
                observed=f"{len(weighted)} weighted group(s) totalling {total:g}: {detail}",
                total=total,
            )


# ---------------------------------------------------------------------------------------------
# D08 — a rubric attached to a graded assignment
# ---------------------------------------------------------------------------------------------


class AssignmentRubric(Detector):
    id = "D08"
    title = "Graded assignments with no rubric attached, and rubrics attached to nothing"
    tier = Tier.A
    collections = ("assignments", "rubrics")
    capability_tag = "assignment_rubric_attached"
    capability_description = (
        "Whether each graded assignment carries an attached rubric, and whether a rubric defined "
        "in the course is attached to any assignment."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        attached: set[str] = set()
        graded = 0
        for assignment in survey.assignments():
            if not assignment.graded or graded_points(assignment) <= 0:
                continue
            graded += 1
            if assignment.has_rubric:
                if assignment.rubric_title:
                    attached.add(assignment.rubric_title.strip().casefold())
                continue
            yield survey.finding(
                code="graded_assignment_without_a_rubric",
                severity=Severity.FRICTION,
                location=survey.location(assignment),
                observed=(
                    f"{graded_points(assignment):g} point(s), graded, and no rubric is attached"
                ),
                points=graded_points(assignment),
            )

        rubrics = list(survey.rubrics())
        survey.counted(graded + len(rubrics))
        for rubric in rubrics:
            if rubric.title.strip().casefold() in attached:
                continue
            yield survey.finding(
                code="rubric_attached_to_nothing",
                severity=Severity.ADVISORY,
                location=survey.course_location(within=f"rubric — {rubric.title}"),
                observed=(
                    f"{rubric.title!r} is defined in the course and no graded assignment names "
                    "it as its attached rubric"
                ),
            )


# ---------------------------------------------------------------------------------------------
# D09 — an activity says what to do and how to submit it
# ---------------------------------------------------------------------------------------------

#: Submission settings that mean Canvas offers a learner nowhere to submit. ``external_tool`` and
#: ``wiki_page`` are deliberately absent: both are real submission routes, and counting them here
#: would report every LTI-submitted assignment as unsubmittable.
_NO_SUBMISSION = {"none", "not_graded"}


class ActivityInstructions(Detector):
    id = "D09"
    title = "Activities with no description, and graded assignments with nothing to submit"
    tier = Tier.A
    collections = ("assignments", "quizzes", "discussions")
    capability_tag = "activity_instructions"
    capability_description = (
        "Whether each assignment, quiz and discussion carries a description, and whether a graded "
        "assignment declares a way for a learner to submit it. Due dates are D10's half."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        seen = 0
        for assignment in survey.assignments():
            seen += 1
            yield from self._described(survey, assignment, "assignment")
            if not assignment.graded:
                continue
            types = [entry for entry in assignment.submission_types if entry]
            if types and not set(types) <= _NO_SUBMISSION:
                continue
            written = ", ".join(types) or "none declared"
            yield survey.finding(
                code="graded_assignment_with_no_way_to_submit",
                severity=Severity.FRICTION,
                location=survey.location(assignment),
                observed=f"graded, with submission types: {written}",
            )
        for quiz in survey.quizzes():
            seen += 1
            yield from self._described(survey, quiz, "quiz")
        for topic in survey.discussions():
            seen += 1
            yield from self._described(survey, topic, "discussion")
        survey.counted(seen)

    def _described(self, survey: Survey, artifact: Artifact, noun: str) -> Iterator[Finding]:
        text = body_text(artifact)
        if text:
            return
        yield survey.finding(
            code="activity_without_a_description",
            severity=Severity.BARRIER,
            location=survey.location(artifact),
            observed=f"the {noun}'s description holds no text",
            activity=noun,
        )


# ---------------------------------------------------------------------------------------------
# D10 — due dates across the graded items
# ---------------------------------------------------------------------------------------------


class DueDateCoverage(Detector):
    id = "D10"
    title = "Graded items carrying no due date"
    tier = Tier.A
    collections = ("assignments", "quizzes", "discussions")
    capability_tag = "due_date_coverage"
    capability_description = (
        "How many of the course's graded items carry a due date, and which ones do not. Counts "
        "assignments, quizzes and graded discussions together."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        undated: list[tuple[Artifact, str]] = []
        graded = 0

        for assignment in survey.assignments():
            if not assignment.graded or graded_points(assignment) <= 0:
                continue
            graded += 1
            if not assignment.due_at:
                undated.append((assignment, "assignment"))
        for quiz in survey.quizzes():
            if (quiz.points_possible or 0) <= 0:
                continue
            graded += 1
            if not quiz.due_at:
                undated.append((quiz, "quiz"))
        for topic in survey.discussions():
            if not topic.graded:
                continue
            graded += 1
            if not topic.due_at:
                undated.append((topic, "discussion"))

        survey.counted(graded)
        dated = graded - len(undated)
        for artifact, noun in undated:
            yield survey.finding(
                code="graded_item_without_a_due_date",
                severity=Severity.FRICTION,
                location=survey.location(artifact),
                observed=(
                    f"a graded {noun} with no due date; {dated} of {graded} graded item(s) in "
                    "the course carry one"
                ),
                graded_items=graded,
                dated_items=dated,
            )


# ---------------------------------------------------------------------------------------------
# D11 — outcomes recorded in the course
# ---------------------------------------------------------------------------------------------


class OutcomesDefined(Detector):
    id = "D11"
    title = "Whether the course records learning outcomes, and whether each says what it measures"
    tier = Tier.A
    collections = ("outcomes", "modules")
    capability_tag = "outcomes_defined"
    capability_description = (
        "How many learning outcomes the course records, which outcome groups they sit in, and "
        "which of them carry no description. Canvas records no link between an outcome and a "
        "module, so nothing here claims one."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        modules = len(list(survey.modules()))
        outcomes = list(survey.outcomes())
        survey.counted(len(outcomes))

        if not outcomes:
            yield survey.finding(
                code="no_outcomes_recorded",
                severity=Severity.FRICTION,
                location=survey.course_location(within="Outcomes"),
                observed=(f"the course records 0 learning outcomes across {modules} module(s)"),
                modules=modules,
            )
            return

        for outcome in outcomes:
            if outcome.description.strip():
                continue
            yield survey.finding(
                code="outcome_without_a_description",
                severity=Severity.FRICTION,
                location=survey.location(outcome),
                observed=(
                    f"{outcome.title!r} is recorded with an empty description, so what it "
                    "measures is not stated"
                ),
                group=outcome.group,
            )


# ---------------------------------------------------------------------------------------------
# D12 — external tools and LTI launches
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolLaunch:
    """One place the course sends a learner out to an external tool."""

    artifact: Artifact
    name: str
    host: str


class ExternalToolInventory(Detector):
    id = "D12"
    title = "External tool launches the course says nothing about"
    tier = Tier.A
    collections = ("modules", "assignments")
    capability_tag = "external_tool_inventory"
    capability_description = (
        "Which external tools the course launches, from where, and whether the tool's name or "
        "address is mentioned anywhere in the course's own text."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        launches: list[ToolLaunch] = []
        for module in survey.modules():
            for item in module_items(module):
                if item.item_type != "ExternalTool":
                    continue
                launches.append(
                    ToolLaunch(
                        artifact=item,
                        name=item.title,
                        host=urlsplit(item.external_url or "").netloc,
                    )
                )
        for assignment in survey.assignments():
            if "external_tool" not in assignment.submission_types:
                continue
            launches.append(ToolLaunch(artifact=assignment, name=assignment.title, host=""))
        survey.counted(len(launches))
        if not launches:
            return

        described = self._described_names(survey, launches)
        for launch in launches:
            if launch.name.strip().casefold() in described:
                continue
            where = f" ({launch.host})" if launch.host else ""
            yield survey.finding(
                code="external_tool_launch_not_described",
                severity=Severity.FRICTION,
                location=survey.location(launch.artifact),
                observed=(
                    f"the course launches {launch.name!r}{where} and no page, syllabus or "
                    "assignment text names it"
                ),
                host=launch.host,
            )

    def _described_names(self, survey: Survey, launches: Sequence[ToolLaunch]) -> set[str]:
        """Tool names the course's own prose mentions somewhere.

        Read from the bodies the course already carries rather than from a second collection: a
        learner reading about a tool reads about it on a page, and ``bodies()`` is the one walk
        that sees every artifact that has one.
        """
        wanted = {launch.name.strip().casefold() for launch in launches if launch.name.strip()}
        if not wanted:
            return set()
        found: set[str] = set()
        for body in survey.bodies():
            if body.artifact.kind.value == "module_item":
                continue
            text = plain_text(body.html).casefold()
            found |= {name for name in wanted if name in text}
        return found


# ---------------------------------------------------------------------------------------------
# D13 / D14 — links out to policies and to support services
# ---------------------------------------------------------------------------------------------

_POLICY = re.compile(
    r"academic[-_\s]?integrity|academic[-_\s]?honesty|plagiaris|misconduct|code[-_\s]of[-_\s]conduct"
    r"|student[-_\s]conduct|grievance|appeal|title[-_\s]?ix|non[-_\s]?discrimination"
    r"|disability[-_\s]?(?:services|accommodat)|accommodations?\b|ada\b|accessibility[-_\s]?policy"
    r"|withdraw|drop[-_\s]?deadline|netiquette|copyright|ferpa|privacy[-_\s]?polic"
    r"|\bpolicies\b|\bpolicy\b",
    re.IGNORECASE,
)

_SUPPORT = re.compile(
    r"tutor|writing[-_\s]?(?:center|centre)|\blibrar|advising|advisor|counsel"
    r"|help[-_\s]?desk|helpdesk|it[-_\s]support|technical[-_\s]?support|tech[-_\s]?support"
    r"|student[-_\s]?(?:support|services|success)|career[-_\s]?services|food[-_\s]?pantry"
    r"|veteran|disability[-_\s]?(?:services|resource)|accessibility[-_\s]?services",
    re.IGNORECASE,
)


class _LinkVocabularyDetector(Detector, abstract=True):
    """Shared walk for the two detectors that look for a kind of link in the course's own text."""

    vocabulary: re.Pattern[str]

    def harvest(self, survey: Survey, artifacts: Sequence[Artifact]) -> list[tuple[Artifact, Link]]:
        """Every link in ``artifacts`` whose address or label names the vocabulary."""
        found: list[tuple[Artifact, Link]] = []
        for artifact in artifacts:
            page = survey.parse(artifact) if artifact.body_html else None
            for link in links_in(artifact, page):
                if matches(self.vocabulary, link.url, link.label):
                    found.append((artifact, link))
        return found


class PolicyLinks(_LinkVocabularyDetector):
    id = "D13"
    title = (
        "Links to campus policies: whether any are present and, when link checking is switched "
        "on, whether they still resolve"
    )
    tier = Tier.A
    collections = ("syllabus", "pages")
    capability_tag = "policy_links"
    vocabulary = _POLICY
    capability_description = (
        "How many links naming a campus policy the syllabus and course pages carry, and — only "
        "when link checking is switched on for the run — which of them the far end says are gone."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        syllabus = survey.syllabus()
        artifacts: list[Artifact] = [*([syllabus] if syllabus else []), *survey.pages()]
        found = self.harvest(survey, artifacts)
        survey.counted(len(found))

        if not found:
            where = survey.location(syllabus) if syllabus else survey.course_location()
            yield survey.finding(
                code="no_policy_links_found",
                severity=Severity.FRICTION,
                location=where,
                observed=(
                    f"{len(artifacts)} artifact(s) read and 0 links naming a campus policy — "
                    "integrity, conduct, grievances, accommodation, withdrawal or copyright"
                ),
            )
            return

        if not survey.link_resolution_enabled:
            # Nothing was requested from outside Canvas, so nothing is claimed about where these
            # go. Reporting them as unread would make every default run non-clean on a course
            # that has the links the criterion asks for.
            return

        for artifact, link in found:
            if not external(link.url):
                continue
            check = survey.resolve(link.url)
            location = survey.location(artifact, within=f"link — {excerpt(link.label, 60)}")
            if check.state is LinkState.GONE:
                yield survey.finding(
                    code="policy_link_is_gone",
                    severity=Severity.FRICTION,
                    location=location,
                    observed=f"{check.detail} ({urlsplit(link.url).netloc})",
                    evidence=verbatim(link.url),
                    status=check.status,
                )
            elif check.state is LinkState.UNKNOWN:
                survey.undetermined(
                    location,
                    reason=f"This policy link could not be checked. {check.detail}",
                    remedy="Follow the link by hand; nothing here says it is broken.",
                )


class SupportResourceLinks(_LinkVocabularyDetector):
    id = "D14"
    title = "Links to learner support services, and whether the syllabus carries any"
    tier = Tier.A
    collections = ("syllabus", "pages", "announcements")
    capability_tag = "support_resource_links"
    vocabulary = _SUPPORT
    capability_description = (
        "How many links naming a learner support service the course carries, where they are, and "
        "whether the syllabus is one of the places they appear."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        syllabus = survey.syllabus()
        elsewhere: list[Artifact] = [*survey.pages(), *survey.announcements()]
        in_syllabus = self.harvest(survey, [syllabus] if syllabus else [])
        outside = self.harvest(survey, elsewhere)
        survey.counted(len(in_syllabus) + len(outside))

        if not in_syllabus and not outside:
            where = survey.location(syllabus) if syllabus else survey.course_location()
            yield survey.finding(
                code="no_support_resource_links_found",
                severity=Severity.FRICTION,
                location=where,
                observed=(
                    f"{len(elsewhere) + (1 if syllabus else 0)} artifact(s) read and 0 links "
                    "naming tutoring, the library, advising, counselling, disability services or "
                    "technical support"
                ),
            )
            return

        if syllabus is not None and not in_syllabus:
            hosts = sorted({urlsplit(link.url).netloc or "a course page" for _, link in outside})
            yield survey.finding(
                code="syllabus_links_no_support_resources",
                severity=Severity.ADVISORY,
                location=survey.location(syllabus),
                observed=(
                    f"the syllabus links 0 support services; {len(outside)} such link(s) appear "
                    f"elsewhere in the course ({', '.join(hosts)})"
                ),
                elsewhere=len(outside),
            )


# ---------------------------------------------------------------------------------------------
# D15 — a time limit a learner is told about
# ---------------------------------------------------------------------------------------------

_TIME_LIMIT = re.compile(
    r"\btime[-\s]?limit|\btimed\b|\bminutes?\b|\bhours?\b|\bcountdown\b|\bonce\s+you\s+(?:begin|start)\b",
    re.IGNORECASE,
)


class TimedAssessmentDisclosure(Detector):
    id = "D15"
    title = "Timed assessments whose own description does not mention the limit"
    tier = Tier.B
    collections = ("quizzes",)
    capability_tag = "timed_assessment_disclosure"
    capability_description = (
        "For each assessment Canvas has a time limit set on, whether the assessment's own "
        "description says anything about a time limit, quoted."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        timed = 0
        for quiz in survey.quizzes():
            if not quiz.time_limit:
                continue
            timed += 1
            text = body_text(quiz)
            if matches(_TIME_LIMIT, quiz.title, text):
                continue
            yield survey.finding(
                code="time_limit_not_mentioned_in_the_description",
                severity=Severity.FRICTION,
                location=survey.location(quiz),
                observed=(
                    f"Canvas has a {quiz.time_limit}-minute limit set on this assessment; its "
                    "description, quoted below, does not mention a limit. Only this "
                    "assessment's own description was read."
                ),
                evidence=verbatim(text) if text else "(the description holds no text)",
                time_limit_minutes=quiz.time_limit,
            )
        survey.counted(timed)


# ---------------------------------------------------------------------------------------------
# D16 — low-stakes practice
# ---------------------------------------------------------------------------------------------

#: Canvas quiz types that are not graded coursework.
_FORMATIVE_QUIZ_TYPES = {"practice_quiz", "survey", "ungraded_survey"}


class FormativeOpportunities(Detector):
    id = "D16"
    title = "Whether the course offers any assessment that does not count towards a grade"
    tier = Tier.A
    collections = ("quizzes", "assignments")
    capability_tag = "formative_opportunities"
    capability_description = (
        "How many of the course's assessments are low-stakes — a practice quiz, a survey, or an "
        "assignment worth zero points or set to not graded — against how many are graded."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        formative = 0
        total = 0
        for quiz in survey.quizzes():
            total += 1
            if (quiz.quiz_type or "").lower() in _FORMATIVE_QUIZ_TYPES:
                formative += 1
        for assignment in survey.assignments():
            total += 1
            if not assignment.graded or graded_points(assignment) <= 0:
                formative += 1
        survey.counted(total)

        if formative:
            return
        yield survey.finding(
            code="no_low_stakes_assessment_found",
            severity=Severity.FRICTION,
            location=survey.course_location(),
            observed=(
                f"{total} assessment(s) examined and none is a practice quiz, a survey, or an "
                "assignment worth zero points or set to not graded"
            ),
            assessments=total,
        )


# ---------------------------------------------------------------------------------------------
# D17 — the discussion inventory
# ---------------------------------------------------------------------------------------------


class DiscussionInventory(Detector):
    id = "D17"
    title = "Whether the course holds discussions, and whether each carries a prompt"
    tier = Tier.A
    collections = ("discussions",)
    capability_tag = "discussion_inventory"
    capability_description = (
        "How many discussions the course holds, how many of them are graded, and which ones open "
        "with no prompt for a learner to answer."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        topics = list(survey.discussions())
        survey.counted(len(topics))
        graded = sum(1 for topic in topics if topic.graded)

        if not topics:
            yield survey.finding(
                code="course_holds_no_discussions",
                severity=Severity.FRICTION,
                location=survey.course_location(within="Discussions"),
                observed="the course holds 0 discussion topics, graded or ungraded",
            )
            return

        for topic in topics:
            if body_text(topic):
                continue
            yield survey.finding(
                code="discussion_without_a_prompt",
                severity=Severity.FRICTION,
                location=survey.location(topic),
                observed=(
                    f"the topic's body holds no text; the course holds {len(topics)} "
                    f"discussion(s), {graded} of them graded"
                ),
            )


# ---------------------------------------------------------------------------------------------
# D18 — something that introduces the instructor
# ---------------------------------------------------------------------------------------------

_INSTRUCTOR_TITLE = re.compile(
    r"\binstructor\b|\bprofessor\b|\bfaculty\b|\bmeet\s+your\b|\babout\s+me\b|\byour\s+teacher\b"
    r"|\bwho\s+(?:i\s+am|am\s+i)\b|\bteaching\s+team\b",
    re.IGNORECASE,
)
_INSTRUCTOR_BODY = re.compile(
    r"about\s+(?:your|the)\s+instructor|meet\s+your\s+(?:instructor|professor)"
    r"|instructor\s+(?:bio|biography|introduction)|my\s+name\s+is"
    r"|i\s+am\s+(?:your|the)\s+(?:instructor|professor)"
    r"|i\s*'?\s*ve\s+(?:been\s+)?(?:taught|been\s+teaching)",
    re.IGNORECASE,
)


class InstructorPresence(Detector):
    id = "D18"
    title = "Whether anything in the course introduces the instructor to learners"
    tier = Tier.B
    collections = ("pages", "syllabus", "announcements", "discussions")
    capability_tag = "instructor_presence"
    capability_description = (
        "Whether any page, announcement, discussion or the syllabus is titled or written as an "
        "introduction to the instructor, quoted where one is found."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        syllabus = survey.syllabus()
        artifacts: list[Artifact] = [
            *([syllabus] if syllabus else []),
            *survey.pages(),
            *survey.announcements(),
            *survey.discussions(),
        ]
        survey.counted(len(artifacts))
        for artifact in artifacts:
            text = body_text(artifact)
            if matches(_INSTRUCTOR_TITLE, artifact.title) or matches(_INSTRUCTOR_BODY, text):
                return
        yield survey.finding(
            code="no_instructor_introduction_found",
            severity=Severity.FRICTION,
            location=survey.course_location(),
            observed=(
                f"{len(artifacts)} artifact(s) read — the syllabus, pages, announcements and "
                "discussions — and none is titled or written as an introduction to the instructor"
            ),
            artifacts=len(artifacts),
        )


# ---------------------------------------------------------------------------------------------
# D19 — a long assessment broken into stages
# ---------------------------------------------------------------------------------------------

#: The share of the course's graded points past which an assessment is treated as prolonged. A
#: threshold rather than a judgement, and the finding states it.
_HIGH_STAKES_SHARE = 0.25

_STAGED = re.compile(
    r"\bdraft\b|\bmilestone\b|\bcheckpoint\b|\bproposal\b|\bstage\s*\d|\bphase\s*\d"
    r"|\bpart\s*(?:\d|one|two|three)\b|\bpeer\s+review\b|\boutline\b|\binterim\b"
    r"|\bprogress\s+report\b|\bannotated\s+bibliograph",
    re.IGNORECASE,
)


class AssessmentScaffolding(Detector):
    id = "D19"
    title = "Assessments carrying a large share of the grade with no staged parts around them"
    tier = Tier.B
    collections = ("assignments", "quizzes")
    capability_tag = "assessment_scaffolding"
    capability_description = (
        "Which assessments carry more than a quarter of the course's graded points, and whether "
        "any staged part — a draft, a proposal, a milestone, a peer review — appears beside them "
        "in the course or in their own instructions."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        assignments = list(survey.assignments())
        quizzes = list(survey.quizzes())
        survey.counted(len(assignments) + len(quizzes))

        total = sum(graded_points(item) for item in assignments if item.graded)
        total += sum(quiz.points_possible or 0.0 for quiz in quizzes)
        if total <= 0:
            return

        titles = [item.title for item in assignments] + [quiz.title for quiz in quizzes]
        for assignment in assignments:
            if not assignment.graded:
                continue
            points = graded_points(assignment)
            share = points / total
            if share < _HIGH_STAKES_SHARE:
                continue
            text = body_text(assignment)
            siblings = [
                title for title in titles if title != assignment.title and matches(_STAGED, title)
            ]
            if siblings or matches(_STAGED, text):
                continue
            yield survey.finding(
                code="high_stakes_assessment_without_staged_parts",
                severity=Severity.FRICTION,
                location=survey.location(assignment),
                observed=(
                    f"{points:g} of {total:g} graded point(s), {share:.0%} of the course, and no "
                    "draft, proposal, milestone, outline or peer review appears in its "
                    "instructions or among the other assessments' titles"
                ),
                evidence=verbatim(text) if text else "(the instructions hold no text)",
                share=round(share, 4),
                points=points,
            )


# ---------------------------------------------------------------------------------------------
# D20 — a way for a learner to give feedback on the course
# ---------------------------------------------------------------------------------------------

_FEEDBACK = re.compile(
    r"\bfeedback\b|\bevaluation\b|\bevaluate\s+(?:this\s+)?course\b|\bsurvey\b"
    r"|\bsuggestion\s*(?:box)?\b|\bmuddiest\b|\bhow\s+is\s+(?:this\s+)?course\s+going\b"
    r"|\bcourse\s+check[-\s]?in\b|\btell\s+me\s+what\b",
    re.IGNORECASE,
)


class CourseFeedbackMechanism(Detector):
    id = "D20"
    title = "Whether the course gives learners any way to comment on the course itself"
    tier = Tier.A
    collections = ("quizzes", "discussions", "pages", "announcements")
    capability_tag = "course_feedback_mechanism"
    capability_description = (
        "Whether the course holds a survey, a feedback discussion, or a page naming a course "
        "evaluation — anywhere a learner could say how the course is going."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        examined = 0
        for quiz in survey.quizzes():
            examined += 1
            if (quiz.quiz_type or "").lower() in {"survey", "ungraded_survey", "graded_survey"}:
                survey.counted(examined)
                return
            if matches(_FEEDBACK, quiz.title):
                survey.counted(examined)
                return
        for group in (survey.discussions(), survey.pages(), survey.announcements()):
            for artifact in group:
                examined += 1
                if matches(_FEEDBACK, artifact.title, body_text(artifact)):
                    survey.counted(examined)
                    return
        survey.counted(examined)
        yield survey.finding(
            code="no_course_feedback_mechanism_found",
            severity=Severity.FRICTION,
            location=survey.course_location(),
            observed=(
                f"{examined} artifact(s) read — quizzes, discussions, pages and announcements — "
                "and none is a survey or names a course evaluation, feedback or suggestion route"
            ),
            artifacts=examined,
        )


__all__ = [
    "ActivityInstructions",
    "AssessmentScaffolding",
    "AssignmentRubric",
    "CourseFeedbackMechanism",
    "CourseSchedule",
    "DiscussionInventory",
    "DueDateCoverage",
    "ExternalToolInventory",
    "FormativeOpportunities",
    "GradebookStructure",
    "InstructorPresence",
    "Link",
    "ModuleOverview",
    "NavigationPruning",
    "OutcomesDefined",
    "PolicyLinks",
    "SupportResourceLinks",
    "SyllabusPresence",
    "SyllabusPrintability",
    "TimedAssessmentDisclosure",
    "WelcomeAnnouncement",
    "body_text",
    "count_dates",
    "external",
    "links_in",
    "plain_text",
]
