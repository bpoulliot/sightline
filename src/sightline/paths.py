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

import contextlib
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

#: Fixed subdirectories of the data root. Named here so that no call site spells them as literals.
SUBDIRECTORIES: tuple[str, ...] = ("exports", "cache", "runs", "reports", "secrets")

#: Filename of the run store, inside ``runs/``.
DB_FILENAME = "sightline.sqlite"

#: Filename of the credential encryption key, inside ``secrets/``.
KEY_FILENAME = "credentials.key"

#: Suffix a stream is written under before it is moved into place. A download that dies halfway
#: leaves one of these rather than a truncated file wearing the real name, which is the difference
#: between an obvious failure and a cache that quietly serves half a course export.
PARTIAL_SUFFIX = ".partial"

#: Permissions for ``secrets/`` and for anything in it. The key is the only thing standing between
#: a copied run store and a plaintext Canvas token, so it is owner-only.
SECRET_DIR_MODE = 0o700
SECRET_FILE_MODE = 0o600


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
        (self.root / "secrets").chmod(SECRET_DIR_MODE)
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

    @property
    def secrets_dir(self) -> Path:
        return self.resolve("secrets")

    @property
    def credentials_key_file(self) -> Path:
        return self.resolve("secrets", KEY_FILENAME)

    # -- writing ---------------------------------------------------------------------------
    #
    # Everything Sightline writes goes through one of these. That is not style: ``tests/
    # test_paths.py`` scans the package for ``mkdir``, ``open(..., "w")``, ``os.replace`` and the
    # rest, and fails the build on any of them outside this module. So a later item that wants to
    # write a file extends this section rather than reaching for ``open``, and the containment
    # guarantee keeps covering call sites nobody has written yet.

    @contextmanager
    def staged_write(self, *parts: str | os.PathLike[str]) -> Iterator[BinaryIO]:
        """Stream bytes to a path inside the root, made visible only once the writer succeeds.

        The bytes go to a sibling ``.partial`` file and are moved into place on a clean exit, so
        the destination never exists half-written. An exception removes the partial and
        propagates: a failed download that leaves nothing behind is easier to reason about than
        one that leaves something plausible.
        """
        target = self.resolve(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + PARTIAL_SUFFIX)
        # Not a `with`: this *is* the context manager, and the handle has to outlive the call so
        # the caller can stream into it. Closing and moving into place happens below.
        handle = open(partial, "wb")  # noqa: SIM115
        try:
            yield handle
        except BaseException:
            handle.close()
            with contextlib.suppress(OSError):
                os.unlink(partial)
            raise
        handle.close()
        os.replace(partial, target)

    def save_bytes(self, *parts: str | os.PathLike[str], data: bytes) -> Path:
        """Write ``data`` to a path inside the root, atomically. Returns the path.

        Named ``save_bytes`` rather than ``write_bytes`` on purpose. The structural guard in
        ``tests/test_paths.py`` matches on the *called name*, so a method here called
        ``write_bytes`` would be indistinguishable from ``pathlib.Path.write_bytes`` at every call
        site and would blunt the guard everywhere. It caught exactly that during item 03.
        """
        with self.staged_write(*parts) as handle:
            handle.write(data)
        return self.resolve(*parts)

    def save_text(self, *parts: str | os.PathLike[str], text: str) -> Path:
        """UTF-8 counterpart of :meth:`save_bytes`."""
        return self.save_bytes(*parts, data=text.encode("utf-8"))

    def discard_partials(self, *parts: str | os.PathLike[str]) -> int:
        """Remove leftover ``.partial`` files beneath a directory in the root.

        Called at startup rather than during a run: a partial belonging to a live writer and a
        partial abandoned by a dead process look identical, and one process in one container
        (SPEC §04) means the only live writer is this one.
        """
        directory = self.resolve(*parts)
        if not directory.is_dir():
            return 0
        removed = 0
        for leftover in sorted(directory.rglob(f"*{PARTIAL_SUFFIX}")):
            if not self.contains(leftover):  # pragma: no cover - rglob cannot leave the root
                continue
            with contextlib.suppress(OSError):
                os.unlink(leftover)
                removed += 1
        return removed

    # -- the one writable-file helper --------------------------------------------------------

    def read_or_create_key(self, generate: Callable[[], bytes]) -> bytes:
        """Return the credential key, creating it with owner-only permissions if absent.

        This module is the only one allowed to create a file (see ``tests/test_paths.py``), so the
        *placement and permissions* of the key live here while the *meaning* of the bytes lives in
        :mod:`sightline.credentials`. ``generate`` is called only when there is no key yet.

        The write is not atomic across processes and does not need to be: Sightline is one process
        in one container (SPEC §04). What it does guarantee is that the file never exists with
        wider permissions than :data:`SECRET_FILE_MODE`, because the descriptor is opened with
        that mode rather than chmod-ed afterwards.
        """
        target = self.credentials_key_file
        if target.is_file():
            return target.read_bytes()

        parent = self.resolve_dir("secrets")
        parent.chmod(SECRET_DIR_MODE)
        material = generate()
        if not material:
            raise DataPathError("the key generator produced no material")
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, SECRET_FILE_MODE)
        try:
            os.write(descriptor, material)
        finally:
            os.close(descriptor)
        return material
