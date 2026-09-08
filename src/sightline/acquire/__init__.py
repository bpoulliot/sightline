"""Acquisition and normalization: Canvas in, one document model out.

Stages 1 and 2 of the pipeline in SPEC §04. Two sources are combined — the Canvas API for
structure, a content export for document bytes — and everything downstream reads
:class:`sightline.model.CourseDocument` and never a Canvas payload.

    from sightline.acquire import CourseAcquisition, load_bundle

    result = await CourseAcquisition(client, paths).acquire("4410")
    document = load_bundle("examples/sample-course")

The fetch and unzip layer is AccessLens's, ported rather than rewritten (see
:mod:`sightline.acquire.archive`); its analysis half is deliberately not here, because Sightline's
analysis is rubric-scoped and much narrower.
"""

from __future__ import annotations

from .archive import (
    ArchiveError,
    ExtractionReport,
    ManifestEntry,
    UnpackedExport,
    archive_digest,
    download_archive,
    extract_archive,
    parse_manifest,
    validate_download_url,
)
from .bundle import BundleError, load_bundle
from .cache import CacheEntry, DocumentCache, course_key, signature_of
from .canvas_api import Collection, CourseBodies, CourseIndex, CourseReader
from .embeds import embeds_for, scan_embeds
from .normalize import normalize
from .service import AcquisitionResult, CourseAcquisition

__all__ = [
    "AcquisitionResult",
    "ArchiveError",
    "BundleError",
    "CacheEntry",
    "Collection",
    "CourseAcquisition",
    "CourseBodies",
    "CourseIndex",
    "CourseReader",
    "DocumentCache",
    "ExtractionReport",
    "ManifestEntry",
    "UnpackedExport",
    "archive_digest",
    "course_key",
    "download_archive",
    "embeds_for",
    "extract_archive",
    "load_bundle",
    "normalize",
    "parse_manifest",
    "scan_embeds",
    "signature_of",
    "validate_download_url",
]
