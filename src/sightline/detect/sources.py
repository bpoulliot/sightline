"""Where a detector gets a document's bytes, and what happens when there are none.

Most of the course arrives as structure and markup. A handful of detectors need the file itself —
whether a PDF carries a tag tree, what typeface a Word document's default style names — and those
bytes come from a content export, which a course may not have. Three facts follow.

**"No bytes" is a reportable state, not an absence.** A source returns ``None`` and the detector
records an :class:`~sightline.detect.base.Undetermined` naming the file and the reason. A document
that was never downloaded is neither an accessible document nor an inaccessible one.

**Bytes stay inside the data root.** An export carries instructor-authored content and lives under
``SIGHTLINE_DATA_DIR`` (SPEC §11). :class:`ExportFileSource` resolves against the unpacked export's
own directory and refuses a path that climbs out of it, because ``local_path`` comes from an
archive written by somebody else.

**Reads are capped.** A course file can be a 2 GB video. A detector that needs a PDF's structure
needs its first megabytes, not all of it, and an unbounded read inside a per-course loop is how one
oversized attachment stalls a batch.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..model import CourseFile

#: Bytes read from any one file. Generous for a document, far below a media file.
MAX_READ_BYTES = 24 * 1024 * 1024


@runtime_checkable
class FileSource(Protocol):
    """Resolves a normalized :class:`~sightline.model.CourseFile` to its bytes, or to ``None``."""

    def read(self, file: CourseFile) -> bytes | None: ...


class NoFileSource:
    """The explicit "this run has no document bytes" source.

    A named class rather than ``None`` threaded through every call, so a detector's behaviour when
    there are no bytes is exercised by the suite the same way the other paths are.
    """

    def read(self, file: CourseFile) -> bytes | None:
        return None


class MappingFileSource:
    """Bytes held in memory, keyed by artifact id or by filename. The suite's source."""

    def __init__(self, contents: Mapping[str, bytes]) -> None:
        self._contents = dict(contents)

    def read(self, file: CourseFile) -> bytes | None:
        for key in (file.id, file.filename, file.local_path or ""):
            if key and key in self._contents:
                return self._contents[key]
        return None


class ExportFileSource:
    """Bytes from an unpacked content export, resolved through ``CourseFile.local_path``.

    ``root`` is the export's ``files/`` directory — the same directory
    :class:`~sightline.acquire.archive.UnpackedExport` lists its documents from, so a file's
    ``local_path`` is relative to it.
    """

    def __init__(self, root: Path | str, *, max_bytes: int = MAX_READ_BYTES) -> None:
        self._root = Path(root).resolve()
        self._max_bytes = max_bytes

    def read(self, file: CourseFile) -> bytes | None:
        if not file.local_path:
            return None
        candidate = (self._root / file.local_path).resolve()
        if not candidate.is_relative_to(self._root):
            # The href came out of an archive somebody else wrote. A path climbing out of the
            # export root is the archive asking to read a file it has no business naming.
            return None
        if not candidate.is_file():
            return None
        with candidate.open("rb") as handle:
            return handle.read(self._max_bytes)


__all__ = [
    "MAX_READ_BYTES",
    "ExportFileSource",
    "FileSource",
    "MappingFileSource",
    "NoFileSource",
]
