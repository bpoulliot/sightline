"""Acquiring one course: what runs, in what order, and what is skipped when nothing changed.

    index (always)  ->  signature  ->  cache hit?  ->  done
                                   \\-> miss -> bodies + export -> normalize -> cache -> done

The index phase is a dozen requests and is what the signature is computed from, so the cache is
consulted with a key that reflects the course as it is *now* rather than as it was when the entry
was written. Everything expensive sits after that decision.

**Sightline does not modify course content; it asks Canvas to package a copy of the course it is
reviewing.** That request is the only thing this package sends that is not a ``GET``, and
``tests/test_canvas_scopes.py`` keeps the count of writes at exactly one. It is the normal path:
without an export, every check that reads a document's own bytes has nothing to read.

**A setting turns it off, and off is a supported configuration.** With it off acquisition does what
item 03 did — reads an export the course already has, and says plainly what to do when there is
none. An institution with a no-writes policy keeps a working tool. The two paths differ in one more
way worth knowing: the fresh path does not list the course's exports at all, because it never reads
one it did not ask for and because its own exports would otherwise move the cache signature on
every run.

**A fresh export is never traded for a faster one.** There is no "reuse a recent export" shortcut
here and there is not supposed to be. Canvas does not bump a course's ``updated_at`` when a page
inside it is edited, so an existing export cannot be shown to match the course as it is now, and a
review of last week's course reported as this week's is the silent wrongness this project exists to
avoid. On the opt-out path an export can still be older than the course, so the API stays
authoritative for structure and page bodies and the export's age is recorded in the document's
provenance for a reviewer to weigh.

**The document names the export its bytes live in** (:attr:`CourseDocument.export_files`, a path
relative to the data root), because the detectors that read a PDF need those bytes and the
unpacked export is otherwise a local of :meth:`CourseAcquisition.acquire`. Recording it on the
document rather than on the result is what makes it survive the cache: a cached document is loaded
without unpacking anything, and it still says where the first run's bytes are. **A cache hit whose
export has since gone is a miss.** If a document carries file paths and the directory they are
relative to is no longer a complete extraction — swept by hand, or a document cached before the
directory was recorded — the entry is not returned; the course is acquired again as on a first
run. Serving it would have every detector that reads a document report "not acquired" on a course
whose export downloaded perfectly well, which reads as an honest answer and is not one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..canvas.client import API_ROOT, CanvasClient
from ..canvas.errors import CanvasError
from ..canvas.exports import (
    DEFAULT_PACING,
    ContentExport,
    PollPacing,
    ProgressCallback,
    build_fresh_export,
)
from ..canvas.refs import parse_course_ref
from ..logging import get_logger
from ..model import EXPORT_FILES_KEY, CourseDocument, Examination, ExaminationState, Source
from ..paths import DataPathError, DataPaths
from .archive import (
    COMPLETE_MARKER,
    ArchiveError,
    UnpackedExport,
    archive_digest,
    download_archive,
    extract_archive,
)
from .cache import CacheEntry, DocumentCache, signature_of
from .canvas_api import CourseIndex, CourseReader
from .normalize import normalize

log = get_logger("sightline.acquire")

#: Export types worth unpacking. A ``qti`` export holds assessments only and a ``zip`` export holds
#: the Files tab with no manifest; a common cartridge is the one that carries the course.
USABLE_EXPORT_TYPES = frozenset({"common_cartridge", "zip"})

REMEDY_NO_EXPORT = (
    "No finished content export exists for this course, so document bytes were not read. "
    "Create one in Canvas — Settings, then Export Course Content — and run the review again, or "
    "switch 'Ask Canvas to build a fresh export' on under Settings and Sightline will ask Canvas "
    "for one itself."
)

#: Said when the fresh-export path failed. It never falls back to an export the course already
#: has: that export cannot be shown to match the course as it is now, and quietly reviewing older
#: bytes is the failure this whole item is arranged against.
REMEDY_FRESH_EXPORT_FAILED = (
    "Sightline asked Canvas to build an export of this course and did not get one, so document "
    "bytes were not read. It did not fall back to an older export: an existing export cannot be "
    "shown to match the course as it is now. Fix what the message above names, or switch 'Ask "
    "Canvas to build a fresh export' off under Settings to review whatever export the course "
    "already has."
)


@dataclass(frozen=True)
class AcquisitionResult:
    """A normalized course and how it was obtained."""

    document: CourseDocument
    signature: str
    from_cache: bool
    entry: CacheEntry

    def summary(self) -> str:
        """One line of counts, in the declarative register of SPEC §08 tier A."""
        counts = self.document.counts()
        total = sum(counts.values())
        parts = ", ".join(f"{value} {name}" for name, value in counts.items())
        source = "read from cache" if self.from_cache else "acquired"
        return f"{total} artifact(s) {source}: {parts or 'none'}."


class CourseAcquisition:
    """Acquires and normalizes courses through one Canvas client and one data root."""

    def __init__(
        self,
        client: CanvasClient,
        paths: DataPaths,
        *,
        cache: DocumentCache | None = None,
        request_export: bool = True,
        on_export_progress: ProgressCallback | None = None,
        export_pacing: PollPacing = DEFAULT_PACING,
    ) -> None:
        self._client = client
        self._paths = paths
        self._reader = CourseReader(client)
        self._cache = cache or DocumentCache(paths)
        self._request_export = request_export
        self._on_export_progress = on_export_progress
        self._export_pacing = export_pacing

    @property
    def request_export(self) -> bool:
        """Whether this acquisition will ask Canvas to build an export.

        Resolved by the caller from :mod:`sightline.preferences`, never read from configuration
        here: one place decides, and it is the place a person can change.
        """
        return self._request_export

    @property
    def base_url(self) -> str:
        return self._client.base_url

    async def acquire(
        self,
        reference: str,
        *,
        use_cache: bool = True,
        include_export: bool = True,
        archive: Path | None = None,
    ) -> AcquisitionResult:
        """Normalize one course, reusing a cached document when nothing has changed."""
        ref = parse_course_ref(reference, expected_host=self._client.host)
        fresh = include_export and archive is None and self._request_export
        index = await self._reader.read_index(ref.api_segment, include_content_exports=not fresh)
        course_id = str(index.course.get("id") or ref.api_segment)
        signature = signature_of(index)
        entry = self._cache.entry(self.base_url, course_id, signature)

        if use_cache:
            cached = self._cache.load(entry)
            if cached is not None and self._bytes_reachable(cached):
                return AcquisitionResult(
                    document=cached, signature=signature, from_cache=True, entry=entry
                )
            if cached is not None:
                # See the module docstring: the document is fine, but the bytes it points at are
                # gone, and serving it would turn every document into a quiet "not acquired".
                log.warning("cache_export_missing", signature=signature[:8])

        slugs = [
            str(page.get("url"))
            for page in index.items("pages")
            if isinstance(page, Mapping) and page.get("url")
        ]
        bodies = await self._reader.read_page_bodies(course_id, slugs)

        export: UnpackedExport | None = None
        export_note: Examination | None = None
        if archive is not None:
            export, export_note = self._unpack(archive, entry.course_key)
        elif fresh:
            export, export_note = await self._fresh_export(course_id, entry.course_key)
        elif include_export:
            export, export_note = await self._existing_export(index, course_id, entry.course_key)

        document = normalize(
            index,
            base_url=self.base_url,
            bodies=bodies,
            export=export,
            provenance=_provenance(
                index,
                signature,
                export_note,
                requested=fresh,
                export_files=self._relative(export.files_root) if export is not None else None,
            ),
        )
        # Recorded whether or not there are bytes. On failure the note is the whole explanation;
        # on success it says *where the bytes came from* — requested now, or already sitting in
        # the course — which is the one question about them a reviewer cannot answer from the
        # document otherwise. Its collection name differs from the one the normalizer writes, so
        # the two sit side by side rather than one overwriting the other.
        if export_note is not None:
            document = _with_examination(document, export_note)

        self._cache.store(entry, document)
        log.info(
            "course_acquired",
            signature=signature[:8],
            counts=document.counts(),
            export=bool(export),
            export_requested=fresh,
        )
        return AcquisitionResult(
            document=document, signature=signature, from_cache=False, entry=entry
        )

    # -- where the bytes are ------------------------------------------------------------------

    def _relative(self, path: Path) -> str:
        """``path`` relative to the data root, as the POSIX string the document records.

        Every export directory is built by :meth:`DataPaths.resolve`, which joins onto the root
        lexically, so this is exact rather than a realpath comparison.
        """
        return path.relative_to(self._paths.root).as_posix()

    def _bytes_reachable(self, document: CourseDocument) -> bool:
        """Whether every file path in ``document`` can still be resolved to where it was.

        True when the document carries no file paths at all — there are no bytes to lose. False
        when it carries paths and either records no export directory (written before the
        directory was recorded) or records one that is no longer a complete extraction.
        """
        if not any(item.local_path for item in document.files):
            return True
        if document.export_files is None:
            return False
        try:
            files_root = self._paths.resolve(document.export_files)
        except DataPathError:
            return False
        return files_root.is_dir() and (files_root.parent / COMPLETE_MARKER).is_file()

    # -- content exports ---------------------------------------------------------------------

    async def _fresh_export(
        self, course_id: str, key: str
    ) -> tuple[UnpackedExport | None, Examination | None]:
        """Ask Canvas to package the course, wait for it, unpack what comes back.

        Every failure lands as an :class:`~sightline.model.Examination` that is *not* usable, which
        is the mechanism that stops a detector reporting "no untagged PDFs" over a course whose
        documents were never examined. A failed export recorded as an empty one is the silent
        under-detection SPEC §11 is about, so the state here is ``REFUSED`` and it carries what
        Canvas said and what to do about it.
        """
        endpoint = f"POST {API_ROOT}/courses/{course_id}/content_exports"

        async def fetch(export: ContentExport) -> Path:
            archive, _ = await download_archive(
                self._client,
                export.attachment_url or "",
                paths=self._paths,
                destination=("exports", key, f"{export.id}.imscc"),
                canvas_base_url=self.base_url,
            )
            return archive

        try:
            fresh = await build_fresh_export(
                self._client,
                course_id,
                download=fetch,
                on_progress=self._on_export_progress,
                pacing=self._export_pacing,
            )
        except (CanvasError, ArchiveError) as exc:
            summary = getattr(exc, "summary", str(exc))
            detail = getattr(exc, "detail", None)
            return None, Examination(
                collection="content_export",
                state=ExaminationState.REFUSED,
                examined=0,
                source=Source.EXPORT,
                endpoint=endpoint,
                detail=" ".join(filter(None, (str(summary), detail))),
                remedy=" ".join(
                    filter(None, (getattr(exc, "remedy", None), REMEDY_FRESH_EXPORT_FAILED))
                ),
            )
        return self._unpack(fresh.archive, key, detail=fresh.detail())

    async def _existing_export(
        self, index: CourseIndex, course_id: str, key: str
    ) -> tuple[UnpackedExport | None, Examination | None]:
        collection = index.collections.get("content_exports")
        endpoint = collection.examination.endpoint if collection else "(not called)"
        if collection is None or not collection.usable:
            return None, Examination(
                collection="content_export",
                state=ExaminationState.SKIPPED,
                examined=0,
                source=Source.EXPORT,
                endpoint=endpoint,
                detail="The course's content exports could not be listed.",
                remedy=(collection.examination.remedy if collection else None) or REMEDY_NO_EXPORT,
            )

        candidate = _newest_finished_export(collection.items)
        if candidate is None:
            return None, Examination(
                collection="content_export",
                state=ExaminationState.ABSENT,
                examined=len(collection.items),
                source=Source.EXPORT,
                endpoint=endpoint,
                detail=(
                    f"Canvas listed {len(collection.items)} export(s) for this course and none "
                    "is a finished course export."
                ),
                remedy=REMEDY_NO_EXPORT,
            )

        attachment = candidate.get("attachment") or {}
        url = str(attachment.get("url") or "")
        export_id = str(candidate.get("id") or "export")
        try:
            archive, _ = await download_archive(
                self._client,
                url,
                paths=self._paths,
                destination=("exports", key, f"{export_id}.imscc"),
                canvas_base_url=self.base_url,
            )
        except (ArchiveError, CanvasError) as exc:
            return None, Examination(
                collection="content_export",
                state=ExaminationState.REFUSED,
                examined=1,
                source=Source.EXPORT,
                endpoint=endpoint,
                detail=f"The export could not be downloaded. {getattr(exc, 'summary', exc)}",
                remedy=getattr(exc, "remedy", None) or REMEDY_NO_EXPORT,
            )
        return self._unpack(archive, key, created_at=str(candidate.get("created_at") or ""))

    def _unpack(
        self, archive: Path, key: str, *, created_at: str = "", detail: str = ""
    ) -> tuple[UnpackedExport | None, Examination | None]:
        """Extract an archive already on disk and record where it came from.

        ``detail`` is the sentence describing the archive's origin. It is a parameter because the
        three origins are genuinely different facts — Canvas built this one on request, this one
        already existed in the course, this one was handed over on the command line — and one
        sentence covering all three would be true of none of them.
        """
        if not self._paths.contains(archive):
            return None, Examination(
                collection="content_export",
                state=ExaminationState.SKIPPED,
                examined=0,
                source=Source.EXPORT,
                endpoint=str(archive.name),
                detail="An archive outside the data root was not read.",
                remedy=(
                    "Course exports carry instructor-authored content and stay under "
                    "SIGHTLINE_DATA_DIR. Move the archive into the data root's exports/ "
                    "directory and try again."
                ),
            )
        try:
            digest = archive_digest(archive)
            extract_archive(
                archive, paths=self._paths, destination=("exports", key, "unpacked", digest)
            )
        except (ArchiveError, OSError) as exc:
            return None, Examination(
                collection="content_export",
                state=ExaminationState.REFUSED,
                examined=1,
                source=Source.EXPORT,
                endpoint=str(archive.name),
                detail=f"The archive could not be unpacked. {getattr(exc, 'summary', exc)}",
                remedy=getattr(exc, "remedy", None),
            )
        export = UnpackedExport(self._paths.resolve("exports", key, "unpacked", digest))
        note = Examination(
            collection="content_export_source",
            state=ExaminationState.OK,
            examined=1,
            source=Source.EXPORT,
            endpoint=str(archive.name),
            detail=detail
            or (
                "Read an export that already existed in the course"
                + (f", created {created_at}." if created_at else ".")
                + " An export can be older than the course, so the API stays authoritative for"
                " structure and page bodies."
            ),
        )
        return (export if export.usable else None), note


def _newest_finished_export(items: tuple[Any, ...]) -> Mapping[str, Any] | None:
    """The most recent finished course export with a downloadable attachment."""
    finished = [
        item
        for item in items
        if isinstance(item, Mapping)
        and item.get("workflow_state") == "exported"
        and str(item.get("export_type") or "common_cartridge") in USABLE_EXPORT_TYPES
        and isinstance(item.get("attachment"), Mapping)
        and item["attachment"].get("url")
    ]
    if not finished:
        return None
    return max(finished, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id"))))


def _provenance(
    index: CourseIndex,
    signature: str,
    export_note: Examination | None,
    *,
    requested: bool,
    export_files: str | None = None,
) -> dict[str, Any]:
    """Facts about the acquisition itself. No wall-clock value: the document must serialize
    identically on a second run (see :mod:`sightline.model`).

    ``content_export_requested`` is recorded on every document, both ways round. Whether the bytes
    under review came from an export Sightline asked Canvas to build, or from one that was already
    sitting in the course, is a fact about how old they might be — and it belongs on the artifact
    rather than in whoever remembers how the deployment was configured that week.
    """
    provenance: dict[str, Any] = {
        "acquired_from": "canvas-api",
        "signature": signature,
        "content_export_requested": requested,
        "collections_readable": sorted(
            name for name, item in index.collections.items() if item.usable
        ),
        "collections_unreadable": sorted(
            name for name, item in index.collections.items() if not item.usable
        ),
    }
    if export_note is not None:
        provenance["content_export"] = export_note.state.value
    if export_files is not None:
        # Relative to the data root and derived from the archive's digest, so it is the same
        # string on every run over the same archive and the document still serializes the same.
        provenance[EXPORT_FILES_KEY] = export_files
    return provenance


def _with_examination(document: CourseDocument, entry: Examination) -> CourseDocument:
    """``document`` with ``entry`` added to its examinations and every other field carried.

    ``dataclasses.replace`` rather than a rebuild from a list of field names: a hand-written list
    once forgot ``outcomes`` and dropped every outcome from every acquisition with an export note.
    """
    examinations = tuple(sorted((*document.examinations, entry), key=lambda item: item.collection))
    return replace(document, examinations=examinations)


__all__ = [
    "REMEDY_FRESH_EXPORT_FAILED",
    "REMEDY_NO_EXPORT",
    "USABLE_EXPORT_TYPES",
    "AcquisitionResult",
    "CourseAcquisition",
]
