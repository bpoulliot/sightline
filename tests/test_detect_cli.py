"""``python -m sightline.detect`` — the documented way to see what the detectors did.

The exit code is the assertion worth making twice. A detector that could not run is a gap in the
review, and a command that exits 0 over one is a command a script reads as a clean course.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest

from sightline.detect import registry
from sightline.detect.__main__ import build_parser, main
from sightline.detect.base import Detector, Finding, Survey, registry_snapshot

#: Read from the registry rather than written down. A number typed here is one a new detector
#: silently falsifies, and the thing worth asserting is that the command ran *every* detector the
#: build ships and left none of them out of the headline.
SHIPPED = len(registry())


def test_the_documented_command_runs_against_the_sample_course(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--bundle", str(repo_root / "examples" / "sample-course")])
    out = capsys.readouterr().out
    assert code == 0
    assert out.startswith(f"{SHIPPED} of {SHIPPED} detector(s) examined the course")
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
    assert len(payload["results"]) == SHIPPED
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


def test_link_checking_is_off_unless_it_is_typed() -> None:
    """The only outbound traffic any detector makes, and it needs a flag.

    Asserted on the parser rather than by running the command, because a test that proved this by
    *not* observing a request would pass just as well if the flag were removed altogether.
    """
    parser = build_parser()
    assert parser.parse_args(["--bundle", "."]).resolve_links is False
    assert parser.parse_args(["--bundle", ".", "--resolve-links"]).resolve_links is True


def test_the_documented_run_makes_no_request_outside_canvas(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """D13 over the sample course, with the flag absent: it reports the policy link it found and
    records nothing it could not read, because it asked nothing."""
    code = main(["--bundle", str(repo_root / "examples" / "sample-course"), "--detector", "D13"])
    out = capsys.readouterr().out
    assert code == 0
    assert "found 0 observation(s)." in out
    assert "not read" not in out


def test_a_detector_that_crashes_is_named_the_rest_report_and_the_command_fails(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Item 27. One detector raising used to end the command with a traceback and no report."""
    with registry_snapshot():

        class Crashing(Detector):
            id = "T08"
            title = "A test detector that raises"
            collections = ("pages",)
            capability_tag = "page_titles"
            capability_description = "A test detector; any tag in the vocabulary serves here."

            def examine(self, survey: Survey) -> Iterable[Finding]:
                for _body in survey.bodies():
                    raise ValueError("unexpected markup")
                return ()

        bundle = str(repo_root / "examples" / "sample-course")
        code = main(["--bundle", bundle])
        captured = capsys.readouterr()
        json_code = main(["--bundle", bundle, "--json"])
        json_out = capsys.readouterr().out

    assert code == 1
    assert captured.out.startswith(f"{SHIPPED} of {SHIPPED + 1} detector(s) examined the course")
    assert "1 crashed and could not run (T08)" in captured.out
    assert "T08  This detector crashed: it raised ValueError" in captured.out
    # The scrubbed traceback goes to the log on stderr, never into the report.
    assert "detector_crashed" in captured.err
    assert "detector_crashed" not in captured.out

    assert json_code == 1
    payload = json.loads(json_out)  # stdout is still one parseable document
    by_id = {result["detector"]: result for result in payload["results"]}
    assert by_id["T08"]["crashed"] == "ValueError"
    assert by_id["T08"]["clean"] is False
    assert all(result["examined"] > 0 for detector, result in by_id.items() if detector != "T08")
