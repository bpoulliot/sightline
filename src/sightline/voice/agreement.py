"""How often two qualified people choose the same rating, said out loud (roadmap item 24).

Hand-completed reviews were analysed on 2026-09-08, and independent human passes over the same
course did not always choose the same rating — the figures, with their sample size, are in
``agreement.yaml`` and nowhere else. That is a fact about **the difficulty of the task**. It tells a reader
what a first pass can possibly be worth, and it is the most direct support there is for SPEC §01's
posture: a tool that says where reviewers disagree is plainly not pretending to be one.

**It says nothing about how often Sightline is right**, and no sentence may imply that it does. The
register lint's ``self_assessment`` rule refuses a percentage in any sentence that names Sightline
or its output, and the vocabulary of accuracy, confidence and reliability anywhere; the suite plants
the forbidden constructions and proves the rule fires on each.

ONE SOURCE
----------
The figures live in ``agreement.yaml`` beside this module, with the sample size, the scope, the
provenance and the three limits attached. The words live in ``phrases.yaml``. Every surface calls
this module — the PDF's *what this document is* block and the review screen take :func:`brief`, the
README carries :func:`markdown` — so a figure is never typed twice, and when the corpus grows it
changes in one file. A test fails the build if one of these percentages is typed anywhere else.

**Not in the workbook.** Item 13's workbook is one header row per sheet, no merged cells, and a
lossless CSV round-trip; a paragraph of prose has nowhere honest to live in that grid, and the
workbook is the designer's working artifact rather than the one forwarded to faculty. The omission
is deliberate, and :mod:`sightline.workbook` says so where the next person would add it.

THE LIMITS TRAVEL WITH THE NUMBERS
----------------------------------
The corpus is a small sample. It holds too few Major Revision ratings to say anything about the
Major boundary. The pairs are different pairings of roles, one of them two passes by the same role
on a later revision of the instrument, so the range is separate measurements rather than one
statistic.
:func:`brief` states the first two in its second sentence and the third in its first; the long form
states each on its own line. The loader refuses a data file that drops a limit, and the renderer
refuses to say "every disagreement was one band apart" once the data stops saying so — that
sentence then needs a person, not a template.

WHY A FIGURE IS A SPAN
----------------------
The ``aggregate`` rule refuses a percentage in Sightline's own words everywhere, because a
percentage about a course is a compliance score. These percentages are not about a course: they are
measured values from a named analysis, and they are inserted as verbatim spans exactly as a
detector's measured value is. The ``self_assessment`` rule reads the whole sentence, spans included,
so masking a figure never lets it sit beside Sightline's name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from ..rubric.effort import EffortModel, default_effort_model
from .lint import Register, Violation, default_register
from .render import Piece, Sentence, Voice, plain, verbatim

#: The figures, beside this module.
AGREEMENT_FILENAME = "agreement.yaml"

#: The limits that must be stated with the figures. A data file without all three is refused.
REQUIRED_LIMITS: tuple[str, ...] = ("small_sample", "major_boundary", "pairings_differ")

#: What every sentence here is doing, for a surface laying it out.
ROLE = "agreement"

#: Every key the data file may carry. Anything else is refused, so a figure nobody decided to
#: publish cannot ride into the product by being added to this file (roadmap item 24).
_TOP_LEVEL = frozenset(
    {"measured", "instrument", "corpus", "scope", "source", "pairs", "disagreements", "limits"}
)
_CORPUS = frozenset({"courses", "rated_instances", "major_ratings"})
_PAIR = frozenset({"id", "passes", "rated_by_both", "identical_percent"})
_DISAGREEMENTS = (
    "total",
    "one_band",
    "two_bands_or_more",
    "sufficiently_present_vs_minor",
    "major_vs_sufficiently_present",
)


def _only(mapping: Mapping[str, Any], allowed: Any, where: str) -> None:
    extra = sorted(set(mapping) - set(allowed))
    if extra:
        raise AgreementError(f"{where} carries {', '.join(extra)}, which is not published")


class AgreementError(ValueError):
    """``agreement.yaml`` is malformed, or no longer supports a sentence the bank says."""


@dataclass(frozen=True, kw_only=True, slots=True)
class Pair:
    """Two independent passes over the same course, and how often they chose the same rating."""

    id: str
    #: Who made the two passes, in Sightline's words. Linted like any other.
    passes: str
    #: Criteria both passes rated.
    rated_by_both: int
    #: As measured. Never recomputed here.
    identical_percent: int


@dataclass(frozen=True, kw_only=True, slots=True)
class Disagreements:
    """Every disagreement across the pairs, by how far apart the two ratings were."""

    total: int
    one_band: int
    two_bands_or_more: int
    sufficiently_present_vs_minor: int
    major_vs_sufficiently_present: int


@dataclass(frozen=True, kw_only=True, slots=True)
class Agreement:
    """The loaded figures, with their sample size, scope, provenance and limits."""

    measured: str
    instrument: str
    courses: int
    rated_instances: int
    major_ratings: int
    scope: str
    source: str
    pairs: tuple[Pair, ...]
    disagreements: Disagreements
    limits: tuple[str, ...]

    @property
    def low(self) -> int:
        return min(pair.identical_percent for pair in self.pairs)

    @property
    def high(self) -> int:
        return max(pair.identical_percent for pair in self.pairs)

    def percentages(self) -> tuple[int, ...]:
        return tuple(pair.identical_percent for pair in self.pairs)


def _count(value: Any, where: str, *, positive: bool = True) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < (1 if positive else 0):
        kind = "a positive" if positive else "a non-negative"
        raise AgreementError(f"{where} must be {kind} whole number, not {value!r}")
    return value


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgreementError(f"{where} must be non-empty text")
    return value.strip()


def parse_agreement(payload: Any, *, origin: str = AGREEMENT_FILENAME) -> Agreement:
    """Build an :class:`Agreement`, refusing anything malformed or inconsistent by name."""
    if not isinstance(payload, Mapping):
        raise AgreementError(f"{origin}: the top level must be a mapping")
    try:
        _only(payload, _TOP_LEVEL, "the file")
        corpus = payload["corpus"]
        if not isinstance(corpus, Mapping):
            raise AgreementError("corpus must be a mapping")
        _only(corpus, _CORPUS, "corpus")
        courses = _count(corpus["courses"], "corpus.courses")
        instances = _count(corpus["rated_instances"], "corpus.rated_instances")
        majors = _count(corpus["major_ratings"], "corpus.major_ratings", positive=False)
        if majors > instances:
            raise AgreementError("corpus.major_ratings exceeds corpus.rated_instances")

        raw_pairs = payload["pairs"]
        if not isinstance(raw_pairs, list) or not raw_pairs:
            raise AgreementError("pairs must be a non-empty list")
        pairs: list[Pair] = []
        for index, entry in enumerate(raw_pairs):
            where = f"pairs[{index}]"
            if not isinstance(entry, Mapping):
                raise AgreementError(f"{where} must be a mapping")
            _only(entry, _PAIR, where)
            percent = _count(entry["identical_percent"], f"{where}.identical_percent", positive=False)
            if percent > 100:
                raise AgreementError(f"{where}.identical_percent is over 100")
            pairs.append(
                Pair(
                    id=_text(entry["id"], f"{where}.id"),
                    passes=_text(entry["passes"], f"{where}.passes"),
                    rated_by_both=_count(entry["rated_by_both"], f"{where}.rated_by_both"),
                    identical_percent=percent,
                )
            )
        if len({pair.id for pair in pairs}) != len(pairs):
            raise AgreementError("pairs repeat an id")

        raw = payload["disagreements"]
        if not isinstance(raw, Mapping):
            raise AgreementError("disagreements must be a mapping")
        _only(raw, _DISAGREEMENTS, "disagreements")
        disagreements = Disagreements(
            **{
                name: _count(raw[name], f"disagreements.{name}", positive=False)
                for name in _DISAGREEMENTS
            }
        )
        if disagreements.one_band + disagreements.two_bands_or_more != disagreements.total:
            raise AgreementError("disagreements: one_band + two_bands_or_more must equal total")
        if disagreements.sufficiently_present_vs_minor > disagreements.one_band:
            raise AgreementError("disagreements.sufficiently_present_vs_minor exceeds one_band")

        limits = payload["limits"]
        if not isinstance(limits, list) or not all(isinstance(name, str) for name in limits):
            raise AgreementError("limits must be a list of names")
        missing = [name for name in REQUIRED_LIMITS if name not in limits]
        if missing:
            raise AgreementError(
                f"limits is missing {', '.join(missing)}: the figures are never stated without it"
            )
        unknown = sorted(set(limits) - set(REQUIRED_LIMITS))
        if unknown:
            raise AgreementError(f"limits names {', '.join(unknown)}, which nothing renders")

        return Agreement(
            measured=_text(str(payload["measured"]), "measured"),
            instrument=_text(payload["instrument"], "instrument"),
            courses=courses,
            rated_instances=instances,
            major_ratings=majors,
            scope=_text(payload["scope"], "scope"),
            source=_text(payload["source"], "source"),
            pairs=tuple(pairs),
            disagreements=disagreements,
            limits=tuple(limits),
        )
    except KeyError as exc:
        raise AgreementError(f"{origin}: missing {exc.args[0]!r}") from exc
    except AgreementError as exc:
        if str(exc).startswith(origin):
            raise
        raise AgreementError(f"{origin}: {exc}") from exc


def load_agreement(path: Path | str | None = None) -> Agreement:
    """The figures at ``path``, or the shipped ones."""
    if path is None:
        return default_agreement()
    source = Path(path)
    return parse_agreement(yaml.safe_load(source.read_text(encoding="utf-8")), origin=str(source))


@cache
def default_agreement() -> Agreement:
    """The shipped figures. Read once."""
    shipped = resources.files(__package__).joinpath(AGREEMENT_FILENAME)
    return parse_agreement(yaml.safe_load(shipped.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------------------------
# Sentences
# ---------------------------------------------------------------------------------------------


def _figure(percent: int) -> Piece:
    """A measured percentage, inserted as a span. See the module docstring for why."""
    return verbatim(f"{percent}%")


def _say(voice: Voice, name: str, **values: Piece | str | int) -> Sentence:
    register, _ = voice.bank.fmt(name)
    piece = voice.fragment(name, **values)
    return Sentence(
        role=ROLE, shape=f"format.{name}", register=register, text=piece.text, spans=piece.spans
    )


def _require_one_band(agreement: Agreement) -> None:
    if agreement.disagreements.two_bands_or_more:
        raise AgreementError(
            "the bank says every disagreement was one band apart and the figures no longer do; "
            "the wording needs a person, not a template"
        )


def brief(agreement: Agreement | None = None, voice: Voice | None = None) -> tuple[Sentence, ...]:
    """The short form: two sentences, for the PDF's *what this document is* block and the review
    screen. The figures, the pairing caveat, the sample size and the Major limit, in one breath."""
    data = agreement or default_agreement()
    words = voice or Voice()
    _require_one_band(data)
    return (
        _say(
            words,
            "agreement_range",
            low=_figure(data.low),
            high=_figure(data.high),
            pairs=len(data.pairs),
            courses=data.courses,
        ),
        _say(
            words,
            "agreement_scope",
            courses=data.courses,
            majors=data.major_ratings,
            instances=data.rated_instances,
        ),
    )


def pairs(agreement: Agreement | None = None, voice: Voice | None = None) -> tuple[Sentence, ...]:
    """One sentence per pair, in the data's order."""
    data = agreement or default_agreement()
    words = voice or Voice()
    return tuple(
        _say(
            words,
            "agreement_pair",
            passes=plain(pair.passes),
            rated=pair.rated_by_both,
            identical=_figure(pair.identical_percent),
        )
        for pair in data.pairs
    )


def disagreements(agreement: Agreement | None = None, voice: Voice | None = None) -> Sentence:
    data = agreement or default_agreement()
    words = voice or Voice()
    _require_one_band(data)
    if data.disagreements.major_vs_sufficiently_present:
        raise AgreementError(
            "the bank says no disagreement set Major Revision against Sufficiently Present and the "
            "figures no longer do; the wording needs a person, not a template"
        )
    return _say(
        words,
        "agreement_disagreements",
        disagreements=data.disagreements.total,
        at_lowest=data.disagreements.sufficiently_present_vs_minor,
    )


def limits(agreement: Agreement | None = None, voice: Voice | None = None) -> tuple[Sentence, ...]:
    """The three limits, each on its own, in the data's order."""
    data = agreement or default_agreement()
    words = voice or Voice()
    values: Mapping[str, Mapping[str, Piece | str | int]] = {
        "small_sample": {"courses": data.courses},
        "major_boundary": {"majors": data.major_ratings, "instances": data.rated_instances},
        "pairings_differ": {"pairs": len(data.pairs)},
    }
    return tuple(_say(words, f"agreement_limit_{name}", **values[name]) for name in data.limits)


def source(agreement: Agreement | None = None, voice: Voice | None = None) -> Sentence:
    data = agreement or default_agreement()
    words = voice or Voice()
    return _say(
        words,
        "agreement_source",
        measured=data.measured,
        courses=data.courses,
        instrument=verbatim(data.instrument),
        instances=data.rated_instances,
    )


def full(agreement: Agreement | None = None, voice: Voice | None = None) -> tuple[Sentence, ...]:
    """Every sentence, in reading order: the short form, the pairs, the disagreements, the limits,
    and where the figures came from."""
    data = agreement or default_agreement()
    words = voice or Voice()
    return (
        *brief(data, words),
        *pairs(data, words),
        disagreements(data, words),
        *limits(data, words),
        source(data, words),
    )


def near_boundary_legend(
    effort: EffortModel | None = None,
    agreement: Agreement | None = None,
    voice: Voice | None = None,
) -> Sentence:
    """The near-boundary marker's one explanation, for the surface where a reader first meets it.

    The margins come from the effort model the marker was computed with, and the reason the lowest
    one is wider comes from the figures above. Nothing here is a percentage or a word about how sure
    anything is: the marker is a distance from a threshold.
    """
    model = effort or default_effort_model()
    data = agreement or default_agreement()
    words = voice or Voice()
    return _say(
        words,
        "near_boundary_legend",
        between=plain(f"{model.margins.between_bands:g} h"),
        lowest=plain(f"{model.margins.lowest:g} h"),
        at_lowest=data.disagreements.sufficiently_present_vs_minor,
        disagreements=data.disagreements.total,
    )


def markdown(agreement: Agreement | None = None, voice: Voice | None = None) -> str:
    """The long form as Markdown, for the README. The README's block must equal this exactly."""
    data = agreement or default_agreement()
    words = voice or Voice()
    lines = [" ".join(sentence.text for sentence in brief(data, words)), ""]
    lines.extend(f"- {sentence.text}" for sentence in pairs(data, words))
    lines.extend(["", disagreements(data, words).text, ""])
    lines.extend(f"- {sentence.text}" for sentence in limits(data, words))
    lines.extend(["", source(data, words).text])
    return "\n".join(lines) + "\n"


def violations(
    sentences: Sequence[Sentence], register: Register | None = None
) -> list[Violation]:
    """Every register violation in ``sentences``. Empty for everything this module renders."""
    rules = register or default_register()
    return [violation for sentence in sentences for violation in sentence.lint(rules)]


__all__ = [
    "AGREEMENT_FILENAME",
    "REQUIRED_LIMITS",
    "Agreement",
    "AgreementError",
    "Disagreements",
    "Pair",
    "brief",
    "default_agreement",
    "disagreements",
    "full",
    "limits",
    "load_agreement",
    "markdown",
    "near_boundary_legend",
    "pairs",
    "parse_agreement",
    "source",
    "violations",
]
