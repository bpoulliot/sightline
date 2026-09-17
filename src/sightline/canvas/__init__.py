"""The Canvas connector: an API client, a connection test, reference resolution and one write.

Nothing institution-specific is compiled in (SPEC §04). The base URL and token arrive as
configuration — entered on the settings screen, with an environment fallback — and both are
treated as secrets, the base URL included, because it identifies the institution (SPEC §11).

Sightline does not modify course content; it asks Canvas to package a copy of the course it is
reviewing (:mod:`sightline.canvas.exports`). That request is the only thing this package sends
that is not a ``GET``, a setting turns it off, and the scope list carries exactly one non-``GET``
entry so a second write cannot arrive without a reviewer seeing the number change.
"""

from __future__ import annotations

from .client import (
    CanvasClient,
    CanvasCourse,
    CanvasUser,
    ConnectionCheck,
    ConnectionReport,
    ContentAccess,
    CourseCount,
    ResolvedCourse,
)
from .errors import (
    CanvasAuthError,
    CanvasError,
    CanvasExportFailedError,
    CanvasExportNotPermittedError,
    CanvasExportStateError,
    CanvasExportTimeoutError,
    CanvasNotApiError,
    CanvasNotConfiguredError,
    CanvasNotFoundError,
    CanvasPermissionError,
    CanvasRateLimitedError,
    CanvasResponseError,
    CanvasServerError,
    CanvasUnreachableError,
)
from .exports import (
    ContentExport,
    ExportProgress,
    FreshExport,
    build_fresh_export,
    read_export,
    request_export,
    wait_for_export,
)
from .refs import (
    CourseRef,
    CourseRefError,
    CsvBatch,
    RefBatch,
    RefRejection,
    host_of,
    normalize_base_url,
    parse_course_ref,
    parse_course_ref_csv,
    parse_course_refs,
)
from .scopes import (
    REQUIRED_PERMISSIONS,
    REQUIRED_SCOPES,
    Scope,
    scope_strings,
    write_scopes,
)

__all__ = [
    "REQUIRED_PERMISSIONS",
    "REQUIRED_SCOPES",
    "CanvasAuthError",
    "CanvasClient",
    "CanvasCourse",
    "CanvasError",
    "CanvasExportFailedError",
    "CanvasExportNotPermittedError",
    "CanvasExportStateError",
    "CanvasExportTimeoutError",
    "CanvasNotApiError",
    "CanvasNotConfiguredError",
    "CanvasNotFoundError",
    "CanvasPermissionError",
    "CanvasRateLimitedError",
    "CanvasResponseError",
    "CanvasServerError",
    "CanvasUnreachableError",
    "CanvasUser",
    "ConnectionCheck",
    "ConnectionReport",
    "ContentAccess",
    "ContentExport",
    "CourseCount",
    "CourseRef",
    "CourseRefError",
    "CsvBatch",
    "ExportProgress",
    "FreshExport",
    "RefBatch",
    "RefRejection",
    "ResolvedCourse",
    "Scope",
    "build_fresh_export",
    "host_of",
    "normalize_base_url",
    "parse_course_ref",
    "parse_course_ref_csv",
    "parse_course_refs",
    "read_export",
    "request_export",
    "scope_strings",
    "wait_for_export",
    "write_scopes",
]
