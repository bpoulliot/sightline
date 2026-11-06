"""``python -m sightline.backup`` — a consistent snapshot of Sightline's state (roadmap item 50).

    python -m sightline.backup /backups              # writes /backups/sightline-backup-<UTC stamp>/
    python -m sightline.backup --check /backups/sightline-backup-20261105T031500Z

**What is kept** is Sightline's state, the things that cannot be fetched again: the run store
(runs, courses, run logs, recorded ratings and overrides, encrypted Canvas tokens, preferences),
and, under the data root, the review files in ``runs/``, the accepted rubric bindings in
``rubrics/``, the delivered artifacts in ``reports/`` and whatever the operator keeps in
``private/`` (the pilot keeps returned, edited reviews there).

**What is left out, on purpose**: ``exports/`` and ``cache/``, which can be fetched again and hold
course content that should not travel off the host; and ``secrets/``. The master key that
decrypts the stored tokens is backed up separately, through the host's secrets handling, never
beside the data it unlocks — in sign-in mode it is ``SIGHTLINE_SECRET_KEY`` and never in the
volume at all. A snapshot restored under a different key restores every token unreadable.

**Why the store is not simply copied.** The store runs in WAL mode: committed transactions live
in ``sightline.sqlite-wal`` until a checkpoint moves them into the main file. A file copy of a live
store is a main file from one moment and a log from another, or no log at all, and either can
lose runs or hand back a database that does not open. The snapshot is taken through SQLite's
online backup API from a read-only connection, which reads one consistent state while a run goes
on writing, and is written out as a single self-contained file.

**Containment.** Everything read is resolved through :class:`sightline.paths.DataPaths`, so a
symlink planted in a kept directory is skipped and named rather than followed off the root; the
snapshot is written through the same gate, into a new owner-only directory that is renamed into
place only when complete. A destination inside the data root is refused.

``--check`` opens a snapshot read-only and reports what it holds: the runs in its store, file
counts per directory, and any problem — a file that does not match the manifest, a left-out
directory that is present, a store that fails SQLite's integrity check. It writes nothing and
needs no configuration, so it runs as well on the machine the snapshot was copied to.

Restoring is documented in ``docs/PILOT.md`` and deliberately not automated. So is the schedule:
taking a backup every night is the host's job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .paths import (
    BACKUP_KEPT,
    BACKUP_LEFT_OUT,
    DB_FILENAME,
    PARTIAL_SUFFIX,
    DataPathError,
    DataPaths,
    abandon_snapshot,
    begin_snapshot,
    finish_snapshot,
)

#: Directories under the data root a snapshot keeps, in the order it reports them.
KEPT: tuple[str, ...] = BACKUP_KEPT

#: Directories a snapshot never holds. ``--check`` reports any of them present as a problem.
LEFT_OUT: tuple[str, ...] = BACKUP_LEFT_OUT

#: The store's own files in ``runs/``. The database arrives through the backup API instead; the
#: write-ahead log, its index and a rollback journal are the store's working state, not data.
STORE_FILES: frozenset[str] = frozenset(
    {DB_FILENAME, *(f"{DB_FILENAME}{suffix}" for suffix in ("-wal", "-shm", "-journal"))}
)

#: Where the store sits inside a snapshot: where it sits in the data root, so a restore is a copy.
STORE_IN_SNAPSHOT = f"runs/{DB_FILENAME}"

MANIFEST = "MANIFEST.json"
MANIFEST_FORMAT = 1
SNAPSHOT_PREFIX = "sightline-backup-"

#: Read and hash in pieces: a delivered PDF or a course's review file can be large.
CHUNK = 1 << 20


class BackupError(RuntimeError):
    """The snapshot could not be taken. Nothing is left behind under the destination."""


@dataclass
class Copied:
    path: str
    size: int
    sha256: str


@dataclass
class Snapshot:
    """What a backup wrote."""

    path: Path
    files: list[Copied]
    skipped: list[dict[str, str]]


# -- taking a snapshot ----------------------------------------------------------------------------


def snapshot_name(now: datetime) -> str:
    return f"{SNAPSHOT_PREFIX}{now.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def take(data: DataPaths, destination: str | os.PathLike[str], *, now: datetime) -> Snapshot:
    """Snapshot ``data`` into a new directory under ``destination``.

    Raises :class:`sightline.paths.SnapshotRefused` for a destination it will not write to, and
    :class:`BackupError` when there is nothing to back up or the copy fails; in the second case
    the partial snapshot is removed.
    """
    store = data.database
    if not store.is_file():
        raise BackupError(f"no run store at {store}: is SIGHTLINE_DATA_DIR the data root?")
    snapshot = begin_snapshot(data, destination, snapshot_name(now))
    try:
        files = [_backup_store(store, snapshot)]
        skipped: list[dict[str, str]] = []
        for name in KEPT:
            for source, relative in _walk_kept(data, name, skipped):
                files.append(_copy(source, relative, snapshot))
        manifest = {
            "format": MANIFEST_FORMAT,
            "created_at": now.astimezone(UTC).isoformat(),
            "sightline_version": __version__,
            "kept": list(KEPT),
            "left_out": list(LEFT_OUT),
            "store": STORE_IN_SNAPSHOT,
            "files": [file.__dict__ for file in files],
            "skipped": skipped,
        }
        snapshot.save_text(MANIFEST, text=json.dumps(manifest, indent=2) + "\n")
        final = finish_snapshot(snapshot)
    except BaseException as exc:
        abandon_snapshot(snapshot)
        if isinstance(exc, (OSError, sqlite3.Error)):
            raise BackupError(f"the snapshot failed and was removed: {exc}") from exc
        raise
    return Snapshot(path=final, files=files, skipped=skipped)


def _backup_store(store: Path, snapshot: DataPaths) -> Copied:
    """Copy the live store through SQLite's online backup API, as one self-contained file."""
    snapshot.resolve_dir("runs")
    target = snapshot.resolve("runs", DB_FILENAME)
    # Read-only, so the backup cannot be what changes the store. A WAL reader holds one
    # consistent view for the length of the copy; the writer carries on beside it.
    source = sqlite3.connect(f"{store.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
            # The header carries the source's WAL mode; a snapshot should open as one file,
            # anywhere, read-only, without a log or an index beside it.
            copy.execute("PRAGMA journal_mode=DELETE")
            (verdict,) = copy.execute("PRAGMA integrity_check").fetchone()
        finally:
            copy.close()
    finally:
        source.close()
    if verdict != "ok":
        raise sqlite3.DatabaseError(f"the copied store failed its integrity check: {verdict}")
    size, digest = _hash(target)
    return Copied(path=STORE_IN_SNAPSHOT, size=size, sha256=digest)


def _walk_kept(
    data: DataPaths, name: str, skipped: list[dict[str, str]]
) -> Iterator[tuple[Path, str]]:
    """Every regular file under the kept directory ``name``, as ``(source, relative path)``.

    Symlinks are never followed, inside the root or out of it: Sightline makes none, so one is
    either somebody's mistake or somebody's attempt, and it is named in the manifest either way.
    """
    top = data.root / name
    if top.is_symlink():
        skipped.append({"path": name, "reason": "symlink"})
        return
    if not top.is_dir():
        return
    top = data.resolve(name)
    for directory, subdirectories, filenames in os.walk(top, followlinks=False):
        here = Path(directory)
        for subdirectory in sorted(subdirectories):
            if (here / subdirectory).is_symlink():
                skipped.append({"path": _relative(data, here / subdirectory), "reason": "symlink"})
        subdirectories.sort()
        for filename in sorted(filenames):
            source = here / filename
            relative = _relative(data, source)
            if name == "runs" and here == top and filename in STORE_FILES:
                continue
            if filename.endswith(PARTIAL_SUFFIX):
                # A write in progress, or abandoned by a dead process: not state either way.
                continue
            if source.is_symlink():
                skipped.append({"path": relative, "reason": "symlink"})
                continue
            if not source.is_file():
                skipped.append({"path": relative, "reason": "not a regular file"})
                continue
            # The gate again, per file, after resolution: what is read is inside the root.
            yield data.resolve(relative), relative


def _relative(data: DataPaths, path: Path) -> str:
    return path.relative_to(data.root).as_posix()


def _copy(source: Path, relative: str, snapshot: DataPaths) -> Copied:
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as reader, snapshot.staged_write(relative) as writer:
        while chunk := reader.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
            writer.write(chunk)
    return Copied(path=relative, size=size, sha256=digest.hexdigest())


def _hash(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as reader:
        while chunk := reader.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


# -- checking a snapshot --------------------------------------------------------------------------


@dataclass
class RunRow:
    id: str
    status: str
    created_at: str
    courses: int


@dataclass
class Report:
    """What ``--check`` found. ``problems`` empty means the snapshot is whole."""

    path: Path
    created_at: str | None = None
    runs: list[RunRow] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    directories: dict[str, tuple[int, int]] = field(default_factory=dict)
    skipped: list[dict[str, str]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def check(path: str | os.PathLike[str]) -> Report:
    """Open a snapshot read-only and report what it holds. Writes nothing."""
    root = Path(path).expanduser().absolute()
    report = Report(path=root)
    if not root.is_dir():
        report.problems.append(f"{root} is not a directory")
        return report
    try:
        manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.problems.append(f"{MANIFEST} cannot be read: {exc}")
        return report
    if manifest.get("format") != MANIFEST_FORMAT:
        report.problems.append(f"{MANIFEST} is format {manifest.get('format')!r}, not 1")
        return report
    report.created_at = manifest.get("created_at")
    report.skipped = list(manifest.get("skipped") or [])

    for name in LEFT_OUT:
        if (root / name).exists():
            report.problems.append(f"{name}/ is present, and a snapshot never holds it")

    listed: dict[str, dict[str, Any]] = {entry["path"]: entry for entry in manifest["files"]}
    for relative, entry in sorted(listed.items()):
        candidate = root / relative
        if not _within(root, candidate) or not candidate.is_file():
            report.problems.append(f"{relative} is listed but missing")
            continue
        size, digest = _hash(candidate)
        if (size, digest) != (entry["size"], entry["sha256"]):
            report.problems.append(f"{relative} does not match the manifest")
    present = {
        file.relative_to(root).as_posix()
        for file in root.rglob("*")
        if file.is_file() or file.is_symlink()
    }
    for relative in sorted(present - set(listed) - {MANIFEST}):
        report.problems.append(f"{relative} is present but not in the manifest")

    for name in KEPT:
        entries = [entry for path, entry in listed.items() if path.split("/", 1)[0] == name]
        report.directories[name] = (len(entries), sum(entry["size"] for entry in entries))

    store = root / manifest.get("store", STORE_IN_SNAPSHOT)
    if store.is_file() and _within(root, store):
        _read_store(store, report)
    else:
        report.problems.append("the run store is missing")
    return report


def _within(root: Path, candidate: Path) -> bool:
    real = Path(os.path.realpath(candidate))
    return real.is_relative_to(Path(os.path.realpath(root)))


def _read_store(store: Path, report: Report) -> None:
    # immutable=1: no lock, no log, no index file is created beside a snapshot by reading it.
    try:
        connection = sqlite3.connect(f"{store.as_uri()}?mode=ro&immutable=1", uri=True)
    except sqlite3.Error as exc:  # pragma: no cover - a missing file is caught above
        report.problems.append(f"the run store does not open: {exc}")
        return
    try:
        (verdict,) = connection.execute("PRAGMA integrity_check").fetchone()
        if verdict != "ok":
            report.problems.append(f"the run store fails its integrity check: {verdict}")
            return
        rows = connection.execute(
            "SELECT runs.id, runs.status, runs.created_at, COUNT(run_courses.id) "
            "FROM runs LEFT JOIN run_courses ON run_courses.run_id = runs.id "
            "GROUP BY runs.id ORDER BY runs.created_at, runs.id"
        ).fetchall()
        report.runs = [
            RunRow(id=str(r[0]), status=str(r[1]), created_at=str(r[2]), courses=int(r[3]))
            for r in rows
        ]
        scalar = connection.execute
        report.counts = {
            "runs": len(rows),
            "courses": scalar("SELECT COUNT(*) FROM run_courses").fetchone()[0],
            "run events": scalar("SELECT COUNT(*) FROM run_events").fetchone()[0],
            "recorded ratings": scalar("SELECT COUNT(*) FROM rating_overrides").fetchone()[0],
            "stored credentials": scalar("SELECT COUNT(*) FROM app_secrets").fetchone()[0],
            "settings": scalar("SELECT COUNT(*) FROM app_settings").fetchone()[0],
        }
    except sqlite3.Error as exc:
        report.problems.append(f"the run store cannot be read: {exc}")
    finally:
        connection.close()


def render(report: Report) -> str:
    """The report as the command prints it: counts and run ids, never a course or a person."""
    lines = [f"Snapshot {report.path}"]
    if report.created_at:
        lines.append(f"  taken {report.created_at}")
    for name, value in report.counts.items():
        lines.append(f"  {name}: {value}")
    for name, (files, size) in report.directories.items():
        lines.append(f"  {name}/: {files} file{'' if files == 1 else 's'}, {size:,} bytes")
    if report.runs:
        lines.append("  runs:")
        lines.extend(
            f"    {run.id}  {run.status:<11}  {run.courses} course"
            f"{'' if run.courses == 1 else 's'}  created {run.created_at}"
            for run in report.runs
        )
    for entry in report.skipped:
        lines.append(f"  skipped when taken: {entry['path']} ({entry['reason']})")
    if report.problems:
        lines.append(f"  {len(report.problems)} problem(s):")
        lines.extend(f"    {problem}" for problem in report.problems)
    else:
        lines.append("  OK: every file matches the manifest and the store passes its check")
    return "\n".join(lines)


# -- the command ----------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sightline.backup",
        description=(
            "Snapshot Sightline's state (the run store, runs/, rubrics/, reports/, private/) "
            "into a new dated directory under DEST, or check a snapshot. Never exports/, "
            "cache/ or secrets/."
        ),
    )
    parser.add_argument(
        "destination",
        nargs="?",
        help="Directory to write the snapshot under. Must not be inside the data root.",
    )
    parser.add_argument(
        "--check",
        metavar="SNAPSHOT",
        help="Open a snapshot read-only and report what it holds, instead of taking one.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if (args.destination is None) == (args.check is None):
        parser.error("give a destination to take a snapshot, or --check SNAPSHOT, not both")
    if args.check is not None:
        report = check(args.check)
        print(render(report))
        return 0 if report.ok else 1

    # Imported here so that --check needs no configuration: it runs wherever the snapshot is.
    from .config import get_settings

    data = get_settings().paths
    try:
        taken = take(data, args.destination, now=datetime.now(UTC))
    except DataPathError as exc:
        print(f"No backup taken: {exc}", file=sys.stderr)
        return 2
    except BackupError as exc:
        print(f"No backup taken: {exc}", file=sys.stderr)
        return 1
    report = check(taken.path)
    print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
