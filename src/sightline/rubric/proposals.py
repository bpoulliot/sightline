"""Model-proposed bindings, confirmed by a person — roadmap item 22, part one.

A pack's rows bind to capability tags, and an imported pack has none: no rubric workbook has a
column naming a detector. That is a finished, usable pack (every row routes to extraction), so a
binding is an accelerator and never a prerequisite. This module is the accelerator. The model is
shown the capability catalogue and one criterion's text at a time, and it **proposes** tags. A
person decides. Once per rubric pack, never per course, and no course is involved at all.

WHAT THE MODEL MAY RETURN, AND WHAT IS DONE WITH IT
---------------------------------------------------

One call per criterion, schema-constrained through the host's ``format`` field exactly as the
extraction layer does (:mod:`sightline.extract.schema`), and validated here whether or not the host
honoured the constraint. The response carries a list of ``{tag, span}`` objects and nothing else:

* ``tag`` must be in this build's vocabulary. **A tag that is not is dropped and reported, never
  written** — the asymmetric vocabulary rule of item 04 makes a pack naming an unknown tag a
  warning, and the proposer is the last place that could stop one being authored.
* ``span`` is the WHY, and it is a **checkable artifact rather than model prose** (SPEC §01): the
  words of the criterion the proposal rests on, verified against the criterion text by
  :func:`sightline.extract.verify.verify`, the same string match every quote in a review passes.
  What is kept is the criterion's own text at the match. An empty span is recorded as *no
  supporting span*; a span that is not in the criterion text is **discarded** and recorded as that,
  and its words are never shown. There is no field for a rationale, so there is no model-written
  explanation to show and none is.

**No confidence signal of any kind** (decided 2026-09-08). No score, rank, probability, strength
or ordering by any of them — it would become a de facto bulk-accept threshold. Proposals are listed
in the instrument's own row order and, within a row, alphabetically by tag, so not even the order a
model returned them in survives as an implicit ranking. ``tests/test_rubric_proposals.py`` scans
every key and every line this module produces for confidence-shaped words.

NOTHING HAPPENS BY INERTIA
--------------------------

Making proposals changes nothing: :func:`propose` returns a :class:`ProposalSet`, a recorded
artifact of one run. **The host is not deterministic at temperature 0** (item 10 measured it), so a
second run can return a different set; the set records the model, the host version and the time,
and it says so in the file. Every proposal in it starts unaccepted. :func:`accept` takes the set,
the pack it was made against, and an affirmative act — proposals named by number, or an explicit
accept-all — and nothing else accepts anything.

**A pack's existing bindings are never replaced.** Accepting only ever adds a tag to a row. A
proposal on a row that is already bound to other tags is a **conflict**: accepting it changes a
binding set somebody authored, so it is not swept up by accept-all and needs its own act,
naming it. A proposal of a tag the row already carries is not a proposal at all; it is reported as
already bound.

WHAT ACCEPTANCE PRODUCES
------------------------

A **mapping file** (:mod:`sightline.rubric.mapping`), the format ``python -m sightline.rubric
import --map`` already applies, so an accepted set becomes a pack through the existing, tested
path. The mapping carries everything the pack already had that a mapping can say — its identity,
section flags, every row's tier, note and bindings — plus the accepted tags; importing the same
workbook with it gives the same pack with those tags added. A pack imported with a declared join
key does not record which *column* that was, so for such a pack the mapping it was built from has
to be supplied, and it is checked against the pack before it is used.

With the mapping comes the **coverage delta**: :func:`sightline.rubric.coverage.pack_coverage` run
over the pack before and after, and the rows that moved between its buckets counted — "3 row(s)
moved from evidence-only to deterministic; 15 remain evidence-only." That is the assurance, in the
artifact rather than in the click, and it is still true when somebody reopens the pack later.

HOW A BINDING CHANGES THE REVIEW, PER TIER
------------------------------------------

A binding changes a row's *handling*, never its tier or its tier's rule, and each proposal says
which of these applies (:func:`consequence`):

* **Tier A** — once bound to a capability a detector implements, the row is answered by count, it
  stops being sent to the model for quotes, and a clean run with every binding examined reads
  Sufficiently Present.
* **Tier B** — the detectors' observations are counted and banded into the row's rating beside the
  quotes it already receives; a clean run still reads No Response, because a Tier B binding bears
  on the criterion without settling it (item 28).
* **Tier C** — the row stays at No Response whatever it binds to. The counts become context for
  the reviewer's question.
* **Tier D** — the row reads Not Applicable bound or not, so a binding changes nothing, and the
  proposer does not ask about Tier D rows at all: each is recorded as not asked, with that reason.
* A tag **no detector implements yet** changes nothing computed in this build, and on a Tier A row
  it keeps the row from reading Sufficiently Present until one does.

Unbinding reverses each of these, which is also stated.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import yaml

from ..capabilities import Vocabulary, vocabulary
from ..detect.base import Tier
from ..extract.runner import thinking_for
from ..extract.schema import SchemaViolation, load_json
from ..extract.verify import verify
from ..llm.client import ModelCard, OllamaClient, Thinking
from ..llm.errors import (
    ModelHostError,
    ModelHostNotConfiguredError,
    ModelHostNotOllamaError,
    ModelHostOutOfMemoryError,
    ModelHostUnreachableError,
    ModelNotFoundError,
    ModelNotUsableError,
    ModelThinkingUnsupportedError,
)
from ..logging import get_logger
from .coverage import PackCoverage, RowCoverage, pack_coverage
from .mapping import PackMapping, RowMapping, SectionMapping
from .pack import Criterion, RubricPack
from .project import TagIndex, tag_descriptions
from .states import Coverage

log = get_logger(__name__)

#: The format version of a proposal file. Bumped when a field changes meaning.
FILE_VERSION = 1

#: Said in every proposal file and at the top of every review, because it is the fact that makes a
#: proposal set a record rather than a property of the rubric.
NONDETERMINISM_NOTE = (
    "The model host is not deterministic at temperature 0, so running the proposer again can "
    "return a different set. This file is the record of one run, not a property of the rubric."
)

#: Failures that end the run rather than the row: each would repeat identically on every row.
FATAL = (
    ModelHostNotConfiguredError,
    ModelHostNotOllamaError,
    ModelHostOutOfMemoryError,
    ModelHostUnreachableError,
    ModelNotFoundError,
    ModelNotUsableError,
    ModelThinkingUnsupportedError,
)

#: Why a Tier D row is not sent to the model. Such a row reads Not Applicable bound or not, so a
#: proposal for it could change nothing in the review and would only cost a call.
NOT_ASKED_TIER_D = (
    "Tier D, not determinable from course artifacts. It reads Not Applicable whether or not it is "
    "bound, so a binding would change nothing in the review and the model was not asked."
)

SYSTEM_PROMPT = (
    "You are given one criterion from a course-review rubric and a fixed catalogue of capability "
    "tags. Each tag names something a parser can establish about a course's content, with what "
    "the parser reports for it.\n\n"
    "List the tags whose concept this criterion is about: the tags whose findings a reviewer "
    "would read when answering this criterion.\n"
    "For each tag, copy into 'span' the words of the criterion that the tag rests on, verbatim "
    "and contiguous, exactly as they are written. If no words of the criterion support it, "
    "return an empty span.\n"
    "Use only tags from the catalogue. Return an empty list if no tag applies; an empty list is a "
    "valid and expected answer.\n"
    "Do not explain, do not rate the criterion, and do not write prose."
)


class ProposalError(ValueError):
    """A proposal file, a pack or an act of acceptance that cannot be used as given."""


# ---------------------------------------------------------------------------------------------
# What is sent
# ---------------------------------------------------------------------------------------------


def response_schema(words: Vocabulary | None = None) -> dict[str, Any]:
    """The JSON schema sent to the host in ``format``. Tags are enumerated from the vocabulary.

    The enumeration is a *constraint the host promises to apply*, not one this module relies on:
    :func:`parse_proposals` refuses a malformed response and :func:`propose` drops an unknown tag
    whatever the host did.
    """
    tags = list((words or vocabulary()).tags)
    return {
        "type": "object",
        "properties": {
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string", "enum": tags},
                        "span": {"type": "string"},
                    },
                    "required": ["tag", "span"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["proposals"],
        "additionalProperties": False,
    }


def catalogue(
    words: Vocabulary | None = None, described: Mapping[str, Sequence[str]] | None = None
) -> str:
    """The capability catalogue as the model reads it — what ``python -m sightline.rubric tags``
    prints, without the detector ids, which are Sightline's numbering and mean nothing to a model.
    """
    words = words or vocabulary()
    described = described if described is not None else tag_descriptions()
    lines: list[str] = []
    for capability in words.capabilities.values():
        lines.append(f"- {capability.tag}: {capability.summary}")
        for description in described.get(capability.tag, ()):
            lines.append(f"    reports: {description}")
    return "\n".join(lines)


def prompt_for(criterion: Criterion, catalogue_text: str) -> list[dict[str, str]]:
    """The messages for one criterion. The row code is not sent: it is the instrument's own
    numbering, and a code a model recognises from another rubric is an invitation to answer from
    memory rather than from the words in front of it."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Catalogue:\n{catalogue_text}\n\nCriterion:\n{criterion.text}",
        },
    ]


# ---------------------------------------------------------------------------------------------
# What comes back
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RawProposal:
    """One ``{tag, span}`` exactly as returned. Neither field is trusted until checked."""

    tag: str
    span: str


def parse_proposals(content: str) -> tuple[RawProposal, ...]:
    """The proposals in one response, or :class:`SchemaViolation`. Refused, never coerced.

    ``{"proposals": []}`` is a successful answer — the model found no tag for this criterion — and
    returns an empty tuple. A key beside ``proposals``, or beside ``tag`` and ``span``, is refused
    rather than dropped: a ``confidence`` or ``reason`` the model chose to add is the model doing
    something it was not asked to, and deleting the key would hide that it did.
    """
    payload = load_json(content)
    if not isinstance(payload, Mapping):
        raise SchemaViolation("expected an object with a 'proposals' key")
    extra = sorted(str(key) for key in payload if key != "proposals")
    if extra:
        raise SchemaViolation(f"carries {', '.join(repr(key) for key in extra)} beside 'proposals'")
    entries = payload.get("proposals")
    if entries is None:
        raise SchemaViolation("has no 'proposals' key")
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise SchemaViolation("'proposals' must be a list", path="response.proposals")
    out: list[RawProposal] = []
    for index, entry in enumerate(entries):
        path = f"response.proposals[{index}]"
        if not isinstance(entry, Mapping):
            raise SchemaViolation("expected an object", path=path)
        if sorted(entry) != ["span", "tag"]:
            raise SchemaViolation(
                f"must carry exactly 'tag' and 'span', not {sorted(map(str, entry))}", path=path
            )
        tag, span = entry["tag"], entry["span"]
        if not isinstance(tag, str) or not tag.strip():
            raise SchemaViolation("'tag' must be a non-empty string", path=path)
        if not isinstance(span, str):
            raise SchemaViolation("'span' must be a string", path=path)
        out.append(RawProposal(tag=tag.strip(), span=span))
    return tuple(out)


# ---------------------------------------------------------------------------------------------
# The proposal set
# ---------------------------------------------------------------------------------------------

#: Span states. A string rather than an enum in the file, so the file reads without this module.
SPAN_VERIFIED = "verified"
SPAN_ABSENT = "absent"
SPAN_DISCARDED = "discarded"
SPAN_STATES = (SPAN_VERIFIED, SPAN_ABSENT, SPAN_DISCARDED)


@dataclass(frozen=True, kw_only=True, slots=True)
class Proposal:
    """One proposed binding: a tag, for one row, and everything the review owes the person.

    WHAT is ``criterion`` beside ``tag`` and ``summary``/``looks_for``; WHY is ``span`` (or its
    stated absence); HOW is ``changes``. There is no other field, and there is deliberately none
    that could carry a strength.
    """

    id: int
    code: str
    tier: Tier
    criterion: str
    tag: str
    #: The capability's own one-line summary, from the vocabulary.
    summary: str
    #: What each detector behind the tag says it reports. Empty when no detector implements it yet.
    looks_for: tuple[str, ...]
    #: The criterion's own words at the verified match; ``None`` unless ``span_state`` is verified.
    span: str | None
    span_state: str
    #: Tags the row is bound to already. Non-empty means accepting this changes an authored set.
    bound: tuple[str, ...]
    #: How accepting this changes the review. Computed (:func:`consequence`), never generated.
    changes: str

    @property
    def conflict(self) -> bool:
        return bool(self.bound)

    def why(self) -> str:
        if self.span_state == SPAN_VERIFIED:
            return f"“{self.span}” (verbatim from the criterion text)"
        if self.span_state == SPAN_DISCARDED:
            return (
                "no supporting span. The model returned words that are not in the criterion "
                "text; they were discarded and are not shown."
            )
        return "no supporting span. The model named no words of the criterion."

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "tier": self.tier.value,
            "criterion": self.criterion,
            "tag": self.tag,
            "summary": self.summary,
            "looks_for": list(self.looks_for),
            "span": self.span,
            "span_state": self.span_state,
            "bound": list(self.bound),
            "changes": self.changes,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class RowOutcome:
    """What one criterion's call came to, including everything that is not a proposal."""

    code: str
    tier: Tier
    criterion: str
    bound: tuple[str, ...]
    #: Ids of the proposals made for this row.
    proposals: tuple[int, ...] = ()
    #: Tags proposed that the row already carries. Nothing to accept.
    already_bound: tuple[str, ...] = ()
    #: Tags not in this build's vocabulary. Dropped, reported, never written.
    unknown: tuple[str, ...] = ()
    #: Why the row could not be asked, when it could not. Distinct from "none proposed".
    failure: str | None = None
    #: Why the row was deliberately not asked. Tier D rows only: a binding changes nothing there.
    not_asked: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "tier": self.tier.value,
            "criterion": self.criterion,
            "bound": list(self.bound),
            "proposals": list(self.proposals),
            "already_bound": list(self.already_bound),
            "unknown": list(self.unknown),
            "failure": self.failure,
            "not_asked": self.not_asked,
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class ProposalSet:
    """Every proposal from one run over one pack: a recorded artifact, not a property of it."""

    pack_id: str
    pack_name: str
    pack_version: str
    #: :func:`fingerprint` of the pack the proposals were made against. Checked at acceptance.
    fingerprint: str
    model: str
    host_version: str | None
    thinking_pinned: bool | None
    created: str
    calls: int
    rows: tuple[RowOutcome, ...]
    proposals: tuple[Proposal, ...]

    def get(self, proposal_id: int) -> Proposal | None:
        for proposal in self.proposals:
            if proposal.id == proposal_id:
                return proposal
        return None

    @property
    def conflicts(self) -> tuple[Proposal, ...]:
        return tuple(proposal for proposal in self.proposals if proposal.conflict)

    @property
    def failures(self) -> tuple[RowOutcome, ...]:
        return tuple(row for row in self.rows if row.failure is not None)

    def statement(self) -> str:
        """Counts, and nothing that could be read as a measure of the proposals themselves."""
        unknown = sum(len(row.unknown) for row in self.rows)
        none = sum(
            1
            for row in self.rows
            if row.failure is None and row.not_asked is None and not row.proposals
        )
        skipped = sum(1 for row in self.rows if row.not_asked is not None)
        verified = sum(1 for p in self.proposals if p.span_state == SPAN_VERIFIED)
        parts = [
            f"{len(self.proposals)} proposal(s) over {len(self.rows)} row(s)",
            f"{none} row(s) with none",
            f"{verified} with a supporting span verified in the criterion text",
        ]
        if self.conflicts:
            parts.append(f"{len(self.conflicts)} on a row already bound to other tags")
        if skipped:
            parts.append(f"{skipped} Tier D row(s) not asked")
        if unknown:
            parts.append(f"{unknown} unknown tag(s) dropped")
        if self.failures:
            parts.append(f"{len(self.failures)} row(s) could not be asked")
        return f"{self.pack_name} {self.pack_version}: " + ", ".join(parts) + "."

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_file": FILE_VERSION,
            "note": NONDETERMINISM_NOTE,
            "pack": {
                "id": self.pack_id,
                "name": self.pack_name,
                "version": self.pack_version,
                "fingerprint": self.fingerprint,
            },
            "made_by": {
                "model": self.model,
                "host_version": self.host_version,
                "thinking_pinned": self.thinking_pinned,
                "created": self.created,
                "calls": self.calls,
            },
            "statement": self.statement(),
            "accepted": "none — every proposal starts unaccepted",
            "rows": [row.to_dict() for row in self.rows],
            "proposals": [proposal.to_dict() for proposal in self.proposals],
        }

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True, width=100)


def loads(text: str, *, origin: str = "<proposals>") -> ProposalSet:
    """Read a proposal file back. Strict: a file that has been edited into another shape is refused
    rather than half-read, because acceptance acts on what it says."""
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProposalError(f"{origin}: not valid YAML: {exc}") from exc
    if not isinstance(payload, Mapping) or payload.get("proposal_file") != FILE_VERSION:
        raise ProposalError(
            f"{origin}: not a proposal file of version {FILE_VERSION}. Make one with "
            "`python -m sightline.rubric propose`."
        )
    try:
        pack = payload["pack"]
        made = payload["made_by"]
        rows = tuple(
            RowOutcome(
                code=str(row["code"]),
                tier=Tier(row["tier"]),
                criterion=str(row["criterion"]),
                bound=tuple(row["bound"]),
                proposals=tuple(int(item) for item in row["proposals"]),
                already_bound=tuple(row["already_bound"]),
                unknown=tuple(row["unknown"]),
                failure=row["failure"],
                not_asked=row["not_asked"],
            )
            for row in payload["rows"]
        )
        proposals = tuple(
            Proposal(
                id=int(item["id"]),
                code=str(item["code"]),
                tier=Tier(item["tier"]),
                criterion=str(item["criterion"]),
                tag=str(item["tag"]),
                summary=str(item["summary"]),
                looks_for=tuple(item["looks_for"]),
                span=item["span"],
                span_state=str(item["span_state"]),
                bound=tuple(item["bound"]),
                changes=str(item["changes"]),
            )
            for item in payload["proposals"]
        )
        result = ProposalSet(
            pack_id=str(pack["id"]),
            pack_name=str(pack["name"]),
            pack_version=str(pack["version"]),
            fingerprint=str(pack["fingerprint"]),
            model=str(made["model"]),
            host_version=made["host_version"],
            thinking_pinned=made["thinking_pinned"],
            created=str(made["created"]),
            calls=int(made["calls"]),
            rows=rows,
            proposals=proposals,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProposalError(
            f"{origin}: a proposal file with a missing or malformed field: {exc}"
        ) from exc
    ids = [proposal.id for proposal in result.proposals]
    if len(ids) != len(set(ids)):
        raise ProposalError(f"{origin}: proposal numbers repeat, so naming one is ambiguous")
    bad = [p.id for p in result.proposals if p.span_state not in SPAN_STATES]
    if bad:
        raise ProposalError(f"{origin}: proposal(s) {bad} carry an unknown span_state")
    return result


def fingerprint(pack: RubricPack) -> str:
    """What a proposal set was made against: the pack's identity and every row's code, text, tier
    and bindings. Accepting a set against a pack that has changed under it is refused."""
    material = {
        "id": pack.id,
        "version": pack.version,
        "rows": [
            [criterion.code, criterion.text, criterion.tier.value, list(criterion.bindings)]
            for criterion in pack.criteria
        ],
    }
    digest = hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


# ---------------------------------------------------------------------------------------------
# How a binding changes the review
# ---------------------------------------------------------------------------------------------


def consequence(
    tier: Tier, bound: Sequence[str], tag: str, *, index: TagIndex | None = None
) -> str:
    """How binding ``tag`` to a row of ``tier`` already bound to ``bound`` changes its review.

    Computed from the tier, the row's coverage before and after, and the detectors behind the tag
    — the same three things the projection reads — so the sentence cannot say something the
    projection does not do.
    """
    index = index or TagIndex.build()
    detectors = index.detectors_by_tag.get(tag, ())
    by = ", ".join(detectors)
    before = _coverage_of(bound, index)
    after = _coverage_of((*bound, tag), index)

    if tier is Tier.D:
        return (
            "None. The row is Tier D, not determinable from course artifacts, and reads Not "
            "Applicable whether or not it is bound."
        )
    if not detectors:
        text = (
            f"Nothing computed, in this build: no detector implements {tag} yet, so the row "
            "gathers the same evidence as before."
        )
        if before is Coverage.NO_DETERMINISTIC_COVERAGE:
            text += " Its coverage reads bound to a capability no detector implements yet."
        if tier is Tier.A:
            text += (
                " On this Tier A row a bound tag nothing examines also keeps the row from reading "
                "Sufficiently Present until a detector for it exists."
            )
        return text + " Unbinding reverses this."
    if tier is Tier.C:
        return (
            f"The row stays at No Response whatever it binds to: it is Tier C, a judgement no "
            f"rating is pre-selected for. Binding adds the observations of {by} to the context "
            "gathered for the reviewer's question. Unbinding removes them."
        )
    moved = (
        "The row moves from evidence-only to deterministic"
        if after is Coverage.DETERMINISTIC and before is not Coverage.DETERMINISTIC
        else "The row already has deterministic coverage"
    )
    if tier is Tier.A:
        return (
            f"{moved}: it is answered by count from {by}, it is not sent to the model for "
            "quotes, and a clean run with every one of its bindings examined reads Sufficiently "
            f"Present. What {by} observe is added into its effort. Unbinding moves it back."
        )
    return (
        f"{moved}: the observations of {by} are counted and banded into its rating, beside "
        "the passages already located and quoted for it. A clean run still reads No Response, "
        "because on a Tier B row a binding bears on the criterion without settling it. "
        "Unbinding moves it back."
    )


def _coverage_of(tags: Iterable[str], index: TagIndex) -> Coverage:
    resolved = [tag for tag in tags if index.recognises(tag) and index.implements(tag)]
    pending = [tag for tag in tags if index.recognises(tag) and not index.implements(tag)]
    if resolved:
        return Coverage.DETERMINISTIC
    if pending:
        return Coverage.NOT_YET_IMPLEMENTED
    return Coverage.NO_DETERMINISTIC_COVERAGE


# ---------------------------------------------------------------------------------------------
# Making proposals
# ---------------------------------------------------------------------------------------------


def _now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


async def propose(
    pack: RubricPack,
    client: OllamaClient,
    model: ModelCard | str,
    *,
    words: Vocabulary | None = None,
    index: TagIndex | None = None,
    clock: Callable[[], str] = _now,
) -> ProposalSet:
    """Ask the model about every row of ``pack``, one call per row, and return what came back.

    Every row is asked, including rows already bound — a proposal there is shown as a conflict —
    and rows of a switched-off section, which a later run may switch on, **except Tier D rows**,
    which are recorded as not asked with the reason (:data:`NOT_ASKED_TIER_D`). The pack is not
    changed. A row whose call failed is recorded as a failure, which is not "none proposed".
    """
    words = words or vocabulary()
    index = index or TagIndex.build()
    card = model if isinstance(model, ModelCard) else await client.card(model)
    thinking = thinking_for(card)
    host_version: str | None
    try:
        host_version = await client.version()
    except FATAL:
        raise
    except ModelHostError:
        host_version = None
    catalogue_text = catalogue(words)
    described = tag_descriptions()
    schema = response_schema(words)

    rows: list[RowOutcome] = []
    proposals: list[Proposal] = []
    calls = 0
    started = time.perf_counter()
    for criterion in pack.criteria:
        base = RowOutcome(
            code=criterion.code,
            tier=criterion.tier,
            criterion=criterion.text,
            bound=criterion.bindings,
        )
        if criterion.tier is Tier.D:
            rows.append(replace(base, not_asked=NOT_ASKED_TIER_D))
            continue
        calls += 1
        try:
            result = await client.chat(
                card,
                prompt_for(criterion, catalogue_text),
                thinking=thinking,
                operation="rubric.propose",
                response_format=schema,
            )
            raw = parse_proposals(result.content)
        except FATAL:
            raise
        except ModelHostError as exc:
            rows.append(replace(base, failure=f"the host failed: {exc.summary}"))
            continue
        except SchemaViolation as exc:
            rows.append(replace(base, failure=f"the response did not fit the schema: {exc}"))
            continue

        unknown: list[str] = []
        already: list[str] = []
        chosen: dict[str, RawProposal] = {}
        for item in raw:
            if item.tag not in words:
                if item.tag not in unknown:
                    unknown.append(item.tag)
            elif item.tag in criterion.bindings:
                if item.tag not in already:
                    already.append(item.tag)
            elif item.tag not in chosen:
                chosen[item.tag] = item
        ids: list[int] = []
        for tag in sorted(chosen):
            span, state = _check_span(chosen[tag].span, criterion.text)
            capability = words.get(tag)
            proposal = Proposal(
                id=len(proposals) + 1,
                code=criterion.code,
                tier=criterion.tier,
                criterion=criterion.text,
                tag=tag,
                summary=capability.summary if capability else "",
                looks_for=tuple(described.get(tag, ())),
                span=span,
                span_state=state,
                bound=criterion.bindings,
                changes=consequence(criterion.tier, criterion.bindings, tag, index=index),
            )
            proposals.append(proposal)
            ids.append(proposal.id)
        if unknown:
            log.warning("binding_proposal_unknown_tag", code=criterion.code, tags=unknown)
        rows.append(
            replace(
                base,
                proposals=tuple(ids),
                already_bound=tuple(already),
                unknown=tuple(unknown),
            )
        )

    result = ProposalSet(
        pack_id=pack.id,
        pack_name=pack.name,
        pack_version=pack.version,
        fingerprint=fingerprint(pack),
        model=card.name,
        host_version=host_version,
        thinking_pinned=thinking is Thinking.ON if card.supports_thinking else None,
        created=clock(),
        calls=calls,
        rows=tuple(rows),
        proposals=tuple(proposals),
    )
    log.info(
        "binding_proposals_finished",
        pack=pack.id,
        model=card.name,
        calls=calls,
        proposals=len(proposals),
        failures=len(result.failures),
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    return result


def _check_span(span: str, text: str) -> tuple[str | None, str]:
    """The criterion's own words at a verified match, or the reason there are none."""
    if not span.strip():
        return None, SPAN_ABSENT
    match = verify(span, text)
    if match is None:
        return None, SPAN_DISCARDED
    return match.text, SPAN_VERIFIED


# ---------------------------------------------------------------------------------------------
# The review a person reads
# ---------------------------------------------------------------------------------------------

HOW_TO_ACCEPT = (
    "Nothing below is accepted. Accept by number (--accept 3 --accept 7), or every proposal that "
    "is not a conflict at once (--accept-all). A conflict — a proposal on a row already bound to "
    "other tags — is never included in --accept-all and needs --accept-conflict with its number. "
    "Acceptance prints a mapping file for `import --map`; it does not change the pack."
)


def review(proposals: ProposalSet) -> str:
    """The review, in instrument order. WHAT, WHY and HOW for every proposal; nothing else."""
    lines = [
        proposals.statement(),
        f"Made by {proposals.model} on Ollama {proposals.host_version or '(version unread)'}, "
        f"{proposals.created}. {NONDETERMINISM_NOTE}",
        HOW_TO_ACCEPT,
    ]
    for row in proposals.rows:
        lines.append("")
        lines.append(f"{row.code}  [tier {row.tier.value}]  “{row.criterion}”")
        lines.append(f"    bound now: {', '.join(row.bound) if row.bound else 'nothing'}")
        if row.failure is not None:
            lines.append(f"    could not be asked: {row.failure}")
            continue
        if row.not_asked is not None:
            lines.append(f"    not asked: {row.not_asked}")
            continue
        for proposal_id in row.proposals:
            proposal = proposals.get(proposal_id)
            if proposal is None:  # pragma: no cover - a file loads() accepted is consistent
                continue
            if proposal.conflict:
                lines.append(
                    f"    [{proposal.id}] CONFLICT — proposes {proposal.tag} beside "
                    f"{', '.join(proposal.bound)}, which the row is bound to now. Accepting adds "
                    f"it and changes an authored binding set: --accept-conflict {proposal.id}."
                )
            else:
                lines.append(f"    [{proposal.id}] proposes {proposal.tag}")
            lines.append(f"        what:    {proposal.summary}")
            for note in proposal.looks_for:
                lines.append(f"                 reports: {note}")
            if not proposal.looks_for:
                lines.append("                 no detector implements this tag yet")
            lines.append(f"        why:     {proposal.why()}")
            lines.append(f"        changes: {proposal.changes}")
        if not row.proposals:
            lines.append("    none proposed")
        for tag in row.already_bound:
            lines.append(f"    {tag}: proposed again and already bound here; nothing to accept")
        for tag in row.unknown:
            lines.append(
                f"    dropped: {tag!r} is not a capability tag this build knows. Reported here "
                "and never written."
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# Accepting
# ---------------------------------------------------------------------------------------------


def mapping_from_pack(pack: RubricPack, *, origin: str = "proposals") -> PackMapping:
    """The mapping that re-imports to ``pack``: identity, section flags, every row's tier, note and
    bindings. Refused for a pack imported with a declared join key, whose column the pack does not
    record — for that one the mapping it was built from is supplied instead."""
    keyed = [c.code for c in pack.criteria if "join_key" in c.source_ids]
    if keyed:
        raise ProposalError(
            f"{pack.id} was imported with a declared join key, and a pack does not record which "
            "column that was. Pass the mapping file it was built from with --map, so the mapping "
            "this prints keeps it."
        )
    return PackMapping(
        origin=origin,
        pack_id=pack.id,
        name=pack.name,
        version=pack.version,
        description=pack.description,
        source_title=pack.source.title,
        sheet=pack.source.sheet,
        imported_at=pack.source.imported_at,
        sections={
            section.code: SectionMapping(
                title=section.title,
                optional=section.optional if section.optional else None,
                enabled=None if section.enabled else False,
                note=section.note,
            )
            for section in pack.sections
        },
        criteria={
            criterion.code: RowMapping(
                tier=criterion.tier, bindings=criterion.bindings, note=criterion.note
            )
            for criterion in pack.criteria
        },
    )


def check_mapping(pack: RubricPack, mapping: PackMapping) -> None:
    """Refuse a mapping the pack was not built from: its bindings or tiers disagree."""
    disagree: list[str] = []
    for criterion in pack.criteria:
        row = mapping.criteria.get(criterion.code)
        bindings = row.bindings if row is not None else ()
        tier = row.tier if row is not None and row.tier is not None else None
        if tuple(bindings) != tuple(criterion.bindings) or (
            tier is not None and tier is not criterion.tier
        ):
            disagree.append(criterion.code)
    stray = [code for code in mapping.criteria if pack.criterion(code) is None]
    if disagree or stray:
        raise ProposalError(
            f"{mapping.origin} is not the mapping {pack.id} was built from: "
            + "; ".join(
                part
                for part in (
                    f"row(s) {', '.join(disagree)} differ in tier or bindings" if disagree else "",
                    f"row(s) {', '.join(stray)} are not in the pack" if stray else "",
                )
                if part
            )
            + ". Rebuild the pack from it first, or leave --map off."
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class CoverageDelta:
    """``pack_coverage`` before and after, and the rows that moved between its buckets."""

    before: PackCoverage
    after: PackCoverage
    #: ``(from bucket, to bucket) -> row codes``, only for rows that moved.
    moved: Mapping[tuple[str, str], tuple[str, ...]] = field(default_factory=dict)

    def statement(self) -> str:
        parts = [
            f"{len(codes)} row(s) moved from {start} to {end}"
            for (start, end), codes in self.moved.items()
        ] or ["no row moved between coverage buckets"]
        parts.append(f"{len(self.after.evidence_only)} remain evidence-only")
        return "; ".join(parts) + "."


def _bucket(row: RowCoverage) -> str:
    if row.tier is Tier.D:
        return "not determinable"
    if row.coverage is Coverage.DETERMINISTIC:
        return "deterministic"
    if row.coverage is Coverage.NOT_YET_IMPLEMENTED:
        return "bound to a capability no detector implements yet"
    return "evidence-only"


def coverage_delta(before: RubricPack, after: RubricPack) -> CoverageDelta:
    old = pack_coverage(before)
    new = pack_coverage(after)
    was = {row.code: _bucket(row) for row in old.rows}
    moved: dict[tuple[str, str], list[str]] = {}
    for row in new.rows:
        start, end = was.get(row.code), _bucket(row)
        if start is not None and start != end:
            moved.setdefault((start, end), []).append(row.code)
    return CoverageDelta(
        before=old, after=new, moved={key: tuple(codes) for key, codes in moved.items()}
    )


@dataclass(frozen=True, kw_only=True, slots=True)
class Acceptance:
    """What an act of acceptance produced: the accepted proposals, the mapping, the delta."""

    accepted: tuple[Proposal, ...]
    mapping: PackMapping
    pack: RubricPack
    delta: CoverageDelta


def accept(
    proposals: ProposalSet,
    pack: RubricPack,
    *,
    names: Sequence[int] = (),
    accept_all: bool = False,
    conflicts: Sequence[int] = (),
    base: PackMapping | None = None,
    words: Vocabulary | None = None,
) -> Acceptance:
    """Accept what the person named, and nothing else.

    ``names`` are proposal numbers; ``accept_all`` is every proposal that is not a conflict;
    ``conflicts`` are conflict proposals, named one by one. With none of the three nothing is
    accepted, and that is refused rather than returned as an empty acceptance, so a script that
    forgot the flag does not print a mapping that looks like a decision.
    """
    words = words or vocabulary()
    if proposals.fingerprint != fingerprint(pack) or proposals.pack_id != pack.id:
        raise ProposalError(
            f"these proposals were made against {proposals.pack_id} {proposals.pack_version} as "
            f"it was then, and {pack.id} {pack.version} is not that pack now. Propose again over "
            "the pack as it is."
        )
    if accept_all and names:
        raise ProposalError("--accept-all already accepts every non-conflict proposal; name none")
    if not (accept_all or names or conflicts):
        raise ProposalError(
            "nothing is accepted by default. Name proposals with --accept, or accept every "
            "non-conflict proposal with --accept-all."
        )

    chosen: list[Proposal] = []
    for number in names:
        proposal = _named(proposals, number)
        if proposal.conflict:
            raise ProposalError(
                f"proposal {number} is a conflict: row {proposal.code} is bound to "
                f"{', '.join(proposal.bound)} already. Accept it with --accept-conflict {number}."
            )
        chosen.append(proposal)
    if accept_all:
        chosen.extend(proposal for proposal in proposals.proposals if not proposal.conflict)
    for number in conflicts:
        proposal = _named(proposals, number)
        if not proposal.conflict:
            raise ProposalError(f"proposal {number} is not a conflict; accept it with --accept")
        chosen.append(proposal)

    accepted: list[Proposal] = []
    for proposal in sorted(chosen, key=lambda item: item.id):
        if proposal in accepted:
            continue
        criterion = pack.criterion(proposal.code)
        if criterion is None or criterion.text != proposal.criterion:
            raise ProposalError(f"proposal {proposal.id} names a row the pack does not hold")
        if proposal.tag not in words:
            raise ProposalError(
                f"proposal {proposal.id} names {proposal.tag!r}, which is not a capability tag "
                "this build knows. It is not written."
            )
        if proposal.span_state == SPAN_VERIFIED and (
            proposal.span is None or verify(proposal.span, criterion.text) is None
        ):
            raise ProposalError(
                f"proposal {proposal.id}'s span is not in row {proposal.code}'s text. The file "
                "has been edited; propose again."
            )
        accepted.append(proposal)

    if base is None:
        base = mapping_from_pack(pack)
    else:
        check_mapping(pack, base)

    added: dict[str, list[str]] = {}
    for proposal in accepted:
        added.setdefault(proposal.code, []).append(proposal.tag)
    criteria: dict[str, RowMapping] = dict(base.criteria)
    for code, tags in added.items():
        row = criteria.get(code) or RowMapping()
        extra = tuple(tag for tag in tags if tag not in row.bindings)
        criteria[code] = replace(row, bindings=(*row.bindings, *extra))
    mapping = replace(base, criteria=criteria)

    updated = replace(
        pack,
        criteria=tuple(
            replace(
                criterion,
                bindings=(
                    *criterion.bindings,
                    *(t for t in added.get(criterion.code, ()) if t not in criterion.bindings),
                ),
            )
            for criterion in pack.criteria
        ),
    )
    return Acceptance(
        accepted=tuple(accepted),
        mapping=mapping,
        pack=updated,
        delta=coverage_delta(pack, updated),
    )


def _named(proposals: ProposalSet, number: int) -> Proposal:
    proposal = proposals.get(number)
    if proposal is None:
        known = f"1 to {len(proposals.proposals)}" if proposals.proposals else "none"
        raise ProposalError(f"there is no proposal {number}; the numbers in this file are {known}")
    return proposal


__all__ = [
    "FILE_VERSION",
    "NONDETERMINISM_NOTE",
    "NOT_ASKED_TIER_D",
    "Acceptance",
    "CoverageDelta",
    "Proposal",
    "ProposalError",
    "ProposalSet",
    "RawProposal",
    "RowOutcome",
    "accept",
    "catalogue",
    "check_mapping",
    "consequence",
    "coverage_delta",
    "fingerprint",
    "loads",
    "mapping_from_pack",
    "parse_proposals",
    "prompt_for",
    "propose",
    "response_schema",
    "review",
]
