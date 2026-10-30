"""The rubric pages: bindings proposed, confirmed and reverted on a screen (item 22, part two).

Part one built the proposal engine and a command-line review. This is the screen, attached to the
New run screen's rubric picker (item 36) and reached from the *Rubrics* entry in the navigation:

- ``/rubrics`` lists the shipped packs and, for each, the bindings runs use and whether proposals
  are waiting.
- ``/rubrics/{pack}`` is one pack's page: the bindings in force with their coverage, the latest
  acceptance and the control that reverts it, the *Propose bindings* action and its progress, the
  proposal sets kept for the pack, and the history of every acceptance and revert.
- ``/rubrics/{pack}/proposals/{set}`` is the confirmation screen for one proposal set.

**The confirmation screen honours every decision of 2026-09-08 as part one recorded it.** Every
box starts unchecked; *check all* checks every proposal that is not a conflict and nothing else;
conflicts — proposals on a row already bound to other tags — are listed apart and each needs its
own box. Each proposal shows *what* (the tag, what it is, and the detectors behind it with what
each reports), *why* (the verified span, or that there is none) and *how* (part one's computed
:func:`~sightline.rubric.proposals.consequence`). Tier D rows read *not asked*, with the reason; a
row whose call did not come back reads as a row that could not be asked, never as one with none
proposed. **No confidence signal, ranking or model order anywhere**: proposals are shown in part
one's order — the instrument's rows, alphabetical within a row.

**The change is shown before it is accepted.** *Accept* is offered only once the coverage delta of
exactly the checked set has been shown; a set that changed after its delta was shown is shown
again instead of accepted. The delta is :func:`~sightline.rubric.proposals.accept`'s own, computed
without writing anything, so what is shown is what acceptance records. Accepting calls ``accept``
and keeps the result through :class:`sightline.rubric.accepted.BindingStore`, with the reviewer
(:func:`sightline.review.identity.reviewer_name`: the display name, no subject id), the time,
the proposal numbers and the delta. Every form works with scripting off.

**A proposal set made over another state of the pack is refused**, as part one refuses it, and the
page says to propose again. Accepting from a set changes the pack's bindings, so a set is accepted
once, as a unit; reverting that acceptance brings its state back, and the set is open again.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import Settings
from .detect.base import detectors
from .llm.client import OllamaClient
from .logging import get_logger
from .new_run import (
    SETTINGS_HREF,
    Blocker,
    active_runs,
    rubric_href,
    short_id,
)
from .paths import DataPaths
from .preferences import PreferenceStore
from .proposing import ProposingRegistry, ProposingState
from .review.identity import reviewer_name
from .rubric.accepted import (
    BindingsError,
    BindingStore,
    DeltaRecord,
    Entry,
    ResolvedPack,
    SavedSet,
    StaleError,
    base_mapping,
    resolve_pack,
    reverted_by,
    standing,
)
from .rubric.coverage import pack_coverage
from .rubric.pack import RubricPack
from .rubric.proposals import (
    NONDETERMINISM_NOTE,
    Acceptance,
    Proposal,
    ProposalError,
    RowOutcome,
    accept,
    coverage_delta,
    fingerprint,
)
from .rubric.shipped import PacksNotFoundError, shipped_packs
from .runner import BackgroundRunner
from .store import RunStore

log = get_logger("sightline.rubric_screens")

#: How often the optional script asks whether proposing moved on, in milliseconds.
POLL_MS = 3000

#: The words the coverage buckets are shown by, in the order they are listed.
BUCKETS: tuple[tuple[str, str], ...] = (
    ("deterministic", "Deterministic coverage"),
    ("evidence_only", "Evidence-only"),
    ("not_yet_implemented", "Bound to a capability no detector implements yet"),
    ("not_determinable", "Not determinable from course artifacts (Tier D)"),
)

#: Why a reviewer is needed before anything is accepted or reverted.
NO_REVIEWER = Blocker(
    summary=(
        "No reviewer is set. Each acceptance and each revert is recorded with who made it, so "
        "nothing is accepted or reverted until one is."
    ),
    remedy="Set the Reviewer on the Settings screen.",
    href=f"{SETTINGS_HREF}#reviewer",
    link="Settings",
)

_PACK_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


# ---------------------------------------------------------------------------------------------
# Page models
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class Bucket:
    label: str
    rows: int


def buckets(pack: RubricPack) -> tuple[Bucket, ...]:
    tally = pack_coverage(pack).counts
    return tuple(Bucket(label=label, rows=tally[key]) for key, label in BUCKETS)


@dataclass(frozen=True, kw_only=True, slots=True)
class SetLine:
    """One kept proposal set, as the rubric's page lists it."""

    saved: SavedSet
    state: str
    href: str

    @property
    def open(self) -> bool:
        return self.state == "open"


@dataclass(frozen=True, kw_only=True, slots=True)
class ProposalCard:
    """One proposal on the confirmation screen, with what is needed to show WHAT, WHY and HOW."""

    proposal: Proposal
    #: ``(detector id, what it says it reports)`` for each detector behind the tag, by id.
    detectors: tuple[tuple[str, str], ...]
    checked: bool
    #: The form field it posts under: ``proposal``, or ``conflict`` for a conflict.
    field: str


@dataclass(frozen=True, kw_only=True, slots=True)
class RowCard:
    """A row of the instrument with the proposals made for it."""

    outcome: RowOutcome
    cards: tuple[ProposalCard, ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class Change:
    """What accepting the checked proposals changes, computed by ``accept`` without writing."""

    names: tuple[int, ...]
    conflicts: tuple[int, ...]
    delta: DeltaRecord | None
    token: str
    error: str | None = None


@dataclass(kw_only=True)
class ConfirmPage:
    """Everything the confirmation screen prints."""

    pack: RubricPack
    saved: SavedSet
    open: bool
    accepted_in: Entry | None
    base: str
    proposals: list[RowCard] = field(default_factory=list)
    conflicts: list[RowCard] = field(default_factory=list)
    others: list[RowOutcome] = field(default_factory=list)
    change: Change | None = None
    reviewer: str | None = None
    banners: list[Blocker] = field(default_factory=list)

    @property
    def flagged(self) -> list[RowOutcome]:
        """Rows that could not be asked, were not asked, or carried something not kept."""
        return [row for row in self.others if row not in self.none_proposed]

    @property
    def none_proposed(self) -> list[RowOutcome]:
        """Rows asked, answered, and given no proposal — and nothing else to say."""
        return [
            row
            for row in self.others
            if row.failure is None
            and row.not_asked is None
            and not row.proposals
            and not row.already_bound
            and not row.unknown
        ]

    @property
    def non_conflicts(self) -> int:
        return sum(len(row.cards) for row in self.proposals)

    @property
    def conflict_count(self) -> int:
        return sum(len(row.cards) for row in self.conflicts)

    @property
    def can_accept(self) -> bool:
        return (
            self.open
            and self.reviewer is not None
            and self.change is not None
            and self.change.error is None
            and bool(self.change.names or self.change.conflicts)
        )


def token_of(names: Sequence[int], conflicts: Sequence[int]) -> str:
    """The checked set as one string, so a posted Accept can be compared to what was shown."""
    return (
        "p:"
        + ",".join(str(n) for n in sorted(set(names)))
        + ";c:"
        + ",".join(str(n) for n in sorted(set(conflicts)))
    )


def set_state(saved: SavedSet, now: str, history: Sequence[Entry]) -> tuple[str, Entry | None]:
    """``open``, ``accepted`` or ``other state``, and the standing acceptance that took it."""
    taken = next(
        (entry for entry in reversed(standing(history)) if entry.proposal_set == saved.id), None
    )
    if saved.proposals.fingerprint == now:
        return ("open" if saved.proposals.proposals else "empty"), taken
    if taken is not None:
        return "accepted", taken
    return "other state", None


def confirm_page(
    *,
    resolved: ResolvedPack,
    saved: SavedSet,
    history: Sequence[Entry],
    checked: Sequence[int] = (),
    conflicts: Sequence[int] = (),
    change: Change | None = None,
    reviewer: str | None,
) -> ConfirmPage:
    state, taken = set_state(saved, fingerprint(resolved.pack), history)
    behind = detectors_behind()
    proposals = saved.proposals
    page = ConfirmPage(
        pack=resolved.pack,
        saved=saved,
        open=state == "open",
        accepted_in=taken,
        base=resolved.digest or "",
        change=change,
        reviewer=reviewer,
    )
    for row in proposals.rows:
        made = [p for p in (proposals.get(n) for n in row.proposals) if p is not None]
        plain = tuple(
            ProposalCard(
                proposal=p,
                detectors=behind.get(p.tag, ()),
                checked=p.id in checked,
                field="proposal",
            )
            for p in made
            if not p.conflict
        )
        crossed = tuple(
            ProposalCard(
                proposal=p,
                detectors=behind.get(p.tag, ()),
                checked=p.id in conflicts,
                field="conflict",
            )
            for p in made
            if p.conflict
        )
        if plain:
            page.proposals.append(RowCard(outcome=row, cards=plain))
        if crossed:
            page.conflicts.append(RowCard(outcome=row, cards=crossed))
        if not made or row.already_bound or row.unknown:
            page.others.append(row)
    return page


def detectors_behind() -> Mapping[str, tuple[tuple[str, str], ...]]:
    """Tag -> every detector declaring it, by id, with the detector's own description."""
    grouped: dict[str, list[tuple[str, str]]] = {}
    for detector in sorted(detectors(), key=lambda item: item.id):
        grouped.setdefault(detector.capability_tag, []).append(
            (detector.id, detector.capability_description)
        )
    return {tag: tuple(pairs) for tag, pairs in grouped.items()}


def preview(
    resolved: ResolvedPack, saved: SavedSet, names: Sequence[int], conflicts: Sequence[int]
) -> tuple[Change, Acceptance | None]:
    """``accept``'s result for the checked set, or why it would be refused. Writes nothing."""
    token = token_of(names, conflicts)
    if not names and not conflicts:
        return (
            Change(
                names=(),
                conflicts=(),
                delta=None,
                token=token,
                error="Nothing is checked, so accepting would change nothing.",
            ),
            None,
        )
    try:
        result = accept(
            saved.proposals,
            resolved.pack,
            names=sorted(set(names)),
            conflicts=sorted(set(conflicts)),
            base=base_mapping(resolved.pack, shipped=resolved.shipped),
        )
    except (ProposalError, BindingsError) as exc:
        return (
            Change(
                names=tuple(names),
                conflicts=tuple(conflicts),
                delta=None,
                token=token,
                error=f"{str(exc)[:1].upper()}{str(exc)[1:]}",
            ),
            None,
        )
    return (
        Change(
            names=tuple(sorted(set(names))),
            conflicts=tuple(sorted(set(conflicts))),
            delta=DeltaRecord.of(result.delta),
            token=token,
        ),
        result,
    )


# ---------------------------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------------------------


def propose_blockers(request: Request) -> list[Blocker]:
    """Why bindings cannot be proposed now, in the order somebody would fix them."""
    preferences = _preferences(request)
    found: list[Blocker] = []
    if not preferences.model_host().configured:
        found.append(
            Blocker(
                summary="No model host is set, so there is nothing to ask for proposals.",
                remedy="Set the local model host on the Settings screen, then propose.",
                href=f"{SETTINGS_HREF}#models",
                link="Settings",
            )
        )
    elif not preferences.review_model().chosen:
        found.append(
            Blocker(
                summary=(
                    "No model is chosen for review work. Proposals are made with the review "
                    "model chosen in Settings."
                ),
                remedy="Choose one of the model host's models on the Settings screen.",
                href=f"{SETTINGS_HREF}#models",
                link="Settings",
            )
        )
    busy = _proposing(request).running
    if busy is not None:
        found.append(
            Blocker(
                summary=(
                    f"Bindings are already being proposed for {busy.pack_name}. One proposer at "
                    "a time asks the model host."
                ),
                remedy="Wait for it to finish. Its page shows how far it has got.",
                href=rubric_href(busy.pack_id),
                link=busy.pack_name,
            )
        )
    for run in active_runs(_store(request), _runner(request)):
        found.append(
            Blocker(
                summary=(
                    f"Run {short_id(run.id)} is running ({len(run.courses)} course(s), "
                    f"{run.rubric_pack}). It is asking the model host, and the proposer would "
                    "compete with it for that host."
                ),
                remedy="Wait for the run to finish, then propose. Its page shows how far it has "
                "got.",
                href=f"/runs/{run.id}",
                link=f"Run {short_id(run.id)}",
            )
        )
    return found


# ---------------------------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------------------------


def build_rubric_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/rubrics", include_in_schema=False)
    async def rubric_list(request: Request) -> Any:
        try:
            packs = shipped_packs()
        except PacksNotFoundError:
            packs = {}
        paths = _paths(request)
        lines: list[dict[str, Any]] = []
        for pack in packs.values():
            line: dict[str, Any] = {"pack": pack, "href": rubric_href(pack.id), "error": None}
            try:
                resolved = resolve_pack(pack.id, paths)
                now = fingerprint(resolved.pack)
                history = BindingStore(paths).history(pack.id)
                line["resolved"] = resolved
                line["open"] = sum(
                    1
                    for saved in BindingStore(paths).proposal_sets(pack.id)
                    if set_state(saved, now, history)[0] == "open"
                )
            except BindingsError as exc:
                line["error"] = str(exc)
            lines.append(line)
        return templates.TemplateResponse(
            request=request,
            name="rubrics.html",
            context={
                "nav": "rubrics",
                "lines": lines,
                "proposing": _proposing(request).running,
            },
        )

    @router.get("/rubrics/{pack_id}", include_in_schema=False)
    async def rubric_page(request: Request, pack_id: str) -> Any:
        return _render_rubric(request, templates, pack_id)

    @router.get("/api/rubrics/{pack_id}/proposing")
    async def proposing_progress(request: Request, pack_id: str) -> JSONResponse:
        """How far proposing has got for one pack, for the page's optional script."""
        state = _proposing(request).get(pack_id)
        if state is None:
            return JSONResponse({"pack": pack_id, "running": False}, status_code=200)
        return JSONResponse(state.to_dict())

    @router.post("/rubrics/{pack_id}/propose", include_in_schema=False)
    async def start_proposing(request: Request, pack_id: str) -> Any:
        """Start proposing over the pack as runs use it now. Refusals are re-checked here."""
        resolved = _resolved_or_404(request, pack_id)
        if isinstance(resolved, _Missing):
            return _missing(request, templates, resolved)
        refusals = propose_blockers(request)
        if refusals:
            return _render_rubric(request, templates, pack_id, refusals=refusals, status=409)
        settings: Settings = request.app.state.settings
        endpoint = _preferences(request).model_host().endpoint or ""
        model = _preferences(request).review_model().name or ""

        def client() -> OllamaClient:
            # Item 09's client at concurrency 1: the proposer asks one row at a time.
            return OllamaClient(
                endpoint, concurrency=1, context_tokens=settings.ollama_context_tokens
            )

        _proposing(request).start(
            resolved.pack,
            over=resolved.digest,
            model=model,
            client_factory=client,
            paths=_paths(request),
        )
        return RedirectResponse(f"{rubric_href(pack_id)}#propose", status_code=303)

    @router.post("/rubrics/{pack_id}/revert", include_in_schema=False)
    async def revert(request: Request, pack_id: str) -> Any:
        resolved = _resolved_or_404(request, pack_id)
        if isinstance(resolved, _Missing):
            return _missing(request, templates, resolved)
        form = await request.form()
        reviewer = reviewer_name(request)
        if reviewer is None:
            return _render_rubric(request, templates, pack_id, refusals=[NO_REVIEWER], status=409)
        try:
            expected = int(str(form.get("acceptance", "")))
        except ValueError:
            expected = -1
        try:
            entry = BindingStore(_paths(request)).revert_latest(
                shipped=resolved.shipped, expected=expected, reviewer=reviewer
            )
        except (StaleError, BindingsError) as exc:
            refusal = Blocker(summary=str(exc), remedy="Nothing was changed.")
            return _render_rubric(request, templates, pack_id, refusals=[refusal], status=409)
        log.info("bindings_reverted", pack=pack_id, entry=entry.number, reverts=entry.reverts)
        return RedirectResponse(
            f"{rubric_href(pack_id)}?done={entry.number}#bindings", status_code=303
        )

    @router.get("/rubrics/{pack_id}/proposals/{set_id}", include_in_schema=False)
    async def confirmation(request: Request, pack_id: str, set_id: str) -> Any:
        found = _set_or_404(request, pack_id, set_id)
        if isinstance(found, _Missing):
            return _missing(request, templates, found)
        resolved, saved = found
        page = confirm_page(
            resolved=resolved,
            saved=saved,
            history=BindingStore(_paths(request)).history(pack_id),
            reviewer=reviewer_name(request),
        )
        return _render_confirm(request, templates, page)

    @router.post("/rubrics/{pack_id}/proposals/{set_id}", include_in_schema=False)
    async def confirm(request: Request, pack_id: str, set_id: str) -> Any:
        """Check all, show the change, or accept. Only an accept writes anything."""
        found = _set_or_404(request, pack_id, set_id)
        if isinstance(found, _Missing):
            return _missing(request, templates, found)
        resolved, saved = found
        paths = _paths(request)
        store = BindingStore(paths)
        history = store.history(pack_id)
        form = await request.form()
        action = str(form.get("action", ""))
        names = _ints(form.getlist("proposal"))
        conflicts = _ints(form.getlist("conflict"))
        reviewer = reviewer_name(request)

        def again(change: Change | None, banners: list[Blocker], status: int) -> Any:
            page = confirm_page(
                resolved=resolved,
                saved=saved,
                history=history,
                checked=names,
                conflicts=conflicts,
                change=change,
                reviewer=reviewer,
            )
            page.banners = banners
            return _render_confirm(request, templates, page, status=status)

        state, _taken = set_state(saved, fingerprint(resolved.pack), history)
        if state != "open":
            return again(None, [_not_open(state, pack_id)], 409)
        if str(form.get("base", "")) != (resolved.digest or ""):
            moved = Blocker(
                summary=(
                    "The bindings in force changed after this page was shown, so nothing was "
                    "accepted."
                ),
                remedy="The page below is shown against the bindings as they are now.",
            )
            return again(None, [moved], 409)
        if action == "check_all":
            names = [p.id for p in saved.proposals.proposals if not p.conflict]
        change, result = preview(resolved, saved, names, conflicts)
        if action != "accept":
            return again(change, [], 200)
        if reviewer is None:
            return again(change, [NO_REVIEWER], 409)
        if change.error is not None or result is None:
            refused = Blocker(summary=change.error or "Nothing was accepted.", remedy="")
            return again(change, [refused], 409)
        if str(form.get("shown", "")) != change.token:
            shown = Blocker(
                summary=(
                    "The checked proposals changed after their change was shown, so nothing was "
                    "accepted."
                ),
                remedy="Below is what accepting the proposals checked now changes. Accept again "
                "to record it.",
            )
            return again(change, [shown], 409)
        try:
            entry = store.record_acceptance(
                saved,
                result,
                shipped=resolved.shipped,
                before=resolved.digest,
                reviewer=reviewer,
            )
        except (StaleError, BindingsError) as exc:
            return again(None, [Blocker(summary=str(exc), remedy="Nothing was changed.")], 409)
        log.info(
            "bindings_accepted",
            pack=pack_id,
            entry=entry.number,
            proposals=len(entry.accepted),
            conflicts=len(entry.conflicts),
        )
        return RedirectResponse(
            f"{rubric_href(pack_id)}?done={entry.number}#bindings", status_code=303
        )

    return router


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------


def _render_rubric(
    request: Request,
    templates: Jinja2Templates,
    pack_id: str,
    *,
    refusals: list[Blocker] | None = None,
    status: int = 200,
) -> Any:
    resolved = _resolved_or_404(request, pack_id)
    if isinstance(resolved, _Missing):
        return _missing(request, templates, resolved)
    paths = _paths(request)
    store = BindingStore(paths)
    history = store.history(pack_id)
    now = fingerprint(resolved.pack)
    sets = [
        SetLine(
            saved=saved,
            state=set_state(saved, now, history)[0],
            href=f"{rubric_href(pack_id)}/proposals/{saved.id}",
        )
        for saved in store.proposal_sets(pack_id)
    ]
    stack = standing(history)
    latest = stack[-1] if stack else None
    revert_delta: DeltaRecord | None = None
    if latest is not None:
        try:
            revert_delta = DeltaRecord.of(
                coverage_delta(
                    store.pack_at(resolved.shipped, history[-1].after),
                    store.pack_at(resolved.shipped, latest.before),
                )
            )
        except BindingsError:
            revert_delta = None
    state = _proposing(request).get(pack_id)
    done = request.query_params.get("done")
    just = next((entry for entry in history if str(entry.number) == done), None)
    return templates.TemplateResponse(
        request=request,
        name="rubric.html",
        context={
            "nav": "rubrics",
            "resolved": resolved,
            "pack": resolved.shipped,
            "buckets": buckets(resolved.pack),
            "latest": latest,
            "revert_delta": revert_delta,
            "history": tuple(reversed(history)),
            "reverted_by": reverted_by(history),
            "sets": sets,
            "proposing": state,
            "proposing_json": json.dumps(state.to_dict(), sort_keys=True) if state else None,
            "poll_ms": POLL_MS,
            "blockers": refusals if refusals is not None else propose_blockers(request),
            "refused": refusals is not None,
            "model": _preferences(request).review_model().name,
            "reviewer": reviewer_name(request),
            "just": just,
            # Its first sentence: the rest speaks of a file, and none exists before proposing.
            "nondeterminism": NONDETERMINISM_NOTE.split(". ")[0] + ".",
            "state_words": STATE_WORDS,
        },
        status_code=status,
    )


def _render_confirm(
    request: Request, templates: Jinja2Templates, page: ConfirmPage, *, status: int = 200
) -> Any:
    return templates.TemplateResponse(
        request=request,
        name="proposals.html",
        context={
            "nav": "rubrics",
            "page": page,
            "href": rubric_href(page.pack.id),
            "nondeterminism": NONDETERMINISM_NOTE,
            "not_open": None if page.open else _not_open(_page_state(page), page.pack.id),
        },
        status_code=status,
    )


def _page_state(page: ConfirmPage) -> str:
    if page.accepted_in is not None and not page.open:
        return "accepted"
    if not page.saved.proposals.proposals:
        return "empty"
    return "other state"


def _not_open(state: str, pack_id: str) -> Blocker:
    if state == "accepted":
        return Blocker(
            summary=(
                "These proposals have been accepted, and the rubric's bindings changed with them. "
                "A proposal set is accepted once, as a unit."
            ),
            remedy="Reverting that acceptance on the rubric's page opens the set again; or "
            "propose again over the bindings as they are now.",
            href=rubric_href(pack_id),
            link="The rubric's page",
        )
    if state == "empty":
        return Blocker(
            summary="The model proposed no binding in this set, so there is nothing to accept.",
            remedy="The rows it asked about are listed below.",
        )
    return Blocker(
        summary=(
            "These proposals were made over the rubric as it was then, and its bindings have "
            "changed since. Accepting them is refused."
        ),
        remedy="Propose again over the rubric as it is now.",
        href=f"{rubric_href(pack_id)}#propose",
        link="Propose again",
    )


# ---------------------------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Missing:
    summary: str
    status: int = 404


def _resolved_or_404(request: Request, pack_id: str) -> ResolvedPack | _Missing:
    if not _PACK_ID.match(pack_id):
        return _Missing(f"No shipped rubric has the id {pack_id!r}.")
    try:
        return resolve_pack(pack_id, _paths(request))
    except (KeyError, PacksNotFoundError):
        return _Missing(f"No shipped rubric has the id {pack_id!r}.")
    except BindingsError as exc:
        return _Missing(
            f"The bindings kept for {pack_id} under the data root cannot be read: {exc}.", 500
        )


def _set_or_404(
    request: Request, pack_id: str, set_id: str
) -> tuple[ResolvedPack, SavedSet] | _Missing:
    resolved = _resolved_or_404(request, pack_id)
    if isinstance(resolved, _Missing):
        return resolved
    try:
        saved = BindingStore(_paths(request)).proposal_set(pack_id, set_id)
    except (ProposalError, BindingsError) as exc:
        return _Missing(f"The proposal set {set_id} cannot be read: {exc}", 500)
    if saved is None:
        return _Missing(f"No proposal set {set_id!r} is kept for {pack_id}.")
    return resolved, saved


def _missing(request: Request, templates: Jinja2Templates, missing: _Missing) -> Any:
    return templates.TemplateResponse(
        request=request,
        name="rubric_missing.html",
        context={"nav": "rubrics", "summary": missing.summary},
        status_code=missing.status,
    )


def _ints(values: Sequence[Any]) -> list[int]:
    out: list[int] = []
    for value in values:
        text = str(value).strip()
        if text.isdigit():
            out.append(int(text))
    return out


def _paths(request: Request) -> DataPaths:
    paths: DataPaths | None = getattr(request.app.state, "paths", None)
    if paths is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the data root is not initialised")
    return paths


def _store(request: Request) -> RunStore:
    store: RunStore | None = getattr(request.app.state, "store", None)
    if store is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the run store is not initialised")
    return store


def _runner(request: Request) -> BackgroundRunner | None:
    return getattr(request.app.state, "runner", None)


def _preferences(request: Request) -> PreferenceStore:
    preferences: PreferenceStore | None = getattr(request.app.state, "preferences", None)
    if preferences is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the preference store is not initialised")
    return preferences


def _proposing(request: Request) -> ProposingRegistry:
    registry: ProposingRegistry | None = getattr(request.app.state, "proposing", None)
    if registry is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the proposing registry is not initialised")
    return registry


#: What each state of a kept proposal set is called on the rubric's page.
STATE_WORDS: Mapping[str, str] = {
    "open": "Open",
    "accepted": "Accepted",
    "empty": "No binding proposed",
    "other state": "Made over an earlier state",
}


__all__ = [
    "BUCKETS",
    "ConfirmPage",
    "ProposingState",
    "build_rubric_router",
    "confirm_page",
    "preview",
    "propose_blockers",
    "set_state",
    "token_of",
]
