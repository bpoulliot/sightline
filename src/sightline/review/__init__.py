"""The review screen (roadmap item 16): the human action the export gate waits for.

- :mod:`sightline.review.screen` arranges one course's review into pages, as data.
- :mod:`sightline.review.routes` serves them, records a rating, and exports.
- :mod:`sightline.review.identity` answers who a recorded rating is recorded as — the one lookup.
"""

from __future__ import annotations

from .identity import Reviewer, current_reviewer
from .routes import build_review_router
from .screen import ReviewCategory, ReviewIndex, ReviewScreen, ReviewScreenError, View

__all__ = [
    "ReviewCategory",
    "Reviewer",
    "ReviewIndex",
    "ReviewScreen",
    "ReviewScreenError",
    "View",
    "build_review_router",
    "current_reviewer",
]
