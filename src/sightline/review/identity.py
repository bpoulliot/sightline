"""Who a rating recorded on the review screen is recorded as (roadmap items 16 and 44).

**This is the one lookup.** The override path calls :func:`current_reviewer` and nothing else asks
who the reviewer is: the form shows its answer, the record stores it, and the refusal fires when it
is ``None``. The rubric screens ask it too, for who accepted or reverted a set of bindings.

Where the answer comes from depends on the mode (SPEC §09, *Sign-in and multiple users*):

* **Local mode**, the default: the *Reviewer* setting on the Settings screen
  (:meth:`sightline.preferences.PreferenceStore.reviewer`). A name and no id; ``None`` until set.
* **Sign-in mode** (item 44): the signed-in identity :class:`sightline.signin.SignInMiddleware`
  put on the request — the display name, and beside it the subject's stable id, which an override
  records so a renamed person keeps their history. Never ``None``: a request with no identity was
  refused before it reached a route.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from ..preferences import PreferenceStore
from ..signin import signed_in


@dataclass(frozen=True)
class Reviewer:
    """Who is recording. ``subject`` is the signed-in stable id, or ``None`` in local mode."""

    name: str
    subject: str | None = None


def current_reviewer(request: Request) -> Reviewer | None:
    """Who a rating recorded in this request is recorded as, or ``None`` when there is nobody."""
    settings = getattr(request.app.state, "settings", None)
    if settings is not None and settings.signin:
        identity = signed_in(request.state)
        if identity is None:  # pragma: no cover - SignInMiddleware refused the request
            raise RuntimeError("sign-in mode, and this request carries no signed-in identity")
        return Reviewer(name=identity.name, subject=identity.subject)
    preferences: PreferenceStore | None = getattr(request.app.state, "preferences", None)
    if preferences is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the preference store is not initialised")
    name = preferences.reviewer().name
    return Reviewer(name=name) if name else None


def reviewer_name(request: Request) -> str | None:
    """:func:`current_reviewer`'s name alone, for the records that keep a name and no id."""
    reviewer = current_reviewer(request)
    return reviewer.name if reviewer else None


__all__ = ["Reviewer", "current_reviewer", "reviewer_name"]
