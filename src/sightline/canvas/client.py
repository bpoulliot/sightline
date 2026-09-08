"""The Canvas API client, and the connection test that proves it works.

Everything here is built around one requirement from the roadmap item: **"Error" alone is not
acceptable output.** A failure has to tell the person reading it what to change. So the client
raises the typed errors in :mod:`sightline.canvas.errors`, each carrying a remedy, and the
connection test returns a per-check report rather than a boolean.

Three Canvas quirks are encoded here rather than discovered later at three in the morning:

* **401 means two different things.** A rejected token comes back with a ``WWW-Authenticate``
  header; a valid token making a call it is not permitted to make comes back without one. The
  remedies are opposite — reissue the token, versus grant a permission — so the client separates
  them on that header.
* **403 is also how Canvas rate-limits.** A throttled request is a 403 whose body says
  ``Rate Limit Exceeded``, not a 429.
* **404 means "not found *or* not visible to you"**, and Canvas does not distinguish them. The
  remedy has to cover both, and it says so rather than guessing.

The base URL and the token are secrets (SPEC §11). The token never appears in a message, a
``repr`` or a log. The **host** does appear in messages, deliberately: a connection test that
cannot say which host answered is useless. Everything these objects produce still passes through
:func:`sightline.redaction.scrub` on its way to a log or the run store, which is what keeps the
host on the screen of the person who asked and out of the log file.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal, Self
from urllib.parse import urlsplit

import httpx

from ..logging import get_logger
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
from .refs import CourseRef, host_of, normalize_base_url, parse_course_ref
from .scopes import REQUIRED_SCOPES

log = get_logger("sightline.canvas")

API_ROOT = "/api/v1"

#: Courses are counted by walking pages. The walk stops here and the count is then reported as a
#: floor ("at least N") rather than as a number that is quietly wrong.
MAX_COUNT_PAGES = 5
COUNT_PAGE_SIZE = 100

DEFAULT_TIMEOUT = 20.0

#: ``<url>; rel="next", <url>; rel="last"`` — Canvas's pagination header.
_LINK = re.compile(r'<(?P<url>[^>]+)>\s*;\s*rel="(?P<rel>[^"]+)"')

CheckStatus = Literal["ok", "failed", "skipped"]


# ---------------------------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CanvasUser:
    """The authenticated user, as the connection test reports it."""

    id: str
    name: str
    short_name: str | None = None

    @property
    def label(self) -> str:
        return f"{self.name} (Canvas user {self.id})"


@dataclass(frozen=True)
class CourseCount:
    """How many courses the token can see, and whether that number is exact."""

    count: int
    exact: bool
    pages_read: int

    @property
    def label(self) -> str:
        if self.exact:
            return f"{self.count} course{'' if self.count == 1 else 's'}"
        return f"at least {self.count} courses"


@dataclass(frozen=True)
class CanvasCourse:
    """A course, reduced to what item 02 needs. Item 03 normalizes the whole thing."""

    id: str
    name: str
    course_code: str | None
    workflow_state: str | None
    html_url: str

    @property
    def label(self) -> str:
        code = f" ({self.course_code})" if self.course_code else ""
        return f"{self.name}{code}"


#: ``readable`` — content came back. ``refused`` — the token was told no. ``unknown`` — Canvas
#: answered in a way that does not separate "feature switched off" from "not permitted", and
#: saying so is better than picking one.
ContentState = Literal["readable", "refused", "unknown"]


@dataclass(frozen=True)
class ContentAccess:
    """Whether the token can read a course's content, not merely see it listed."""

    state: ContentState
    detail: str
    endpoint: str
    remedy: str | None = None

    @property
    def readable(self) -> bool:
        return self.state == "readable"


@dataclass(frozen=True)
class ResolvedCourse:
    """A reference, the course it resolved to, and whether its content can be read."""

    ref: CourseRef
    course: CanvasCourse
    access: ContentAccess


@dataclass(frozen=True)
class ConnectionCheck:
    """One step of the connection test, with what it examined and what to change."""

    name: str
    status: CheckStatus
    detail: str
    scope: str | None = None
    remedy: str | None = None
    elapsed_ms: float | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True)
class ConnectionReport:
    """The whole connection test. Concrete on success and on failure.

    ``host`` is rendered on the settings screen on purpose — the test's job is to say which host
    answered — and is redacted out of logs and stored run events by
    :func:`sightline.redaction.scrub`.
    """

    host: str | None
    checks: tuple[ConnectionCheck, ...] = ()
    user: CanvasUser | None = None
    courses: CourseCount | None = None

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(check.status == "ok" for check in self.checks)

    @property
    def remedy(self) -> str | None:
        for check in self.checks:
            if check.status == "failed" and check.remedy:
                return check.remedy
        return None

    def summary(self) -> str:
        """One line of counts and facts, in the declarative register of SPEC §08 tier A."""
        passed = sum(1 for check in self.checks if check.status == "ok")
        head = f"{passed} of {len(self.checks)} checks passed against {self.host or 'no host'}."
        if self.ok and self.user and self.courses:
            return f"{head} Authenticated as {self.user.label}; {self.courses.label} visible."
        return head

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "host": self.host,
            "summary": self.summary(),
            "remedy": self.remedy,
            "checks": [
                {
                    "name": check.name,
                    "status": check.status,
                    "detail": check.detail,
                    "scope": check.scope,
                    "remedy": check.remedy,
                    "elapsed_ms": check.elapsed_ms,
                }
                for check in self.checks
            ],
        }


# ---------------------------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------------------------


class CanvasClient:
    """An async Canvas API client. One instance per base URL and token.

    ``transport`` exists so the suite runs against recorded fixtures rather than a live
    instance — a test suite that needs a real Canvas token is a test suite that does not run in
    CI, so there is no code path here that only a real instance exercises.
    """

    def __init__(
        self,
        base_url: str | None,
        token: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = "sightline",
    ) -> None:
        if not base_url:
            raise CanvasNotConfiguredError("No Canvas base URL is configured.")
        if not token:
            raise CanvasNotConfiguredError("No Canvas API token is configured.")
        self.base_url = normalize_base_url(base_url)
        self.host = host_of(self.base_url)
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            headers={
                # The one place the token is written. Nothing else in this package reads it.
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": user_agent,
            },
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def __repr__(self) -> str:
        # Never the token, and never the full base URL: a repr turns up in tracebacks and in
        # pytest failure output, both of which are places a secret escapes from.
        return f"<CanvasClient host={self.host!r}>"

    # -- transport ---------------------------------------------------------------------------

    async def _get(self, path: str, **params: Any) -> httpx.Response:
        """One GET, with every failure mapped to an error that carries its remedy."""
        endpoint = f"GET {path}"
        try:
            response = await self._client.get(path, params=params or None)
        except httpx.TimeoutException as exc:
            raise CanvasUnreachableError(
                f"{self.host} did not answer in time.",
                detail=f"{endpoint} timed out after {DEFAULT_TIMEOUT:.0f}s.",
                endpoint=endpoint,
                remedy=(
                    f"Check that {self.host} is reachable from this machine. A container on an "
                    "isolated network sees a timeout, not a refusal, when a firewall drops its "
                    "traffic."
                ),
            ) from exc
        except httpx.ConnectError as exc:
            raise CanvasUnreachableError(
                f"Nothing answered at {self.host}.",
                detail=f"{endpoint} could not open a connection.",
                endpoint=endpoint,
                remedy=(
                    f"Check {self.host} for a typo, then check DNS and that this machine may "
                    "reach it on the port in the base URL."
                ),
            ) from exc
        except httpx.TransportError as exc:
            raise CanvasUnreachableError(
                f"The connection to {self.host} failed.",
                detail=f"{endpoint}: {type(exc).__name__}.",
                endpoint=endpoint,
            ) from exc

        self._raise_for_status(response, endpoint)
        return response

    def _raise_for_status(self, response: httpx.Response, endpoint: str) -> None:
        status = response.status_code
        if status < 400:
            return

        message = _canvas_message(response)
        said = f" Canvas said: {message}" if message else ""
        detail = f"{endpoint} returned HTTP {status}.{said}"

        if status == 401:
            # A rejected token carries WWW-Authenticate; a permitted-but-not-authorised call does
            # not. Opposite remedies, so the header is what separates them.
            if response.headers.get("www-authenticate") or "access token" in message.lower():
                raise CanvasAuthError(
                    f"{self.host} rejected the token.",
                    detail=detail,
                    status_code=401,
                    endpoint=endpoint,
                )
            raise CanvasPermissionError(
                f"{self.host} accepted the token but refused this request.",
                detail=detail,
                status_code=401,
                endpoint=endpoint,
            )
        if status == 403:
            if "rate limit" in (message + response.text[:200]).lower():
                raise CanvasRateLimitedError(
                    f"{self.host} is throttling this token.",
                    detail=detail,
                    status_code=403,
                    endpoint=endpoint,
                )
            raise CanvasPermissionError(
                f"{self.host} refused this request.",
                detail=detail,
                status_code=403,
                endpoint=endpoint,
            )
        if status == 404:
            raise CanvasNotFoundError(
                f"{self.host} has nothing at that address for this token.",
                detail=detail,
                status_code=404,
                endpoint=endpoint,
            )
        if status == 429:
            raise CanvasRateLimitedError(
                f"{self.host} is throttling this token.",
                detail=detail,
                status_code=429,
                endpoint=endpoint,
            )
        if status >= 500:
            raise CanvasServerError(
                f"{self.host} returned a server error.",
                detail=detail,
                status_code=status,
                endpoint=endpoint,
            )
        raise CanvasError(
            f"{self.host} refused the request with HTTP {status}.",
            detail=detail,
            status_code=status,
            endpoint=endpoint,
            remedy=(
                "This status is not one Sightline expects from Canvas. Check that the base URL "
                "is the root of a Canvas instance and not a proxy in front of one."
            ),
        )

    def _json(self, response: httpx.Response, endpoint: str) -> Any:
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            raise CanvasNotApiError(
                f"{self.host} answered, but not with Canvas API JSON.",
                detail=(
                    f"{endpoint} returned HTTP {response.status_code} with content-type "
                    f"'{content_type or 'unset'}'."
                ),
                status_code=response.status_code,
                endpoint=endpoint,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise CanvasNotApiError(
                f"{self.host} claimed to answer with JSON but the body did not parse.",
                detail=f"{endpoint} returned HTTP {response.status_code}.",
                status_code=response.status_code,
                endpoint=endpoint,
            ) from exc

    # -- endpoints ---------------------------------------------------------------------------

    async def current_user(self) -> CanvasUser:
        """``GET /api/v1/users/self`` — who this token is."""
        endpoint = f"GET {API_ROOT}/users/self"
        payload = self._json(await self._get(f"{API_ROOT}/users/self"), endpoint)
        if not isinstance(payload, dict) or "id" not in payload:
            raise CanvasResponseError("Canvas did not return a user object.", endpoint=endpoint)
        return CanvasUser(
            id=str(payload["id"]),
            name=str(payload.get("name") or payload.get("short_name") or "unnamed user"),
            short_name=_optional_str(payload.get("short_name")),
        )

    async def visible_courses(self) -> CourseCount:
        """``GET /api/v1/courses`` — how many courses this token can see.

        Canvas's ``/courses`` lists the courses the token's **user is enrolled in**. An
        account-level token can therefore legitimately see zero here and still resolve any course
        by id, which is why the connection test reports the number rather than failing on it.
        """
        endpoint = f"{API_ROOT}/courses"
        total = 0
        pages = 0
        path: str | None = endpoint
        params: dict[str, Any] | None = {"per_page": COUNT_PAGE_SIZE}

        while path is not None and pages < MAX_COUNT_PAGES:
            response = await self._get(path, **(params or {}))
            body = self._json(response, f"GET {path}")
            if not isinstance(body, list):
                raise CanvasResponseError(
                    "Canvas did not return a list of courses.", endpoint=f"GET {path}"
                )
            total += len(body)
            pages += 1
            next_url = parse_link_header(response.headers.get("link", "")).get("next")
            # Only the path and query are reused. Following the absolute URL would let a
            # misconfigured instance walk the client onto another host.
            path, params = (_same_host_path(next_url), None) if next_url else (None, None)

        exact = path is None
        return CourseCount(count=total, exact=exact, pages_read=pages)

    async def course(self, course_id: str) -> CanvasCourse:
        """``GET /api/v1/courses/:id`` — resolve an id to a named course."""
        endpoint = f"GET {API_ROOT}/courses/{course_id}"
        payload = self._json(await self._get(f"{API_ROOT}/courses/{course_id}"), endpoint)
        if not isinstance(payload, dict) or "id" not in payload:
            raise CanvasResponseError("Canvas did not return a course object.", endpoint=endpoint)
        resolved_id = str(payload["id"])
        return CanvasCourse(
            id=resolved_id,
            name=str(payload.get("name") or f"course {resolved_id}"),
            course_code=_optional_str(payload.get("course_code")),
            workflow_state=_optional_str(payload.get("workflow_state")),
            html_url=f"{self.base_url}/courses/{resolved_id}",
        )

    async def content_access(self, course_id: str) -> ContentAccess:
        """Can this token read the course's content, or only see that it exists?

        A token can list a course and still be refused its pages — the case a reviewer hits when
        they have been given an observer enrolment. That is worth separating from "no such
        course", because the remedies differ, so it is probed rather than assumed.
        """
        endpoint = f"GET {API_ROOT}/courses/{course_id}/pages"
        try:
            response = await self._get(f"{API_ROOT}/courses/{course_id}/pages", per_page=1)
        except CanvasPermissionError as exc:
            return ContentAccess(
                state="refused",
                detail=f"Canvas refused the course's pages. {exc.detail or ''}".strip(),
                endpoint=endpoint,
                remedy=(
                    "The token can see this course but not read its content. Give the token's "
                    "user a role with 'Course Content - view' in this course, or use an "
                    "account-level token with read access to the sub-account."
                ),
            )
        except CanvasNotFoundError:
            return ContentAccess(
                state="unknown",
                detail=(
                    "Canvas returned 404 for the course's pages. That is what it returns both "
                    "when Pages are switched off for the course and when the token may not read "
                    "them; it does not distinguish the two."
                ),
                endpoint=endpoint,
                remedy=(
                    "Open the course in Canvas and check whether Pages is enabled. If it is, the "
                    "token is not permitted to read course content."
                ),
            )
        body = self._json(response, endpoint)
        count = len(body) if isinstance(body, list) else 0
        return ContentAccess(
            state="readable",
            detail=f"Course content is readable. The pages endpoint returned {count} item(s).",
            endpoint=endpoint,
        )

    async def resolve_course(
        self, reference: str | CourseRef, *, check_content: bool = True
    ) -> ResolvedCourse:
        """Take a URL, a bare id or an already-parsed reference and return the course.

        All three input forms converge here, which is the property
        ``tests/test_canvas_client.py`` asserts: the same course id comes back whichever way it
        was written.
        """
        ref = (
            reference
            if isinstance(reference, CourseRef)
            else parse_course_ref(reference, expected_host=self.host)
        )
        course = await self.course(ref.api_segment)
        access = (
            await self.content_access(course.id)
            if check_content
            else ContentAccess(
                state="unknown",
                detail="Content readability was not probed.",
                endpoint="(not called)",
            )
        )
        return ResolvedCourse(ref=ref, course=course, access=access)

    # -- the connection test -----------------------------------------------------------------

    async def test_connection(self, *, course_reference: str | None = None) -> ConnectionReport:
        """Run the checks a person needs to see, and report each one concretely.

        Never raises for a Canvas-side failure: a failed check is a row in the report with the
        remedy attached. A check that could not run because an earlier one failed is marked
        ``skipped`` and says why, rather than being omitted — an absent check looks like a check
        nobody needed.
        """
        checks: list[ConnectionCheck] = []
        user: CanvasUser | None = None
        courses: CourseCount | None = None

        identity_scope = REQUIRED_SCOPES[0].scope
        started = time.perf_counter()
        try:
            user = await self.current_user()
        except CanvasError as exc:
            checks.append(
                ConnectionCheck(
                    name="Authenticated user",
                    status="failed",
                    detail=" ".join(filter(None, (exc.summary, exc.detail))),
                    scope=identity_scope,
                    remedy=exc.remedy,
                    elapsed_ms=_ms(started),
                )
            )
            reason = "skipped: the first request did not succeed, so nothing later could be tested."
            checks.append(
                ConnectionCheck(
                    name="Courses visible",
                    status="skipped",
                    detail=reason,
                    scope=REQUIRED_SCOPES[1].scope,
                )
            )
            if course_reference:
                checks.append(ConnectionCheck(name="Course", status="skipped", detail=reason))
            self._log(checks)
            return ConnectionReport(host=self.host, checks=tuple(checks))

        checks.append(
            ConnectionCheck(
                name="Authenticated user",
                status="ok",
                detail=f"{self.host} answered. The token belongs to {user.label}.",
                scope=identity_scope,
                elapsed_ms=_ms(started),
            )
        )

        started = time.perf_counter()
        try:
            courses = await self.visible_courses()
        except CanvasError as exc:
            checks.append(
                ConnectionCheck(
                    name="Courses visible",
                    status="failed",
                    detail=" ".join(filter(None, (exc.summary, exc.detail))),
                    scope=REQUIRED_SCOPES[1].scope,
                    remedy=exc.remedy,
                    elapsed_ms=_ms(started),
                )
            )
        else:
            note = (
                " This endpoint lists courses the token's user is enrolled in; an account-level "
                "token can see none here and still resolve a course by id."
            )
            if not courses.exact:
                note += (
                    f" The walk stopped after {courses.pages_read} pages, so this is a floor, "
                    "not a total."
                )
            checks.append(
                ConnectionCheck(
                    name="Courses visible",
                    status="ok",
                    detail=f"{courses.label} visible to this token.{note}",
                    scope=REQUIRED_SCOPES[1].scope,
                    elapsed_ms=_ms(started),
                )
            )

        if course_reference:
            checks.extend(await self._course_checks(course_reference))

        self._log(checks)
        return ConnectionReport(host=self.host, checks=tuple(checks), user=user, courses=courses)

    async def _course_checks(self, reference: str) -> list[ConnectionCheck]:
        started = time.perf_counter()
        try:
            resolved = await self.resolve_course(reference)
        except CanvasError as exc:
            return [
                ConnectionCheck(
                    name="Course",
                    status="failed",
                    detail=" ".join(filter(None, (exc.summary, exc.detail))),
                    scope=REQUIRED_SCOPES[2].scope,
                    remedy=exc.remedy,
                    elapsed_ms=_ms(started),
                ),
                ConnectionCheck(
                    name="Course content",
                    status="skipped",
                    detail="skipped: the course did not resolve.",
                    scope=REQUIRED_SCOPES[3].scope,
                ),
            ]
        access = resolved.access
        return [
            ConnectionCheck(
                name="Course",
                status="ok",
                detail=(
                    f"'{reference}' resolved to course {resolved.course.id}: "
                    f"{resolved.course.label}."
                ),
                scope=REQUIRED_SCOPES[2].scope,
                elapsed_ms=_ms(started),
            ),
            ConnectionCheck(
                name="Course content",
                status="ok" if access.readable else "failed",
                detail=access.detail,
                scope=REQUIRED_SCOPES[3].scope,
                remedy=access.remedy,
            ),
        ]

    def _log(self, checks: list[ConnectionCheck]) -> None:
        """Log the shape of the result, never its content.

        Check details name the host and the authenticated user, and neither belongs in a log
        file. Redaction would catch the host; it would not catch a person's name. So what is
        logged is counts and check names, which is enough to see that a test ran and how it went.
        """
        log.info(
            "canvas_connection_test",
            checks=len(checks),
            passed=sum(1 for check in checks if check.status == "ok"),
            failed=[check.name for check in checks if check.status == "failed"],
            skipped=[check.name for check in checks if check.status == "skipped"],
        )


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def parse_link_header(value: str) -> dict[str, str]:
    """``rel`` -> URL, from an RFC 5988 ``Link`` header. Unparseable pieces are ignored."""
    return {match["rel"]: match["url"] for match in _LINK.finditer(value or "")}


def _same_host_path(url: str) -> str:
    """Path and query of ``url``, discarding scheme and host.

    Pagination follows the client's own base URL, never the one in the header. A Canvas behind a
    misconfigured proxy emits absolute URLs pointing somewhere else, and a client that follows
    them sends the token to that somewhere else.
    """
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _canvas_message(response: httpx.Response) -> str:
    """The human-readable part of a Canvas error body, clipped.

    Clipped because the body is not ours: a Canvas error can echo course content back, and an
    unbounded slice of somebody's course does not belong in an error message.
    """
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict) and first.get("message"):
                return str(first["message"])[:200]
            if isinstance(first, str):
                return first[:200]
        for key in ("message", "error", "status"):
            if payload.get(key):
                return str(payload[key])[:200]
    return ""


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


__all__ = [
    "API_ROOT",
    "CanvasClient",
    "CanvasCourse",
    "CanvasUser",
    "ConnectionCheck",
    "ConnectionReport",
    "ContentAccess",
    "CourseCount",
    "ResolvedCourse",
    "parse_link_header",
]
