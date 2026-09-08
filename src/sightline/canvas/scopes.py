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


#: Every scope Sightline requires, connection test and acquisition together. One tuple, extended
#: by each item that calls a new endpoint, so there is one place to look — and ``tests/
#: test_canvas_scopes.py`` fails the build when the README table and this tuple disagree.
#:
#: **Every entry is a GET.** Sightline reads Canvas and never writes to it. That is not an
#: accident of the current feature set: requesting a content export is a ``POST``, and item 03
#: chose to *use* an export a course already has rather than ask Canvas to build one, precisely so
#: this list could stay read-only. ``test_the_scopes_are_all_read_only`` is what keeps it that way,
#: and flipping it is a product decision recorded in ``docs/ROADMAP.md``.
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
    # -- acquisition (item 03) ---------------------------------------------------------------
    Scope(
        scope="url:GET|/api/v1/courses/:course_id/pages/:url",
        request="GET /api/v1/courses/{id}/pages/{page_url}",
        reason="Reads a page's body. The listing endpoint returns titles only, never the HTML.",
    ),
    Scope(
        scope="url:GET|/api/v1/courses/:course_id/modules",
        request="GET /api/v1/courses/{id}/modules",
        reason="Reads the module structure every artifact's place in the course is derived from.",
    ),
    Scope(
        scope="url:GET|/api/v1/courses/:course_id/modules/:module_id/items",
        request="GET /api/v1/courses/{id}/modules/{module_id}/items",
        reason=(
            "Re-reads a long module's items. Canvas caps the items it includes with a module "
            "listing and gives no signal that it did."
        ),
    ),
    Scope(
        scope="url:GET|/api/v1/courses/:course_id/assignments",
        request="GET /api/v1/courses/{id}/assignments",
        reason="Reads assignments, their due dates, submission types and attached rubrics.",
    ),
    Scope(
        scope="url:GET|/api/v1/courses/:course_id/quizzes",
        request="GET /api/v1/courses/{id}/quizzes",
        reason="Reads quizzes, including whether each is timed and how it is weighted.",
    ),
    Scope(
        scope="url:GET|/api/v1/courses/:course_id/discussion_topics",
        request="GET /api/v1/courses/{id}/discussion_topics",
        reason=(
            "Reads discussions and announcements. Canvas models both as topics and separates "
            "them with a query parameter, not a second endpoint."
        ),
    ),
    Scope(
        scope="url:GET|/api/v1/courses/:course_id/assignment_groups",
        request="GET /api/v1/courses/{id}/assignment_groups",
        reason=(
            "Reads the assignment groups and their weights, which one criterion expects to "
            "total 100."
        ),
    ),
    Scope(
        scope="url:GET|/api/v1/courses/:course_id/rubrics",
        request="GET /api/v1/courses/{id}/rubrics",
        reason="Reads the rubrics defined in the course, so an unattached one is still visible.",
    ),
    Scope(
        scope="url:GET|/api/v1/courses/:course_id/tabs",
        request="GET /api/v1/courses/{id}/tabs",
        reason=(
            "Reads the course navigation menu, including which entries are hidden from learners."
        ),
    ),
    Scope(
        scope="url:GET|/api/v1/courses/:course_id/files",
        request="GET /api/v1/courses/{id}/files",
        reason=(
            "Lists the course's files, so a document with no bytes in the export is still named."
        ),
    ),
    Scope(
        scope="url:GET|/api/v1/courses/:course_id/content_exports",
        request="GET /api/v1/courses/{id}/content_exports",
        reason=(
            "Finds a content export the course already has. Sightline reads one; it never asks "
            "Canvas to create one, because creating one is a write."
        ),
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
