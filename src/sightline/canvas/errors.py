"""Canvas failures, each carrying the next move.

"Error" alone is not acceptable output. Every failure this package raises carries three things:

``summary``
    What happened, in one line.
``remedy``
    What to change. Not "check your configuration" — the specific field, the specific Canvas
    screen, or the specific permission.
``detail``
    Whatever is checkable: the status code, the host that answered, the endpoint that was called.

The base URL and the token are secrets (SPEC §11), and an exception message is one of the easiest
places to leak one. So the rule here is: a message may name the **host**, because a connection test
that cannot say which host answered is useless, and the person reading it typed the host in. It
may never carry the token, a query string, or an Authorization header. Anything these objects
render still passes through :func:`sightline.redaction.scrub` on its way to a log or the run store,
which is what removes the host from those surfaces while leaving it on the screen of the person who
asked for the test.
"""

from __future__ import annotations


class CanvasError(Exception):
    """Base for every failure this package raises."""

    #: Overridden by subclasses that map to one obvious fix.
    default_remedy = "Check the Canvas base URL and token on the Settings screen."

    def __init__(
        self,
        summary: str,
        *,
        remedy: str | None = None,
        detail: str | None = None,
        status_code: int | None = None,
        endpoint: str | None = None,
    ) -> None:
        super().__init__(summary)
        self.summary = summary
        self.remedy = remedy or self.default_remedy
        self.detail = detail
        self.status_code = status_code
        self.endpoint = endpoint

    def __str__(self) -> str:
        parts = [self.summary]
        if self.detail:
            parts.append(self.detail)
        parts.append(self.remedy)
        return " ".join(parts)

    def as_dict(self) -> dict[str, object]:
        """The fields a UI renders, so a template need not parse prose out of ``str``."""
        return {
            "summary": self.summary,
            "detail": self.detail,
            "remedy": self.remedy,
            "status_code": self.status_code,
            "endpoint": self.endpoint,
            "kind": type(self).__name__,
        }


class CanvasNotConfiguredError(CanvasError):
    """No base URL or no token. Raised before any request is attempted."""

    default_remedy = (
        "Enter the Canvas base URL and an API token on the Settings screen, or set "
        "CANVAS_BASE_URL and CANVAS_API_TOKEN in the environment."
    )


class CanvasUnreachableError(CanvasError):
    """Nothing answered: DNS, TCP, TLS or a timeout. The token was never presented."""

    default_remedy = (
        "Check the hostname for a typo, and check that this machine can reach it — a container "
        "on an isolated network cannot reach a host its firewall drops."
    )


class CanvasAuthError(CanvasError):
    """The host answered and rejected the token itself."""

    default_remedy = (
        "The token was rejected as invalid or expired. Issue a new one in Canvas under "
        "Account -> Settings -> Approved Integrations -> New Access Token, and paste it on the "
        "Settings screen."
    )


class CanvasPermissionError(CanvasError):
    """The token is valid; its user or its scopes do not permit this call."""

    default_remedy = (
        "The token is valid but is not permitted to make this call. Either the token's user lacks "
        "the Canvas permission, or the token comes from a scoped developer key that was not "
        "granted this scope. The scopes Sightline needs are listed in the README under "
        "'Canvas token scopes'."
    )


class CanvasNotFoundError(CanvasError):
    """The host answered 404. In Canvas this also means "exists, but not visible to you"."""

    default_remedy = (
        "Canvas answers 404 both for a course that does not exist and for one this token cannot "
        "see, and does not distinguish them. Check the course id, then check that the token's "
        "user is enrolled in the course or has account-level read access to it."
    )


class CanvasRateLimitedError(CanvasError):
    """Canvas throttled the request."""

    default_remedy = (
        "Canvas is throttling this token. Wait a minute and try again; if it persists, the token "
        "is shared with another job that is spending the same rate-limit bucket."
    )


class CanvasServerError(CanvasError):
    """A 5xx from Canvas. Not something the configuration can fix."""

    default_remedy = (
        "Canvas returned a server error, which nothing in Sightline's configuration changes. "
        "Retry, and check the instance's status page if it keeps happening."
    )


class CanvasNotApiError(CanvasError):
    """Something answered, but it was not the Canvas API.

    The usual cause is a base URL pointing at a page inside Canvas rather than its root, or an
    SSO portal intercepting the request and returning a login page.
    """

    default_remedy = (
        "The host answered but did not return Canvas API JSON. Check that the base URL is the "
        "root of the instance (https://canvas.example.edu) and not a page inside it, and that no "
        "login portal is intercepting API requests."
    )


class CanvasResponseError(CanvasError):
    """Canvas returned JSON that did not have the shape this client requires."""

    default_remedy = (
        "Canvas answered with JSON of an unexpected shape. This is a Sightline bug or a Canvas "
        "version difference; the endpoint and status are recorded above."
    )


# ---------------------------------------------------------------------------------------------
# Asking Canvas to build a content export
# ---------------------------------------------------------------------------------------------
#
# These four are separate from the HTTP mapping above because they are failures of the *export*,
# not of the request that asked for it: Canvas answers 200 and then reports the export as failed,
# or never finishes it, or reports it in a state this code has never heard of. Each is reported as
# a failure with its own remedy. A failed export normalized into a document with no files would be
# the silent under-detection SPEC §11 is about — every detector downstream would then see a course
# with no documents in it and report it as clean.


class CanvasExportNotPermittedError(CanvasPermissionError):
    """The token may read the course and may not ask Canvas to package it.

    A subclass of :class:`CanvasPermissionError` because it *is* one — but the remedy is different
    enough to be worth its own class. Reading a course and exporting it are different permissions
    in Canvas, so the remedy for a 403 here is not the remedy for a 403 on a ``GET``.
    """

    default_remedy = (
        "This token can read the course but is not permitted to ask Canvas to package it. "
        "Exporting needs the permission behind Canvas's own Settings -> Export Course Content "
        "button, which a reader, observer or student enrolment does not carry. Either give the "
        "token's user a role that can export content in this course, use an account-level token "
        "with that permission, or switch 'Ask Canvas to build a fresh export' off on the Settings "
        "screen — Sightline then reads an export the course already has."
    )


class CanvasExportFailedError(CanvasError):
    """Canvas reported ``workflow_state: failed``. Not an empty export — a failed one."""

    default_remedy = (
        "Canvas reported the export as failed, so no document bytes were read. This is not an "
        "empty course: nothing was examined. Open the course in Canvas, choose Settings -> Export "
        "Course Content and run the export by hand to see what Canvas reports — a very large "
        "course, or one file the instance cannot read, is the usual cause."
    )


class CanvasExportStateError(CanvasError):
    """Canvas reported a ``workflow_state`` this code does not recognise.

    Treated as a failure rather than as "keep waiting". An unknown state polled forever is a run
    that looks frozen, and the state is named in the message so the next person does not have to
    reproduce it to find out what it was.
    """

    default_remedy = (
        "Sightline stopped rather than polling a state it does not recognise. Check the course's "
        "export list in Canvas under Settings -> Export Course Content. If this state is a normal "
        "part of exporting on this instance, it belongs in the list of states Sightline waits on."
    )


class CanvasExportTimeoutError(CanvasError):
    """The export did not finish inside the poll ceiling."""

    default_remedy = (
        "Canvas had not finished the export when Sightline stopped waiting. The export may still "
        "finish: check the course's export list in Canvas under Settings -> Export Course "
        "Content, and run the review again once it is complete. Very large courses can take "
        "several minutes."
    )
