"""``python -m sightline.acquire`` — the entry point that needs no Canvas token.

It is documented in the README, so it is tested: a documented command that nobody runs in CI stops
working quietly. The assertions are about what it prints and, as much, about what it does not —
a course body is instructor-authored content and a terminal is somewhere things get pasted from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sightline.acquire.__main__ import build_parser, main


@pytest.fixture
def sample_dir(repo_root: Path) -> Path:
    return repo_root / "examples" / "sample-course"


def test_the_sample_course_can_be_read_without_a_token(
    sample_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--bundle", str(sample_dir)]) == 0
    out = capsys.readouterr().out
    assert "Introduction to Synthetic Studies" in out
    assert "artifacts:" in out
    assert "examined:" in out


def test_the_description_prints_no_course_body(
    sample_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--bundle", str(sample_dir)])
    out = capsys.readouterr().out
    assert "<h2>" not in out
    assert "Analysis is the part of the course" not in out


def test_the_description_states_counts_rather_than_a_verdict(
    sample_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """SPEC §08 tier A: the count and the location, no hedge and no judgement."""
    main(["--bundle", str(sample_dir)])
    out = capsys.readouterr().out.lower()
    for verdict in ("score", "grade", "evaluation", "pass/fail", "poor", "excellent"):
        assert verdict not in out


def test_json_output_is_the_whole_document(
    sample_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    assert main(["--bundle", str(sample_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["course"]["id"] == "4410"
    assert payload["base_url"] == "https://canvas.example.edu"


def test_a_directory_that_is_not_a_bundle_fails_with_a_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--bundle", str(tmp_path)]) == 1
    assert "course.json" in capsys.readouterr().err


def test_no_argument_at_all_explains_itself(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "--bundle" in capsys.readouterr().err


def test_a_canvas_run_without_configuration_says_what_to_set(
    monkeypatch: pytest.MonkeyPatch,
    data_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Never "Error". Every failure names what to change.

    Settings are supplied explicitly rather than left to be read from the environment: a developer
    with a working ``.env`` in the repository would otherwise have this test reach a real Canvas
    instance, which is the one thing the suite must never do.
    """
    from sightline.config import Settings

    monkeypatch.setattr(
        "sightline.acquire.__main__.get_settings",
        lambda: Settings(_env_file=None, data_dir=data_root),
    )
    assert main(["4410"]) == 2
    err = capsys.readouterr().err
    assert "CANVAS_BASE_URL" in err
    assert "--bundle" in err


def test_the_fresh_export_request_can_be_turned_off_for_one_run() -> None:
    """A deployment with a no-writes policy sets the preference. Somebody debugging one course
    wants it for one run, and should not have to change a stored setting to get it."""
    args = build_parser().parse_args(["4410", "--no-request-export"])
    assert args.no_request_export is True
    assert build_parser().parse_args(["4410"]).no_request_export is False


def test_the_two_export_flags_are_different_things_and_say_so() -> None:
    """``--no-export`` uses no export at all; ``--no-request-export`` uses one that already
    exists. Conflating them would silently drop document bytes that were available."""
    # Read off the actions rather than the formatted help: argparse wraps, and an assertion that
    # fails on a line break is an assertion about terminal width.
    described = {
        action.dest: action.help or "" for action in build_parser()._actions  # noqa: SLF001
    }
    assert "no content export at all" in described["no_export"]
    assert "already has" in described["no_request_export"]
    assert described["no_export"] != described["no_request_export"]
