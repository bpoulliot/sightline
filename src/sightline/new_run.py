"""What the New run screen offers and what it refuses (roadmap item 36).

The New run screen is the course-resolution screen (:mod:`sightline.views`, item 02) with a Start
form under its results: the courses Canvas resolved, a rubric, and a button that records a batch
with :func:`sightline.batch.create_batch_run` and hands it to
:class:`sightline.runner.BackgroundRunner`. This module holds the two things that screen and its
Start action share, so the page and the POST cannot disagree about either.

*The rubric choices.* Every pack :func:`sightline.rubric.shipped.shipped_packs` finds, the default
selected (SPEC §09: rubric selection is per run, defaulting to OSCQR 3.1.3). A choice is a value,
not markup, so item 22's binding confirmation attaches to a choice without the picker being
rebuilt: each choice says whether the pack has *bindings proposed, not confirmed* and links to its
page (:mod:`sightline.rubric_screens`), and whether runs use bindings accepted there. Neither ever
blocks Start — bindings are an accelerator, not a prerequisite, and a run uses only confirmed ones.

*The reasons a run cannot start.* Each one is a :class:`Blocker` with a remedy, shown before
anybody presses Start and checked again when they do:

- no Canvas credential, because acquisition is the first stage;
- no model host, or no model chosen, because the extract stage aborts a run without them;
- **bindings are being proposed** on a rubric's page (item 22, part two): the proposer uses the
  same model host, so the two would compete for it as two runs would;
- **another run is running.** Two batches would compete for one model host, which is the reason
  :meth:`sightline.runner.BackgroundRunner.resume_all` resumes serially. A run this process is
  executing is in the runner's task list; one started by ``python -m sightline.batch run`` is
  another process and shows only as ``running`` in the store, with that process as its owner.
  Both refuse a start the same way — the store's only while its owner is live (roadmap item 43),
  so a command-line run that crashed does not block Start for ever.

**Starting a run requests a Canvas content export** when that setting is on (roadmap item 23):
it writes to the course's export list. The Start form says so beside the button.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from .batch import DEFAULT_RUBRIC_PACK
from .batch.service import CANVAS_REMEDY
from .credentials import CredentialError, CredentialStore
from .models import Run, RunStatus
from .ownership import owner_is_live
from .paths import DataPaths
from .preferences import PreferenceStore
from .proposing import ProposingRegistry, ProposingState
from .rubric.accepted import BindingsError, BindingStore, resolve_pack
from .rubric.proposals import fingerprint
from .rubric.shipped import PacksNotFoundError, shipped_packs
from .runner import BackgroundRunner
from .store import RunStore

#: Where every remedy below sends somebody.
SETTINGS_HREF = "/settings"


@dataclass(frozen=True, kw_only=True, slots=True)
class RubricChoice:
    """One rubric a run can be reviewed against."""

    id: str
    name: str
    version: str
    description: str | None
    criteria: int
    selected: bool
    #: Item 22: a kept proposal set made over the bindings in force has proposals nobody has
    #: accepted. Said beside the choice, with a link; never a reason the run cannot start.
    unconfirmed: bool = False
    #: Item 22: runs over this pack use bindings accepted on its page, rather than the shipped
    #: pack's own. ``None`` when they use the shipped pack's.
    accepted: str | None = None
    #: The rubric's page, where bindings are proposed, confirmed and reverted.
    href: str = ""


@dataclass(frozen=True, kw_only=True, slots=True)
class Blocker:
    """One reason a run cannot start now, and what to do about it."""

    summary: str
    remedy: str
    #: A page that resolves it, and what the link says.
    href: str | None = None
    link: str | None = None


def rubric_choices(
    selected: str | None = None, *, paths: DataPaths | None = None
) -> tuple[RubricChoice, ...]:
    """The shipped packs in file order, with ``selected`` (or the default) chosen.

    An empty tuple when no pack can be found; the screen says so rather than offering nothing.
    With ``paths``, each choice also carries its bindings state (item 22).
    """
    try:
        packs = shipped_packs()
    except PacksNotFoundError:
        return ()
    chosen = selected if selected in packs else DEFAULT_RUBRIC_PACK
    choices: list[RubricChoice] = []
    for pack in packs.values():
        choice = RubricChoice(
            id=pack.id,
            name=pack.name,
            version=pack.version,
            description=pack.description,
            criteria=len(pack.criteria),
            selected=pack.id == chosen,
            href=rubric_href(pack.id),
        )
        if paths is not None:
            choice = _with_bindings(choice, pack.id, paths)
        choices.append(choice)
    return tuple(choices)


def rubric_href(pack_id: str) -> str:
    """The rubric's page: its bindings, their history, and the proposals kept for it."""
    return f"/rubrics/{pack_id}"


def unconfirmed_sets(pack_id: str, paths: DataPaths) -> int:
    """How many kept proposal sets were made over the bindings in force and hold proposals.

    A set made over any other state of the pack can no longer be accepted (part one refuses it),
    so it is not open, whatever it holds.
    """
    resolved = resolve_pack(pack_id, paths)
    now = fingerprint(resolved.pack)
    return sum(
        1
        for saved in BindingStore(paths).proposal_sets(pack_id)
        if saved.proposals.fingerprint == now and saved.proposals.proposals
    )


def _with_bindings(choice: RubricChoice, pack_id: str, paths: DataPaths) -> RubricChoice:
    try:
        resolved = resolve_pack(pack_id, paths)
        unconfirmed = unconfirmed_sets(pack_id, paths) > 0
    except BindingsError:
        # The rubric's page says what is wrong with the kept bindings; the picker still offers
        # the pack, and a run started now records what it resolves to when it starts.
        return choice
    accepted = resolved.accepted
    return replace(
        choice,
        unconfirmed=unconfirmed,
        accepted=(
            None
            if accepted is None
            else f"Runs use the bindings accepted on {accepted.at[:10]} by {accepted.reviewer}."
        ),
    )


def active_runs(store: RunStore, runner: BackgroundRunner | None) -> list[Run]:
    """Runs executing now: this process's tasks, then store-``running`` runs with a live owner.

    A run the background runner holds is listed even in the instant before its status is written,
    because the task exists before ``_execute`` marks it running. A store-``running`` run whose
    owner is gone — a command-line run that was killed, a row older than owner records — is not
    executing, and does not refuse a start; the next recovery marks it interrupted.
    """
    found: list[Run] = []
    seen: set[str] = set()
    for run_id in runner.active_run_ids if runner is not None else ():
        run = store.get_run(run_id)
        if run is not None:
            found.append(run)
            seen.add(run.id)
    found.extend(run for run in store.running_runs() if run.id not in seen and owner_is_live(run))
    return found


def blockers(
    *,
    store: RunStore,
    runner: BackgroundRunner | None,
    credentials: CredentialStore,
    preferences: PreferenceStore,
    proposing: ProposingRegistry | None = None,
) -> list[Blocker]:
    """Every reason a run cannot start now, in the order somebody would fix them."""
    found: list[Blocker] = []
    try:
        canvas_ready = credentials.canvas().complete
    except CredentialError:
        canvas_ready = False
    if not canvas_ready:
        found.append(
            Blocker(
                summary="No Canvas credential is set, so no course could be acquired.",
                remedy=CANVAS_REMEDY,
                href=SETTINGS_HREF,
                link="Settings",
            )
        )
    if not preferences.model_host().configured:
        found.append(
            Blocker(
                summary=(
                    "No model host is set. The extract stage stops a run that has none, so a run "
                    "is not started without one."
                ),
                remedy="Set the local model host on the Settings screen, then start the run.",
                href=f"{SETTINGS_HREF}#models",
                link="Settings",
            )
        )
    elif not preferences.review_model().chosen:
        found.append(
            Blocker(
                summary=(
                    "No model is chosen for review work. The extract stage stops a run that has "
                    "none, so a run is not started without one."
                ),
                remedy=(
                    "Check the model host on the Settings screen and choose one of its models, "
                    "then start the run."
                ),
                href=f"{SETTINGS_HREF}#models",
                link="Settings",
            )
        )
    busy = proposing.running if proposing is not None else None
    if busy is not None:
        found.append(proposing_blocker(busy))
    for run in active_runs(store, runner):
        found.append(running_blocker(run))
    return found


def proposing_blocker(state: ProposingState) -> Blocker:
    """The refusal while bindings are being proposed: it names the rubric they are for."""
    return Blocker(
        summary=(
            f"Bindings are being proposed for {state.pack_name}. The proposer asks the same model "
            "host, one row at a time, and a run would compete with it for that host."
        ),
        remedy="Wait for the proposing to finish, then start the run. Its page shows how far it "
        "has got.",
        href=rubric_href(state.pack_id),
        link=state.pack_name,
    )


def running_blocker(run: Run) -> Blocker:
    """The refusal for one run that is already running."""
    return Blocker(
        summary=(
            f"Run {short_id(run.id)} is running ({len(run.courses)} course(s), "
            f"{run.rubric_pack}). One run at a time: courses are reviewed one after another "
            "against a single model host, and a second batch would compete with it for that host."
        ),
        remedy="Wait for it to finish, then start this one. Its page shows how far it has got.",
        href=f"/runs/{run.id}",
        link=f"Run {short_id(run.id)}",
    )


def short_id(run_id: str) -> str:
    """The first eight characters of a run id, as the status page and the run list show it."""
    return run_id[:8]


def is_running(status: str | RunStatus) -> bool:
    return RunStatus(status) in {RunStatus.RUNNING, RunStatus.PENDING}


def chosen_courses(values: Sequence[str]) -> list[str]:
    """The course ids a Start form posted, stripped and deduplicated, in the order given."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            ordered.append(cleaned)
    return ordered


__all__ = [
    "Blocker",
    "RubricChoice",
    "active_runs",
    "blockers",
    "chosen_courses",
    "is_running",
    "proposing_blocker",
    "rubric_choices",
    "rubric_href",
    "running_blocker",
    "short_id",
    "unconfirmed_sets",
]
