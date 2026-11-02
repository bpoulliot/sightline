"""The review screen's routes (roadmap item 16): the index, a page per category, and two actions.

Server-rendered, and every action is a form post that works with JavaScript off, like
:mod:`sightline.views`, carrying the CSRF token :mod:`sightline.csrf` checks. A course is reached
by its **run-store** course id — the id ``python -m sightline.batch status`` prints — so neither a
Canvas host nor a Canvas course id is ever in a path.

**Opening is loading.** The first request for any page of a course's review records that a person
opened it (:meth:`sightline.batch.gate.ExportGate.open_review`, idempotent: a later page never
moves the timestamp). That is the human action the export gate waits for. A course whose review
file cannot be read is not opened: there was nothing to look at. The export endpoint never opens a
review itself, and refuses an unopened course with the gate's own message.

**Recording a rating** is one form for one row: the machine proposal is read from the review file
(:meth:`sightline.rubric.overrides.MachineProposal.from_review`), the reviewer from
:func:`sightline.review.identity.current_reviewer` — the one lookup, which in sign-in mode is the
signed-in identity, recorded by subject id with the display name beside it (item 44) — and both go
to item 12a's append-only record. There is no route that records more than one row, by design
(SPEC §09: no bulk-accept).

**Only the run's starter or an admin reaches a course** (roadmap item 45, :mod:`sightline.access`).
Every route here asks :func:`_known` first, which answers a course of somebody else's run exactly
as a course that does not exist — so nobody else can open the review, and opening is what the
export gate waits for.
"""

from __future__ import annotations

import json
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..access import visible_course
from ..batch.gate import ExportGate, ReviewNotOpenedError
from ..detect.base import Tier
from ..logging import get_logger
from ..paths import DataPaths
from ..report.export import export_course
from ..report.loader import ReviewFileError, parse_review, review_path
from ..report.model import ReportError, anchor_for
from ..report.pdf import REPORT_FILENAME
from ..rubric.overrides import MachineProposal, latest_overrides, record_override
from ..rubric.states import Rating
from ..store import RunStore
from ..voice.render import Voice
from ..workbook import WORKBOOK_FILENAME, WorkbookError
from .identity import current_reviewer, reviewer_name
from .screen import NOTE_MAX, ExportFile, ReviewScreen, View, category_href, index_href

log = get_logger("sightline.review")

#: The files an export writes, and the only names the download route serves.
EXPORT_FILES: dict[str, str] = {
    REPORT_FILENAME: "application/pdf",
    WORKBOOK_FILENAME: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


class _Refused(Exception):
    """A request the screen answers with a message page rather than a review page."""

    def __init__(self, status: int, summary: str, remedy: str | None = None) -> None:
        super().__init__(summary)
        self.status = status
        self.summary = summary
        self.remedy = remedy


def build_review_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/review/{course_id}", include_in_schema=False)
    async def review_index(request: Request, course_id: str, exported: str | None = None) -> Any:
        try:
            screen, raw = _open(request, course_id)
        except _Refused as refused:
            return _message(request, templates, refused, course_id=None)
        gate = _gate(request)
        files = [
            ExportFile(name=name, href=f"{index_href(course_id)}/files/{name}")
            for name in EXPORT_FILES
            if gate.destination(course_id, name).is_file()
        ]
        page = screen.index(exported=bool(exported) and bool(files), files=files)
        return templates.TemplateResponse(
            request=request,
            name="review_index.html",
            context={"nav": "review", "page": page},
        )

    @router.get("/review/{course_id}/category/{section}", include_in_schema=False)
    async def review_category(
        request: Request,
        course_id: str,
        section: str,
        sort: str | None = None,
        reading: str | None = None,
        tier: str | None = None,
        only: str | None = None,
        saved: str | None = None,
    ) -> Any:
        try:
            screen, _raw = _open(request, course_id)
        except _Refused as refused:
            return _message(request, templates, refused, course_id=None)
        view = View.parse(sort=sort, reading=reading, tier=tier, only=only)
        return _category(
            request,
            templates,
            screen,
            section,
            view,
            saved=screen.saved(saved) if saved else None,
        )

    @router.post("/review/{course_id}/override", include_in_schema=False)
    async def record(
        request: Request,
        course_id: str,
        code: Annotated[str, Form()] = "",
        rating: Annotated[str, Form()] = "",
        note: Annotated[str, Form()] = "",
        section: Annotated[str, Form()] = "",
        sort: Annotated[str, Form()] = "",
        reading: Annotated[str, Form()] = "",
        tier: Annotated[str, Form()] = "",
        only: Annotated[str, Form()] = "",
    ) -> Any:
        view = View.parse(sort=sort, reading=reading, tier=tier, only=only)
        try:
            _known(request, course_id)
            gate = _gate(request)
            if not gate.is_open(course_id):
                raise _not_opened(gate, course_id)
            screen, raw = _open(request, course_id)
        except _Refused as refused:
            return _message(request, templates, refused, course_id=None)
        row = screen.review.projection.row(code)
        if row is None:
            refused = _Refused(400, f"This review has no criterion {code!r}; nothing was recorded.")
            return _message(request, templates, refused, course_id=course_id)
        section = row.section

        reviewer = current_reviewer(request)
        if reviewer is None:
            # The form is not offered without a name; a post that arrives anyway is refused the
            # same way, naming Settings, and records nothing.
            return _category(
                request,
                templates,
                screen,
                section,
                view,
                refused=screen.label("review_needs_reviewer"),
                status=409,
            )

        if not rating:
            if row.tier is Tier.C:
                # A question is never answered by default: No Response is a choice like any other.
                return _category(
                    request,
                    templates,
                    screen,
                    section,
                    view,
                    refused=screen.choose_question(row.code),
                    status=400,
                )
            rating = row.rating.value  # a note with no change confirms the proposal
        try:
            human = Rating(rating)
        except ValueError:
            refused = _Refused(400, f"{rating!r} is not a rating; nothing was recorded.")
            return _message(request, templates, refused, course_id=course_id)
        text = note.strip()
        if len(text) > NOTE_MAX:
            refused = _Refused(
                400,
                f"The note is {len(text)} characters; a note holds at most {NOTE_MAX}. Nothing "
                "was recorded.",
                "Shorten the note and record the rating again.",
            )
            return _message(request, templates, refused, course_id=course_id)

        proposal = MachineProposal.from_review(raw["projection"], row.code)
        record_override(
            _store(request),
            course_id=course_id,
            proposal=proposal,
            human=human,
            reviewer=reviewer.name,
            reviewer_subject=reviewer.subject,
            note=text or None,
        )
        log.info("rating_recorded", course_id=course_id, criterion=row.code)
        target = category_href(course_id, section, view)
        joiner = "&" if "?" in target else "?"
        return RedirectResponse(
            f"{target}{joiner}saved={quote(row.code, safe="")}#{anchor_for(row.code)}",
            status_code=303,
        )

    @router.post("/review/{course_id}/export", include_in_schema=False)
    async def export(request: Request, course_id: str) -> Any:
        """Write the PDF and the workbook through the gate. Never opens the review itself."""
        try:
            _known(request, course_id)
            gate = _gate(request)
            if not gate.is_open(course_id):
                raise _not_opened(gate, course_id)
            export_course(_store(request), _paths(request), course_id)
        except _Refused as refused:
            return _message(request, templates, refused, course_id=None)
        except (ReviewFileError, ReportError, WorkbookError) as exc:
            refused = _Refused(409, f"Nothing was exported: {exc}")
            return _message(request, templates, refused, course_id=course_id)
        return RedirectResponse(f"{index_href(course_id)}?exported=1#export", status_code=303)

    @router.get("/review/{course_id}/files/{name}", include_in_schema=False)
    async def exported_file(request: Request, course_id: str, name: str) -> Any:
        """An exported file, read back through :meth:`ExportGate.destination` and nowhere else."""
        try:
            _known(request, course_id)
            if name not in EXPORT_FILES:
                raise _Refused(404, f"An export writes {', '.join(EXPORT_FILES)}; not {name!r}.")
            gate = _gate(request)
            try:
                path = gate.destination(course_id, name)
            except ReviewNotOpenedError as exc:
                raise _Refused(409, exc.summary, exc.remedy) from exc
            if not path.is_file():
                raise _Refused(404, f"{name} has not been written for this course yet.")
        except _Refused as refused:
            return _message(request, templates, refused, course_id=None)
        return FileResponse(path, media_type=EXPORT_FILES[name], filename=name)

    return router


# ---------------------------------------------------------------------------------------------
# Shared work
# ---------------------------------------------------------------------------------------------


def _store(request: Request) -> RunStore:
    store: RunStore | None = getattr(request.app.state, "store", None)
    if store is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the run store is not initialised")
    return store


def _paths(request: Request) -> DataPaths:
    paths: DataPaths | None = getattr(request.app.state, "paths", None)
    if paths is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the data paths are not initialised")
    return paths


def _gate(request: Request) -> ExportGate:
    return ExportGate(_store(request), _paths(request))


def _known(request: Request, course_id: str) -> None:
    """Refuse, as unknown, a course that is not in the store *or* not this person's to see.

    Every route here calls this first (roadmap item 45). In sign-in mode a course of a run
    somebody else started answers exactly as a course that does not exist: 404, the same words.
    Opening is what satisfies the export gate, so the refusal comes before anything is read.
    """
    if visible_course(request, _store(request), course_id) is None:
        voice = Voice()
        _, summary = voice.label("review_unknown_course")
        raise _Refused(404, summary.text)


def _not_opened(gate: ExportGate, course_id: str) -> _Refused:
    try:
        gate.require_open(course_id)
    except ReviewNotOpenedError as exc:
        return _Refused(409, exc.summary, exc.remedy)
    raise AssertionError("the review is open")  # pragma: no cover - callers checked


def _open(request: Request, course_id: str) -> tuple[ReviewScreen, dict[str, Any]]:
    """Read the course's review file, then record that a person opened it. Raises ``_Refused``.

    The file is read *before* the review is marked opened, so a course with no readable review is
    never recorded as opened by a page that could not show it.
    """
    _known(request, course_id)
    store, paths = _store(request), _paths(request)
    try:
        text = review_path(paths, store, course_id).read_text(encoding="utf-8")
        loaded = parse_review(text, paths=paths)
    except ReviewFileError as exc:
        raise _Refused(409, str(exc)) from exc
    raw = json.loads(text)
    opened = _gate(request).open_review(course_id)
    screen = ReviewScreen(
        course_id=course_id,
        review=loaded,
        opened=opened,
        records=latest_overrides(store, course_id),
        reviewer=reviewer_name(request),
    )
    return screen, raw


def _category(
    request: Request,
    templates: Jinja2Templates,
    screen: ReviewScreen,
    section: str,
    view: View,
    *,
    saved: Any = None,
    refused: Any = None,
    status: int = 200,
) -> Any:
    try:
        page = screen.category(section, view, saved=saved, refused=refused)
    except KeyError:
        return _message(
            request,
            templates,
            _Refused(404, f"This review has no category {section!r}."),
            course_id=screen.course_id,
        )
    return templates.TemplateResponse(
        request=request,
        name="review_category.html",
        context={"nav": "review", "page": page},
        status_code=status,
    )


def _message(
    request: Request, templates: Jinja2Templates, refused: _Refused, *, course_id: str | None
) -> Any:
    voice = Voice()
    _, back = voice.label("review_back")
    return templates.TemplateResponse(
        request=request,
        name="review_message.html",
        context={
            "nav": "review",
            "summary": refused.summary,
            "remedy": refused.remedy,
            "back": back.text if course_id else None,
            "back_href": index_href(course_id) if course_id else None,
        },
        status_code=refused.status,
    )


__all__ = ["EXPORT_FILES", "build_review_router"]
