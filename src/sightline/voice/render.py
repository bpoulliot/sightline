"""The finding renderer: typed findings in, sentences out. THE ONLY PLACE A FINDING BECOMES ENGLISH.

The XLSX writer, the PDF report and the review screen (roadmap items 13, 14 and 16) all render
text, and all three call this. If a later item writes a second function that turns a finding into a
sentence, that is the bug item 15 exists to prevent: three surfaces, three voices, and a rewrite.

WHAT THIS DOES NOT DO
---------------------
It makes no model call and writes no prose of its own. Every sentence is a variant from the phrase
bank with computed facts put into it. The quotes are item 10's — model-*located*, string-matched
against the course before they were accepted, and inserted here exactly as the course wrote them.
What is never allowed is a model composing the sentence around them.

Nor does it decide anything. The rating, the band, the coverage state and the hours were computed by
:mod:`sightline.rubric.project`; the renderer reads them and says them in the register of the row's
tier (SPEC §08). A Tier C row is never worded with a rating. A row nothing examined is never worded
like a clean one.

THE THREE COVERAGE STATES STAY THREE SENTENCES
----------------------------------------------
``NOT_YET_IMPLEMENTED`` names the capability no check examines yet; ``NO_DETERMINISTIC_COVERAGE``
says the rubric assigns no check to the row; ``SUFFICIENTLY_PRESENT`` says what was examined and
that it found none of the conditions checked. The first two come from shapes the register lint
forbids clean wording in. Collapsing them into one reassuring sentence at the last step before a
person reads the report is exactly how silent under-detection (SPEC §11) would come back.

VERBATIM SPANS
--------------
Every value the course, a detector or the instrument wrote — a quote, a title, a measured value, a
criterion code, a rating label — is inserted as a verbatim span, and its position is recorded on
the :class:`Sentence`. The register lint masks exactly those positions, so a page titled "Grading
Policy" is quoted without being mistaken for Sightline's own vocabulary, and nothing else is ever
excused.
"""

from __future__ import annotations

import string
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..detect.base import DetectorResult, Finding, Location, ResultState, Tier, registry
from ..detect.runner import DetectionReport
from ..extract.base import Evidence, ExtractorResult
from ..extract.runner import RowExtraction
from ..model import ArtifactKind
from ..rubric.effort import EffortModel
from ..rubric.project import Contribution, Projection, RowResult
from ..rubric.states import Coverage, Rating
from .bank import PhraseBank, default_bank, placeholders, register_of
from .lint import Register, Violation, default_register

_FORMATTER = string.Formatter()

# ---------------------------------------------------------------------------------------------
# Pieces of text, with the verbatim spans inside them
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Piece:
    """Text plus the positions inside it that are verbatim rather than Sightline's own words."""

    text: str
    spans: tuple[tuple[int, int], ...] = ()

    def __add__(self, other: Piece) -> Piece:
        offset = len(self.text)
        shifted = tuple((start + offset, end + offset) for start, end in other.spans)
        return Piece(self.text + other.text, self.spans + shifted)


def verbatim(text: str) -> Piece:
    """A value somebody other than Sightline wrote: the course, a detector, the instrument."""
    return Piece(text, ((0, len(text)),) if text else ())


def plain(text: str) -> Piece:
    """Sightline's own words. Linted."""
    return Piece(text)


def join(pieces: Sequence[Piece], separator: Piece) -> Piece:
    out = Piece("")
    for index, piece in enumerate(pieces):
        if index:
            out = out + separator
        out = out + piece
    return out


def fill(template: str, values: Mapping[str, Piece | str | int]) -> Piece:
    """Put ``values`` into ``template``, carrying every verbatim span to its final position."""
    out = Piece("")
    for literal, name, _, _ in _FORMATTER.parse(template):
        out = out + plain(literal)
        if name is None:
            continue
        value = values[name]
        if isinstance(value, Piece):
            out = out + value
        else:
            out = out + plain(str(value))
    return out


# ---------------------------------------------------------------------------------------------
# What the renderer hands back
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class Sentence:
    """One rendered sentence, and everything a surface needs to lay it out and to check it.

    ``role`` is what the sentence is doing in the row — ``coverage``, ``headline``, ``check``,
    ``finding``, ``reading``, ``evidence``, ``quote``, ``question`` or ``narrative`` — so a
    surface can give Tier A findings and Tier C questions different visual channels (SPEC §02)
    without parsing text. ``register`` is the tier whose register the words are in.
    """

    role: str
    shape: str
    register: str
    text: str
    spans: tuple[tuple[int, int], ...] = ()
    #: The Canvas page or item the sentence is about, item-level (SPEC §08). ``None`` without a
    #: base URL, or for a sentence about the whole row.
    link: str | None = None
    #: False when the link finds the artifact by search rather than by id.
    link_exact: bool = True

    @property
    def verbatim(self) -> tuple[str, ...]:
        return tuple(self.text[start:end] for start, end in self.spans)

    def lint(self, register: Register | None = None, *, where: str = "") -> list[Violation]:
        rules = register or default_register()
        return rules.lint(
            self.text,
            self.register,
            where=where or self.shape,
            shape=self.shape,
            sentence=True,
            spans=self.spans,
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": self.role,
            "shape": self.shape,
            "register": self.register,
            "text": self.text,
            "verbatim": list(self.verbatim),
        }
        if self.link is not None:
            payload["link"] = self.link
            payload["link_exact"] = self.link_exact
        return payload


@dataclass(frozen=True, kw_only=True, slots=True)
class RenderedRow:
    """Every sentence for one rubric row, in reading order."""

    code: str
    section: str
    tier: Tier
    rating: Rating
    label: str
    priority: str | None
    sentences: tuple[Sentence, ...]

    def by_role(self, role: str) -> tuple[Sentence, ...]:
        return tuple(sentence for sentence in self.sentences if sentence.role == role)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "section": self.section,
            "tier": self.tier.value,
            "rating": self.rating.value,
            "label": self.label,
            "priority": self.priority,
            "sentences": [sentence.to_dict() for sentence in self.sentences],
        }


# ---------------------------------------------------------------------------------------------
# The voice: the bank, and the two operations everything else is built from
# ---------------------------------------------------------------------------------------------


class Voice:
    """The bank, wrapped in the operations the renderer needs. Holds no state that changes."""

    def __init__(self, bank: PhraseBank | None = None) -> None:
        self.bank = bank or default_bank()

    def say(
        self,
        shape: str,
        key: str,
        role: str,
        *,
        location: Location | None = None,
        base_url: str | None = None,
        **values: Piece | str | int,
    ) -> Sentence:
        """One sentence: the variant ``key`` selects, filled with ``values``."""
        template = self.bank.variant(shape, key)
        piece = fill(template, values)
        link = None
        exact = True
        if location is not None and base_url:
            link = location.deep_link(base_url)
            exact = location.link_exact
        return Sentence(
            role=role,
            shape=shape,
            register=register_of(shape),
            text=piece.text,
            spans=piece.spans,
            link=link,
            link_exact=exact,
        )

    def fragment(self, name: str, **values: Piece | str | int) -> Piece:
        _, template = self.bank.fmt(name)
        return fill(template, values)

    def count(self, n: int, noun: str) -> Piece:
        return plain(f"{n} {self.bank.noun(noun, n)}")

    def kind_count(self, n: int, kind: str) -> Piece:
        return plain(f"{n} {self.bank.kind(kind, n)}")

    def listing(self, pieces: Sequence[Piece]) -> Piece:
        return join(pieces, self.fragment("list_separator"))

    def codes(self, codes: Sequence[str]) -> Piece:
        return self.listing([verbatim(code) for code in codes])

    def place(self, location: Location) -> Piece:
        title = verbatim(location.title)
        # A detector often names the line inside ``within`` already ("h5 at line 5", "line 8"); saying
        # it twice reads as two places.
        line = location.line
        if line is not None and location.within and f"line {line}" in location.within:
            line = None
        if location.within and line is not None:
            return self.fragment(
                "place_within_line", title=title, within=verbatim(location.within), line=line
            )
        if location.within:
            return self.fragment("place_within", title=title, within=verbatim(location.within))
        if line is not None:
            return self.fragment("place_line", title=title, line=line)
        return self.fragment("place", title=title)


# ---------------------------------------------------------------------------------------------
# Stable identities — what rotation is keyed on
# ---------------------------------------------------------------------------------------------


def finding_id(finding: Finding) -> str:
    """A finding's identity, from what it *is* — never from where it sits in a list."""
    loc = finding.location
    return "|".join(
        (
            finding.detector,
            finding.code,
            loc.artifact_kind.value,
            loc.artifact_id,
            loc.within or "",
            "" if loc.line is None else str(loc.line),
            finding.observed,
        )
    )


def evidence_id(evidence: Evidence) -> str:
    loc = evidence.location
    return "|".join(
        (
            evidence.extractor,
            loc.artifact_id,
            loc.within or "",
            "" if loc.line is None else str(loc.line),
            evidence.quote,
        )
    )


def _key(code: str, stable: str = "") -> str:
    """The rotation key: the criterion's code, plus the thing described. Nothing positional."""
    return f"{code}\x1f{stable}"


# ---------------------------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class _Context:
    voice: Voice
    effort: EffortModel
    report: DetectionReport | None
    base_url: str | None

    def result(self, detector: str) -> DetectorResult | None:
        return self.report.by_detector(detector) if self.report else None

    def title(self, detector: str) -> str:
        result = self.result(detector)
        if result is not None:
            return result.title
        cls = registry().get(detector)
        return cls.title if cls is not None else detector


def examined_phrase(voice: Voice, contributions: Sequence[Contribution]) -> Piece:
    """What the checks examined, never summed across checks.

    Two checks over the same fifteen pages examined fifteen pages, not thirty, and a headline
    saying thirty is a number a reviewer can falsify in seconds — the wrong way. So one check reads
    "15 artifacts", and several read "15 and 21 artifacts": each count as its check reported it.
    """
    counts = [entry.examined for entry in contributions]
    if len(counts) == 1:
        return voice.count(counts[0], "artifact")
    head = ", ".join(str(n) for n in counts[:-1])
    noun = voice.bank.noun("artifact", 2)
    return plain(f"{head} and {counts[-1]} {noun}")


def _artifacts_with(voice: Voice, findings: Sequence[Finding]) -> Piece:
    return voice.count(len({finding.location.artifact_id for finding in findings}), "artifact")


def render_row(
    row: RowResult,
    *,
    voice: Voice | None = None,
    effort: EffortModel | None = None,
    report: DetectionReport | None = None,
    extraction: RowExtraction | None = None,
    base_url: str | None = None,
) -> RenderedRow:
    """Every sentence for one row.

    ``extraction`` is the row's located evidence, if the extraction layer answered it. A routed row
    without one reads "was not gathered in this run", which is a different fact from "searched and
    located nothing" and is worded differently.
    """
    from ..rubric.effort import default_effort_model

    if row.rating is Rating.SUFFICIENTLY_PRESENT and not _may_read_clean(row):
        # The projection never produces this; a hand-built or deserialised row could. Refused here
        # rather than worded, because the clean sentence is the one wording that must never be
        # reachable from a row nothing examined.
        raise ValueError(
            f"{row.code} reads {row.label} but is Tier {row.tier.value}, "
            f"{row.coverage.value}, with {len(row.contributions)} contribution(s): only a Tier A "
            "row whose checks all examined something and recorded nothing may be worded as clean"
        )
    context = _Context(
        voice=voice or Voice(),
        effort=effort or default_effort_model(),
        report=report,
        base_url=base_url,
    )
    return RenderedRow(
        code=row.code,
        section=row.section,
        tier=row.tier,
        rating=row.rating,
        label=row.label,
        priority=row.priority,
        sentences=tuple(_row_sentences(row, context, extraction)),
    )


def _row_sentences(
    row: RowResult, ctx: _Context, extraction: RowExtraction | None
) -> Iterable[Sentence]:
    voice = ctx.voice
    code = verbatim(row.code)
    label = verbatim(row.label)
    key = _key(row.code)

    if row.tier is Tier.D:
        yield voice.say("D.limit", key, "headline", code=code, label=label)
        return

    # -- the coverage state, stated first when nothing computed the row ------------------------
    if row.coverage is Coverage.NOT_YET_IMPLEMENTED:
        yield voice.say(
            "D.not_yet_implemented", key, "coverage", code=code, tags=voice.codes(row.pending_tags)
        )
    elif row.coverage is Coverage.NO_DETERMINISTIC_COVERAGE:
        if row.bindings:
            yield voice.say(
                "D.unresolved", key, "coverage", code=code, tags=voice.codes(row.bindings)
            )
        else:
            yield voice.say("D.no_deterministic_coverage", key, "coverage", code=code)

    if row.tier is Tier.C:
        # A question and its context. No reading, no label, no band — ever (SPEC §02).
        if row.contributions:
            found = row.findings
            unreadable = sum(entry.undetermined for entry in row.contributions)
            yield voice.say(
                "C.question_checks",
                key,
                "question",
                code=code,
                checks=voice.count(len(row.contributions), "check"),
                examined=examined_phrase(voice, row.contributions),
                found=voice.count(len(found), "observation"),
                unreadable_count=voice.count(unreadable, "item"),
            )
            yield from _check_lines(row, ctx)
            yield from _finding_lines(row, ctx)
        yield from _evidence(row, ctx, extraction, tier=Tier.C)
        return

    # -- Tier A and B: what the checks did ----------------------------------------------------
    if row.contributions:
        yield from _headline(row, ctx)
        yield from _check_lines(row, ctx)
        yield from _finding_lines(row, ctx)
        yield from _reading(row, ctx)
    else:
        yield voice.say("D.held", key, "reading", label=label)

    if row.needs_extraction:
        yield from _evidence(row, ctx, extraction, tier=Tier.B)


def _may_read_clean(row: RowResult) -> bool:
    return (
        row.tier is Tier.A
        and row.coverage is Coverage.DETERMINISTIC
        and bool(row.contributions)
        and all(entry.clean for entry in row.contributions)
    )


def _unexamined(row: RowResult) -> tuple[str, ...]:
    """Bound tags that contributed nothing: no detector yet, unknown, or absent from the report."""
    return tuple(tag for tag in row.bindings if tag not in row.resolved_tags)


def _headline(row: RowResult, ctx: _Context) -> Iterable[Sentence]:
    voice = ctx.voice
    key = _key(row.code)
    code = verbatim(row.code)
    label = verbatim(row.label)
    checks = voice.count(len(row.contributions), "check")
    examined = examined_phrase(voice, row.contributions)
    findings = row.findings

    not_run = [entry for entry in row.contributions if entry.state is not ResultState.EXAMINED]
    if row.rating is Rating.SUFFICIENTLY_PRESENT:
        yield voice.say("A.clean", key, "headline", code=code, checks=checks, examined=examined)
        return
    if row.rating.is_band:
        shape = "A.findings" if row.tier is Tier.A else "B.findings"
        yield voice.say(
            shape,
            key,
            "headline",
            code=code,
            checks=checks,
            examined=examined,
            found=voice.count(len(findings), "observation"),
            artifacts=_artifacts_with(voice, findings),
        )
        unexamined = _unexamined(row)
        if unexamined and row.tier is Tier.A:
            yield voice.say(
                "A.findings_partial", key, "headline", tags=voice.codes(unexamined)
            )
        return
    # No Response on a Tier A or B row with checks behind it: say which reason.
    if not_run:
        yield voice.say(
            "A.not_examined",
            key,
            "headline",
            code=code,
            names=voice.codes([entry.detector for entry in not_run]),
            label=label,
        )
        return
    unreadable = sum(entry.undetermined for entry in row.contributions)
    if unreadable and not findings:
        yield voice.say(
            "A.unreadable",
            key,
            "headline",
            code=code,
            checks=checks,
            examined=examined,
            unreadable=voice.count(unreadable, "unreadable"),
            label=label,
        )
        return
    if row.tier is Tier.A:
        yield voice.say(
            "A.partial", key, "headline", code=code, tags=voice.codes(_unexamined(row)), label=label
        )
        return
    yield voice.say(
        "B.clean_checks", key, "headline", code=code, checks=checks, examined=examined, label=label
    )


def _check_lines(row: RowResult, ctx: _Context) -> Iterable[Sentence]:
    voice = ctx.voice
    for entry in row.contributions:
        key = _key(row.code, f"check|{entry.detector}")
        result = ctx.result(entry.detector)
        title = verbatim(ctx.title(entry.detector))
        detector = verbatim(entry.detector)
        if result is not None and result.crashed is not None:
            yield voice.say("A.check_crashed", key, "check", title=title, detector=detector)
            continue
        if entry.state is not ResultState.EXAMINED:
            reason = (result.not_examined_because if result else None) or ""
            yield voice.say(
                "A.check_not_examined",
                key,
                "check",
                title=title,
                detector=detector,
                reason=verbatim(reason),
            )
            continue
        subjects = voice.count(result.subjects if result else 0, "item_of_interest")
        values: dict[str, Piece | str | int] = {
            "title": title,
            "detector": detector,
            "examined": voice.count(entry.examined, "artifact"),
            "subjects": subjects,
            "found": voice.count(len(entry.findings), "observation"),
        }
        if entry.undetermined:
            values["unreadable"] = voice.count(entry.undetermined, "unreadable")
            yield voice.say("A.check_unreadable", key, "check", **values)
        else:
            yield voice.say("A.check", key, "check", **values)


def _finding_lines(row: RowResult, ctx: _Context) -> Iterable[Sentence]:
    voice = ctx.voice
    for finding in row.findings:
        key = _key(row.code, finding_id(finding))
        values: dict[str, Piece | str | int] = {
            "place": voice.place(finding.location),
            "observed": verbatim(finding.observed),
        }
        shape = "A.finding"
        if finding.evidence:
            values["evidence"] = verbatim(finding.evidence)
            shape = "A.finding_source"
        yield voice.say(
            shape, key, "finding", location=finding.location, base_url=ctx.base_url, **values
        )


def _reading(row: RowResult, ctx: _Context) -> Iterable[Sentence]:
    voice = ctx.voice
    key = _key(row.code, "reading")
    label = verbatim(row.label)
    if row.rating is Rating.SUFFICIENTLY_PRESENT:
        yield voice.say("A.reading_clean", key, "reading", label=label)
    elif row.rating.is_band and row.hours is not None:
        shape = "A.reading_calibrated" if ctx.effort.calibrated else "A.reading_placeholder"
        yield voice.say(shape, key, "reading", label=label, hours=plain(f"{row.hours:g} h"))


def _evidence(
    row: RowResult,
    ctx: _Context,
    extraction: RowExtraction | None,
    *,
    tier: Tier,
) -> Iterable[Sentence]:
    """Located evidence for a routed row, in the register of Tier B, or of Tier C for a question."""
    voice = ctx.voice
    key = _key(row.code, "evidence")
    code = verbatim(row.code)
    head = "C.question" if tier is Tier.C else "B.quotes"
    role = "question" if tier is Tier.C else "evidence"

    if extraction is None:
        yield voice.say(f"{head}_not_run", key, role, code=code)
        return
    result: ExtractorResult = extraction.result
    if result.state is ResultState.NOT_EXAMINED:
        yield voice.say(f"{head}_nothing_examined", key, role, code=code)
        return
    examined = voice.count(result.examined, "artifact")
    if result.passages == 0:
        yield voice.say(f"{head}_no_text", key, role, code=code, examined=examined)
        return
    passages = voice.count(result.passages, "passage")
    unread = result.passages - result.answered

    if not result.evidence:
        if unread:
            yield voice.say(
                f"{head}_none_incomplete",
                key,
                role,
                code=code,
                unread=unread,
                passages=passages,
                examined=examined,
            )
        else:
            yield voice.say(
                f"{head}_none", key, role, code=code, passages=passages, examined=examined
            )
        return

    quotes = voice.count(len(result.evidence), "quote")
    if tier is Tier.C:
        yield voice.say(
            "C.question",
            key,
            role,
            code=code,
            quotes=quotes,
            artifacts=voice.count(
                len({item.location.artifact_id for item in result.evidence}), "artifact"
            ),
            distribution=_distribution(voice, result.evidence),
        )
    else:
        yield voice.say(
            "B.quotes", key, role, code=code, passages=passages, examined=examined, quotes=quotes
        )
    if unread:
        shape = "C.question_incomplete" if tier is Tier.C else "B.quotes_incomplete"
        yield voice.say(shape, _key(row.code, "incomplete"), role, unread=unread, passages=passages)
    for item in result.evidence:
        yield voice.say(
            "B.quote",
            _key(row.code, evidence_id(item)),
            "quote",
            location=item.location,
            base_url=ctx.base_url,
            place=voice.place(item.location),
            quote=verbatim(item.quote),
        )


def _distribution(voice: Voice, evidence: Sequence[Evidence]) -> Piece:
    """How the located quotes spread across kinds of artifact, in a fixed kind order."""
    quotes: dict[str, int] = {}
    artifacts: dict[str, set[str]] = {}
    for item in evidence:
        kind = item.location.artifact_kind.value
        quotes[kind] = quotes.get(kind, 0) + 1
        artifacts.setdefault(kind, set()).add(item.location.artifact_id)
    order = [member.value for member in ArtifactKind]
    parts = [
        voice.fragment(
            "kind_count",
            quotes=voice.count(quotes[kind], "quote"),
            artifacts=voice.kind_count(len(artifacts[kind]), kind),
        )
        for kind in order
        if kind in quotes
    ]
    return voice.listing(parts)


# ---------------------------------------------------------------------------------------------
# A whole review
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True, slots=True)
class RenderedReview:
    """Every row of one projection, and the closing narrative, as sentences."""

    pack_id: str
    pack_name: str
    pack_version: str
    rows: tuple[RenderedRow, ...]
    narrative: Any  # sightline.voice.narrative.Narrative; typed loosely to avoid an import cycle

    def row(self, code: str) -> RenderedRow | None:
        for row in self.rows:
            if row.code == code:
                return row
        return None

    def sentences(self) -> tuple[Sentence, ...]:
        rows = tuple(sentence for row in self.rows for sentence in row.sentences)
        return rows + tuple(self.narrative.sentences())

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "pack_name": self.pack_name,
            "pack_version": self.pack_version,
            "rows": [row.to_dict() for row in self.rows],
            "narrative": self.narrative.to_dict(),
        }


def render_review(
    projection: Projection,
    *,
    report: DetectionReport | None = None,
    extractions: Sequence[RowExtraction] | Mapping[str, RowExtraction] | None = None,
    base_url: str | None = None,
    course_id: str | None = None,
    voice: Voice | None = None,
) -> RenderedReview:
    """Render every row of ``projection``, then the closing narrative.

    ``extractions`` is ``None`` when the extraction layer did not run; every routed row then says
    its evidence was not gathered, and the narrative's fourth slot counts them. A row's sentences
    depend on that row and nothing else, so rendering a subset of rows reproduces the shared rows
    byte for byte.
    """
    from .narrative import build_narrative

    voice = voice or Voice()
    by_code: Mapping[str, RowExtraction]
    if extractions is None:
        by_code = {}
    elif isinstance(extractions, Mapping):
        by_code = extractions
    else:
        by_code = {entry.criterion: entry for entry in extractions}
    rows = tuple(
        render_row(
            row,
            voice=voice,
            effort=projection.effort,
            report=report,
            extraction=by_code.get(row.code),
            base_url=base_url,
        )
        for row in projection.rows
    )
    narrative = build_narrative(
        projection, report=report, extractions=by_code, voice=voice, course_id=course_id
    )
    return RenderedReview(
        pack_id=projection.pack_id,
        pack_name=projection.pack_name,
        pack_version=projection.pack_version,
        rows=rows,
        narrative=narrative,
    )


__all__ = [
    "Piece",
    "RenderedReview",
    "RenderedRow",
    "Sentence",
    "Voice",
    "evidence_id",
    "examined_phrase",
    "fill",
    "join",
    "finding_id",
    "placeholders",
    "plain",
    "render_review",
    "render_row",
    "verbatim",
]
