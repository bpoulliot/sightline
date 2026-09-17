"""``python -m sightline.detect`` — the documented way to see what the detectors did.

The exit code is the assertion worth making twice. A detector that could not run is a gap in the
review, and a command that exits 0 over one is a command a script reads as a clean course.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sightline.detect.__main__ import main


def test_the_documented_command_runs_against_the_sample_course(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--bundle", str(repo_root / "examples" / "sample-course")])
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith("10 of 10 detector(s) examined the course")
    assert "alt_missing" in out
    assert "https://canvas.example.edu/courses/4410/pages/start-here" in out


def test_a_detector_that_could_not_run_is_listed_and_the_command_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bundle = tmp_path / "empty-course"
    (bundle / "bodies").mkdir(parents=True)
    (bundle / "course.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "base_url": "https://canvas.example.edu",
                "course": {"id": "4410", "name": "Empty"},
            }
        ),
        encoding="utf-8",
    )
    code = main(["--bundle", str(bundle)])
    out = capsys.readouterr().out
    assert code == 1
    assert "could not run" in out
    assert "their criteria are NOT covered by this run" in out


def test_json_output_is_the_whole_report(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--bundle", str(repo_root / "examples" / "sample-course"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert len(payload["results"]) == 10
    assert all(result["examined"] > 0 for result in payload["results"])


def test_one_detector_can_be_run_on_its_own(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--bundle", str(repo_root / "examples" / "sample-course"), "--detector", "D24"])
    out = capsys.readouterr().out
    assert code == 0
    assert "1 of 1 detector(s)" in out
    assert "table_without_header_cells" in out


def test_an_unknown_detector_says_which_ones_exist(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--bundle", str(repo_root / "examples" / "sample-course"), "--detector", "D99"])
    assert code == 2
    assert "no such detector: D99" in capsys.readouterr().err


def test_a_directory_that_is_not_a_bundle_is_reported_rather_than_traced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--bundle", str(tmp_path)])
    assert code == 1
    assert "not a course bundle" in capsys.readouterr().err
