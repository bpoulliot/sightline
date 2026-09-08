"""Data-root containment.

The repository is public, so a write that lands outside ``SIGHTLINE_DATA_DIR`` is the bug that
eventually commits a course export. Two guarantees are tested here:

1. **Behavioural** — every path the resolver can hand out is inside the configured root, including
   when the root is somewhere outside the repository entirely, and including when something inside
   the root is a symlink pointing out of it.
2. **Structural** — no module in the package creates a directory or opens a file for writing
   except through :mod:`sightline.paths`. A behavioural test only covers the call sites it knows
   about; this one covers the ones a later item adds.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from sightline.paths import SUBDIRECTORIES, DataPathError, DataPathEscape, DataPaths

ESCAPE_ATTEMPTS = [
    "..",
    "../outside.txt",
    "../../outside.txt",
    "exports/../../outside.txt",
    "exports/../../../etc/passwd",
    "/etc/passwd",
    "/",
    "\\windows\\system32",
    "C:/Windows/system32",
    "reports/./../../escape",
    "a/b/../../../c",
]

INSIDE_PATHS = [
    ("exports",),
    ("exports", "course-1.imscc"),
    ("cache", "canvas", "pages.json"),
    ("runs", "abc123", "state.json"),
    ("reports", "abc123", "report.pdf"),
    ("exports/nested/deep/file.txt",),
    ("./exports", "file.txt"),
]


def test_ensure_creates_root_and_subdirectories(data_root: Path) -> None:
    paths = DataPaths.from_setting(data_root)
    assert not data_root.exists()
    paths.ensure()
    assert data_root.is_dir()
    for name in SUBDIRECTORIES:
        assert (data_root / name).is_dir()


def test_ensure_is_idempotent(paths: DataPaths) -> None:
    paths.ensure()
    paths.ensure()
    assert paths.root.is_dir()


@pytest.mark.parametrize("attempt", ESCAPE_ATTEMPTS)
def test_escape_attempts_are_rejected(paths: DataPaths, attempt: str) -> None:
    with pytest.raises(DataPathEscape):
        paths.resolve(attempt)


@pytest.mark.parametrize("attempt", ESCAPE_ATTEMPTS)
def test_escape_attempts_are_rejected_as_second_component(paths: DataPaths, attempt: str) -> None:
    with pytest.raises(DataPathError):
        paths.resolve("exports", attempt)


@pytest.mark.parametrize("parts", INSIDE_PATHS)
def test_permitted_paths_land_inside_the_root(paths: DataPaths, parts: tuple[str, ...]) -> None:
    resolved = paths.resolve(*parts)
    assert paths.contains(resolved)
    assert resolved.is_relative_to(paths.root)


def test_empty_requests_are_rejected(paths: DataPaths) -> None:
    with pytest.raises(DataPathError):
        paths.resolve()
    with pytest.raises(DataPathError):
        paths.resolve("")
    with pytest.raises(DataPathError):
        paths.resolve(".")


def test_symlink_out_of_the_root_is_rejected(paths: DataPaths, tmp_path: Path) -> None:
    """A symlink planted inside the root must not become a way out of it."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (paths.root / "exports" / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(DataPathEscape):
        paths.resolve("exports", "link", "stolen.txt")


def test_named_locations_are_all_inside_the_root(paths: DataPaths) -> None:
    for location in (paths.exports, paths.cache, paths.runs, paths.reports, paths.database):
        assert paths.contains(location), location


def test_run_dir_is_inside_the_root_and_created(paths: DataPaths) -> None:
    run_dir = paths.run_dir("0123456789abcdef")
    assert run_dir.is_dir()
    assert paths.contains(run_dir)


def test_run_dir_rejects_a_hostile_run_id(paths: DataPaths) -> None:
    with pytest.raises(DataPathEscape):
        paths.run_dir("../../etc")


def test_database_url_points_inside_the_root(paths: DataPaths) -> None:
    url = paths.database_url()
    assert url.startswith("sqlite+pysqlite:///")
    assert paths.contains(url.removeprefix("sqlite+pysqlite:///"))


def test_a_root_outside_the_repository_is_honoured(tmp_path: Path, repo_root: Path) -> None:
    """The default root is ``./data``; a real deployment points somewhere else entirely."""
    external = tmp_path / "elsewhere" / "sightline"
    paths = DataPaths.from_setting(external).ensure()
    assert not paths.root.is_relative_to(repo_root)
    target = paths.resolve("exports", "course.imscc")
    assert target.is_relative_to(external)
    assert not paths.contains(repo_root / "data")


# ---------------------------------------------------------------------------------------------
# Structural guard
# ---------------------------------------------------------------------------------------------

#: Calls that create or write to a filesystem location whatever they are called on. Anything here
#: outside ``paths.py`` is a write path the containment tests above cannot see.
WRITE_CALLS = {
    "mkdir",
    "makedirs",
    "write_text",
    "write_bytes",
    "touch",
    "mkstemp",
    "mkdtemp",
    "NamedTemporaryFile",
    "TemporaryDirectory",
    "copyfile",
    "copytree",
    "rmtree",
}

#: Names that only mean "write to the filesystem" when qualified. ``str.replace`` is not a write.
QUALIFIED_WRITE_CALLS = {
    "os.replace",
    "os.rename",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "shutil.move",
    "shutil.copy",
    "shutil.copy2",
    "pathlib.Path.replace",
}

#: The only module allowed to build a writable path.
GATEKEEPER = "paths.py"

WRITE_MODES = set("wxa+")


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _dotted_name(node: ast.Call) -> str:
    """Best-effort dotted spelling of the callee, e.g. ``os.replace``."""
    parts: list[str] = []
    current: ast.expr = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _is_write_open(node: ast.Call) -> bool:
    if _called_name(node) not in {"open"}:
        return False
    mode: str | None = None
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
        mode = str(node.args[1].value)
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            mode = str(keyword.value.value)
    if mode is None:
        return False
    return bool(WRITE_MODES & set(mode))


def test_only_the_paths_module_creates_writable_locations(repo_root: Path) -> None:
    package = repo_root / "src" / "sightline"
    offenders: list[str] = []
    scanned = 0
    for source in sorted(package.rglob("*.py")):
        if source.name == GATEKEEPER:
            continue
        scanned += 1
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _called_name(node)
            if (
                name in WRITE_CALLS
                or _dotted_name(node) in QUALIFIED_WRITE_CALLS
                or _is_write_open(node)
            ):
                offenders.append(f"{source.relative_to(repo_root)}:{node.lineno} {name or 'open'}")

    # A guard that examined nothing is not a guard. Report what was examined, as every detector
    # in this project is required to.
    assert scanned > 0, "the structural guard examined zero modules"
    assert not offenders, (
        "these call sites write outside sightline.paths, so the data-root containment tests "
        f"cannot see them: {offenders}"
    )
