"""The Canvas connector: an API client, a connection test, and reference resolution.

Nothing institution-specific is compiled in (SPEC §04). The base URL and token arrive as
configuration — entered on the settings screen, with an environment fallback — and both are
treated as secrets, the base URL included, because it identifies the institution (SPEC §11).
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
    CanvasNotApiError,
    CanvasNotConfiguredError,
    CanvasNotFoundError,
    CanvasPermissionError,
    CanvasRateLimitedError,
    CanvasResponseError,
    CanvasServerError,
    CanvasUnreachableError,
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
from .scopes import REQUIRED_PERMISSIONS, REQUIRED_SCOPES, Scope, scope_strings

__all__ = [
    "REQUIRED_PERMISSIONS",
    "REQUIRED_SCOPES",
    "CanvasAuthError",
    "CanvasClient",
    "CanvasCourse",
    "CanvasError",
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
    "CourseCount",
    "CourseRef",
    "CourseRefError",
    "CsvBatch",
    "RefBatch",
    "RefRejection",
    "ResolvedCourse",
    "Scope",
    "host_of",
    "normalize_base_url",
    "parse_course_ref",
    "parse_course_ref_csv",
    "parse_course_refs",
    "scope_strings",
]
