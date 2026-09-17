"""Reading a course's structure out of the Canvas API.

The API is the better source for *structure* — what exists, how it is grouped, what is graded,
what the navigation menu hides — and this module reads exactly that and nothing else. Bytes come
from elsewhere (:mod:`sightline.acquire.archive`), and interpretation comes from later items.

Two rules shape everything here.

**Nothing raises for a collection Canvas would not hand over.** A course with the Quizzes tab
switched off is a normal course, not a failed acquisition, so every collection is wrapped and the
outcome recorded as an :class:`~sightline.model.Examination` naming the endpoint, what came back,
and what to change. A detector that later says "no quizzes" can then check whether quizzes were
readable at all. Reporting an unreadable collection as an empty one is the silent under-detection
SPEC §11 is about.

**The walk is two-phase.** The index phase is cheap and is always run; it is what the cache
signature is computed from. The body phase costs one request per page plus a download per file,
and is skipped entirely when the signature says nothing has changed.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..canvas.client import CanvasClient, Listing
from ..canvas.errors import (
    CanvasError,
    CanvasNotFoundError,
    CanvasPermissionError,
)
from ..logging import get_logger
from ..model import Examination, ExaminationState, Source

log = get_logger("sightline.acquire")

API_ROOT = "/api/v1"

#: A course with more pages than this is real, and fetching every body would be tens of thousands
#: of requests. The walk stops and says so rather than running for an hour.
MAX_PAGE_BODIES = 400

#: Page bodies are the one place acquisition makes many small requests, so they are the one place
#: it runs any concurrently. Kept low deliberately: Canvas throttles per token, and a 403 that
#: means "rate limited" reads exactly like a 403 that means "not permitted".
PAGE_BODY_CONCURRENCY = 4


@dataclass(frozen=True)
class Collection:
    """One fetched collection: its payloads and the record of what was examined."""

    name: str
    items: tuple[Any, ...]
    examination: Examination

    @property
    def usable(self) -> bool:
        return self.examination.usable


@dataclass(frozen=True)
class CourseIndex:
    """Everything the cheap phase reads. Enough to compute a cache signature."""

    course: dict[str, Any]
    collections: dict[str, Collection]

    def items(self, name: str) -> tuple[Any, ...]:
        collection = self.collections.get(name)
        return collection.items if collection else ()

    def examinations(self) -> tuple[Examination, ...]:
        return tuple(self.collections[name].examination for name in sorted(self.collections))


@dataclass(frozen=True)
class CourseBodies:
    """The expensive phase: page bodies keyed by page slug, and what it cost."""

    page_bodies: dict[str, str] = field(default_factory=dict)
    examination: Examination | None = None


#: ``name -> (path template, query parameters)``. Declared as data so the scope table, the README
#: and this module cannot drift into disagreement about which endpoints are called.
INDEX_ENDPOINTS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("tabs", "/courses/{course_id}/tabs", {}),
    ("assignment_groups", "/courses/{course_id}/assignment_groups", {}),
    ("rubrics", "/courses/{course_id}/rubrics", {}),
    ("modules", "/courses/{course_id}/modules", {"include[]": "items"}),
    ("pages", "/courses/{course_id}/pages", {}),
    ("assignments", "/courses/{course_id}/assignments", {}),
    ("quizzes", "/courses/{course_id}/quizzes", {}),
    (
        "discussions",
        "/courses/{course_id}/discussion_topics",
        {"only_announcements": "false"},
    ),
    (
        "announcements",
        "/courses/{course_id}/discussion_topics",
        {"only_announcements": "true"},
    ),
    ("files", "/courses/{course_id}/files", {}),
    # Outcomes come from the group *links* rather than from /outcomes, because one call then
    # answers both halves of the question — which outcomes the course defines, and which group
    # each is filed under. Asking for the outcomes alone would need a second call per group.
    (
        "outcomes",
        "/courses/{course_id}/outcome_group_links",
        {"outcome_style": "full"},
    ),
    # Listing the course's exports. Read only when Sightline is NOT asking Canvas to build a
    # fresh one — see read_index's `include_content_exports`, which is not a micro-optimisation
    # but a correctness rule: Sightline's own export appears in this listing, the listing feeds
    # the cache signature, and a signature that moves because Sightline ran is a cache that can
    # never hit and a fresh export on every single run.
    ("content_exports", "/courses/{course_id}/content_exports", {}),
)


class CourseReader:
    """Reads one course through a :class:`~sightline.canvas.client.CanvasClient`."""

    def __init__(self, client: CanvasClient) -> None:
        self._client = client

    # -- phase one: the index ----------------------------------------------------------------

    async def read_index(
        self, course_id: str, *, include_content_exports: bool = True
    ) -> CourseIndex:
        """Fetch the course and every collection listing. Always run; never expensive.

        ``include_content_exports`` is off when the caller is going to ask Canvas for a fresh
        export. Two reasons, and the second is the one that matters. Sightline never reads an
        export it did not ask for on that path, so the listing would be unused — and the listing
        contains Sightline's *own* previous exports, so including it would move the cache
        signature every time Sightline ran. The cache would then never hit and every run would ask
        Canvas to build another export.
        """
        course = await self._client.get_json(
            f"{API_ROOT}/courses/{course_id}", **{"include[]": "syllabus_body"}
        )
        if not isinstance(course, dict):  # pragma: no cover - the client raises first
            course = {}

        collections: dict[str, Collection] = {}
        for name, template, params in INDEX_ENDPOINTS:
            if name == "content_exports" and not include_content_exports:
                continue
            path = f"{API_ROOT}{template.format(course_id=course_id)}"
            collections[name] = await self._collection(name, path, params)

        # Module items are capped by Canvas when requested with ``include[]=items`` — it stops at
        # ten per module and gives no signal that it did. So any module whose item count matches
        # the cap is re-read from its own endpoint. Trusting the include would silently lose the
        # eleventh item of every long module, which is exactly the module worth reviewing.
        modules = collections["modules"]
        if modules.usable:
            collections["modules"] = await self._complete_module_items(course_id, modules)
        return CourseIndex(course=course, collections=collections)

    async def _collection(self, name: str, path: str, params: dict[str, Any]) -> Collection:
        try:
            listing = await self._client.get_list(path, **params)
        except CanvasPermissionError as exc:
            return _refused(name, path, exc)
        except CanvasNotFoundError as exc:
            return _absent(name, path, exc)
        except CanvasError as exc:
            return _refused(name, path, exc)
        return _fetched(name, path, listing)

    async def _complete_module_items(self, course_id: str, modules: Collection) -> Collection:
        """Re-read the items of any module that came back at exactly the include cap."""
        completed: list[Any] = []
        for module in modules.items:
            items = module.get("items")
            count = module.get("items_count")
            needs_reread = isinstance(count, int) and (items is None or len(items) < count)
            if not needs_reread:
                completed.append(module)
                continue
            path = f"{API_ROOT}/courses/{course_id}/modules/{module.get('id')}/items"
            try:
                listing = await self._client.get_list(path)
            except CanvasError as exc:
                log.warning(
                    "module_items_unreadable",
                    module_id=str(module.get("id")),
                    reason=type(exc).__name__,
                )
                completed.append(module)
                continue
            completed.append({**module, "items": list(listing.items)})
        return Collection(
            name=modules.name,
            items=tuple(completed),
            examination=modules.examination,
        )

    # -- phase two: bodies -------------------------------------------------------------------

    async def read_page_bodies(self, course_id: str, slugs: Sequence[str]) -> CourseBodies:
        """Fetch the body of each page. One request per page; Canvas has no bulk form.

        A page whose body cannot be read is left out of the mapping rather than recorded as an
        empty body: an empty body is a finding, and inventing one would be a fabricated fact.
        """
        wanted = list(dict.fromkeys(slugs))[:MAX_PAGE_BODIES]
        truncated = len(dict.fromkeys(slugs)) > len(wanted)
        bodies: dict[str, str] = {}
        refused = 0
        semaphore = asyncio.Semaphore(PAGE_BODY_CONCURRENCY)

        async def one(slug: str) -> tuple[str, str | None]:
            async with semaphore:
                try:
                    payload = await self._client.get_json(
                        f"{API_ROOT}/courses/{course_id}/pages/{slug}"
                    )
                except CanvasError:
                    return slug, None
            body = payload.get("body") if isinstance(payload, dict) else None
            return slug, body if isinstance(body, str) else None

        for slug, body in await asyncio.gather(*(one(slug) for slug in wanted)):
            if body is None:
                refused += 1
            else:
                bodies[slug] = body

        endpoint = f"GET {API_ROOT}/courses/{course_id}/pages/:url"
        detail = f"Read {len(bodies)} of {len(wanted)} page bodies."
        if refused:
            detail += f" {refused} could not be read and are recorded as having no body."
        if truncated:
            detail += f" The walk stopped at {MAX_PAGE_BODIES} pages."
        return CourseBodies(
            page_bodies=bodies,
            examination=Examination(
                collection="page_bodies",
                state=ExaminationState.OK if bodies else ExaminationState.EMPTY,
                examined=len(wanted),
                source=Source.API,
                endpoint=endpoint,
                detail=detail,
            ),
        )


# ---------------------------------------------------------------------------------------------
# Examination records
# ---------------------------------------------------------------------------------------------


def _fetched(name: str, path: str, listing: Listing) -> Collection:
    detail = f"Read {len(listing)} item(s) over {listing.pages_read} page(s)."
    if listing.truncated:
        detail += " The walk hit its page cap, so this collection is a floor, not a total."
    return Collection(
        name=name,
        items=listing.items,
        examination=Examination(
            collection=name,
            state=ExaminationState.OK if listing.items else ExaminationState.EMPTY,
            examined=len(listing),
            source=Source.API,
            endpoint=f"GET {path}",
            detail=detail,
        ),
    )


def _refused(name: str, path: str, exc: CanvasError) -> Collection:
    return Collection(
        name=name,
        items=(),
        examination=Examination(
            collection=name,
            state=ExaminationState.REFUSED,
            examined=0,
            source=Source.API,
            endpoint=f"GET {path}",
            detail=" ".join(filter(None, (exc.summary, exc.detail))),
            remedy=exc.remedy,
        ),
    )


def _absent(name: str, path: str, exc: CanvasError) -> Collection:
    return Collection(
        name=name,
        items=(),
        examination=Examination(
            collection=name,
            state=ExaminationState.ABSENT,
            examined=0,
            source=Source.API,
            endpoint=f"GET {path}",
            detail=(
                "Canvas returned 404. That is what it returns both when the feature is switched "
                "off for this course and when the token may not read it; it does not "
                "distinguish the two."
            ),
            remedy=exc.remedy,
        ),
    )


__all__ = [
    "INDEX_ENDPOINTS",
    "MAX_PAGE_BODIES",
    "Collection",
    "CourseBodies",
    "CourseIndex",
    "CourseReader",
]
