"""Acquiring one course: what runs, in what order, and what is skipped when nothing changed.

    index (always)  ->  signature  ->  cache hit?  ->  done
                                   \\-> miss -> bodies + export -> normalize -> cache -> done

The index phase is a dozen requests and is what the signature is computed from, so the cache is
consulted with a key that reflects the course as it is *now* rather than as it was when the entry
was written. Everything expensive sits after that decision.

**Sightline does not ask Canvas to build an export.** Creating one is a ``POST``, and every request
this package makes is a ``GET`` — an invariant the README states and ``tests/test_canvas_scopes.py``
enforces. What acquisition does instead is *use* an export that already exists in the course, which
is a read, and say plainly what to do when there is none. Whether Sightline should ever make that
one write is a product decision, recorded in ``docs/ROADMAP.md`` rather than settled here.

An existing export can be older than the course. So the API stays authoritative for structure and
for page bodies; the export contributes document bytes and fills in a body the API would not hand
over, and its age is recorded in the document's provenance so a reviewer can weigh it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..canvas.client import CanvasClient
from ..canvas.errors import CanvasError
from ..canvas.refs import parse_course_ref
from ..logging import get_logger
from ..model import CourseDocument, Examination, ExaminationState, Source
from ..paths import DataPaths
from .archive import (
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
    "Create one in Canvas — Settings, then Export Course Content — and run the review again. "
    "Sightline never creates one itself: every request it makes to Canvas is a read."
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
    ) -> None:
        self._client = client
        self._paths = paths
        self._reader = CourseReader(client)
        self._cache = cache or DocumentCache(paths)

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
        index = await self._reader.read_index(ref.api_segment)
        course_id = str(index.course.get("id") or ref.api_segment)
        signature = signature_of(index)
        entry = self._cache.entry(self.base_url, course_id, signature)

        if use_cache:
            cached = self._cache.load(entry)
            if cached is not None:
                return AcquisitionResult(
                    document=cached, signature=signature, from_cache=True, entry=entry
                )

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
        elif include_export:
            export, export_note = await self._existing_export(index, course_id, entry.course_key)

        document = normalize(
            index,
            base_url=self.base_url,
            bodies=bodies,
            export=export,
            provenance=_provenance(index, signature, export_note),
        )
        if export_note is not None and export is None:
            document = _with_examination(document, export_note)

        self._cache.store(entry, document)
        log.info(
            "course_acquired",
            signature=signature[:8],
            counts=document.counts(),
            export=bool(export),
        )
        return AcquisitionResult(
            document=document, signature=signature, from_cache=False, entry=entry
        )

    # -- content exports ---------------------------------------------------------------------

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
        self, archive: Path, key: str, *, created_at: str = ""
    ) -> tuple[UnpackedExport | None, Examination | None]:
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
            detail=(
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
    index: CourseIndex, signature: str, export_note: Examination | None
) -> dict[str, Any]:
    """Facts about the acquisition itself. No wall-clock value: the document must serialize
    identically on a second run (see :mod:`sightline.model`)."""
    provenance: dict[str, Any] = {
        "acquired_from": "canvas-api",
        "signature": signature,
        "collections_readable": sorted(
            name for name, item in index.collections.items() if item.usable
        ),
        "collections_unreadable": sorted(
            name for name, item in index.collections.items() if not item.usable
        ),
    }
    if export_note is not None:
        provenance["content_export"] = export_note.state.value
    return provenance


def _with_examination(document: CourseDocument, entry: Examination) -> CourseDocument:
    examinations = tuple(sorted((*document.examinations, entry), key=lambda item: item.collection))
    return CourseDocument(
        **{
            **{
                field: getattr(document, field)
                for field in (
                    "schema_version",
                    "base_url",
                    "course",
                    "syllabus",
                    "modules",
                    "pages",
                    "assignments",
                    "quizzes",
                    "discussions",
                    "announcements",
                    "files",
                    "embeds",
                    "navigation",
                    "assignment_groups",
                    "rubrics",
                    "provenance",
                )
            },
            "examinations": examinations,
        }
    )


__all__ = [
    "REMEDY_NO_EXPORT",
    "USABLE_EXPORT_TYPES",
    "AcquisitionResult",
    "CourseAcquisition",
]
