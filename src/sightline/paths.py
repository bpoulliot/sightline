"""The data root, and the guarantee that nothing the app writes escapes it.

Everything Sightline downloads or generates — course exports, caches, run state, reports — lives
beneath ``SIGHTLINE_DATA_DIR``. The repository is public, so a write that lands outside that root
is the bug that eventually commits a course export. This module is the only place in the package
allowed to create a directory or hand out a writable path; ``tests/test_paths.py`` enforces that
structurally by scanning the source tree.

:meth:`DataPaths.resolve` is the single gate. It rejects absolute components, parent traversal and
symlinks that point out of the root, and it does so after resolving the real path, not before.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: Fixed subdirectories of the data root. Named here so that no call site spells them as literals.
SUBDIRECTORIES: tuple[str, ...] = ("exports", "cache", "runs", "reports")

#: Filename of the run store, inside ``runs/``.
DB_FILENAME = "sightline.sqlite"


class DataPathError(ValueError):
    """Base class for data-root violations."""


class DataPathEscape(DataPathError):
    """A requested path resolved outside the configured data root."""


@dataclass(frozen=True)
class DataPaths:
    """Resolver for every path the application may write to.

    ``root`` is expanded and made absolute at construction. It is *not* resolved through symlinks
    at construction, because the root itself may legitimately be a symlink (a bind mount, a volume
    under ``/data``); resolution happens per-request against the realpath of the root.
    """

    root: Path

    @classmethod
    def from_setting(cls, value: str | os.PathLike[str]) -> DataPaths:
        return cls(root=Path(value).expanduser().absolute())

    # -- construction ------------------------------------------------------------------------

    def ensure(self) -> DataPaths:
        """Create the root and its fixed subdirectories. Idempotent."""
        self.root.mkdir(parents=True, exist_ok=True)
        for name in SUBDIRECTORIES:
            (self.root / name).mkdir(parents=True, exist_ok=True)
        return self

    # -- the gate ----------------------------------------------------------------------------

    def resolve(self, *parts: str | os.PathLike[str]) -> Path:
        """Return an absolute path inside the root, or raise :class:`DataPathEscape`.

        Rejects, in order: an empty request, an absolute or drive-qualified component, any
        ``..`` component, and finally anything whose real path falls outside the real root. The
        last check is what catches a symlink planted inside the root.
        """
        if not parts:
            raise DataPathError("a data path requires at least one component")

        pieces: list[str] = []
        for part in parts:
            text = os.fspath(part)
            if text == "":
                raise DataPathError("empty path component")
            if text.startswith(("/", "\\")) or (len(text) > 1 and text[1] == ":"):
                raise DataPathEscape(f"absolute path component is not allowed: {text!r}")
            pieces.extend(p for p in PurePosixPath(text.replace("\\", "/")).parts if p != ".")

        if not pieces:
            raise DataPathError("path resolved to no components")
        if any(piece == ".." for piece in pieces):
            raise DataPathEscape("parent traversal is not allowed")

        candidate = self.root.joinpath(*pieces)

        # os.path.realpath resolves symlinks for the parts that exist and leaves the rest
        # lexically joined, which is exactly what is wanted for a path about to be created.
        real_root = Path(os.path.realpath(self.root))
        real_candidate = Path(os.path.realpath(candidate))
        if real_candidate != real_root and not real_candidate.is_relative_to(real_root):
            raise DataPathEscape("resolved path is outside the data root")
        return candidate

    def resolve_dir(self, *parts: str | os.PathLike[str]) -> Path:
        """Resolve a directory inside the root and create it."""
        target = self.resolve(*parts)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def contains(self, path: str | os.PathLike[str]) -> bool:
        """True when ``path`` is the root or lies beneath it, after symlink resolution."""
        real_root = Path(os.path.realpath(self.root))
        real_path = Path(os.path.realpath(Path(path).expanduser().absolute()))
        return real_path == real_root or real_path.is_relative_to(real_root)

    # -- named locations ---------------------------------------------------------------------

    @property
    def exports(self) -> Path:
        return self.resolve("exports")

    @property
    def cache(self) -> Path:
        return self.resolve("cache")

    @property
    def runs(self) -> Path:
        return self.resolve("runs")

    @property
    def reports(self) -> Path:
        return self.resolve("reports")

    @property
    def database(self) -> Path:
        return self.resolve("runs", DB_FILENAME)

    def database_url(self) -> str:
        """SQLAlchemy URL for the run store."""
        return f"sqlite+pysqlite:///{self.database}"

    def run_dir(self, run_id: str) -> Path:
        """Per-run working directory. ``run_id`` is untrusted and goes through the gate."""
        return self.resolve_dir("runs", run_id)
