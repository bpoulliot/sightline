"""Nothing exports until a human has opened the review, and the guard is structural.

SPEC §09 states it as a product rule. This module holds it as two facts that can fail a build: no
module outside :mod:`sightline.paths` and :mod:`sightline.batch.gate` names ``reports`` as a path,
and a completed batch leaves that directory empty.

The second is the one worth being loud about. Roadmap items 13 and 14 write the XLSX workbook and
the PDF report, and neither exists yet — so the thing that catches them reaching past the gate has
to exist *before* they are written, not after somebody notices a report in a directory nobody
opened.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from sightline.batch import (
    BatchRunner,
    CourseWork,
    ExportGate,
    ReviewNotOpenedError,
    Stage,
    StageOutcome,
    create_batch_run,
    pipeline_factory,
)
from sightline.paths import DataPaths
from sightline.store import RunStore

#: The two modules allowed to name the reports directory. Everything else asks the gate.
ALLOWED = {"paths.py", "gate.py"}


@pytest.fixture
def store(paths: DataPaths) -> Iterator[RunStore]:
    opened = RunStore.open(paths)
    yield opened
    opened.dispose()


class TrivialPipeline:
    """Five stages that succeed and examine something. Enough to finish a course."""

    async def run(self, stage: Stage, work: CourseWork) -> StageOutcome:
        return work.record(
            StageOutcome(stage=stage, examined=1, statement=f"{stage.value}: examined 1.")
        )

    def review(self, work: CourseWork) -> Mapping[str, Any]:
        return {"course": {"reference": work.reference}}


# ---------------------------------------------------------------------------------------------
# The structural half
# ---------------------------------------------------------------------------------------------


def test_only_the_gate_and_the_paths_module_name_the_reports_directory(repo_root: Path) -> None:
    package = repo_root / "src" / "sightline"
    offenders: list[str] = []
    scanned = 0
    for source in sorted(package.rglob("*.py")):
        if source.name in ALLOWED:
            continue
        scanned += 1
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            named = (isinstance(node, ast.Constant) and node.value == "reports") or (
                isinstance(node, ast.Attribute) and node.attr == "reports"
            )
            if named:
                offenders.append(f"{source.relative_to(repo_root)}:{node.lineno}")

    assert scanned > 0, "the structural guard examined zero modules"
    assert not offenders, (
        "these call sites name the reports directory directly, which routes around the review "
        f"gate in sightline.batch.gate: {offenders}"
    )


def test_the_batch_runner_does_not_import_the_gate(repo_root: Path) -> None:
    """The loop has no way to export, rather than a rule saying it must not."""
    batch = repo_root / "src" / "sightline" / "batch"
    for name in ("runner.py", "pipeline.py", "service.py", "stages.py"):
        source = (batch / name).read_text(encoding="utf-8")
        assert "gate" not in {
            node.module.split(".")[-1] if node.module else ""
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
        }, f"{name} imports the export gate"


# ---------------------------------------------------------------------------------------------
# The behavioural half
# ---------------------------------------------------------------------------------------------


async def test_a_completed_batch_writes_nothing_under_reports(
    store: RunStore, paths: DataPaths
) -> None:
    run_id = create_batch_run(store, ["one", "two"], rubric_pack="uccs-oscqr-3-1-3")
    summary = await BatchRunner(store, paths, pipeline_factory(TrivialPipeline())).execute(run_id)

    assert len(summary.reviewed) == 2
    assert list(paths.reports.rglob("*")) == []
    # The results a reviewer opens are there, under runs/ — the run is not empty, it is unexported.
    assert [outcome.review_path for outcome in summary.reviewed] != [None, None]


def test_an_export_is_refused_until_somebody_opens_the_review(
    store: RunStore, paths: DataPaths
) -> None:
    run_id = create_batch_run(store, ["one"], rubric_pack="uccs-oscqr-3-1-3")
    run = store.get_run(run_id)
    assert run is not None
    course_id = run.courses[0].id
    gate = ExportGate(store, paths)

    assert gate.is_open(course_id) is False
    with pytest.raises(ReviewNotOpenedError) as raised:
        gate.destination(course_id, "review.xlsx")
    assert "Nobody has opened the review" in raised.value.summary
    assert "does not generate a report nobody has read" in (raised.value.remedy or "")

    opened = gate.open_review(course_id)
    destination = gate.destination(course_id, "review.xlsx")
    assert destination == paths.resolve("reports", course_id, "review.xlsx")
    assert gate.opened_at(course_id) == opened
    # Nothing was written by opening the gate. It permits a write; it does not perform one.
    assert list(paths.reports.rglob("*")) == []


def test_opening_a_review_twice_does_not_move_the_timestamp(
    store: RunStore, paths: DataPaths
) -> None:
    run_id = create_batch_run(store, ["one"], rubric_pack="uccs-oscqr-3-1-3")
    run = store.get_run(run_id)
    assert run is not None
    course_id = run.courses[0].id
    gate = ExportGate(store, paths)

    first = gate.open_review(course_id)
    assert gate.open_review(course_id) == first
    opened = [event for event in store.events_in_order(run_id) if event.event == "review_opened"]
    assert len(opened) == 1


def test_the_gate_refuses_a_course_it_has_never_heard_of(store: RunStore, paths: DataPaths) -> None:
    with pytest.raises(KeyError):
        ExportGate(store, paths).require_open("not-a-course")


def test_an_exported_filename_cannot_escape_the_data_root(
    store: RunStore, paths: DataPaths
) -> None:
    """A later item will build a filename from a course name. The containment gate still applies."""
    run_id = create_batch_run(store, ["one"], rubric_pack="uccs-oscqr-3-1-3")
    run = store.get_run(run_id)
    assert run is not None
    course_id = run.courses[0].id
    gate = ExportGate(store, paths)
    gate.open_review(course_id)

    from sightline.paths import DataPathEscape

    with pytest.raises(DataPathEscape):
        gate.destination(course_id, "../../escaped.xlsx")
