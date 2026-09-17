"""Progress per course and per stage, read back out of the run store (SPEC §09).

Nothing here is a second source of truth. A restart has only the database to read, so the progress
surfaces read the database too — and a failure that is in the store but not on the surface would
be exactly the silent failure the spec's "failures visible, never silent" is about.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sightline.app import create_app
from sightline.batch import (
    BatchRunner,
    CourseWork,
    ExportGate,
    Stage,
    StageError,
    StageOutcome,
    create_batch_run,
    pipeline_factory,
    progress_of,
)
from sightline.batch.progress import StageState
from sightline.config import Settings
from sightline.models import RunStatus
from sightline.paths import DataPaths
from sightline.store import RunStore

PACK = "uccs-oscqr-3-1-3"


@pytest.fixture
def store(paths: DataPaths) -> Iterator[RunStore]:
    opened = RunStore.open(paths)
    yield opened
    opened.dispose()


class HalfWorking:
    """Succeeds everywhere except one course, which fails at the extract stage."""

    def __init__(self, failing: str) -> None:
        self.failing = failing

    async def run(self, stage: Stage, work: CourseWork) -> StageOutcome:
        if work.reference == self.failing and stage is Stage.EXTRACT:
            raise StageError(
                stage,
                "The model host refused the chosen model for memory.",
                remedy="Choose a smaller model on the Settings screen.",
            )
        return work.record(
            StageOutcome(stage=stage, examined=2, statement=f"{stage.value}: examined 2.")
        )

    def review(self, work: CourseWork) -> Mapping[str, Any]:
        return {"course": {"reference": work.reference}}


async def _run_batch(store: RunStore, paths: DataPaths) -> str:
    run_id = create_batch_run(store, ["one", "two"], rubric_pack=PACK)
    await BatchRunner(store, paths, pipeline_factory(HalfWorking("two"))).execute(run_id)
    return run_id


async def test_progress_reports_every_stage_including_the_ones_not_reached(
    store: RunStore, paths: DataPaths
) -> None:
    run_id = await _run_batch(store, paths)
    progress = progress_of(store, run_id)
    assert progress is not None

    reviewed, failed = progress.courses
    assert [stage.state for stage in reviewed.stages] == [StageState.DONE] * 5
    assert [stage.state for stage in failed.stages] == [
        StageState.DONE,
        StageState.DONE,
        StageState.DONE,
        StageState.FAILED,
        StageState.PENDING,
    ]
    # A stage nobody reached is reported as pending rather than left out, so "three of five" is
    # answerable from the surface.
    assert failed.stages[-1].stage is Stage.SCORE


async def test_a_failure_is_carried_with_its_remedy(store: RunStore, paths: DataPaths) -> None:
    run_id = await _run_batch(store, paths)
    progress = progress_of(store, run_id)
    assert progress is not None
    failed = progress.courses[1]

    assert RunStatus(failed.status) is RunStatus.FAILED
    assert "refused the chosen model for memory" in (failed.error or "")
    assert "Choose a smaller model" in (failed.error or "")
    assert "Choose a smaller model" in (failed.stages[3].statement or "")
    assert failed.reviewable is False


async def test_the_headline_counts_reviews_that_have_been_opened(
    store: RunStore, paths: DataPaths
) -> None:
    run_id = await _run_batch(store, paths)
    progress = progress_of(store, run_id)
    assert progress is not None
    assert "0 review(s) opened" in progress.statement()
    assert progress.courses[0].reviewable is True
    assert progress.courses[0].review_opened is False

    ExportGate(store, paths).open_review(progress.courses[0].course_id)
    reopened = progress_of(store, run_id)
    assert reopened is not None
    assert "1 review(s) opened" in reopened.statement()
    assert reopened.courses[0].review_opened is True


def test_progress_of_an_unknown_run_is_none(store: RunStore) -> None:
    assert progress_of(store, "nope") is None


# ---------------------------------------------------------------------------------------------
# The JSON surface
# ---------------------------------------------------------------------------------------------


async def test_the_run_endpoint_reports_per_course_and_per_stage(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        store: RunStore = client.app.state.store
        paths: DataPaths = client.app.state.paths
        run_id = await _run_batch(store, paths)

        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["run_id"] == run_id
        assert payload["status"] == "partial"
        assert payload["status_label"] == "Completed with failures"
        assert len(payload["courses"]) == 2
        assert [stage["state"] for stage in payload["courses"][0]["stages"]] == ["done"] * 5
        assert payload["courses"][1]["stages"][3]["state"] == "failed"
        assert payload["courses"][1]["review_opened"] is False

        missing = client.get("/api/runs/nope")
        assert missing.status_code == 404
        assert "no run" in missing.json()["error"]
