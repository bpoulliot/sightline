"""Who a rating recorded on the review screen is recorded as (roadmap item 16, decision Q1).

**This is the one lookup.** The override path calls :func:`current_reviewer` and nothing else asks
who the reviewer is: the form shows its answer, the record stores it, and the refusal fires when it
is ``None``. Today the answer is the *Reviewer* setting on the Settings screen
(:meth:`sightline.preferences.PreferenceStore.reviewer`), because Sightline has no login
(:mod:`sightline.views`). When sign-in lands (roadmap item 40), the signed-in identity replaces the
setting **here**, from the request this function already takes — without touching the form, the
record, or :func:`sightline.rubric.overrides.record_override`.
"""

from __future__ import annotations

from fastapi import Request

from ..preferences import PreferenceStore


def current_reviewer(request: Request) -> str | None:
    """The name a rating recorded in this request is recorded as, or ``None`` when there is none."""
    preferences: PreferenceStore | None = getattr(request.app.state, "preferences", None)
    if preferences is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the preference store is not initialised")
    return preferences.reviewer().name


__all__ = ["current_reviewer"]
