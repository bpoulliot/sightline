"""``.dockerignore`` keeps host bytecode and secret-shaped files out of the image at any depth.

Items 37 (bytecode) and 39 (secret- and data-shaped files).

Docker matches a bare ``.dockerignore`` pattern against the build context's root only, so
``__pycache__/`` excluded nothing under ``src/`` or ``tests/``: a locally built image carried the
developer's bytecode, and every ``.pyc`` in it carried the absolute path it was compiled under — a
username and a home directory. ``.gitignore`` means "at any depth" by the same line; this one has
to say so with ``**/``.

The file is part of the build context, not of the image, so the dev image does not carry it. The
test skips there and runs everywhere the context is — CI's checkout included. It skips only when the
Dockerfile is missing too, so a ``.dockerignore`` that disappears from a checkout fails rather than
skipping.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERIGNORE = REPO_ROOT / ".dockerignore"

#: Each pattern the file must carry, exactly as Docker reads it: anchored at no particular depth.
REQUIRED_PATTERNS = ("**/__pycache__", "**/*.py[cod]")


def _patterns() -> set[str]:
    if not DOCKERIGNORE.is_file():
        if not (REPO_ROOT / "Dockerfile").is_file():
            pytest.skip("not a build context (the dev image carries no .dockerignore)")
        pytest.fail(".dockerignore is missing beside the Dockerfile")
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


@pytest.mark.parametrize("pattern", REQUIRED_PATTERNS)
def test_bytecode_is_excluded_at_every_depth(pattern: str) -> None:
    assert pattern in _patterns()


# ---------------------------------------------------------------------------------------------
# Secret- and data-shaped files at every depth (roadmap item 39). Every pattern above was bare too,
# so a `.env` or an `.imscc` under examples/ or src/ was kept out of git by .gitignore and still
# copied into the image. These tests plant one file of each shape below the root and compute the
# build context the way Docker does.


def _docker_regex(pattern: str) -> re.Pattern[str]:
    """One ``.dockerignore`` pattern as Docker's pattern matcher (moby/patternmatcher) reads it.

    ``**`` spans any number of path segments, ``*`` and ``?`` stay inside one, ``[...]`` is a
    class, and the whole pattern is anchored at the context root.
    """
    pattern = pattern.strip("/")
    out, i = "^", 0
    while i < len(pattern):
        ch = pattern[i]
        if pattern.startswith("**", i):
            i += 2
            if pattern.startswith("/", i):
                out += "(.*/)?"
                i += 1
            else:
                out += ".*"
            continue
        if ch == "*":
            out += "[^/]*"
        elif ch == "?":
            out += "[^/]"
        elif ch == "[":
            end = pattern.index("]", i)
            out += pattern[i : end + 1]
            i = end
        else:
            out += re.escape(ch)
        i += 1
    return re.compile(out + "$")


def _rules() -> list[tuple[re.Pattern[str], bool]]:
    if not DOCKERIGNORE.is_file():
        _patterns()  # skips or fails exactly as the pattern tests do
    rules = []
    for raw in DOCKERIGNORE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keep = line.startswith("!")
        rules.append((_docker_regex(line.lstrip("!")), keep))
    return rules


def _in_context(path: str, rules: list[tuple[re.Pattern[str], bool]]) -> bool:
    """Whether Docker sends ``path`` (relative, ``/``-separated) to the build.

    The last pattern that matches the path or any directory above it decides, as in Docker.
    """
    parts = path.split("/")
    candidates = ["/".join(parts[: n + 1]) for n in range(len(parts))]
    included = True
    for regex, keep in rules:
        if any(regex.match(candidate) for candidate in candidates):
            included = keep
    return included


#: One planted file of each secret- and data-shape, below the root where the image's COPYs reach.
NESTED_SECRETS = (
    "examples/sample-course/.env",
    "examples/sample-course/.env.local",
    "src/sightline/course.imscc",
    "tests/fixtures/server.pem",
    "src/sightline/server.key",
    "examples/canvas.token",
    "src/sightline/config.local.toml",
    "tests/run.sqlite",
    "tests/run.sqlite3",
    "src/sightline/runs.db",
    "examples/secrets/canvas.txt",
    "rubrics/private/notes.txt",
    "src/instance/state.json",
    "examples/data/export.zip",
    "src/.pytest_cache/v/cache",
    "src/.ruff_cache/content",
    "tests/.coverage",
    "src/.venv/pyvenv.cfg",
    "src/sightline.egg-info/PKG-INFO",
)


def test_matcher_reads_docker_patterns_both_ways() -> None:
    # The fact item 37 measured on a real build: a bare pattern is root-only, `**/` is any depth.
    bare = [(_docker_regex("__pycache__"), False)]
    deep = [(_docker_regex("**/__pycache__"), False)]
    assert not _in_context("__pycache__/a.pyc", bare)
    assert _in_context("src/__pycache__/a.pyc", bare)
    assert not _in_context("src/__pycache__/a.pyc", deep)
    assert not _in_context("__pycache__/a.pyc", deep)
    assert _in_context("src/pycache/a.py", deep)
    # A later exception re-includes, an earlier one does not.
    assert _in_context(
        "docs/tokens.css",
        [(_docker_regex("docs/"), False), (_docker_regex("docs/tokens.css"), True)],
    )
    assert not _in_context(
        "docs/tokens.css",
        [(_docker_regex("docs/tokens.css"), True), (_docker_regex("docs/"), False)],
    )


@pytest.mark.parametrize("path", NESTED_SECRETS)
def test_nested_secret_and_data_files_stay_out_of_the_context(path: str, tmp_path: Path) -> None:
    planted = tmp_path / path
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text("synthetic\n", encoding="utf-8")
    sent = {
        p.relative_to(tmp_path).as_posix()
        for p in tmp_path.rglob("*")
        if p.is_file() and _in_context(p.relative_to(tmp_path).as_posix(), _rules())
    }
    assert path not in sent


def test_env_example_is_kept_at_every_depth() -> None:
    rules = _rules()
    assert _in_context(".env.example", rules)
    assert _in_context("examples/.env.example", rules)


def test_every_tracked_file_the_image_copies_stays_in_the_context() -> None:
    """Widening a pattern must not drop a file the Dockerfile copies (``**/data/`` above all)."""
    rules = _rules()
    try:
        listed = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
            capture_output=True,
            check=True,
        ).stdout.decode()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    copied = ("src/", "tests/", "examples/", "rubrics/", "assets/")
    copied_files = ("pyproject.toml", "uv.lock", "README.md", "docs/tokens.css", "docs/SPEC.md")
    tracked = [p for p in listed.split("\0") if p.startswith(copied) or p in copied_files]
    assert tracked
    assert [p for p in tracked if not _in_context(p, rules)] == []
