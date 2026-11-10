#!/usr/bin/env python3
"""The decisions `.gitea/workflows/release.yml` makes, where a test can reach them (item 49).

Standard library only, and Python 3.11 or later (`tomllib`): the release job runs in the runner's
`node:20` container, whose interpreter is Debian bookworm's 3.11, not this package's 3.12.

    release.py check-version TAG       print X.Y.Z; exit 1 unless TAG is vX.Y.Z and pyproject.toml
                                       says X.Y.Z
    release.py names VERSION           print the image tags to push: X.Y.Z, X.Y and, when VERSION is
                                       the highest v* tag by version order, latest
    release.py notes TAG [--to REF]    print the release note: the Conventional Commits since the
                                       previous v* tag, grouped by type (every commit if none)

`names` and `notes` read the repository's tags with git; the parsing and ordering they do are the
pure functions below, which is what the tests exercise.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

#: Only a plain `vMAJOR.MINOR.PATCH` publishes (roadmap item 49, *Decided 2026-11-02*). A
#: pre-release or build suffix is refused, not guessed at: `:X.Y` and `:latest` have no defined
#: meaning for one.
TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

#: `type(scope)!: subject`, the Conventional Commits header.
HEADER_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?: (?P<subject>.+)$")

#: Section order and headings of the note. A type not listed here goes under "Other".
SECTIONS: tuple[tuple[str, str], ...] = (
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("perf", "Performance"),
    ("refactor", "Refactoring"),
    ("docs", "Documentation"),
    ("test", "Tests"),
    ("build", "Build"),
    ("ci", "CI"),
    ("chore", "Chores"),
    ("style", "Style"),
    ("revert", "Reverts"),
)
OTHER = "Other (not Conventional Commits)"

REPO_ROOT = Path(__file__).resolve().parent.parent


class ReleaseError(Exception):
    """A tag or version the release must refuse. The message says why."""


def parse_tag(tag: str) -> tuple[int, int, int]:
    match = TAG_RE.match(tag)
    if match is None:
        raise ReleaseError(f"tag {tag!r} is not vMAJOR.MINOR.PATCH")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def pyproject_version(path: Path) -> str:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    try:
        return str(data["project"]["version"])
    except KeyError as exc:
        raise ReleaseError(f"{path} has no [project] version") from exc


def check_version(tag: str, pyproject: Path) -> str:
    """Return X.Y.Z when `tag` is vX.Y.Z and pyproject.toml says X.Y.Z; refuse anything else."""
    parse_tag(tag)
    version = tag[1:]
    declared = pyproject_version(pyproject)
    if declared != version:
        raise ReleaseError(
            f"tag {tag} says version {version}, pyproject.toml says {declared}: "
            "bump pyproject.toml in a PR first, then tag the merge"
        )
    return version


def version_tags(tags: list[str]) -> list[tuple[int, int, int]]:
    """Every tag that is a plain vX.Y.Z, as a version tuple. Other tags are not releases."""
    found = []
    for tag in tags:
        try:
            found.append(parse_tag(tag))
        except ReleaseError:
            continue
    return found


def image_names(version: str, tags: list[str]) -> list[str]:
    """The registry tags for `version`: X.Y.Z and X.Y, then latest only for the highest version.

    "Highest" is by version order over every vX.Y.Z tag, never by date, so a patch to an older
    minor (0.1.5 cut after 0.2.0) does not take `latest` back.
    """
    current = parse_tag(f"v{version}")
    names = [version, f"{current[0]}.{current[1]}"]
    known = version_tags(tags)
    if all(current >= other for other in known):
        names.append("latest")
    return names


def previous_tag(tag: str, tags: list[str]) -> str | None:
    """The highest vX.Y.Z tag below `tag` by version order, or None when there is none."""
    current = parse_tag(tag)
    below = [v for v in version_tags(tags) if v < current]
    if not below:
        return None
    major, minor, patch = max(below)
    return f"v{major}.{minor}.{patch}"


def render_notes(tag: str, previous: str | None, commits: list[tuple[str, str]]) -> str:
    """The release note in Markdown. `commits` is (sha, subject), newest first, merges excluded."""
    groups: dict[str, list[str]] = {}
    breaking: list[str] = []
    for sha, subject in commits:
        match = HEADER_RE.match(subject)
        short = sha[:7]
        if match is None:
            groups.setdefault(OTHER, []).append(f"- {subject} ({short})")
            continue
        kind = match["type"]
        heading = dict(SECTIONS).get(kind, OTHER)
        scope = f"**{match['scope']}:** " if match["scope"] else ""
        line = f"- {scope}{match['subject']} ({short})"
        if heading == OTHER:
            line = f"- {subject} ({short})"
        groups.setdefault(heading, []).append(line)
        if match["bang"]:
            breaking.append(line)

    if previous:
        since = f"since {previous}"
    else:
        since = "since the start of the history (no earlier v* tag)"
    out = [f"# Sightline {tag}", "", f"{len(commits)} commit(s) {since}, merges excluded.", ""]
    if breaking:
        out += ["## Breaking changes", "", *breaking, ""]
    for heading in [h for _, h in SECTIONS] + [OTHER]:
        if heading in groups:
            out += [f"## {heading}", "", *groups[heading], ""]
    if not commits:
        out += ["No commits.", ""]
    return "\n".join(out).rstrip("\n") + "\n"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], check=True, capture_output=True, text=True
    ).stdout


def _all_tags() -> list[str]:
    return _git("tag", "--list", "v*").split()


def _commits(rev_range: str) -> list[tuple[str, str]]:
    out = _git("log", "--no-merges", "--format=%H%x1f%s", rev_range)
    return [tuple(line.split("\x1f", 1)) for line in out.splitlines() if line]  # type: ignore[misc]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-version")
    check.add_argument("tag")
    check.add_argument("--pyproject", type=Path, default=REPO_ROOT / "pyproject.toml")
    names = sub.add_parser("names")
    names.add_argument("version")
    notes = sub.add_parser("notes")
    notes.add_argument("tag")
    notes.add_argument("--to", default=None, help="end of the range (default: the tag itself)")
    args = parser.parse_args(argv)

    try:
        if args.command == "check-version":
            print(check_version(args.tag, args.pyproject))
        elif args.command == "names":
            print(" ".join(image_names(args.version, _all_tags())))
        else:
            end = args.to or args.tag
            previous = previous_tag(args.tag, _all_tags())
            rev_range = f"{previous}..{end}" if previous else end
            print(render_notes(args.tag, previous, _commits(rev_range)), end="")
    except ReleaseError as exc:
        print(f"release: REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
