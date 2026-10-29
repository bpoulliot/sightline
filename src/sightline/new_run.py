"""What the New run screen offers and what it refuses (roadmap item 36).

The New run screen is the course-resolution screen (:mod:`sightline.views`, item 02) with a Start
form under its results: the courses Canvas resolved, a rubric, and a button that records a batch
with :func:`sightline.batch.create_batch_run` and hands it to
:class:`sightline.runner.BackgroundRunner`. This module holds the two things that screen and its
Start action share, so the page and the POST cannot disagree about either.

*The rubric choices.* Every pack :func:`sightline.rubric.shipped.shipped_packs` finds, the default
selected (SPEC §09: rubric selection is per run, defaulting to OSCQR 3.1.3). A choice is a value,
not markup, so a later screen — item 22's binding confirmation — can attach to a choice without
the picker being rebuilt.

*The reasons a run cannot start.* Each one is a :class:`Blocker` with a remedy, shown before
anybody presses Start and checked again when they do:

- no Canvas credential, because acquisition is the first stage;
- no model host, or no model chosen, because the extract stage aborts a run without them;
- **another run is running.** Two batches would compete for one model host, which is the reason
  :meth:`sightline.runner.BackgroundRunner.resume_all` resumes serially. A run this process is
  executing is in the runner's task list; one started by ``python -m sightline.batch run`` is
  another process and shows only as ``running`` in the store. Both refuse a start the same way.

**Starting a run requests a Canvas content export** when that setting is on (roadmap item 23):
it writes to the course's export list. The Start form says so beside the button.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .batch import DEFAULT_RUBRIC_PACK
from .batch.service import CANVAS_REMEDY
from .credentials import CredentialError, CredentialStore
from .models import Run, RunStatus
from .preferences import PreferenceStore
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


@dataclass(frozen=True, kw_only=True, slots=True)
class Blocker:
    """One reason a run cannot start now, and what to do about it."""

    summary: str
    remedy: str
    #: A page that resolves it, and what the link says.
    href: str | None = None
    link: str | None = None


def rubric_choices(selected: str | None = None) -> tuple[RubricChoice, ...]:
    """The shipped packs in file order, with ``selected`` (or the default) chosen.

    An empty tuple when no pack can be found; the screen says so rather than offering nothing.
    """
    try:
        packs = shipped_packs()
    except PacksNotFoundError:
        return ()
    chosen = selected if selected in packs else DEFAULT_RUBRIC_PACK
    return tuple(
        RubricChoice(
            id=pack.id,
            name=pack.name,
            version=pack.version,
            description=pack.description,
            criteria=len(pack.criteria),
            selected=pack.id == chosen,
        )
        for pack in packs.values()
    )


def active_runs(store: RunStore, runner: BackgroundRunner | None) -> list[Run]:
    """Runs executing now: this process's tasks, then anything else the store records as running.

    A run the background runner holds is listed even in the instant before its status is written,
    because the task exists before ``_execute`` marks it running.
    """
    found: list[Run] = []
    seen: set[str] = set()
    for run_id in runner.active_run_ids if runner is not None else ():
        run = store.get_run(run_id)
        if run is not None:
            found.append(run)
            seen.add(run.id)
    found.extend(run for run in store.running_runs() if run.id not in seen)
    return found


def blockers(
    *,
    store: RunStore,
    runner: BackgroundRunner | None,
    credentials: CredentialStore,
    preferences: PreferenceStore,
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
    for run in active_runs(store, runner):
        found.append(running_blocker(run))
    return found


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
    "rubric_choices",
    "running_blocker",
    "short_id",
]
