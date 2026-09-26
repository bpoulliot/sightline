"""The real pipeline: five stages over one course, against a bundle and a recorded model host.

No Canvas and no GPU. The course comes from ``examples/sample-course`` or from the synthetic
builders the detector suite uses, and the model host is item 09's recorded one — a suite that
needs either is a suite that does not run in CI.

What is asserted here that is not asserted anywhere else: the **order** is a dependency and not a
preference (project before extract, because routing reads coverage), every stage reports what it
examined, and the rollup carries counts and hours and nothing resembling a score.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

import detector_fixtures as fx
import ollama_recordings as recordings
from sightline.batch import (
    BatchAborted,
    BundleSource,
    CourseWork,
    NothingExamined,
    ReviewPipeline,
    Stage,
    StageError,
)
from sightline.batch.stages import ORDER
from sightline.llm.client import OllamaClient
from sightline.llm.errors import ModelHostUnreachableError
from sightline.model import CourseDocument
from sightline.rubric.shipped import shipped_pack

MODEL = "llama3.1:8b"

SYLLABUS = (
    "<h2>Course policies</h2>"
    "<p>Late work loses 10% per day, to a maximum of three days.</p>"
    "<p>I reply to email within 24 hours on weekdays.</p>"
)


class StaticSource:
    """A course that is already normalized. Keeps a pipeline test off the acquisition layer."""

    def __init__(self, document: CourseDocument) -> None:
        self.document = document

    async def acquire(self, _reference: str) -> tuple[CourseDocument, str]:
        return self.document, "3 artifact(s) read from a fixture."


def work(reference: str = "fixture") -> CourseWork:
    return CourseWork(reference=reference, course_id="course-1", ordinal=0)


def quoting_host(quote: str) -> recordings.FakeHost:
    """A host that answers every call with one verifiable quote from the syllabus."""
    host = recordings.only([MODEL])
    body = json.dumps({"findings": [{"quote": quote, "location": "Syllabus"}]})
    host.chat_responses = [recordings.chat_response(MODEL, body)]
    return host


def pipeline_over(
    document: CourseDocument, client: OllamaClient | None = None, pack: str = "uccs-oscqr-rsi"
) -> ReviewPipeline:
    return ReviewPipeline(
        source=StaticSource(document), pack=shipped_pack(pack), client=client, model=MODEL
    )


# ---------------------------------------------------------------------------------------------
# Acquire
# ---------------------------------------------------------------------------------------------


async def test_a_bundle_is_acquired_and_its_artifacts_counted(repo_root: Path) -> None:
    pipeline = ReviewPipeline(
        source=BundleSource(), pack=shipped_pack("uccs-oscqr-rsi"), client=None, model=None
    )
    state = work(str(repo_root / "examples" / "sample-course"))

    outcome = await pipeline.run(Stage.ACQUIRE, state)

    assert outcome.examined == sum(state.document.counts().values())
    assert outcome.examined > 20
    assert "artifact(s) read from a course bundle" in outcome.statement
    assert "7 page" in outcome.statement


async def test_a_bundle_that_cannot_be_read_fails_the_course_with_what_to_do(
    tmp_path: Path,
) -> None:
    pipeline = ReviewPipeline(
        source=BundleSource(), pack=shipped_pack("uccs-oscqr-rsi"), client=None, model=None
    )
    with pytest.raises(StageError) as raised:
        await pipeline.run(Stage.ACQUIRE, work(str(tmp_path)))
    assert raised.value.stage is Stage.ACQUIRE
    assert "could not be read" in raised.value.summary
    assert "examples/sample-course" in (raised.value.remedy or "")


async def test_a_course_with_no_artifacts_is_a_failure_not_a_clean_review() -> None:
    """Acquiring nothing is an unread course, not an empty one — SPEC §11's under-detection."""
    pipeline = pipeline_over(fx.empty_course())
    with pytest.raises(NothingExamined) as raised:
        await pipeline.run(Stage.ACQUIRE, work())
    assert "examined nothing" in raised.value.summary
    assert "not a clean course" in raised.value.summary


# ---------------------------------------------------------------------------------------------
# The order is a dependency
# ---------------------------------------------------------------------------------------------


def test_the_order_projects_before_it_extracts() -> None:
    assert ORDER == (Stage.ACQUIRE, Stage.DETECT, Stage.PROJECT, Stage.EXTRACT, Stage.SCORE)


async def test_extracting_before_projecting_has_nothing_to_ask_about() -> None:
    """Why the order differs from SPEC §04's table: routing reads a row's coverage.

    Coverage exists only once the detection report has been projected onto the pack, so a pipeline
    that extracted first would have to decide coverage somewhere else or ask a model about all 68
    rows of the reference instrument.
    """
    pipeline = pipeline_over(fx.one_page("<p>Hello.</p>"))
    state = work()
    await pipeline.run(Stage.ACQUIRE, state)
    await pipeline.run(Stage.DETECT, state)

    with pytest.raises(StageError) as raised:
        await pipeline.run(Stage.EXTRACT, state)
    assert "nothing was routed" in raised.value.summary


# ---------------------------------------------------------------------------------------------
# Detect, project, extract, score
# ---------------------------------------------------------------------------------------------


async def test_the_whole_pipeline_over_one_course() -> None:
    document = fx.course(syllabus=fx.syllabus(SYLLABUS), pages=[fx.page("week-1", "<p>Hi.</p>")])
    host = quoting_host("I reply to email within 24 hours on weekdays.")
    state = work()

    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=host.transport()) as client:
        pipeline = pipeline_over(document, client)
        outcomes = [await pipeline.run(stage, state) for stage in ORDER]
        review = pipeline.review(state)

    assert [outcome.stage for outcome in outcomes] == list(ORDER)
    # Every stage says what it examined, and nothing examined nothing.
    assert all(outcome.examined > 0 for outcome in outcomes)

    detect = outcomes[1]
    assert "detector(s) examined the course" in detect.statement

    project = outcomes[2]
    assert "row(s) projected" in project.statement
    assert "model call(s) on this course" in project.statement
    assert project.detail["routed"] > 0

    extract = outcomes[3]
    assert extract.detail["quotes"] >= 1
    assert extract.detail["model"] == MODEL

    score = outcomes[4]
    assert score.examined == len(state.projection.rows)
    assert "rolled up" in score.statement

    # What a reviewer opens: the arithmetic and the quotes side by side, joined on the code.
    assert review["projection"]["rows"]
    assert review["extraction"]["rows"]
    assert review["rollup"]["rows"] == len(state.projection.rows)
    assert review["course"]["name"] == document.course.name
    assert review["pack"]["id"] == "uccs-oscqr-rsi"


async def test_the_rollup_states_that_its_hours_are_uncalibrated() -> None:
    document = fx.course(syllabus=fx.syllabus(SYLLABUS))
    state = work()
    pipeline = pipeline_over(document)
    await pipeline.run(Stage.ACQUIRE, state)
    await pipeline.run(Stage.DETECT, state)
    await pipeline.run(Stage.PROJECT, state)
    outcome = await pipeline.run(Stage.SCORE, state)

    assert state.rollup is not None
    assert state.rollup.calibrated is False
    assert "uncalibrated" in outcome.statement
    assert "starting point for the reviewer, not a measurement" in outcome.statement


def test_the_rollup_carries_no_aggregate_quality_number() -> None:
    """SPEC §11 forbids one, and the instrument's own averaged VALUE column is why."""
    from sightline.batch.pipeline import Rollup

    fields = set(Rollup.__dataclass_fields__)
    assert not fields & {"score", "grade", "percent", "percentage", "quality", "average", "mean"}
    # Nor a course-level hours total: effort is not a schedule (roadmap item 12a took it out).
    assert not fields & {"hours", "total_hours", "total"}
    assert fields >= {"by_rating", "by_coverage", "rows_with_hours", "action_plan"}


# ---------------------------------------------------------------------------------------------
# The model host is a condition of the run
# ---------------------------------------------------------------------------------------------


async def test_no_model_configured_stops_the_batch_rather_than_the_course() -> None:
    document = fx.course(syllabus=fx.syllabus(SYLLABUS))
    state = work()
    pipeline = ReviewPipeline(
        source=StaticSource(document),
        pack=shipped_pack("uccs-oscqr-rsi"),
        client=None,
        model=None,
    )
    for stage in (Stage.ACQUIRE, Stage.DETECT, Stage.PROJECT):
        await pipeline.run(stage, state)

    with pytest.raises(BatchAborted) as raised:
        await pipeline.run(Stage.EXTRACT, state)
    assert "No model host and model are configured" in raised.value.summary
    assert "Settings screen" in (raised.value.remedy or "")


async def test_an_unreachable_host_aborts_the_batch_rather_than_failing_one_course() -> None:
    document = fx.course(syllabus=fx.syllabus(SYLLABUS))
    state = work()
    transport = recordings.raising_transport(
        ModelHostUnreachableError("Nothing answered at that address.", remedy="Check the endpoint.")
    )
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=transport) as client:
        pipeline = pipeline_over(document, client)
        for stage in (Stage.ACQUIRE, Stage.DETECT, Stage.PROJECT):
            await pipeline.run(stage, state)
        with pytest.raises(BatchAborted) as raised:
            await pipeline.run(Stage.EXTRACT, state)

    assert raised.value.stage is Stage.EXTRACT
    assert "Nothing answered at that address." in raised.value.message


def test_a_bundle_reference_is_recognised_by_the_filesystem(repo_root: Path) -> None:
    from sightline.batch.pipeline import is_bundle_reference

    assert is_bundle_reference(str(repo_root / "examples" / "sample-course")) is True
    assert is_bundle_reference("12345") is False
    assert is_bundle_reference("https://canvas.example.edu/courses/12345") is False


def test_the_review_artifact_keeps_arithmetic_and_quotes_apart() -> None:
    """A rating is arithmetic over findings; a quote is what a model located. Never one field."""
    state = work()
    pipeline = pipeline_over(fx.one_page("<p>Hello.</p>"))
    review: Mapping[str, object] = pipeline.review(state)
    assert set(review) >= {"projection", "extraction", "detection", "rollup", "stages"}
    assert "evidence" not in str(review["projection"])
