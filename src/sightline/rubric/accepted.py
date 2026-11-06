"""Each person's copy of a pack: accepted bindings, their history, and resolving a pack id.

Part one (:mod:`sightline.rubric.proposals`) proposes bindings and prints a mapping file. Part two
puts the proposals on a screen (item 22), and **accepting applies to a copy of the pack under the
data root** (decided 2026-10-30): the shipped YAML in the repository never changes. **Each person
has their own copy** (roadmap item 48, decided 2026-11-02): one person's accepted bindings never
change another person's runs. The record, beside the pack id::

    <data root>/rubrics/<pack id>/
        states/<hex>.json         every accepted state, anybody's, immutable, named by its digest
        history.json              local mode's one user: every acceptance and revert, in order
        proposals/<set id>.yaml   local mode's one user: every proposal set the screen made
        people/<key>/owner.json   a signed-in person: the subject id ``key`` is a digest of
        people/<key>/history.json   their history, as above
        people/<key>/proposals/     their proposal sets, as above

**Whose copy is named by the subject id**, the signed-in person's stable id (item 44), never the
display name; ``key`` is a digest of it (:func:`owner_key`), as their Canvas token's name is, so no
identity-provider id becomes a file name. ``owner.json`` holds the subject and the latest display
name, so an admin's read-only view can say whose history it is. **Local mode's one user** (item 45)
owns the record at the pack's own level, which is where every acceptance made before item 48 was
kept, so a local install reads exactly as it did and nothing was moved.

**States are not anybody's.** They are named by digest alone and kept once per pack, so a digest a
run recorded resolves whoever's history it came from, after any later acceptance or revert by
anybody, and moving a person's history could never orphan one.

**A state is the whole of a pack's bindings**, row by row, with the pack it applies to and the
fingerprint of the shipped pack it was made over. Its digest is a sha256 over exactly that, so the
same bindings always have the same name and a state file cannot be edited without its name ceasing
to match (:meth:`BindingStore.state` checks). States are written once and never removed: a run
records the digest it ran with, and that digest must still resolve when its report is exported a
month and three acceptances later.

**The history is a stack with a record.** An acceptance moves the pack from the state before it to
a new one; a revert undoes the latest acceptance still standing and moves the pack back to the
state before that acceptance. Both are appended, with who and when, and nothing is ever taken out:
a reverted acceptance is still in the history, marked by the revert that undid it. The state in
force is the ``after`` of the last entry, or the shipped bindings when there is none.

**One function answers "which pack does this run use".** :func:`resolve_pack` takes a pack id and
the run's starter, and returns the shipped pack with the starter's accepted bindings in force
applied, and the digest that names them — or, given a digest a run recorded, the pack with exactly
those bindings, whoever accepted them. The New run screen,
``python -m sightline.batch``, :mod:`sightline.batch.service` and :mod:`sightline.report.loader`
all resolve through it. A run with no digest — every run recorded before this item, and every run
over a pack nobody accepted bindings for — reads the shipped pack, which is what it ran with.

**A state made over a pack this build no longer ships is not applied.** If the shipped pack's
rows, tiers or bindings change under an accepted state, applying the state would put bindings
somebody accepted for one instrument onto another. The shipped pack is used instead, and the
screen says why and to propose again. A run that recorded such a state still renders with it.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..detect.base import Tier
from ..paths import DataPaths
from .coverage import PackCoverage
from .mapping import PackMapping
from .mapping import load as load_mapping
from .pack import RubricPack
from .proposals import (
    Acceptance,
    CoverageDelta,
    ProposalError,
    ProposalSet,
    coverage_delta,
    fingerprint,
    loads,
    mapping_from_pack,
)
from .shipped import packs_dir, shipped_pack

#: Where under the data root the app keeps its copy of each pack's bindings.
ROOT = "rubrics"

#: The format version of a state file and of the history. Bumped when a field changes meaning.
FILE_VERSION = 1

HISTORY_FILENAME = "history.json"

#: Where a signed-in person's history and proposal sets are kept, under the pack's directory.
PEOPLE = "people"
OWNER_FILENAME = "owner.json"

#: A person's directory name: :func:`owner_key` of their subject id.
OWNER_KEY = re.compile(r"^[0-9a-f]{48}$")

#: A proposal set's id: the time it was saved and a few random characters, so ids sort by time.
SET_ID = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")

#: A state's file name: the hex of its digest.
STATE_HEX = re.compile(r"^[0-9a-f]{64}$")

ACCEPTED = "accepted"
REVERTED = "reverted"


class BindingsError(ValueError):
    """A state, a history or an act on them that cannot be used as given. Says why."""


class StaleError(BindingsError):
    """The bindings in force changed after the page that asked for this was rendered."""


def _now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def owner_key(subject: str) -> str:
    """The directory a signed-in person's bindings are kept in: a digest of their subject id.

    The same construction as their Canvas token's name (:func:`sightline.credentials.
    user_secret_name`), so the identity provider's id is never a path.
    """
    if not subject:
        raise BindingsError("a person's bindings are named by their subject id, and none was given")
    return hashlib.blake2b(subject.encode("utf-8"), digest_size=24).hexdigest()


# ---------------------------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class BindingState:
    """Every row's bindings for one pack, and the shipped pack they were made over."""

    pack_id: str
    pack_version: str
    #: :func:`sightline.rubric.proposals.fingerprint` of the shipped pack.
    base: str
    #: Row code -> bound tags, for every row of the pack, in the pack's order.
    bindings: Mapping[str, tuple[str, ...]]

    def material(self) -> dict[str, Any]:
        return {
            "state_file": FILE_VERSION,
            "pack": {"id": self.pack_id, "version": self.pack_version, "base": self.base},
            "bindings": {code: list(tags) for code, tags in self.bindings.items()},
        }

    @property
    def digest(self) -> str:
        text = json.dumps(self.material(), sort_keys=True, ensure_ascii=False)
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    def to_json(self) -> str:
        return json.dumps({**self.material(), "digest": self.digest}, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str, *, origin: str) -> BindingState:
        try:
            payload = json.loads(text)
            if payload.get("state_file") != FILE_VERSION:
                raise BindingsError(f"{origin}: not a state file of version {FILE_VERSION}")
            pack = payload["pack"]
            state = cls(
                pack_id=str(pack["id"]),
                pack_version=str(pack["version"]),
                base=str(pack["base"]),
                bindings={
                    str(code): tuple(str(tag) for tag in tags)
                    for code, tags in payload["bindings"].items()
                },
            )
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            if isinstance(exc, BindingsError):
                raise
            raise BindingsError(
                f"{origin}: a state file with a missing or malformed field"
            ) from exc
        if payload.get("digest") != state.digest:
            raise BindingsError(
                f"{origin}: the bindings in the file do not match the digest it is named by, so "
                "it has been edited. States are written once and never changed."
            )
        return state


def state_of(pack: RubricPack, *, shipped: RubricPack) -> BindingState:
    """The state ``pack`` is in: every row's bindings, over ``shipped``."""
    return BindingState(
        pack_id=pack.id,
        pack_version=pack.version,
        base=fingerprint(shipped),
        bindings={criterion.code: tuple(criterion.bindings) for criterion in pack.criteria},
    )


def apply(shipped: RubricPack, state: BindingState) -> RubricPack:
    """``shipped`` with every row's bindings taken from ``state``. Tiers and text are untouched."""
    if state.pack_id != shipped.id or state.pack_version != shipped.version:
        raise BindingsError(
            f"the bindings were accepted for {state.pack_id} version {state.pack_version}, not "
            f"{shipped.id} version {shipped.version}"
        )
    stray = [code for code in state.bindings if shipped.criterion(code) is None]
    if stray:
        raise BindingsError(
            f"the bindings name row(s) {', '.join(stray)}, which {shipped.id} does not hold"
        )
    return replace(
        shipped,
        criteria=tuple(
            replace(criterion, bindings=state.bindings.get(criterion.code, criterion.bindings))
            for criterion in shipped.criteria
        ),
    )


# ---------------------------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class DeltaRecord:
    """A :class:`~sightline.rubric.proposals.CoverageDelta` as it is kept: counts, never prose
    alone, so the statement can be checked against the rows it names."""

    statement: str
    #: ``(from bucket, to bucket, row codes)`` for every move.
    moved: tuple[tuple[str, str, tuple[str, ...]], ...]
    before: Mapping[str, int]
    after: Mapping[str, int]

    @classmethod
    def of(cls, delta: CoverageDelta) -> DeltaRecord:
        return cls(
            statement=delta.statement(),
            moved=tuple((start, end, codes) for (start, end), codes in delta.moved.items()),
            before=dict(_counts(delta.before)),
            after=dict(_counts(delta.after)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "moved": [{"from": a, "to": b, "rows": list(codes)} for a, b, codes in self.moved],
            "before": dict(self.before),
            "after": dict(self.after),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> DeltaRecord:
        return cls(
            statement=str(payload["statement"]),
            moved=tuple(
                (str(item["from"]), str(item["to"]), tuple(str(code) for code in item["rows"]))
                for item in payload["moved"]
            ),
            before={str(k): int(v) for k, v in payload["before"].items()},
            after={str(k): int(v) for k, v in payload["after"].items()},
        )


def _counts(coverage: PackCoverage) -> Mapping[str, int]:
    tally = coverage.counts
    return {
        "deterministic": tally["deterministic"],
        "evidence_only": tally["evidence_only"],
        "not_yet_implemented": tally["not_yet_implemented"],
        "not_determinable": tally["not_determinable"],
    }


@dataclass(frozen=True, kw_only=True, slots=True)
class Entry:
    """One acceptance or one revert, as recorded. Never changed once written."""

    number: int
    kind: str
    at: str
    reviewer: str
    #: The digest in force before and after; ``None`` is the shipped bindings.
    before: str | None
    after: str | None
    delta: DeltaRecord
    #: Acceptances only: the proposal set, the proposal numbers accepted (conflicts among them
    #: listed again in ``conflicts``) and the ``(row, tag)`` each one added.
    proposal_set: str | None = None
    accepted: tuple[int, ...] = ()
    conflicts: tuple[int, ...] = ()
    added: tuple[tuple[str, str], ...] = ()
    #: Reverts only: the number of the acceptance this one undid.
    reverts: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "kind": self.kind,
            "at": self.at,
            "reviewer": self.reviewer,
            "before": self.before,
            "after": self.after,
            "delta": self.delta.to_dict(),
            "proposal_set": self.proposal_set,
            "accepted": list(self.accepted),
            "conflicts": list(self.conflicts),
            "added": [[code, tag] for code, tag in self.added],
            "reverts": self.reverts,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Entry:
        return cls(
            number=int(payload["number"]),
            kind=str(payload["kind"]),
            at=str(payload["at"]),
            reviewer=str(payload["reviewer"]),
            before=payload["before"],
            after=payload["after"],
            delta=DeltaRecord.from_dict(payload["delta"]),
            proposal_set=payload.get("proposal_set"),
            accepted=tuple(int(n) for n in payload.get("accepted", ())),
            conflicts=tuple(int(n) for n in payload.get("conflicts", ())),
            added=tuple((str(code), str(tag)) for code, tag in payload.get("added", ())),
            reverts=payload.get("reverts"),
        )


def standing(history: Sequence[Entry]) -> tuple[Entry, ...]:
    """The acceptances no revert has undone, oldest first. The last one is what a revert undoes."""
    stack: list[Entry] = []
    for entry in history:
        if entry.kind == ACCEPTED:
            stack.append(entry)
        elif entry.kind == REVERTED and stack and stack[-1].number == entry.reverts:
            stack.pop()
        else:
            raise BindingsError(
                f"history entry {entry.number} is not an acceptance or a revert of the latest "
                "standing acceptance, so the history has been edited"
            )
    return tuple(stack)


def reverted_by(history: Sequence[Entry]) -> Mapping[int, Entry]:
    """Acceptance number -> the revert that undid it."""
    return {entry.reverts: entry for entry in history if entry.reverts is not None}


# ---------------------------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class SavedSet:
    """A proposal set the screen made, and the id it is kept under."""

    id: str
    proposals: ProposalSet


@dataclass(frozen=True, kw_only=True, slots=True)
class Owner:
    """Whose bindings: a signed-in person by subject id, or local mode's one user (``None``)."""

    subject: str | None
    #: The display name last recorded with the subject; for local mode's user, ``None``.
    name: str | None = None

    @property
    def key(self) -> str | None:
        return None if self.subject is None else owner_key(self.subject)


class BindingStore:
    """One person's proposal sets and history, and every accepted state, under the data root.

    ``owner`` is the subject id of the person whose sets and history are read and appended to;
    ``None`` is local mode's one user. ``name`` is that person's display name, recorded beside the
    subject so an admin's view can say whose history it is. States are shared by digest, so
    :meth:`state` answers for any owner's.
    """

    def __init__(
        self, paths: DataPaths, owner: str | None = None, *, name: str | None = None
    ) -> None:
        self.paths = paths
        self.owner = Owner(subject=owner, name=name)

    def _own(self, pack_id: str, *parts: str) -> tuple[str, ...]:
        key = self.owner.key
        if key is None:
            return (ROOT, pack_id, *parts)
        return (ROOT, pack_id, PEOPLE, key, *parts)

    def _dir(self, pack_id: str, *parts: str) -> Path:
        return self.paths.resolve(*self._own(pack_id, *parts))

    def _states(self, pack_id: str, *parts: str) -> Path:
        return self.paths.resolve(ROOT, pack_id, "states", *parts)

    def _claim(self, pack_id: str) -> None:
        """Record whose directory this is, before anything is written into it."""
        if self.owner.subject is None:
            return
        payload = {
            "owner_file": FILE_VERSION,
            "subject": self.owner.subject,
            "name": self.owner.name or self.owner.subject,
        }
        path = self._dir(pack_id, OWNER_FILENAME)
        if path.is_file():
            kept = _read_owner(path)
            if kept.subject != self.owner.subject:
                raise BindingsError(
                    f"{pack_id}: a person's bindings directory names another subject, so it has "
                    "been edited"
                )
            if self.owner.name is None or kept.name == payload["name"]:
                return
        self.paths.save_text(
            *self._own(pack_id, OWNER_FILENAME),
            text=json.dumps(payload, indent=2, ensure_ascii=False),
        )

    # -- proposal sets --------------------------------------------------------------------------

    def save_proposals(self, proposals: ProposalSet, *, clock: Callable[[], str] = _now) -> str:
        stamp = dt.datetime.fromisoformat(clock()).astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        set_id = f"{stamp}-{secrets.token_hex(3)}"
        self._claim(proposals.pack_id)
        self.paths.save_text(
            *self._own(proposals.pack_id, "proposals", f"{set_id}.yaml"), text=proposals.to_yaml()
        )
        return set_id

    def proposal_set(self, pack_id: str, set_id: str) -> SavedSet | None:
        if not SET_ID.match(set_id):
            return None
        path = self._dir(pack_id, "proposals", f"{set_id}.yaml")
        if not path.is_file():
            return None
        proposals = loads(path.read_text(encoding="utf-8"), origin=f"proposal set {set_id}")
        if proposals.pack_id != pack_id:
            raise BindingsError(f"proposal set {set_id} was made for {proposals.pack_id}")
        return SavedSet(id=set_id, proposals=proposals)

    def proposal_sets(self, pack_id: str) -> tuple[SavedSet, ...]:
        """Every set kept for the pack, newest first."""
        directory = self._dir(pack_id, "proposals")
        if not directory.is_dir():
            return ()
        found: list[SavedSet] = []
        for path in sorted(directory.glob("*.yaml"), reverse=True):
            saved = self.proposal_set(pack_id, path.stem)
            if saved is not None:
                found.append(saved)
        return tuple(found)

    # -- states ---------------------------------------------------------------------------------

    def save_state(self, state: BindingState) -> str:
        """Write ``state`` under its digest unless it is there already. Returns the digest."""
        digest = state.digest
        name = f"{digest.removeprefix('sha256:')}.json"
        path = self._states(state.pack_id, name)
        if path.is_file():
            self.state(state.pack_id, digest)  # an existing file must still be that state
            return digest
        self.paths.save_text(ROOT, state.pack_id, "states", name, text=state.to_json())
        return digest

    def state(self, pack_id: str, digest: str) -> BindingState:
        """The accepted state named ``digest``, whoever accepted it."""
        hexdigest = digest.removeprefix("sha256:")
        if not digest.startswith("sha256:") or not STATE_HEX.match(hexdigest):
            raise BindingsError(f"{digest!r} is not the digest of an accepted state")
        path = self._states(pack_id, f"{hexdigest}.json")
        if not path.is_file():
            raise BindingsError(
                f"no accepted state {digest} is kept for {pack_id} under this data root"
            )
        state = BindingState.from_json(path.read_text(encoding="utf-8"), origin=path.name)
        if state.pack_id != pack_id:
            raise BindingsError(f"state {digest} belongs to {state.pack_id}, not {pack_id}")
        return state

    # -- history --------------------------------------------------------------------------------

    def history(self, pack_id: str) -> tuple[Entry, ...]:
        path = self._dir(pack_id, HISTORY_FILENAME)
        if not path.is_file():
            return ()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("history_file") != FILE_VERSION:
                raise BindingsError(f"{pack_id}: not a history file of version {FILE_VERSION}")
            entries = tuple(Entry.from_dict(item) for item in payload["entries"])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            if isinstance(exc, BindingsError):
                raise
            raise BindingsError(f"{pack_id}: the bindings history cannot be read") from exc
        standing(entries)  # refuses a history that does not replay
        return entries

    def current(self, pack_id: str) -> str | None:
        """The digest in force: the last entry's ``after``, or ``None`` for the shipped bindings."""
        entries = self.history(pack_id)
        return entries[-1].after if entries else None

    def _append(self, pack_id: str, entry: Entry, history: Sequence[Entry]) -> Entry:
        payload = {
            "history_file": FILE_VERSION,
            "pack": pack_id,
            "entries": [item.to_dict() for item in (*history, entry)],
        }
        self._claim(pack_id)
        self.paths.save_text(
            *self._own(pack_id, HISTORY_FILENAME),
            text=json.dumps(payload, indent=2, ensure_ascii=False),
        )
        return entry

    def record_acceptance(
        self,
        saved: SavedSet,
        acceptance: Acceptance,
        *,
        shipped: RubricPack,
        before: str | None,
        reviewer: str,
        clock: Callable[[], str] = _now,
    ) -> Entry:
        """Keep the state ``acceptance`` produced and append the act that produced it.

        ``before`` is the digest the person was shown as in force. If the bindings in force have
        moved since, nothing is written: :class:`StaleError`.
        """
        pack_id = shipped.id
        history = self.history(pack_id)
        in_force = resolve_pack(
            pack_id, self.paths, starter=self.owner.subject, shipped=shipped
        ).digest
        if in_force != before:
            raise StaleError(
                "the bindings in force changed after this page was shown, so nothing was "
                "accepted. Open the proposals again to see them against the bindings as they are."
            )
        after = self.save_state(state_of(acceptance.pack, shipped=shipped))
        conflicts = {proposal.id for proposal in acceptance.accepted if proposal.conflict}
        entry = Entry(
            number=len(history) + 1,
            kind=ACCEPTED,
            at=clock(),
            reviewer=reviewer,
            before=before,
            after=after,
            delta=DeltaRecord.of(acceptance.delta),
            proposal_set=saved.id,
            accepted=tuple(proposal.id for proposal in acceptance.accepted),
            conflicts=tuple(sorted(conflicts)),
            added=tuple((proposal.code, proposal.tag) for proposal in acceptance.accepted),
        )
        return self._append(pack_id, entry, history)

    def revert_latest(
        self,
        *,
        shipped: RubricPack,
        expected: int,
        reviewer: str,
        clock: Callable[[], str] = _now,
    ) -> Entry:
        """Undo the latest standing acceptance, which must be number ``expected``.

        The bindings go back to the state before it. The acceptance stays in the history and the
        revert is appended after it.
        """
        pack_id = shipped.id
        history = self.history(pack_id)
        stack = standing(history)
        if not stack or stack[-1].number != expected:
            raise StaleError(
                "the acceptance this page offered to revert is no longer the latest standing "
                "one, so nothing was reverted. Open the rubric's page again to see the history "
                "as it is."
            )
        undone = stack[-1]
        current = history[-1].after if history else None
        before_pack = self.pack_at(shipped, current)
        after_pack = self.pack_at(shipped, undone.before)
        entry = Entry(
            number=len(history) + 1,
            kind=REVERTED,
            at=clock(),
            reviewer=reviewer,
            before=current,
            after=undone.before,
            delta=DeltaRecord.of(coverage_delta(before_pack, after_pack)),
            reverts=undone.number,
        )
        return self._append(pack_id, entry, history)

    def pack_at(self, shipped: RubricPack, digest: str | None) -> RubricPack:
        """``shipped`` with the bindings of state ``digest``; the shipped pack for ``None``."""
        if digest is None:
            return shipped
        return apply(shipped, self.state(shipped.id, digest))


def _read_owner(path: Path) -> Owner:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("owner_file") != FILE_VERSION:
            raise BindingsError(f"{path.name}: not an owner file of version {FILE_VERSION}")
        subject = str(payload["subject"])
        name = str(payload.get("name") or subject)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        if isinstance(exc, BindingsError):
            raise
        raise BindingsError(
            f"{path.name}: an owner file with a missing or malformed field"
        ) from exc
    if owner_key(subject) != path.parent.name:
        raise BindingsError(
            f"{path.parent.name}: the owner file names a subject this directory is not kept "
            "under, so it has been edited"
        )
    return Owner(subject=subject, name=name)


def owners(paths: DataPaths, pack_id: str) -> tuple[Owner, ...]:
    """Everybody who has bindings or proposals kept for ``pack_id``, for an admin's view.

    Local mode's one user first, when anything is kept at the pack's own level (everything
    accepted before item 48, and everything accepted in local mode since); then each signed-in
    person, by display name. A directory whose owner file does not match it is refused.
    """
    found: list[Owner] = []
    base = paths.resolve(ROOT, pack_id)
    if (base / HISTORY_FILENAME).is_file() or (base / "proposals").is_dir():
        found.append(Owner(subject=None))
    people = base / PEOPLE
    signed: list[Owner] = []
    if people.is_dir():
        for directory in sorted(people.iterdir()):
            if not directory.is_dir() or not OWNER_KEY.match(directory.name):
                continue
            path = directory / OWNER_FILENAME
            if path.is_file():
                signed.append(_read_owner(path))
    found.extend(sorted(signed, key=lambda owner: ((owner.name or "").casefold(), owner.key)))
    return tuple(found)


# ---------------------------------------------------------------------------------------------
# Resolving a pack id: the one function
# ---------------------------------------------------------------------------------------------


class _Current:
    def __repr__(self) -> str:
        return "CURRENT"


#: Pass as ``digest`` to ask for the bindings in force now, rather than a recorded state.
CURRENT: Any = _Current()


@dataclass(frozen=True, kw_only=True, slots=True)
class ResolvedPack:
    """A pack as a run uses it: the shipped pack with the bindings named by ``digest`` applied."""

    pack: RubricPack
    shipped: RubricPack
    #: The accepted state applied, or ``None`` for the shipped bindings.
    digest: str | None
    #: A state in force that was not applied, because the shipped pack changed under it.
    stale: str | None = None
    entries: tuple[Entry, ...] = field(default_factory=tuple)

    @property
    def accepted(self) -> Entry | None:
        """The standing acceptance that produced the bindings in force, if any."""
        stack = standing(self.entries)
        return stack[-1] if stack and self.digest is not None else None


def resolve_pack(
    pack_id: str,
    paths: DataPaths | None,
    *,
    digest: str | None = CURRENT,
    starter: str | None = None,
    packs_dir: Path | str | None = None,
    shipped: RubricPack | None = None,
) -> ResolvedPack:
    """The pack a run over ``pack_id`` uses, and the digest of the bindings applied to it.

    ``digest=CURRENT`` (the default) applies ``starter``'s bindings in force now — what a run they
    are starting records. ``starter`` is their subject id; ``None`` is local mode's one user. A
    digest a run recorded applies exactly that state, whoever accepted it and whatever has been
    accepted or reverted since. ``None`` is the shipped pack. Raises ``KeyError`` for a pack this
    build does not ship and :class:`BindingsError` for a recorded state that is not kept here.
    """
    if shipped is None:
        shipped = shipped_pack(pack_id, packs_dir)
    if paths is None:
        if digest is CURRENT or digest is None:
            return ResolvedPack(pack=shipped, shipped=shipped, digest=None)
        raise BindingsError(
            f"the run used the bindings accepted as {digest}, and no data root was given to "
            "read them from"
        )
    store = BindingStore(paths, starter)
    if digest is CURRENT:
        entries = store.history(pack_id)
        current = entries[-1].after if entries else None
        if current is None:
            return ResolvedPack(pack=shipped, shipped=shipped, digest=None, entries=entries)
        state = store.state(pack_id, current)
        if state.base != fingerprint(shipped):
            return ResolvedPack(
                pack=shipped, shipped=shipped, digest=None, stale=current, entries=entries
            )
        return ResolvedPack(
            pack=apply(shipped, state), shipped=shipped, digest=current, entries=entries
        )
    if digest is None:
        return ResolvedPack(pack=shipped, shipped=shipped, digest=None)
    return ResolvedPack(
        pack=apply(shipped, store.state(pack_id, digest)), shipped=shipped, digest=digest
    )


def resolve_named(
    pack_id: str, paths: DataPaths, digest: str, *, packs_dir: Path | str | None = None
) -> ResolvedPack:
    """A new run's pack with the accepted state ``digest`` applied, named by digest alone.

    For a run started on the command line with ``--bindings`` (roadmap item 48), which has no
    signed-in starter whose bindings in force could be meant. Any accepted state kept for the pack
    resolves, whoever accepted it. A state made over a pack this build no longer ships is refused
    rather than applied, as it is not applied to a run started on the screen.
    """
    shipped = shipped_pack(pack_id, packs_dir)
    state = BindingStore(paths).state(pack_id, digest)
    if state.base != fingerprint(shipped):
        raise BindingsError(
            f"the bindings {digest} were accepted over {pack_id} as an earlier build shipped it, "
            "so they are not applied to a new run"
        )
    return ResolvedPack(pack=apply(shipped, state), shipped=shipped, digest=digest)


def base_mapping(pack: RubricPack, *, shipped: RubricPack) -> PackMapping | None:
    """The mapping ``pack`` re-imports from, for :func:`~sightline.rubric.proposals.accept`.

    ``None`` where :func:`~sightline.rubric.proposals.mapping_from_pack` can build it. A pack
    imported with a declared join key does not record that column, so for one of those the mapping
    the shipped pack was built from (``rubrics/maps/<id>.yaml``, beside the packs) is read and the
    bindings in force are written into it; ``accept`` then checks it against the pack.
    """
    try:
        mapping_from_pack(pack)
        return None
    except ProposalError:
        pass
    path = packs_dir().parent / "maps" / f"{shipped.id}.yaml"
    if not path.is_file():
        raise BindingsError(
            f"{shipped.id} was imported with a declared join key, and the mapping it was built "
            f"from is not at {path.parent.name}/{path.name}, so no acceptance can be written for it"
        )
    mapping = load_mapping(path)
    criteria = dict(mapping.criteria)
    for criterion in pack.criteria:
        row = criteria.get(criterion.code)
        if row is not None:
            criteria[criterion.code] = replace(row, bindings=tuple(criterion.bindings))
    return replace(mapping, criteria=criteria)


def asked_rows(pack: RubricPack) -> int:
    """How many rows :func:`~sightline.rubric.proposals.propose` sends to the model: all but D."""
    return sum(1 for criterion in pack.criteria if criterion.tier is not Tier.D)


__all__ = [
    "ACCEPTED",
    "CURRENT",
    "REVERTED",
    "ROOT",
    "BindingState",
    "BindingStore",
    "BindingsError",
    "DeltaRecord",
    "Entry",
    "Owner",
    "ResolvedPack",
    "SavedSet",
    "StaleError",
    "apply",
    "asked_rows",
    "base_mapping",
    "owner_key",
    "owners",
    "resolve_named",
    "resolve_pack",
    "reverted_by",
    "standing",
    "state_of",
]
