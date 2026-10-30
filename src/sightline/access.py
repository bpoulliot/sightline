"""Whose runs a request may see, and who may change the instance (roadmap item 45, SPEC §09).

**Local mode** (the default) has one implicit user, and that user is the admin: every run is
theirs to see, every setting theirs to change, and the one stored Canvas credential is theirs.
Nothing a local user sees changes with this module.

**Sign-in mode** answers from the identity :class:`sightline.signin.SignInMiddleware` put on the
request:

* **A person sees their own runs.** A run records the subject who started it
  (``runs.starter_subject``), and the run list, a run's page, ``GET /api/runs/{id}``, a course's
  review pages, a rating posted to it and its exports answer only that subject — or an admin.
  Anybody else gets **404, the same answer as a run that does not exist**, never 403: a 403 would
  confirm that the id is somebody's run. Opening a review is what satisfies the export gate, so
  this is access, not presentation; a hidden link would leave the door open.
* **An admin sees every run** and alone changes the instance settings: the model host, the model,
  the Canvas base URL and the export preference. Admin is membership in the identity-provider
  group named by ``SIGHTLINE_SIGNIN_ADMIN_GROUP``, read from item 44's groups header and matched
  exactly. With the setting unset there is no admin.
* **A run recorded with no starter** — every run from before this item, and every run started on
  the command line — is visible to an admin only.

:func:`viewer` is the one lookup, as :func:`sightline.review.identity.current_reviewer` is for who
records a rating; the routes ask it and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from .models import Run, RunCourse
from .signin import signed_in
from .store import RunStore

#: What a person who is not an admin is told when a post would change an instance setting.
ADMIN_ONLY_SUMMARY = (
    "Nothing was saved: this is an instance setting, and only an admin changes instance settings."
)


def admin_only_remedy(group: str | None) -> str:
    """Who is an admin, said without naming anything a deployment did not configure."""
    if group:
        return (
            "Admins are the members of the sign-in group this deployment names as its admin "
            "group. Ask one of them to make the change, or to add you to that group."
        )
    return (
        "This deployment names no admin group (SIGHTLINE_SIGNIN_ADMIN_GROUP), so nobody signed in "
        "is an admin. Whoever runs the deployment sets it."
    )


@dataclass(frozen=True)
class Viewer:
    """Who is asking. ``subject`` is ``None`` in local mode, where there is one user."""

    subject: str | None
    name: str | None
    admin: bool
    signed_in: bool

    def can_see(self, run: Run) -> bool:
        """Whether this person may see ``run``, review its courses and export them."""
        if self.admin:
            return True
        return run.starter_subject is not None and run.starter_subject == self.subject

    @property
    def runs_filter(self) -> str | None:
        """The starter :meth:`sightline.store.RunStore.list_runs` filters on; ``None`` is all."""
        return None if self.admin else self.subject


#: The one user of local mode.
LOCAL = Viewer(subject=None, name=None, admin=True, signed_in=False)


def viewer(request: Request) -> Viewer:
    """Who this request is from, and whether they are an admin."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not settings.signin:
        return LOCAL
    identity = signed_in(request.state)
    if identity is None:  # pragma: no cover - SignInMiddleware refused the request
        raise RuntimeError("sign-in mode, and this request carries no signed-in identity")
    group = settings.signin_admin_group
    return Viewer(
        subject=identity.subject,
        name=identity.name,
        admin=group is not None and group in identity.groups,
        signed_in=True,
    )


def visible_run(request: Request, store: RunStore, run_id: str) -> Run | None:
    """``run_id``'s run if this request may see it; ``None`` whether it is absent or not theirs."""
    run = store.get_run(run_id)
    if run is None or not viewer(request).can_see(run):
        return None
    return run


def visible_course(request: Request, store: RunStore, course_id: str) -> RunCourse | None:
    """A course of a run this request may see; ``None`` whether it is absent or not theirs."""
    course = store.get_course(course_id)
    if course is None:
        return None
    run = store.get_run(course.run_id)
    if run is None or not viewer(request).can_see(run):
        return None
    return course


__all__ = [
    "ADMIN_ONLY_SUMMARY",
    "LOCAL",
    "Viewer",
    "admin_only_remedy",
    "viewer",
    "visible_course",
    "visible_run",
]
