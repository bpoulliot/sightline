"""Item 16: the review screen, the Reviewer setting, and the reviewer's rating in both exports.

The course is the committed sample review (``examples/sample-report/review.json``) — what a real
batch run over the synthetic ``examples/sample-course`` saved — put into a run store exactly as the
batch runner saves it. Nothing here calls a model host or Canvas. Every page is read back as HTML,
with a small tree builder below, and asserted on what a person would see.
"""

from __future__ import annotations

import asyncio
import copy
import io
import json
import re
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from pdf_reader import read_pdf
from sightline.app import TEMPLATE_DIR, create_app
from sightline.batch import (
    BatchRunner,
    CourseWork,
    ExportGate,
    Stage,
    StageOutcome,
    create_batch_run,
    pipeline_factory,
)
from sightline.config import Settings
from sightline.detect import Tier
from sightline.paths import DataPaths
from sightline.preferences import REVIEWER_MAX, PreferenceStore
from sightline.report import REPORT_FILENAME, ReportError, build_report, parse_review, to_pdf
from sightline.report.model import anchor_for
from sightline.review import routes as review_routes
from sightline.review.screen import QUESTION, SORTS, ReviewScreen, View, reading_key
from sightline.rubric import Coverage, Rating
from sightline.rubric.overrides import MachineProposal, latest_overrides, reviewer_ratings
from sightline.store import RunStore
from sightline.voice import Voice, default_register
from sightline.voice.lint import mask
from sightline.workbook import WORKBOOK_FILENAME, WorkbookError, build_workbook, to_xlsx

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "examples" / "sample-report" / "review.json"
REFERENCE = "uccs-oscqr-3-1-3"
REVIEWER = "Pat Example"
OPENED = datetime(2026, 9, 1, 12, 30, tzinfo=UTC)


# -- reading HTML back ----------------------------------------------------------------------------


class Node:
    def __init__(self, tag: str, attrs: Mapping[str, str | None], parent: Node | None) -> None:
        self.tag = tag
        self.attrs = {key: value or "" for key, value in attrs.items()}
        self.parent = parent
        self.children: list[Node | str] = []

    def walk(self) -> Iterator[Node]:
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.walk()

    def find_all(self, tag: str, **attrs: str) -> list[Node]:
        return [
            node
            for node in self.walk()
            if node.tag == tag and all(node.attrs.get(k.rstrip("_")) == v for k, v in attrs.items())
        ]

    def has_class(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()

    def text(self) -> str:
        parts: list[str] = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.text())
        return " ".join(" ".join(parts).split())


_VOID = {"input", "meta", "link", "br", "img", "hr"}


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(tag, dict(attrs), self.current)
        self.current.children.append(node)
        if tag not in _VOID:
            self.current = node

    def handle_endtag(self, tag: str) -> None:
        node: Node | None = self.current
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.current = node.parent

    def handle_data(self, data: str) -> None:
        self.current.children.append(data)


def parse(html: str) -> Node:
    builder = _Builder()
    builder.feed(html)
    return builder.root


def main_of(html: str) -> Node:
    (main,) = parse(html).find_all("main")
    return main


def articles(html: str) -> list[Node]:
    return [node for node in main_of(html).walk() if node.tag == "article"]


# -- the run --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


class SavedReviewPipeline:
    """Five stages that succeed, and a review that is the committed sample's."""

    def __init__(self, review: Mapping[str, Any]) -> None:
        self.review_payload = review

    async def run(self, stage: Stage, work: CourseWork) -> StageOutcome:
        return work.record(
            StageOutcome(stage=stage, examined=1, statement=f"{stage.value}: examined 1.")
        )

    def review(self, work: CourseWork) -> Mapping[str, Any]:
        return self.review_payload


def finished_course(paths: DataPaths, review: Mapping[str, Any]) -> str:
    store = RunStore.open(paths)
    try:
        run_id = create_batch_run(store, ["examples/sample-course"], rubric_pack=REFERENCE)
        pipeline = SavedReviewPipeline(review)
        asyncio.run(BatchRunner(store, paths, pipeline_factory(pipeline)).execute(run_id))
        run = store.get_run(run_id)
        assert run is not None
        return run.courses[0].id
    finally:
        store.dispose()


@pytest.fixture
def course(paths: DataPaths, payload) -> str:
    return finished_course(paths, payload)


@pytest.fixture
def client(settings: Settings, course: str) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def store(client: TestClient) -> RunStore:
    return client.app.state.store  # type: ignore[attr-defined]


@pytest.fixture
def gate(client: TestClient) -> ExportGate:
    state = client.app.state  # type: ignore[attr-defined]
    return ExportGate(state.store, state.paths)


@pytest.fixture(scope="module")
def loaded():
    return parse_review(SAMPLE.read_text(encoding="utf-8"))


def set_reviewer(client: TestClient, name: str = REVIEWER) -> None:
    response = client.post("/settings/reviewer", data={"reviewer": name}, follow_redirects=False)
    assert response.status_code == 303


def category(client: TestClient, course: str, section: str, query: str = "") -> str:
    response = client.get(f"/review/{course}/category/{section}{query}")
    assert response.status_code == 200, response.text
    return response.text


def codes_on(html: str) -> list[str]:
    return [node.attrs["data-code"] for node in articles(html)]


def record(client: TestClient, course: str, code: str, **form: str) -> Any:
    return client.post(
        f"/review/{course}/override", data={"code": code, **form}, follow_redirects=False
    )


def sections_of(loaded) -> list[str]:
    present = {row.section for row in loaded.projection.rows}
    return [section.code for section in loaded.pack.sections if section.code in present]


# -- opening is loading, and the gate -------------------------------------------------------------


def test_opening_any_page_opens_the_review_and_never_moves_the_timestamp(
    client, course, gate
) -> None:
    assert not gate.is_open(course)
    category(client, course, "MOB")
    first = gate.opened_at(course)
    assert first is not None
    assert client.get(f"/review/{course}").status_code == 200
    category(client, course, "ACD", "?sort=severity")
    assert gate.opened_at(course) == first


def test_export_is_refused_before_opening_with_the_gates_reason_and_allowed_after(
    client, course, gate, paths
) -> None:
    refused = client.post(f"/review/{course}/export", follow_redirects=False)
    assert refused.status_code == 409
    text = main_of(refused.text).text()
    assert "Nobody has opened the review" in text
    assert "does not generate a report nobody has read" in text
    assert not gate.is_open(course), "the export endpoint must never open a review itself"
    assert list(paths.resolve("reports").rglob("*")) == []
    # A file cannot be fetched before the review is opened either.
    assert client.get(f"/review/{course}/files/{REPORT_FILENAME}").status_code == 409

    assert client.get(f"/review/{course}").status_code == 200
    done = client.post(f"/review/{course}/export", follow_redirects=False)
    assert done.status_code == 303
    page = client.get(done.headers["location"]).text
    assert "Written." in main_of(page).text()
    for name in (REPORT_FILENAME, WORKBOOK_FILENAME):
        served = client.get(f"/review/{course}/files/{name}")
        assert served.status_code == 200
        assert served.content == gate.destination(course, name).read_bytes()
    assert client.get(f"/review/{course}/files/elsewhere.txt").status_code == 404


def test_a_course_the_store_does_not_know_is_a_404_that_opens_nothing(client) -> None:
    response = client.get("/review/no-such-course")
    assert response.status_code == 404
    assert "python -m sightline.batch status" in main_of(response.text).text()


def test_a_course_with_no_review_file_is_not_opened(client, course, gate, paths, store) -> None:
    run_id = store.get_course(course).run_id
    paths.resolve("runs", run_id, "courses", course, "review.json").unlink()
    response = client.get(f"/review/{course}")
    assert response.status_code == 409
    assert not gate.is_open(course)


# -- the shape of the screen ----------------------------------------------------------------------


def test_categories_render_in_the_instruments_order(client, course, loaded) -> None:
    html = client.get(f"/review/{course}").text
    rows = [
        node.attrs["data-category"]
        for node in main_of(html).find_all("tr")
        if node.attrs.get("data-category")
    ]
    assert rows == sections_of(loaded) == ["COAL", "GSOV", "OREP", "ACD", "ENIN", "ASFE", "MOB"]
    # Each category's page lists its rows, in the instrument's own row order by default.
    for section in rows:
        expected = [row.code for row in loaded.projection.rows if row.section == section]
        assert codes_on(category(client, course, section)) == expected


def test_tier_is_visible_on_every_row(client, course, loaded) -> None:
    tiers = {row.code: row.tier.value for row in loaded.projection.rows}
    for section in sections_of(loaded):
        for node in articles(category(client, course, section)):
            chips = [n for n in node.walk() if n.has_class("tier")]
            assert chips and chips[0].text() == tiers[node.attrs["data-code"]]
            assert chips[0].has_class(f"tier-{tiers[node.attrs['data-code']].lower()}")


def test_sort_and_filter_are_linkable_query_parameters(client, course, loaded) -> None:
    set_reviewer(client)
    html = category(client, course, "ACD", "?sort=severity&reading=minor&tier=A")
    order = [
        row.code
        for row in loaded.projection.action_plan_order()
        if row.section == "ACD" and row.rating is Rating.MINOR and row.tier is Tier.A
    ]
    assert order and codes_on(html) == order
    main = main_of(html)
    selected = {
        select.attrs["name"]: [
            o.attrs["value"] for o in select.find_all("option") if "selected" in o.attrs
        ]
        for select in main.find_all("select")
    }
    assert selected == {"sort": ["severity"], "reading": ["minor"], "tier": ["A"]}
    # The form submits to the category's own address, so the result is the same link.
    (form,) = [f for f in main.find_all("form") if f.attrs.get("method") == "get"]
    assert form.attrs["action"] == f"/review/{course}/category/ACD"
    # And the view rides along on a recorded rating, so the reviewer lands back where they were.
    (hidden,) = [
        n.attrs["value"]
        for n in articles(html)[0].find_all("input")
        if n.attrs.get("name") == "sort"
    ]
    assert hidden == "severity"


def test_the_nine_mob_rows_survive_every_sort_and_filter(client, course, loaded) -> None:
    mob = [row for row in loaded.projection.rows if row.section == "MOB"]
    assert len(mob) == 9 and all(row.priority is None for row in mob)
    every = {row.code for row in mob}
    for sort in SORTS:
        assert set(codes_on(category(client, course, "MOB", f"?sort={sort}"))) == every
        by_reading: list[str] = []
        for key in [rating.value for rating in Rating] + [QUESTION]:
            by_reading += codes_on(category(client, course, "MOB", f"?sort={sort}&reading={key}"))
        assert sorted(by_reading) == sorted(every), "the reading filter partitions the nine"
        by_tier: list[str] = []
        for tier in Tier:
            by_tier += codes_on(category(client, course, "MOB", f"?sort={sort}&tier={tier.value}"))
        assert sorted(by_tier) == sorted(every), "the tier filter partitions the nine"
    severity = codes_on(category(client, course, "MOB", "?sort=severity"))
    assert severity == [
        row.code for row in loaded.projection.action_plan_order() if row.section == "MOB"
    ]
    # Every row says it has no priority flag, rather than borrowing one.
    no_flag = Voice().fragment("no_priority").text
    for node in articles(category(client, course, "MOB")):
        assert no_flag in node.text()


def test_asfe_skips_6_as_the_instrument_does(client, course) -> None:
    codes = codes_on(category(client, course, "ASFE"))
    assert "ASFE 7" in codes and "ASFE 6" not in codes


def test_a_switched_off_section_is_neither_shown_nor_counted(paths, payload, settings) -> None:
    trimmed = copy.deepcopy(payload)
    projection = trimmed["projection"]
    projection["omitted"] = [row["code"] for row in projection["rows"] if row["section"] == "MOB"]
    projection["rows"] = [row for row in projection["rows"] if row["section"] != "MOB"]
    course = finished_course(paths, trimmed)
    with TestClient(create_app(settings)) as client:
        html = client.get(f"/review/{course}").text
        rows = main_of(html).find_all("tr")
        assert "MOB" not in [node.attrs.get("data-category") for node in rows]
        # MOB 6 is a question; with the section off it is not work remaining.
        unresolved = sum(
            int(node.find_all("td")[-1].text()) for node in rows if node.attrs.get("data-category")
        )
        questions = [r for r in projection["rows"] if r["tier"] == "C"]
        assert unresolved == len(questions)
        assert client.get(f"/review/{course}/category/MOB").status_code == 404


def test_the_index_counts_by_reading_and_adds_nothing_up(client, course, loaded) -> None:
    main = main_of(client.get(f"/review/{course}").text)
    (table,) = main.find_all("table")
    header = [th.text() for th in table.find_all("th") if th.attrs.get("scope") == "col"]
    assert header[0] == "Category"
    for row in table.find_all("tr"):
        code = row.attrs.get("data-category")
        if not code:
            continue
        cells = [td.text() for td in row.find_all("td")]
        members = [r for r in loaded.projection.rows if r.section == code]
        assert sum(int(cell) for cell in cells[:-1]) == len(members)
        assert int(cells[-1]) == sum(r.tier is Tier.C for r in members)
    text = main.text()
    assert not re.search(r"\d\s*%|\btotal\b|\boverall\b|\bscore\b", text, re.I)
    assert main.find_all("progress") == [] and main.find_all("meter") == []


def test_the_jump_goes_to_the_first_unresolved_question(client, course, loaded) -> None:
    main = main_of(client.get(f"/review/{course}").text)
    (jump,) = [n for n in main.walk() if n.has_class("jump")]
    (link,) = jump.find_all("a")
    first = next(
        r
        for s in sections_of(loaded)
        for r in loaded.projection.rows
        if r.section == s and r.tier is Tier.C
    )
    assert (
        link.attrs["href"] == f"/review/{course}/category/{first.section}#{anchor_for(first.code)}"
    )
    html = category(client, course, first.section, "?only=unresolved")
    assert codes_on(html) == [
        r.code for r in loaded.projection.rows if r.section == first.section and r.tier is Tier.C
    ]
    assert all(node.attrs.get("data-unresolved") == "yes" for node in articles(html))


def test_no_deterministic_coverage_reads_differently_from_sufficiently_present(
    client, course, loaded
) -> None:
    rows = {row.code: row for row in loaded.projection.rows}
    clean = next(c for c, r in rows.items() if r.rating is Rating.SUFFICIENTLY_PRESENT)
    unbound = next(
        c
        for c, r in rows.items()
        if r.coverage is Coverage.NO_DETERMINISTIC_COVERAGE and r.tier is Tier.B
    )
    pending = next(c for c, r in rows.items() if r.coverage is Coverage.NOT_YET_IMPLEMENTED)
    found: dict[str, dict[str, str]] = {}
    for code in (clean, unbound, pending):
        html = category(client, course, rows[code].section)
        (node,) = [a for a in articles(html) if a.attrs["data-code"] == code]
        fields = {
            div.find_all("dt")[0].text(): div.find_all("dd")[0].text()
            for div in node.find_all("dl")[0].children
            if isinstance(div, Node)
        }
        found[code] = {"coverage": fields["Coverage"], "proposed": fields["Proposed rating"]}
        assert node.attrs["data-coverage"] == rows[code].coverage.value
    word = loaded.pack.rating_scale.sufficiently_present
    assert found[clean]["proposed"] == word
    assert found[unbound]["proposed"] != word and found[pending]["proposed"] != word
    assert len({f["coverage"] for f in found.values()}) == 3, found
    assert word not in found[unbound]["coverage"] + found[pending]["coverage"]


# -- no pre-selection, no bulk-accept -------------------------------------------------------------


def test_no_tier_c_row_ships_a_preselected_rating_and_no_bulk_accept_exists(
    client, course, loaded
) -> None:
    set_reviewer(client)
    questions = 0
    for section in sections_of(loaded):
        html = category(client, course, section)
        main = main_of(html)
        posts = [f for f in main.find_all("form") if f.attrs.get("method") == "post"]
        nodes = articles(html)
        assert len(posts) == len(nodes), "one form per row, and no form outside a row"
        for node in nodes:
            (form,) = [f for f in node.find_all("form") if f.attrs.get("method") == "post"]
            codes = [i for i in form.find_all("input") if i.attrs.get("name") == "code"]
            assert [i.attrs["value"] for i in codes] == [node.attrs["data-code"]]
            radios = [i for i in form.find_all("input") if i.attrs.get("type") == "radio"]
            checked = [i.attrs["value"] for i in radios if "checked" in i.attrs]
            if node.attrs["data-tier"] == "C":
                questions += 1
                assert checked == [], f"{node.attrs['data-code']} ships a pre-selected rating"
            else:
                assert len(checked) == 1
            assert not [i for i in form.find_all("input") if i.attrs.get("type") == "checkbox"]
        assert not re.search(
            r"\b(?:accept|confirm|apply|record) (?:all|every)\b", main.text(), re.I
        )
    assert questions == sum(row.tier is Tier.C for row in loaded.projection.rows) == 9
    # The only route that records a rating takes one row.
    router = review_routes.build_review_router(Jinja2Templates(directory=str(TEMPLATE_DIR)))
    posts = {route.path for route in router.routes if "POST" in getattr(route, "methods", set())}
    assert posts == {"/review/{course_id}/override", "/review/{course_id}/export"}


def test_the_screen_refuses_to_build_a_question_with_a_rating_selected(loaded) -> None:
    """The guard in the page model fires, so the test above is not reading a model that cannot."""
    screen = ReviewScreen(
        course_id="c", review=loaded, opened=OPENED, records={}, reviewer=REVIEWER
    )
    page = screen.category("MOB", View())
    (question,) = [row for row in page.rows if row.question]
    from dataclasses import replace

    from sightline.review import screen as module

    planted = replace(
        page,
        rows=tuple(
            (
                replace(row, choices=tuple(replace(c, checked=True) for c in row.choices[:1]))
                if row is question
                else row
            )
            for row in page.rows
        ),
    )
    with pytest.raises(module.ReviewScreenError, match="pre-selected"):
        module._check(planted)


# -- recording a rating: the headline -------------------------------------------------------------


def test_an_override_round_trips_through_12as_record(
    client, course, store, loaded, payload
) -> None:
    """The headline: the machine proposal, the human value and the Reviewer setting, readable."""
    set_reviewer(client)
    category(client, course, "COAL")  # open it
    row = next(r for r in loaded.projection.rows if r.rating is Rating.MINOR and r.tier is Tier.B)
    response = record(client, course, row.code, rating="major", note="Checked every outcome.")
    assert response.status_code == 303
    assert response.headers["location"].endswith(f"#{anchor_for(row.code)}")

    (saved,) = store.list_overrides(course_id=course)
    proposal = MachineProposal.from_review(payload["projection"], row.code)
    assert saved.criterion == row.code
    assert saved.machine_rating == proposal.rating.value == Rating.MINOR.value
    assert saved.machine_hours == proposal.hours == row.hours
    assert saved.machine_findings == [dict(f) for f in proposal.findings]
    assert saved.machine_near_boundary == (
        dict(proposal.near_boundary) if proposal.near_boundary else None
    )
    assert saved.pack_id == REFERENCE and saved.pack_version == loaded.pack.version
    assert saved.human_rating == Rating.MAJOR.value
    assert saved.reviewer == REVIEWER
    assert saved.note == "Checked every outcome."

    html = client.get(response.headers["location"]).text
    (node,) = [a for a in articles(html) if a.attrs["data-code"] == row.code]
    text = node.text()
    assert "Major Revision, recorded by Pat Example on" in text
    assert "Note: Checked every outcome." in text
    fields = {
        div.find_all("dt")[0].text(): div.find_all("dd")[0].text()
        for div in node.find_all("dl")[0].children
        if isinstance(div, Node)
    }
    assert fields["Proposed rating"] == "Minor Revision", "the proposal is never replaced"
    assert fields["Estimated effort"].startswith(f"{row.hours:g} h")
    assert "Recorded for" in main_of(html).text()
    checked = [i.attrs["value"] for i in node.find_all("input") if "checked" in i.attrs]
    assert checked == ["major"], "the reviewer's latest rating is what the form now holds"

    # A second record appends; the latest is what the screen shows.
    record(client, course, row.code, rating="moderate")
    assert [r.human_rating for r in store.list_overrides(course_id=course)] == ["major", "moderate"]
    assert latest_overrides(store, course)[row.code].human_rating == "moderate"
    html = category(client, course, row.section)
    (node,) = [a for a in articles(html) if a.attrs["data-code"] == row.code]
    assert "Moderate Revision, recorded by" in node.text()


def test_a_note_with_no_rating_change_records_the_proposal_as_the_human_rating(
    client, course, store, loaded
) -> None:
    set_reviewer(client)
    category(client, course, "MOB")
    row = next(r for r in loaded.projection.rows if r.code == "MOB 2")
    assert record(client, course, row.code, note="Looked at it.").status_code == 303
    (saved,) = store.list_overrides(course_id=course)
    assert saved.human_rating == saved.machine_rating == row.rating.value


def test_a_question_is_never_answered_by_default(client, course, store, loaded) -> None:
    set_reviewer(client)
    category(client, course, "MOB")
    response = record(client, course, "MOB 6", note="Thinking about it.")
    assert response.status_code == 400
    assert "Nothing was recorded for MOB 6" in main_of(response.text).text()
    assert store.list_overrides(course_id=course) == []

    before = main_of(client.get(f"/review/{course}").text)
    assert record(client, course, "MOB 6", rating="no_response").status_code == 303
    (saved,) = store.list_overrides(course_id=course)
    assert saved.human_rating == saved.machine_rating == "no_response"
    html = category(client, course, "MOB")
    (node,) = [a for a in articles(html) if a.attrs["data-code"] == "MOB 6"]
    assert "data-unresolved" not in node.attrs
    assert not [i for i in node.find_all("input") if "checked" in i.attrs]
    after = main_of(client.get(f"/review/{course}").text)

    def mob_unresolved(main: Node) -> int:
        (row,) = [n for n in main.find_all("tr") if n.attrs.get("data-category") == "MOB"]
        return int(row.find_all("td")[-1].text())

    assert mob_unresolved(before) == 1 and mob_unresolved(after) == 0


def test_the_override_form_refuses_with_no_reviewer_set_and_names_settings(
    client, course, store
) -> None:
    html = category(client, course, "MOB")
    main = main_of(html)
    assert [f for f in main.find_all("form") if f.attrs.get("method") == "post"] == []
    assert "set the name under Reviewer on the Settings screen" in main.text()
    assert [a for a in main.find_all("a") if a.attrs.get("href") == "/settings#reviewer"]

    response = record(client, course, "MOB 2", rating="major")
    assert response.status_code == 409
    assert "set the name under Reviewer on the Settings screen" in main_of(response.text).text()
    assert store.list_overrides(course_id=course) == []


def test_the_form_says_whose_name_it_will_record(client, course) -> None:
    set_reviewer(client)
    html = category(client, course, "MOB")
    for node in articles(html):
        (form,) = node.find_all("form")
        assert f"recorded as {REVIEWER}" in form.text()


def test_the_override_path_takes_the_reviewer_from_the_one_lookup(
    client, course, store, monkeypatch
) -> None:
    """Item 40 swaps the source of :func:`current_reviewer`; nothing else needs to change."""
    monkeypatch.setattr(review_routes, "current_reviewer", lambda request: "Signed-in Person")
    category(client, course, "MOB")
    assert record(client, course, "MOB 2", rating="major").status_code == 303
    (saved,) = store.list_overrides(course_id=course)
    assert saved.reviewer == "Signed-in Person"
    # And nothing in the review package reads the preference except the lookup itself.
    package = REPO / "src" / "sightline" / "review"
    readers = sorted(
        path.name for path in package.glob("*.py") if ".reviewer()" in path.read_text("utf-8")
    )
    assert readers == ["identity.py"]


def test_an_unknown_rating_or_criterion_records_nothing(client, course, store) -> None:
    set_reviewer(client)
    category(client, course, "MOB")
    assert record(client, course, "MOB 2", rating="excellent").status_code == 400
    assert record(client, course, "NOPE 1", rating="major").status_code == 400
    assert record(client, course, "MOB 2", rating="major", note="x" * 4001).status_code == 400
    assert store.list_overrides(course_id=course) == []


# -- the words ------------------------------------------------------------------------------------


@pytest.fixture
def pages(client, course, monkeypatch) -> list[tuple[str, Any]]:
    """Every review page as rendered, with the page model it was rendered from."""
    built: list[Any] = []
    original_index, original_category = ReviewScreen.index, ReviewScreen.category

    def index(self, **kw):  # type: ignore[no-untyped-def]
        built.append(original_index(self, **kw))
        return built[-1]

    def category_page(self, *args, **kw):  # type: ignore[no-untyped-def]
        built.append(original_category(self, *args, **kw))
        return built[-1]

    monkeypatch.setattr(ReviewScreen, "index", index)
    monkeypatch.setattr(ReviewScreen, "category", category_page)
    out: list[tuple[str, Any]] = []
    # Without a reviewer, then with one and a rating recorded, so every piece of text is printed.
    for step in range(2):
        if step == 1:
            set_reviewer(client)
            record(
                client, course, "MOB 6", rating="minor", note="A note, with grading words quoted."
            )
        html = client.get(f"/review/{course}").text
        out.append((html, built[-1]))
        for section in ("COAL", "GSOV", "OREP", "ACD", "ENIN", "ASFE", "MOB"):
            html = client.get(f"/review/{course}/category/{section}").text
            out.append((html, built[-1]))
    return out


def _words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z'’\-]*", text)


def test_every_piece_of_every_page_passes_the_register_lint(pages) -> None:
    for _html, page in pages:
        assert page.violations() == []


def test_the_templates_print_no_word_the_page_model_did_not_supply(pages) -> None:
    for html, page in pages:
        supplied = " ".join(
            [text.text for _, text in page.texts()]
            + [sentence.text for _, sentence in page.sentences()]
            + [row.code for row in getattr(page, "rows", ())]
            + [line.code for line in getattr(page, "categories", ())]
            + [file.name for file in getattr(page, "files", ())]
            + ["A", "B", "C", "D", "UTC"]
        )
        known = {word.lower() for word in _words(supplied)}
        stray = sorted({word.lower() for word in _words(main_of(html).text())} - known)
        assert stray == [], stray


#: The elements a page's words are printed in. Each is read as one piece, as a sentence is.
_TEXT_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "dt",
    "dd",
    "th",
    "td",
    "option",
    "legend",
    "button",
    "label",
    "a",
    "span",
}


def _spans(flat: str, values: list[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for value in sorted(set(values), key=len, reverse=True):
        needle = " ".join(value.split())
        if len(needle) >= 2:
            spans.extend((m.start(), m.end()) for m in re.finditer(re.escape(needle), flat))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def test_item_15s_lint_passes_over_the_rendered_pages(pages) -> None:
    """Read in the register every tier shares — grading, aggregate, self-assessment — over the
    visible text of each element a page prints words in, with the verbatim values the page model
    printed masked, as the lint does. Element by element, as a sentence is linted: read as one
    blob, a percentage quoted from the course in one row would sit "beside" Sightline's name in
    another."""
    rules = default_register()
    for html, page in pages:
        main = main_of(html)
        values = page.verbatim()
        linted: set[str] = set()
        for node in main.walk():
            if node.tag not in _TEXT_TAGS:
                continue
            flat = node.text()
            if not flat:
                continue
            linted.update(word.lower() for word in _words(flat))
            spans = _spans(flat, values)
            violations = rules.lint(flat, "B", where=node.tag, sentence=False, spans=spans)
            assert violations == [], [v.line() for v in violations]
            own = mask(flat, spans)
            assert not re.search(r"\d\s*%|\bper ?cent", own), flat
            assert not re.search(r"\b(total|overall|score|grade|grading|average)\b", own, re.I)
        # Every word on the page sat inside an element that was read.
        missed = {word.lower() for word in _words(main.text())} - linted
        assert missed == set(), sorted(missed)
    # The same reading fires on a planted aggregate, so the pass above is not a reading of nothing.
    planted = rules.lint("The overall score is 80%.", "B", where="planted", sentence=False)
    assert {violation.rule for violation in planted} >= {"grading", "aggregate"}


def test_no_attention_flag_near_boundary_marker_or_run_count_is_rendered(pages) -> None:
    for html, _page in pages:
        text = main_of(html).text()
        assert "near a band threshold" not in text.lower()
        assert "Human reviewers" not in text
        assert not re.search(r"\b\d+ (?:of \d+ )?runs?\b", text)
        assert "attention" not in text.lower()


def test_the_status_page_no_longer_says_the_review_screens_arrive_later(client) -> None:
    text = main_of(client.get("/").text).text()
    assert "arrive with later roadmap items" not in text
    assert "/review/" in text


# -- the Reviewer setting -------------------------------------------------------------------------


def test_the_reviewer_setting_saves_collapses_whitespace_and_can_be_forgotten(client) -> None:
    page = client.get("/settings").text
    assert 'id="reviewer"' in page and "Not set." in page
    set_reviewer(client, "  Pat   Example ")
    preferences: PreferenceStore = client.app.state.preferences  # type: ignore[attr-defined]
    assert preferences.reviewer().name == REVIEWER
    page = client.get("/settings?saved=reviewer").text
    assert f"recorded as\n        <strong>{REVIEWER}</strong>" in page or REVIEWER in page
    assert client.post("/settings/reviewer/forget", follow_redirects=False).status_code == 303
    assert preferences.reviewer().name is None


@pytest.mark.parametrize("name", ["", "   ", "x" * (REVIEWER_MAX + 1)])
def test_a_reviewer_name_that_cannot_be_stored_is_refused_with_a_reason(client, name) -> None:
    response = client.post("/settings/reviewer", data={"reviewer": name})
    assert response.status_code == 200
    assert "banner-bad" in response.text
    assert client.app.state.preferences.reviewer().name is None  # type: ignore[attr-defined]


# -- the exports carry the reviewer's rating (Q2) -------------------------------------------------

OVERRIDES = {"ACD 1": Rating.MAJOR, "COAL 2": Rating.NOT_APPLICABLE, "MOB 6": Rating.MINOR}


def _workbook(loaded, overrides=None):
    return build_workbook(
        loaded.projection,
        loaded.pack,
        base_url=loaded.base_url,
        created=OPENED,
        report=loaded.report,
        extractions=loaded.extractions,
        course_id=loaded.course_id,
        overrides=overrides,
    )


def test_the_workbook_carries_a_recorded_rating_as_the_reviewers_beside_the_unchanged_proposal(
    loaded,
) -> None:
    plain = _workbook(loaded).values()
    assert _workbook(loaded, {}).values() == plain, "no override, no change"
    reviewed = _workbook(loaded, OVERRIDES).values()
    for (name, before), (_, after) in zip(plain, reviewed, strict=True):
        header = after[0]
        at = header.index("Proposed rating")
        assert header[at + 1] == "Reviewer's rating"
        code_at = header.index("Code")
        for old, new in zip(before[1:], after[1:], strict=True):
            # Every machine cell is exactly where it was, unchanged: nothing re-banded.
            assert new[: at + 1] + new[at + 2 :] == old, name
            human = OVERRIDES.get(new[code_at])
            expected = (
                dict(_rating_words(loaded))[human.value]
                if human
                else "No rating recorded by the reviewer"
            )
            assert new[at + 1] == expected
    # And the file holds it, every cell text, nothing empty.
    book = openpyxl.load_workbook(io.BytesIO(to_xlsx(_workbook(loaded, OVERRIDES))))
    first = [cell.value for cell in next(book.worksheets[0].iter_rows(max_row=1))]
    assert "Reviewer's rating" in first


def _rating_words(loaded) -> list[tuple[str, str]]:
    scale = loaded.pack.rating_scale
    words = [("sufficiently_present", scale.sufficiently_present)]
    words += [(band.rating, band.label) for band in scale.bands]
    words += [("no_response", scale.no_response), ("not_applicable", scale.not_applicable)]
    return words


def test_the_pdf_carries_a_recorded_rating_as_the_reviewers_beside_the_unchanged_proposal(
    loaded,
) -> None:
    plain = build_report(loaded, created=OPENED)
    assert not plain.reviewed
    assert build_report(loaded, created=OPENED, overrides={}) == plain
    model = build_report(loaded, created=OPENED, overrides=OVERRIDES)
    assert model.reviewed and model.violations() == []
    # The action plan keeps the machine's order and readings; the reviewer's column sits beside it.
    assert [e.code for e in model.plan] == [e.code for e in plain.plan]
    assert [e.reading for e in model.plan] == [e.reading for e in plain.plan]
    by_code = {e.code: e for e in model.plan}
    assert by_code["ACD 1"].reviewer.text == "Major Revision"
    assert all(
        e.reviewer.text == "No rating recorded by the reviewer"
        for e in model.plan
        if e.code not in OVERRIDES
    )
    # A row in full: a Reviewer's rating field after the proposal's, which is unchanged.
    details = {d.code: d for s in model.full for d in s.details}
    plain_details = {d.code: d for s in plain.full for d in s.details}
    names = [f.name.text for f in details["ACD 1"].fields]
    assert names[names.index("Proposed rating") + 1] == "Reviewer's rating"
    assert (
        details["ACD 1"].fields[: len(plain_details["ACD 1"].fields) - 1]
        == plain_details["ACD 1"].fields[: len(plain_details["ACD 1"].fields) - 1]
    )
    assert [f for f in details["ACD 1"].fields if f.name.text != "Reviewer's rating"] == list(
        plain_details["ACD 1"].fields
    )
    assert details["ACD 2"] == plain_details["ACD 2"]
    # A compact line: the proposal labelled as proposed, the reviewer's labelled as theirs.
    lines = {line.code: line for s in model.compact for line in s.lines}
    assert lines["COAL 2"].reading.text.startswith("Proposed: ")
    assert lines["COAL 2"].reviewer.text == "Reviewer's rating: Not Applicable"
    assert lines["MOB 6"].reading.text == "No rating proposed: the reviewer's call"
    assert lines["MOB 6"].reviewer.text == "Reviewer's rating: Minor Revision"
    assert lines["COAL 1"].reviewer is None

    text = " ".join(" ".join(page.text.split()) for page in read_pdf(to_pdf(model)))
    assert "Reviewer's rating: Not Applicable" in text
    assert "Proposed rating" in text and "Reviewer's rating" in text
    assert "it is printed as the reviewer's rating beside the proposed rating" in text
    plain_text = " ".join(" ".join(page.text.split()) for page in read_pdf(to_pdf(plain)))
    assert "Reviewer's rating" not in plain_text


def test_an_override_for_a_row_the_review_does_not_have_is_refused(loaded) -> None:
    with pytest.raises(WorkbookError, match="no row"):
        _workbook(loaded, {"NOPE 1": Rating.MAJOR})
    with pytest.raises(ReportError, match="no row"):
        build_report(loaded, created=OPENED, overrides={"NOPE 1": Rating.MAJOR})


def test_the_screens_export_carries_what_was_recorded(client, course, gate) -> None:
    set_reviewer(client)
    category(client, course, "ACD")
    assert record(client, course, "ACD 1", rating="major").status_code == 303
    assert client.post(f"/review/{course}/export", follow_redirects=False).status_code == 303
    assert reviewer_ratings(client.app.state.store, course) == {"ACD 1": Rating.MAJOR}  # type: ignore[attr-defined]
    book = openpyxl.load_workbook(
        io.BytesIO(gate.destination(course, WORKBOOK_FILENAME).read_bytes())
    )
    rows = list(book.worksheets[0].iter_rows(values_only=True))
    at = rows[0].index("Reviewer's rating")
    (acd1,) = [row for row in rows if row[1] == "ACD 1"]
    assert acd1[at] == "Major Revision" and acd1[at - 1] == "Minor Revision"
    text = " ".join(
        " ".join(page.text.split())
        for page in read_pdf(gate.destination(course, REPORT_FILENAME).read_bytes())
    )
    assert "Reviewer's rating" in text and REVIEWER not in text


def test_reading_keys_file_a_question_under_question_never_under_no_response(loaded) -> None:
    for row in loaded.projection.rows:
        if row.tier is Tier.C:
            assert reading_key(row) == QUESTION
        else:
            assert reading_key(row) == row.rating.value
