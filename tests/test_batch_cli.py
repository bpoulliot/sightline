"""``python -m sightline.batch``: what it refuses, what it prints, and what it will not do.

The command is how a batch is started and read back until the review UI lands (roadmap item 16),
so the refusals matter as much as the run: a cap that is only enforced in a library is a cap the
one caller that exists can miss.

The bundle path is exercised end to end against ``examples/sample-course`` — acquire, detect and
project really run — and stops where a run without a model host has to stop.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from sightline.batch.__main__ import main
from sightline.config import reset_settings_cache
from sightline.models import RunStatus
from sightline.paths import DataPaths
from sightline.store import RunStore

SAMPLE = "examples/sample-course"


@pytest.fixture
def cli_root(monkeypatch: pytest.MonkeyPatch, data_root: Path, repo_root: Path) -> Iterator[Path]:
    """Point the command at an isolated data root, the way a deployment points it at a volume."""
    monkeypatch.setenv("SIGHTLINE_DATA_DIR", str(data_root))
    monkeypatch.chdir(repo_root)
    reset_settings_cache()
    yield data_root
    reset_settings_cache()


def _store(root: Path) -> RunStore:
    return RunStore.open(DataPaths.from_setting(root))


def test_course_and_bundle_cannot_be_mixed(
    cli_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", "--course", "101", "--bundle", SAMPLE]) == 2
    assert "not both" in capsys.readouterr().err


def test_nothing_to_review_is_refused(cli_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run"]) == 2
    assert "Nothing to review" in capsys.readouterr().err


def test_an_unknown_pack_lists_the_ones_that_exist(
    cli_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["run", "--bundle", SAMPLE, "--pack", "quality-matters"]) == 2
    err = capsys.readouterr().err
    assert "No rubric pack 'quality-matters'" in err
    assert "uccs-oscqr-3-1-3" in err


def test_the_cap_is_enforced_at_the_command(
    cli_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["run"]
    for _ in range(17):
        argv += ["--bundle", SAMPLE]
    assert main(argv) == 2
    assert "17 courses were given and the cap for one run is 16." in capsys.readouterr().err


def test_a_run_without_a_model_host_stops_and_says_what_to_do(
    cli_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The three stages that need no model really run; the fourth stops the batch, with a remedy."""
    assert main(["run", "--bundle", SAMPLE, "--pack", "uccs-oscqr-rsi"]) == 1
    printed = capsys.readouterr().out

    assert "artifact(s) read from a course bundle" in printed
    assert "detector(s) examined the course" in printed
    assert "row(s) projected" in printed
    assert "No model host and model are configured" in printed
    assert "Settings screen" in printed

    store = _store(cli_root)
    try:
        run = store.list_runs()[0]
        assert RunStatus(run.status) is RunStatus.INTERRUPTED
        assert RunStatus(run.courses[0].status) is RunStatus.INTERRUPTED
    finally:
        store.dispose()


def test_status_lists_runs_and_then_stages(
    cli_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["status"]) == 0
    assert "No runs recorded yet." in capsys.readouterr().out

    main(["run", "--bundle", SAMPLE, "--pack", "uccs-oscqr-rsi"])
    capsys.readouterr()

    store = _store(cli_root)
    try:
        run_id = store.list_runs()[0].id
    finally:
        store.dispose()

    assert main(["status"]) == 0
    assert run_id in capsys.readouterr().out

    assert main(["status", run_id]) == 0
    printed = capsys.readouterr().out
    assert "acquire  done" in printed
    assert "extract  failed" in printed
    assert "score    pending" in printed

    assert main(["status", "nope"]) == 2
    assert "No run 'nope'" in capsys.readouterr().err


def test_open_review_is_a_typed_action_and_says_what_it_permits(
    cli_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["open-review", "nope"]) == 2
    assert "No course 'nope'" in capsys.readouterr().err

    store = _store(cli_root)
    try:
        run_id = store.create_run(["101"], rubric_pack="uccs-oscqr-rsi")
        run = store.get_run(run_id)
        assert run is not None
        course_id = run.courses[0].id
    finally:
        store.dispose()

    assert main(["open-review", course_id]) == 0
    printed = capsys.readouterr().out
    assert "opened at" in printed
    assert "Sightline still writes none by itself" in printed

    store = _store(cli_root)
    try:
        course = store.get_course(course_id)
        assert course is not None and course.review_opened_at is not None
    finally:
        store.dispose()
