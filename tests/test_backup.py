"""``python -m sightline.backup``: a consistent snapshot of Sightline's state (roadmap item 50).

The trap the item names is that the store runs in WAL mode, so a file copy of a live store is not
a backup. The central test takes a snapshot while a run is writing, and its control proves the
setup really is that trap: a plain copy of ``sightline.sqlite`` taken at the same moment does not
hold the run the snapshot lists. Without the control, a passing snapshot test could be a test of a
store that happened to have been checkpointed.

Everything here is synthetic: invented run contents, ``canvas.example.edu``, fake tokens.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from sightline import backup
from sightline.config import reset_settings_cache
from sightline.paths import DB_FILENAME, PARTIAL_SUFFIX, DataPaths, SnapshotRefused
from sightline.store import RunStore

NOW = datetime(2026, 11, 5, 3, 15, 0, tzinfo=UTC)
COURSE = "https://canvas.example.edu/courses/101"

#: One synthetic file per directory: three kept, three left out.
PLANTED = {
    "rubrics/subject-fake/bindings.yaml": b"bindings: []\n",
    "reports/run-fake/report.pdf": b"%PDF-1.7 fake report\n",
    "private/returned/action-plan-edited.docx": b"fake edited action plan\n",
    "exports/course-101.imscc": b"fake course export\n",
    "cache/blob-0001": b"fake cached page\n",
    "secrets/credentials.key": b"fake-key-file\n",
}


@pytest.fixture
def live(paths: DataPaths) -> RunStore:
    store = RunStore.open(paths)
    yield store
    store.dispose()


def plant(paths: DataPaths, run_id: str) -> None:
    for relative, data in PLANTED.items():
        paths.save_bytes(relative, data=data)
    paths.save_text("runs", run_id, "course-0", "review.json", text='{"fake": "review"}\n')


def runs_in(database: Path) -> list[str]:
    """Run ids in a store file, read without touching it; [] when it holds no run table."""
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        return [row[0] for row in connection.execute("SELECT id FROM runs")]
    except sqlite3.DatabaseError:
        return []
    finally:
        connection.close()


# -- the trap -------------------------------------------------------------------------------------


def test_a_snapshot_taken_while_a_run_is_writing_opens_and_lists_the_run(
    paths: DataPaths, live: RunStore, tmp_path: Path
) -> None:
    run_id = live.create_run([COURSE], rubric_pack="oscqr-3.1.3")
    plant(paths, run_id)
    assert (paths.database.with_name(DB_FILENAME + "-wal")).stat().st_size > 0

    # The control, taken first: a file copy of the live store does not hold the run, because the
    # run is still in the write-ahead log. If this ever fails, the test below proves nothing.
    naive = tmp_path / "naive.sqlite"
    shutil.copyfile(paths.database, naive)
    assert run_id not in runs_in(naive)

    stop = threading.Event()
    written: list[int] = []

    def writer() -> None:
        while not stop.is_set():
            live.append_event(run_id, "course_progress", stage="detect", n=len(written))
            written.append(1)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        while len(written) < 20:  # the writer is demonstrably going before the snapshot starts
            pass
        taken = backup.take(paths, tmp_path / "backups", now=NOW)
        during = len(written)
    finally:
        stop.set()
        thread.join()
    assert during > 20 and len(written) > 0

    report = backup.check(taken.path)
    assert report.ok, report.problems
    assert [run.id for run in report.runs] == [run_id]
    assert report.runs[0].courses == 1
    # A consistent state from while the writer ran: at least the events before it started, and
    # no more than were ever written (plus the run's own creation event).
    assert 20 <= report.counts["run events"] - 1 <= len(written)


def test_the_snapshot_keeps_state_and_leaves_out_exports_cache_and_secrets(
    paths: DataPaths, live: RunStore, tmp_path: Path
) -> None:
    run_id = live.create_run([COURSE], rubric_pack="oscqr-3.1.3")
    plant(paths, run_id)
    taken = backup.take(paths, tmp_path / "backups", now=NOW)

    assert taken.path == tmp_path / "backups" / "sightline-backup-20261105T031500Z"
    held = sorted(
        p.relative_to(taken.path).as_posix() for p in taken.path.rglob("*") if p.is_file()
    )
    assert held == sorted(
        [
            backup.MANIFEST,
            "private/returned/action-plan-edited.docx",
            "reports/run-fake/report.pdf",
            "rubrics/subject-fake/bindings.yaml",
            f"runs/{DB_FILENAME}",
            f"runs/{run_id}/course-0/review.json",
        ]
    )
    for left_out in ("exports", "cache", "secrets"):
        assert not (taken.path / left_out).exists()
    # One self-contained file: no log, no index, and it opens on its own.
    assert runs_in(taken.path / "runs" / DB_FILENAME) == [run_id]
    assert oct(taken.path.stat().st_mode & 0o777) == oct(0o700)
    report = backup.check(taken.path)
    assert report.ok, report.problems
    assert report.directories["private"] == (
        1,
        len(PLANTED["private/returned/action-plan-edited.docx"]),
    )


# -- where it may write ---------------------------------------------------------------------------


@pytest.mark.parametrize("inside", ["", "runs", "reports/backups", "private"])
def test_a_destination_inside_the_data_root_is_refused(
    paths: DataPaths, live: RunStore, inside: str
) -> None:
    before = sorted(paths.root.rglob("*"))
    with pytest.raises(SnapshotRefused, match="inside the data root"):
        backup.take(paths, paths.root / inside if inside else paths.root, now=NOW)
    assert sorted(paths.root.rglob("*")) == before


def test_a_destination_that_resolves_into_the_data_root_is_refused(
    paths: DataPaths, live: RunStore, tmp_path: Path
) -> None:
    alias = tmp_path / "looks-elsewhere"
    alias.symlink_to(paths.root / "reports", target_is_directory=True)
    with pytest.raises(SnapshotRefused, match="inside the data root"):
        backup.take(paths, alias, now=NOW)
    assert list((paths.root / "reports").iterdir()) == []


def test_an_existing_snapshot_is_not_written_into(
    paths: DataPaths, live: RunStore, tmp_path: Path
) -> None:
    backup.take(paths, tmp_path / "backups", now=NOW)
    with pytest.raises(SnapshotRefused, match="already exists"):
        backup.take(paths, tmp_path / "backups", now=NOW)
    later = backup.take(paths, tmp_path / "backups", now=NOW + timedelta(seconds=1))
    assert later.path.name == "sightline-backup-20261105T031501Z"


def test_no_store_means_no_backup_and_nothing_written(tmp_path: Path) -> None:
    empty = DataPaths.from_setting(tmp_path / "never-used")
    with pytest.raises(backup.BackupError, match="no run store"):
        backup.take(empty, tmp_path / "backups", now=NOW)
    assert not (tmp_path / "backups").exists()
    assert not (tmp_path / "never-used").exists()


def test_a_failed_snapshot_leaves_nothing_behind(
    paths: DataPaths, live: RunStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = live.create_run([COURSE], rubric_pack="oscqr-3.1.3")
    plant(paths, run_id)

    def fail(*_args: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup, "_copy", fail)
    with pytest.raises(backup.BackupError, match="removed"):
        backup.take(paths, tmp_path / "backups", now=NOW)
    assert list((tmp_path / "backups").iterdir()) == []


# -- what it reads --------------------------------------------------------------------------------


def test_a_symlink_in_a_kept_directory_is_named_and_not_followed(
    paths: DataPaths, live: RunStore, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "not-sightline.txt").write_text("fake data from elsewhere\n", encoding="utf-8")
    paths.resolve_dir("reports")
    (paths.root / "reports" / "escape").symlink_to(outside, target_is_directory=True)
    (paths.root / "reports" / "file-link").symlink_to(outside / "not-sightline.txt")
    paths.resolve_dir("private")
    os.rmdir(paths.root / "private")
    (paths.root / "private").symlink_to(outside, target_is_directory=True)

    taken = backup.take(paths, tmp_path / "backups", now=NOW)
    assert sorted(entry["path"] for entry in taken.skipped) == [
        "private",
        "reports/escape",
        "reports/file-link",
    ]
    assert not list(taken.path.rglob("not-sightline.txt"))
    report = backup.check(taken.path)
    assert report.ok and len(report.skipped) == 3


def test_partials_and_the_stores_own_files_are_not_copied(
    paths: DataPaths, live: RunStore, tmp_path: Path
) -> None:
    run_id = live.create_run([COURSE], rubric_pack="oscqr-3.1.3")
    paths.save_text("runs", run_id, f"review.json{PARTIAL_SUFFIX}", text="half a write")
    taken = backup.take(paths, tmp_path / "backups", now=NOW)
    names = {p.name for p in taken.path.rglob("*")}
    assert f"{DB_FILENAME}-wal" not in names and f"{DB_FILENAME}-shm" not in names
    assert not any(name.endswith(PARTIAL_SUFFIX) for name in names)


# -- --check, in both directions ------------------------------------------------------------------


@pytest.fixture
def snapshot(paths: DataPaths, live: RunStore, tmp_path: Path) -> Path:
    plant(paths, live.create_run([COURSE], rubric_pack="oscqr-3.1.3"))
    path = backup.take(paths, tmp_path / "backups", now=NOW).path
    assert backup.check(path).ok
    return path


def test_check_reports_a_file_that_no_longer_matches(snapshot: Path) -> None:
    (snapshot / "reports" / "run-fake" / "report.pdf").write_bytes(b"%PDF changed\n")
    assert backup.check(snapshot).problems == [
        "reports/run-fake/report.pdf does not match the manifest"
    ]


def test_check_reports_a_left_out_directory_and_an_unlisted_file(snapshot: Path) -> None:
    (snapshot / "exports").mkdir()
    (snapshot / "exports" / "course.imscc").write_bytes(b"fake")
    problems = backup.check(snapshot).problems
    assert "exports/ is present, and a snapshot never holds it" in problems
    assert "exports/course.imscc is present but not in the manifest" in problems


def test_check_reports_a_missing_file_and_a_damaged_store(snapshot: Path) -> None:
    (snapshot / "private" / "returned" / "action-plan-edited.docx").unlink()
    store = snapshot / "runs" / DB_FILENAME
    store.write_bytes(store.read_bytes()[:4096])
    problems = backup.check(snapshot).problems
    assert "private/returned/action-plan-edited.docx is listed but missing" in problems
    assert f"runs/{DB_FILENAME} does not match the manifest" in problems
    assert any("run store" in problem for problem in problems)


def test_check_writes_nothing(snapshot: Path) -> None:
    before = {p: p.stat().st_mtime_ns for p in snapshot.rglob("*")}
    backup.check(snapshot)
    assert {p: p.stat().st_mtime_ns for p in snapshot.rglob("*")} == before


def test_the_report_names_no_course(snapshot: Path) -> None:
    text = backup.render(backup.check(snapshot))
    assert "OK:" in text and "runs: 1" in text
    assert "canvas.example.edu" not in text and "courses/101" not in text


# -- the command ----------------------------------------------------------------------------------


def test_the_command_takes_and_checks_a_snapshot(
    paths: DataPaths,
    live: RunStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = live.create_run([COURSE], rubric_pack="oscqr-3.1.3")
    monkeypatch.setenv("SIGHTLINE_DATA_DIR", str(paths.root))
    monkeypatch.chdir(tmp_path)  # no .env from the checkout
    reset_settings_cache()
    try:
        assert backup.main([str(tmp_path / "backups")]) == 0
        out = capsys.readouterr().out
        assert run_id in out and "OK:" in out
        (taken,) = (tmp_path / "backups").iterdir()
        assert backup.main(["--check", str(taken)]) == 0
        assert run_id in capsys.readouterr().out
        assert backup.main([str(paths.root / "reports")]) == 2
        assert "inside the data root" in capsys.readouterr().err
    finally:
        reset_settings_cache()


def test_the_command_wants_exactly_one_of_a_destination_and_check() -> None:
    with pytest.raises(SystemExit):
        backup.main([])
    with pytest.raises(SystemExit):
        backup.main(["/tmp/a", "--check", "/tmp/b"])


def test_check_runs_as_a_module_with_no_configuration(snapshot: Path, tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("SIGHTLINE_", "CANVAS_"))}
    # Sign-in mode with no key would refuse any command that reads settings; --check reads none.
    env["SIGHTLINE_SIGNIN"] = "true"
    result = subprocess.run(
        [sys.executable, "-m", "sightline.backup", "--check", str(snapshot)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    manifest = json.loads((snapshot / backup.MANIFEST).read_text(encoding="utf-8"))
    assert manifest["left_out"] == ["exports", "cache", "secrets"]
    assert "OK:" in result.stdout
