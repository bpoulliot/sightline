"""The minimum Canvas read scopes Sightline needs, and nothing beyond them.

An institution should be able to issue a least-privilege token rather than an admin one, so the
set of endpoints this package touches is declared here rather than being discoverable only by
reading the client. ``tests/test_canvas_scopes.py`` asserts that the README lists exactly this set,
so the documentation cannot drift away from the code, and that every scope named here is exercised
by at least one client method.

Two kinds of Canvas token exist and they behave differently, which matters when someone is trying
to hand Sightline the smallest possible credential:

*A personal access token* (Account -> Settings -> New Access Token) carries the **full permissions
of the user who created it** and cannot be scoped. Least privilege for one of these means creating
a service account with a restricted Canvas role, not restricting the token.

*A token issued through a developer key* can be scoped, and these are the scope strings to grant.
Canvas spells a scope as ``url:<METHOD>|<route>`` using the route's parameterised path, not the
concrete one — ``/api/v1/courses/:id``, never ``/api/v1/courses/4410``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    """One Canvas API scope, with the reason Sightline needs it."""

    #: The exact string granted on a developer key.
    scope: str
    #: The concrete request the client makes, for someone auditing the traffic.
    request: str
    #: Why this item needs it. Kept short enough to sit in a README table cell.
    reason: str


#: Every scope item 02 requires. Acquisition (item 03) adds more; it extends this tuple rather
#: than introducing a second list, so there is one place to look.
REQUIRED_SCOPES: tuple[Scope, ...] = (
    Scope(
        scope="url:GET|/api/v1/users/:id",
        request="GET /api/v1/users/self",
        reason="Names the authenticated user in the connection test, so a token is identifiable.",
    ),
    Scope(
        scope="url:GET|/api/v1/courses",
        request="GET /api/v1/courses",
        reason="Counts the courses the token can see, so an empty result is visible up front.",
    ),
    Scope(
        scope="url:GET|/api/v1/courses/:id",
        request="GET /api/v1/courses/{id}",
        reason="Resolves a pasted URL, id or CSV row to a named course.",
    ),
    Scope(
        scope="url:GET|/api/v1/courses/:course_id/pages",
        request="GET /api/v1/courses/{id}/pages",
        reason="Confirms course content is readable, not merely that the course is listed.",
    ),
)

#: Canvas permissions the token's *user* needs, whether or not the token itself is scoped. A
#: scope grants access to a route; it does not grant the underlying permission.
REQUIRED_PERMISSIONS: tuple[str, ...] = (
    "Read course content",
    "Course Content - view",
)


def scope_strings() -> tuple[str, ...]:
    return tuple(item.scope for item in REQUIRED_SCOPES)
