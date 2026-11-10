"""scripts/release.py: the decisions the release workflow makes (roadmap item 49).

The workflow itself runs only on the casper runner against the real registry; what can be tested
here is every refusal and every choice it delegates to the script: the tag must match
pyproject.toml, only the highest version takes `latest`, and the note groups commits by type.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "release.py"
_spec = importlib.util.spec_from_file_location("sightline_release_script", SCRIPT)
assert _spec is not None and _spec.loader is not None
release = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = release
_spec.loader.exec_module(release)


def _pyproject(tmp_path: Path, version: str) -> Path:
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[project]\nname = "x"\nversion = "{version}"\n', encoding="utf-8")
    return path


def test_a_tag_matching_pyproject_passes_and_prints_the_bare_version(tmp_path: Path) -> None:
    assert release.check_version("v0.1.0", _pyproject(tmp_path, "0.1.0")) == "0.1.0"


@pytest.mark.parametrize("tag", ["v0.1.1", "v1.1.0", "v0.10.0"])
def test_a_tag_whose_version_differs_from_pyproject_is_refused(tmp_path: Path, tag: str) -> None:
    with pytest.raises(release.ReleaseError, match="pyproject.toml says 0.1.0"):
        release.check_version(tag, _pyproject(tmp_path, "0.1.0"))


@pytest.mark.parametrize("tag", ["0.1.0", "v0.1", "v0.1.0-rc1", "v01.1.0", "vx.y.z", "v0.1.0 "])
def test_a_tag_that_is_not_plain_semver_is_refused(tmp_path: Path, tag: str) -> None:
    with pytest.raises(release.ReleaseError, match="not vMAJOR.MINOR.PATCH"):
        release.check_version(tag, _pyproject(tmp_path, "0.1.0"))


def test_the_command_line_exits_1_on_a_mismatch_and_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pyproject = _pyproject(tmp_path, "0.1.0")
    assert release.main(["check-version", "v0.1.0", "--pyproject", str(pyproject)]) == 0
    assert capsys.readouterr().out == "0.1.0\n"
    assert release.main(["check-version", "v9.9.9", "--pyproject", str(pyproject)]) == 1
    assert "REFUSED" in capsys.readouterr().err


def test_the_committed_pyproject_version_is_one_a_tag_can_match() -> None:
    version = release.pyproject_version(SCRIPT.parent.parent / "pyproject.toml")
    assert release.check_version(f"v{version}", SCRIPT.parent.parent / "pyproject.toml") == version


def test_the_first_release_takes_latest() -> None:
    assert release.image_names("0.1.0", ["v0.1.0"]) == ["0.1.0", "0.1", "latest"]
    assert release.image_names("0.1.0", []) == ["0.1.0", "0.1", "latest"]


def test_latest_goes_by_version_order_not_by_date() -> None:
    # A patch to an older minor, cut after 0.2.0, must not take `latest` back. git lists tags in
    # name order, where v0.10.0 sorts before v0.9.0: the comparison has to be numeric.
    tags = ["v0.1.0", "v0.1.1", "v0.10.0", "v0.2.0", "v0.9.0"]
    assert release.image_names("0.1.1", tags) == ["0.1.1", "0.1"]
    assert release.image_names("0.9.0", tags) == ["0.9.0", "0.9"]
    assert release.image_names("0.10.0", tags) == ["0.10.0", "0.10", "latest"]


def test_tags_that_are_not_releases_do_not_count_toward_latest() -> None:
    assert release.image_names("0.1.0", ["v0.1.0", "v9.0.0-rc1", "vnext"]) == [
        "0.1.0",
        "0.1",
        "latest",
    ]


def test_the_previous_tag_is_by_version_order() -> None:
    tags = ["v0.10.0", "v0.2.0", "v0.9.0", "v0.1.0"]
    assert release.previous_tag("v0.10.0", tags) == "v0.9.0"
    assert release.previous_tag("v0.1.0", tags) is None
    assert release.previous_tag("v0.3.0", tags) == "v0.2.0"


def test_the_note_groups_conventional_commits_by_type_in_a_fixed_order() -> None:
    commits = [
        ("a" * 40, "fix(ci): name the container"),
        ("b" * 40, "feat: a pushed tag publishes the image"),
        ("c" * 40, "docs: item 49 DONE"),
        ("d" * 40, "feat(api)!: drop the old endpoint"),
        ("e" * 40, "Tidy things up"),
        ("f" * 40, "wip: something"),
    ]
    note = release.render_notes("v0.2.0", "v0.1.0", commits)
    lines = note.splitlines()
    assert lines[0] == "# Sightline v0.2.0"
    assert "6 commit(s) since v0.1.0, merges excluded." in note
    order = [line for line in lines if line.startswith("## ")]
    assert order == [
        "## Breaking changes",
        "## Features",
        "## Fixes",
        "## Documentation",
        "## Other (not Conventional Commits)",
    ]
    assert "- a pushed tag publishes the image (bbbbbbb)" in lines
    assert "- **ci:** name the container (aaaaaaa)" in lines
    assert lines.count("- **api:** drop the old endpoint (ddddddd)") == 2  # breaking, and feat
    assert "- Tidy things up (eeeeeee)" in lines
    assert "- wip: something (fffffff)" in lines


def test_the_first_note_says_there_was_no_earlier_tag() -> None:
    note = release.render_notes("v0.1.0", None, [("a" * 40, "feat: first")])
    assert "since the start of the history (no earlier v* tag)" in note
    assert release.render_notes("v0.1.0", None, []).rstrip().endswith("No commits.")
