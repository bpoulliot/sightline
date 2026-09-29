"""The phrase bank: loading it, holding it to its contract, and choosing a variant.

The bank is data — ``phrases.yaml`` beside this module — so the project's voice can be read and
revised by someone who does not write Python. This module is what stops an edit to it from quietly
breaking a promise:

* **Every shape the renderer uses exists, and no other.** :data:`SHAPES` is the renderer's side of
  the contract: the placeholders each shape is handed. A bank missing a shape, or carrying one
  nothing renders, is refused at load.
* **Every variant of a shape uses exactly the shape's placeholders.** Variety is in how a fact is
  introduced, never in which facts are stated. A variant that dropped ``{examined}`` would have
  dropped the count that tells a detector that passed from one that broke (SPEC §11).
* **Every variant passes the register lint** (:mod:`sightline.voice.lint`). A hedged Tier A
  variant fails the load, not a test that somebody might skip.

ROTATION IS A PURE FUNCTION OF A STABLE KEY
-------------------------------------------
:meth:`PhraseBank.variant` picks by hashing the shape name and a key the caller builds from the
criterion's code and the finding's own identity. Never ``random``, never the clock, and never a
counter. A counter is the trap: if the variant chosen for GSOV 4 depended on how many sentences
were rendered before it, fixing one defect in Module 2 would reword unrelated criteria on the next
run and two reviews of the same course could no longer be diffed. ``random.seed(0)`` at import is
the same counter with extra steps. ``tests/test_voice.py`` renders a subset and a superset and
asserts the rows they share are byte-identical.
"""

from __future__ import annotations

import hashlib
import string
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from ..model import ArtifactKind
from .lint import REGISTERS, Register, Violation, default_register

#: The shipped bank, beside this module.
PHRASES_FILENAME = "phrases.yaml"

#: The fewest variants a sentence shape may carry. Three is the floor below which a 68-row report
#: reads as a form letter.
MIN_VARIANTS = 3

#: Which register a narrative shape is read in. The first three slots state computed aggregates, in
#: the register of Tier A. The fourth lives under ``D`` in the bank, because it states a limit.
NARRATIVE_REGISTER = "A"

#: THE RENDERER'S SIDE OF THE CONTRACT. Shape -> the placeholders it is handed. A bank variant must
#: use exactly these, no more and no fewer.
SHAPES: Mapping[str, frozenset[str]] = {
    name: frozenset(fields)
    for name, fields in {
        "A.clean": ("code", "checks", "examined"),
        "A.findings": ("code", "checks", "examined", "found", "artifacts"),
        "A.findings_partial": ("tags",),
        "A.unreadable": ("code", "checks", "examined", "unreadable", "label"),
        "A.not_examined": ("code", "names", "label"),
        "A.partial": ("code", "tags", "label"),
        "A.check": ("title", "detector", "examined", "subjects", "found"),
        "A.check_unreadable": ("title", "detector", "examined", "subjects", "found", "unreadable"),
        "A.check_not_examined": ("title", "detector", "reason"),
        "A.check_crashed": ("title", "detector"),
        "A.finding": ("place", "observed"),
        "A.finding_source": ("place", "observed", "evidence"),
        "A.reading_placeholder": ("label", "hours"),
        "A.reading_calibrated": ("label", "hours"),
        "A.reading_clean": ("label",),
        "B.findings": ("code", "checks", "examined", "found", "artifacts"),
        "B.clean_checks": ("code", "checks", "examined", "label"),
        "B.quotes": ("code", "passages", "examined", "quotes"),
        "B.quote": ("place", "quote"),
        "B.quotes_none": ("code", "passages", "examined"),
        "B.quotes_none_incomplete": ("code", "unread", "passages", "examined"),
        "B.quotes_incomplete": ("unread", "passages"),
        "B.quotes_no_text": ("code", "examined"),
        "B.quotes_nothing_examined": ("code",),
        "B.quotes_not_run": ("code",),
        "C.question": ("code", "quotes", "artifacts", "distribution"),
        "C.question_none": ("code", "passages", "examined"),
        "C.question_none_incomplete": ("code", "unread", "passages", "examined"),
        "C.question_checks": ("code", "checks", "examined", "found", "unreadable_count"),
        "C.question_incomplete": ("unread", "passages"),
        "C.question_no_text": ("code", "examined"),
        "C.question_nothing_examined": ("code",),
        "C.question_not_run": ("code",),
        "D.limit": ("code", "label"),
        "D.not_yet_implemented": ("code", "tags"),
        "D.no_deterministic_coverage": ("code",),
        "D.unresolved": ("code", "tags"),
        "D.held": ("label",),
        "D.narrative_not_yet_implemented": ("rows", "codes"),
        "D.narrative_unbound": ("rows", "codes"),
        "D.narrative_not_gathered": ("rows", "codes"),
        "D.narrative_judgement": ("rows", "codes"),
        "D.narrative_out_of_scope": ("rows", "codes"),
        "D.narrative_unread": ("rows", "codes"),
        "D.narrative_crashed": ("checks", "names"),
        "D.narrative_limits_none": (),
        "narrative.strong": ("rows", "label", "items"),
        "narrative.strong_none": ("label",),
        "narrative.work": ("rows", "groups"),
        "narrative.work_none": (),
        "narrative.first": ("items",),
        "narrative.first_none": (),
    }.items()
}

#: Fixed fragments. Same rule: exactly these placeholders.
FORMATS: Mapping[str, frozenset[str]] = {
    name: frozenset(fields)
    for name, fields in {
        "place": ("title",),
        "place_within": ("title", "within"),
        "place_line": ("title", "line"),
        "place_within_line": ("title", "within", "line"),
        "list_separator": (),
        "kind_count": ("quotes", "artifacts"),
        "strong_item": ("code", "examined"),
        "work_group": ("label", "codes"),
        "work_separator": (),
        "first_item": ("code", "priority", "label"),
        "no_priority": (),
        "heading_strong": (),
        "heading_work": (),
        "heading_first": (),
        "heading_limits": (),
    }.items()
}

#: The nouns the renderer counts with.
NOUNS: frozenset[str] = frozenset(
    {
        "artifact",
        "check",
        "item",
        "item_of_interest",
        "observation",
        "passage",
        "quote",
        "row",
        "unreadable",
    }
)

_FORMATTER = string.Formatter()


class PhraseBankError(ValueError):
    """The bank breaks its contract or its register. Raised at load time, naming every problem."""

    def __init__(self, origin: str, problems: list[str]) -> None:
        self.origin = origin
        self.problems = tuple(problems)
        listed = "\n  ".join(problems)
        super().__init__(f"{origin}: {len(problems)} problem(s)\n  {listed}")


def placeholders(template: str) -> frozenset[str]:
    """The ``{names}`` a template uses. A positional ``{}`` or a format spec is not allowed."""
    names: set[str] = set()
    for _, name, spec, conversion in _FORMATTER.parse(template):
        if name is None:
            continue
        if not name or not name.isidentifier() or spec or conversion:
            raise ValueError(f"placeholder {{{name}}} must be a bare {{name}}")
        names.add(name)
    return frozenset(names)


def register_of(shape: str) -> str:
    """The register a shape is read in: its tier, or Tier A's for the narrative."""
    head = shape.split(".", 1)[0]
    return NARRATIVE_REGISTER if head == "narrative" else head


def choose(count: int, shape: str, key: str) -> int:
    """Which of ``count`` variants a sentence uses. A pure function of ``shape`` and ``key``."""
    if count <= 0:
        raise ValueError("no variants to choose from")
    digest = hashlib.sha256(f"{shape}\x1f{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % count


@dataclass(frozen=True, kw_only=True, slots=True)
class PhraseBank:
    """The loaded bank. Immutable; one per process is plenty."""

    shapes: Mapping[str, tuple[str, ...]]
    formats: Mapping[str, tuple[str, str]]
    nouns: Mapping[str, tuple[str, str]]
    kinds: Mapping[str, tuple[str, str]]

    def variant(self, shape: str, key: str) -> str:
        variants = self.shapes[shape]
        return variants[choose(len(variants), shape, key)]

    def fmt(self, name: str) -> tuple[str, str]:
        """``(register, template)`` for a fixed fragment."""
        return self.formats[name]

    def noun(self, name: str, count: int) -> str:
        singular, plural = self.nouns[name]
        return singular if count == 1 else plural

    def kind(self, kind: str, count: int) -> str:
        singular, plural = self.kinds[kind]
        return singular if count == 1 else plural


def _pair(value: Any, where: str, problems: list[str]) -> tuple[str, str] | None:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, str) and item for item in value)
    ):
        return value[0], value[1]
    problems.append(f"{where} must be [singular, plural]")
    return None


def parse_bank(
    payload: Any,
    *,
    origin: str = PHRASES_FILENAME,
    register: Register | None = None,
    shapes: Mapping[str, frozenset[str]] = SHAPES,
    formats: Mapping[str, frozenset[str]] = FORMATS,
) -> PhraseBank:
    """Build a bank from parsed YAML, or raise :class:`PhraseBankError` listing every problem."""
    rules = register or default_register()
    problems: list[str] = []
    if not isinstance(payload, Mapping):
        raise PhraseBankError(origin, ["the top level must be a mapping"])

    found: dict[str, tuple[str, ...]] = {}
    for head in (*REGISTERS, "narrative"):
        section = payload.get(head)
        if not isinstance(section, Mapping):
            problems.append(f"section {head!r} is missing or is not a mapping")
            continue
        for name, variants in section.items():
            shape = f"{head}.{name}"
            if not isinstance(variants, list) or not all(isinstance(v, str) for v in variants):
                problems.append(f"{shape} must be a list of strings")
                continue
            found[shape] = tuple(variants)

    for shape in sorted(set(shapes) - set(found)):
        problems.append(f"{shape} is missing: the renderer uses it")
    for shape in sorted(set(found) - set(shapes)):
        problems.append(f"{shape} is not a shape the renderer uses")

    for shape, variants in sorted(found.items()):
        expected = shapes.get(shape)
        if expected is None:
            continue
        if len(variants) < MIN_VARIANTS:
            problems.append(f"{shape} has {len(variants)} variant(s); at least {MIN_VARIANTS}")
        if len(set(variants)) != len(variants):
            problems.append(f"{shape} repeats a variant")
        for index, template in enumerate(variants):
            where = f"{origin} {shape}[{index}]"
            try:
                used = placeholders(template)
            except ValueError as exc:
                problems.append(f"{where}: {exc}")
                continue
            if used != expected:
                missing = ", ".join(sorted(expected - used)) or "none"
                extra = ", ".join(sorted(used - expected)) or "none"
                problems.append(
                    f"{where}: placeholders differ from the shape's (missing: {missing}; "
                    f"not handed to this shape: {extra})"
                )
            problems.extend(
                violation.line()
                for violation in rules.lint_template(
                    template, register_of(shape), where=where, shape=shape, sentence=True
                )
            )

    fixed: dict[str, tuple[str, str]] = {}
    raw_formats = payload.get("formats")
    if not isinstance(raw_formats, Mapping):
        problems.append("section 'formats' is missing or is not a mapping")
        raw_formats = {}
    for name in sorted(set(formats) - set(raw_formats)):
        problems.append(f"formats.{name} is missing: the renderer uses it")
    for name in sorted(set(raw_formats) - set(formats)):
        problems.append(f"formats.{name} is not a format the renderer uses")
    for name, entry in sorted(raw_formats.items()):
        where = f"{origin} formats.{name}"
        if (
            not isinstance(entry, Mapping)
            or entry.get("register") not in REGISTERS
            or not isinstance(entry.get("text"), str)
        ):
            problems.append(f"{where} must be {{register: A|B|C|D, text: ...}}")
            continue
        template = entry["text"]
        if name in formats:
            try:
                used = placeholders(template)
            except ValueError as exc:
                problems.append(f"{where}: {exc}")
                continue
            if used != formats[name]:
                problems.append(f"{where}: placeholders differ from the format's")
        problems.extend(
            violation.line()
            for violation in rules.lint_template(
                template, entry["register"], where=where, shape=None, sentence=False
            )
        )
        fixed[name] = (entry["register"], template)

    nouns: dict[str, tuple[str, str]] = {}
    raw_nouns = payload.get("nouns") if isinstance(payload.get("nouns"), Mapping) else {}
    for name in sorted(NOUNS - set(raw_nouns)):
        problems.append(f"nouns.{name} is missing: the renderer counts with it")
    for name, value in sorted(raw_nouns.items()):
        pair = _pair(value, f"nouns.{name}", problems)
        if pair is not None:
            nouns[name] = pair
            for word in pair:
                problems.extend(
                    violation.line()
                    for violation in rules.lint(
                        word, "A", where=f"{origin} nouns.{name}", sentence=False
                    )
                )

    kinds: dict[str, tuple[str, str]] = {}
    raw_kinds = payload.get("kinds") if isinstance(payload.get("kinds"), Mapping) else {}
    for kind in sorted({member.value for member in ArtifactKind} - set(raw_kinds)):
        problems.append(f"kinds.{kind} is missing: every artifact kind needs a name")
    for name, value in sorted(raw_kinds.items()):
        pair = _pair(value, f"kinds.{name}", problems)
        if pair is not None:
            kinds[name] = pair

    if problems:
        raise PhraseBankError(origin, problems)
    return PhraseBank(shapes=found, formats=fixed, nouns=nouns, kinds=kinds)


def lint_bank(
    payload: Any, *, register: Register | None = None, origin: str = "bank"
) -> list[Violation]:
    """Every register violation in a bank-shaped mapping, **without** the structural checks.

    For a fragment of a bank — the suite's planted phrases — where the contract with the renderer
    is not the thing under test. Reads the four tier sections and ``narrative``.
    """
    rules = register or default_register()
    out: list[Violation] = []
    if not isinstance(payload, Mapping):
        return out
    for head in (*REGISTERS, "narrative"):
        section = payload.get(head) or {}
        for name, variants in section.items():
            shape = f"{head}.{name}"
            for index, template in enumerate(variants or ()):
                out.extend(
                    rules.lint_template(
                        template,
                        register_of(shape),
                        where=f"{origin} {shape}[{index}]",
                        shape=shape,
                        sentence=True,
                    )
                )
    return out


def load_bank(path: Path | str | None = None, *, register: Register | None = None) -> PhraseBank:
    """The bank at ``path``, or the shipped one. Refused whole if any part of it breaks a rule."""
    if path is None and register is None:
        return default_bank()
    if path is None:
        text = resources.files(__package__).joinpath(PHRASES_FILENAME).read_text(encoding="utf-8")
        origin = PHRASES_FILENAME
    else:
        text = Path(path).read_text(encoding="utf-8")
        origin = str(path)
    return parse_bank(yaml.safe_load(text), origin=origin, register=register)


@cache
def default_bank() -> PhraseBank:
    """The shipped bank, loaded and checked once."""
    text = resources.files(__package__).joinpath(PHRASES_FILENAME).read_text(encoding="utf-8")
    return parse_bank(yaml.safe_load(text))


__all__ = [
    "FORMATS",
    "MIN_VARIANTS",
    "NOUNS",
    "SHAPES",
    "PhraseBank",
    "PhraseBankError",
    "choose",
    "default_bank",
    "lint_bank",
    "load_bank",
    "parse_bank",
    "placeholders",
    "register_of",
]
