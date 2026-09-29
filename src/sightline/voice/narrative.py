"""The closing narrative: four slots, filled from computed aggregates, and nothing else.

DECIDED BY A HUMAN ON 2026-09-08, AND NOT TO BE RE-OPENED HERE: **the narrative is assembled from
computed facts; the model does not write prose.** The closing narrative is the most quotable part of
the report — the paragraph that gets pasted into an email — and a paragraph a model wrote is neither
a computed count, nor a verbatim quote with a location, nor a statement that no evidence was found
(SPEC §01). So it goes through the same phrase bank as every other sentence and there is one voice
in the document, not two.

FOUR SLOTS AND ONLY FOUR (SPEC §08)
-----------------------------------
``strong``
    Rows that read Sufficiently Present, each with what its checks examined. Only a Tier A row
    whose checks all examined something and found nothing reaches that reading (item 28), so this
    slot can never be padded with rows nothing looked at.
``work``
    Rows carrying a proposed revision band, grouped by band and listed by code. **Never summed**:
    no count of rows over a total, no hours across rows. A course-level figure is an aggregate
    score whatever unit it is in (SPEC §11).
``first``
    The start of the action plan: the instrument's priority flag crossed with the band (SPEC §07),
    through :meth:`~sightline.rubric.project.Projection.action_plan_order`, which sorts a null
    priority after every labelled one rather than dropping it or defaulting it — the reference
    instrument's mobile rows carry no flag at all.
``limits``
    What Sightline could not determine. **Not optional and not an apology**: a checkable statement
    of the limits of this pass — capabilities with no check yet, rows no check is assigned to,
    evidence not gathered, judgements left to the reviewer, rows outside what artifacts can show,
    things that could not be read, checks that crashed. A report without it is overclaiming. When
    there is genuinely nothing to list it still says what the checks cannot see.

Rotation is keyed on the slot and the course, never on a counter; see :mod:`sightline.voice.bank`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..detect.base import ResultState, Tier
from ..detect.runner import DetectionReport
from ..extract.runner import RowExtraction
from ..rubric.project import Projection, RowResult
from ..rubric.states import Coverage, Rating
from .render import Piece, Sentence, Voice, examined_phrase, join, verbatim

#: How many rows the "what to do first" slot names. The full order is the action plan's job.
FIRST_ROWS = 5

#: The bands, most effort first — the order the "needs work" slot lists them in.
_BAND_ORDER: tuple[Rating, ...] = (Rating.MAJOR, Rating.MODERATE, Rating.MINOR)

#: The slots, in reading order.
SLOTS: tuple[str, ...] = ("strong", "work", "first", "limits")


@dataclass(frozen=True, kw_only=True, slots=True)
class Slot:
    key: str
    heading: Sentence
    sentences: tuple[Sentence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "heading": self.heading.text,
            "sentences": [sentence.to_dict() for sentence in self.sentences],
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class Narrative:
    slots: tuple[Slot, ...]

    def slot(self, key: str) -> Slot | None:
        for slot in self.slots:
            if slot.key == key:
                return slot
        return None

    def sentences(self) -> tuple[Sentence, ...]:
        return tuple(
            sentence for slot in self.slots for sentence in (slot.heading, *slot.sentences)
        )

    def to_dict(self) -> dict[str, Any]:
        return {"slots": [slot.to_dict() for slot in self.slots]}


def _heading(voice: Voice, name: str) -> Sentence:
    register, _ = voice.bank.fmt(name)
    piece = voice.fragment(name)
    return Sentence(
        role="heading",
        shape=f"formats.{name}",
        register=register,
        text=piece.text,
        spans=piece.spans,
    )


def build_narrative(
    projection: Projection,
    *,
    report: DetectionReport | None = None,
    extractions: Mapping[str, RowExtraction] | None = None,
    voice: Voice | None = None,
    course_id: str | None = None,
) -> Narrative:
    """The four slots for one projection. ``extractions`` empty or ``None`` means none gathered."""
    voice = voice or Voice()
    gathered = extractions or {}
    rows = projection.rows

    def key(slot: str) -> str:
        return f"narrative:{slot}\x1f{course_id or ''}\x1f{projection.pack_id}"

    def say(shape: str, slot: str, **values: Piece | str | int) -> Sentence:
        return voice.say(shape, key(f"{slot}:{shape}"), "narrative", **values)

    clean_label = (
        projection.rating_scale.sufficiently_present
        if projection.rating_scale is not None
        else Rating.SUFFICIENTLY_PRESENT.value
    )

    # -- what is strong ------------------------------------------------------------------------
    strong_rows = [row for row in rows if row.rating is Rating.SUFFICIENTLY_PRESENT]
    if strong_rows:
        items = voice.listing(
            [
                voice.fragment(
                    "strong_item",
                    code=verbatim(row.code),
                    examined=examined_phrase(voice, row.contributions),
                )
                for row in strong_rows
            ]
        )
        strong = [
            say(
                "narrative.strong",
                "strong",
                rows=voice.count(len(strong_rows), "row"),
                label=verbatim(clean_label),
                items=items,
            )
        ]
    else:
        strong = [say("narrative.strong_none", "strong", label=verbatim(clean_label))]

    # -- what needs work -------------------------------------------------------------------------
    banded = [row for row in rows if row.rating.is_band]
    if banded:
        groups = []
        for rating in _BAND_ORDER:
            members = [row for row in banded if row.rating is rating]
            if members:
                groups.append(
                    voice.fragment(
                        "work_group",
                        label=verbatim(members[0].label),
                        codes=voice.codes([row.code for row in members]),
                    )
                )
        work = [
            say(
                "narrative.work",
                "work",
                rows=voice.count(len(banded), "row"),
                groups=join(groups, voice.fragment("work_separator")),
            )
        ]
    else:
        work = [say("narrative.work_none", "work")]

    # -- what to do first ------------------------------------------------------------------------
    ordered = [row for row in projection.action_plan_order() if row.rating.is_band][:FIRST_ROWS]
    if ordered:
        items = voice.listing(
            [
                voice.fragment(
                    "first_item",
                    code=verbatim(row.code),
                    priority=(
                        verbatim(row.priority)
                        if row.priority is not None
                        else voice.fragment("no_priority")
                    ),
                    label=verbatim(row.label),
                )
                for row in ordered
            ]
        )
        first = [say("narrative.first", "first", items=items)]
    else:
        first = [say("narrative.first_none", "first")]

    # -- what Sightline could not determine ------------------------------------------------------
    limits = list(_limits(projection, report, gathered, voice, say))

    return Narrative(
        slots=(
            Slot(key="strong", heading=_heading(voice, "heading_strong"), sentences=tuple(strong)),
            Slot(key="work", heading=_heading(voice, "heading_work"), sentences=tuple(work)),
            Slot(key="first", heading=_heading(voice, "heading_first"), sentences=tuple(first)),
            Slot(key="limits", heading=_heading(voice, "heading_limits"), sentences=tuple(limits)),
        )
    )


def held_unread(row: RowResult) -> bool:
    """Whether a Tier A or B row stays at No Response because something it needed was not read.

    A Tier B row whose checks ran clean is *not* one of these: its checks bear on the criterion
    without settling it, which its own sentence says, and its evidence is the located passages.
    """
    if row.tier not in (Tier.A, Tier.B) or row.rating is not Rating.NO_RESPONSE:
        return False
    if row.coverage is Coverage.NO_DETERMINISTIC_COVERAGE:
        return bool(row.bindings)
    if row.coverage is not Coverage.DETERMINISTIC:
        return False
    if any(entry.state is not ResultState.EXAMINED for entry in row.contributions):
        return True
    if any(entry.undetermined for entry in row.contributions):
        return True
    return row.tier is Tier.A and any(tag not in row.resolved_tags for tag in row.bindings)


def _limits(projection, report, gathered, voice, say):  # type: ignore[no-untyped-def]
    rows = projection.rows

    def codes_of(members: Sequence[RowResult]) -> Piece:
        return voice.codes([row.code for row in members])

    crashed = report.crashed if report is not None else ()
    unread = [row for row in rows if held_unread(row)]
    pending = [
        row
        for row in rows
        if row.coverage is Coverage.NOT_YET_IMPLEMENTED and row.tier is not Tier.D
    ]
    unbound = [
        row
        for row in rows
        if row.coverage is Coverage.NO_DETERMINISTIC_COVERAGE
        and not row.bindings
        and row.tier in (Tier.A, Tier.B)
    ]
    not_gathered = [row for row in rows if row.needs_extraction and row.code not in gathered]
    judgement = [row for row in rows if row.tier is Tier.C]
    out_of_scope = [row for row in rows if row.tier is Tier.D]

    said = False
    if crashed:
        said = True
        yield say(
            "D.narrative_crashed",
            "limits",
            checks=voice.count(len(crashed), "check"),
            names=voice.codes([result.detector for result in crashed]),
        )
    for shape, members in (
        ("D.narrative_unread", unread),
        ("D.narrative_not_yet_implemented", pending),
        ("D.narrative_unbound", unbound),
        ("D.narrative_not_gathered", not_gathered),
        ("D.narrative_judgement", judgement),
        ("D.narrative_out_of_scope", out_of_scope),
    ):
        if members:
            said = True
            yield say(
                shape,
                "limits",
                rows=voice.count(len(members), "row"),
                codes=codes_of(members),
            )
    if not said:
        yield say("D.narrative_limits_none", "limits")


__all__ = ["FIRST_ROWS", "SLOTS", "Narrative", "Slot", "build_narrative", "held_unread"]
