"""The register lint: the check that Sightline's sentences say what their tier allows and no more.

**This is item 15's real deliverable.** The phrase bank can be edited by anyone, and the renderer can
be extended by any later item; neither is safe unless something reads every sentence and refuses the
ones that drift. The word lists are data, in ``register.yaml`` beside this module, so the voice can
be tightened without touching this file or the test that runs it.

The rules, per SPEC §08 and §11:

``hedge``
    A computed count is not hedged. Tier A states a count and Tier D states a limit; "appears" or
    "may" in either is inaccuracy, not politeness.
``verdict``
    A Tier C sentence poses a question. "Insufficient" or "should be" in one has answered it.
``handoff``
    A Tier C *sentence* must hand the call to the reviewer in so many words.
``grading``
    No grading vocabulary anywhere, in any tier.
``aggregate``
    No whole-course judgement anywhere, however phrased — including a count of rows over a total,
    which is a compliance percentage with the division left undone.
``clean_wording``
    The shapes that describe a row nothing computed may never be worded like a clean result. This
    is where silent under-detection would re-enter the product at the last step before a person
    reads it (roadmap item 04).
``required``
    Wording a shape must carry in every variant.

**What is read, and what is not.** Only the words Sightline wrote. Course text quoted verbatim, an
artifact's title, a value a detector measured and the instrument's own labels are *spans* the
renderer records by position as it inserts them, and exactly those positions are masked. Nothing is
masked by pattern: a curly-quoted phrase typed into the bank is Sightline's own wording and is
linted like the rest, and a masked span never hides the same word written elsewhere in the sentence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

#: The shipped word lists, beside this module.
REGISTER_FILENAME = "register.yaml"

#: The registers a sentence can carry: the four tiers' own.
REGISTERS: tuple[str, ...] = ("A", "B", "C", "D")

#: What a masked span is replaced with. A word-break on both sides, so masking never fuses two words
#: into a token that is on a list.
MASK = " … "

#: A placeholder in a bank variant. Replaced with :data:`MASK` when a variant is linted raw.
_PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


class RegisterConfigError(ValueError):
    """``register.yaml`` is malformed. Raised at load time."""


@dataclass(frozen=True, kw_only=True, slots=True)
class Violation:
    """One sentence breaking one rule. Says which rule, which word, and where."""

    rule: str
    token: str
    where: str
    text: str

    def line(self) -> str:
        return f"{self.where}: {self.rule} rule — {self.token!r} in {self.text!r}"


@dataclass(frozen=True, kw_only=True, slots=True)
class _Rule:
    applies_to: frozenset[str]
    patterns: tuple[tuple[str, re.Pattern[str]], ...]


@dataclass(frozen=True, kw_only=True, slots=True)
class Register:
    """The loaded word lists. Built once from ``register.yaml``; a test may build its own."""

    hedge: _Rule
    verdict: _Rule
    handoff: _Rule
    grading: _Rule
    aggregate: _Rule
    clean_forbidden_in: frozenset[str]
    clean: tuple[tuple[str, re.Pattern[str]], ...]
    required: Mapping[str, tuple[tuple[str, re.Pattern[str]], ...]] = field(default_factory=dict)

    def lint(
        self,
        text: str,
        register: str,
        *,
        where: str,
        shape: str | None = None,
        sentence: bool = True,
        spans: Sequence[tuple[int, int]] = (),
    ) -> list[Violation]:
        """Every rule ``text`` breaks, read in ``register``.

        ``spans`` are ``(start, end)`` positions of verbatim text — quotes, titles, measured values,
        instrument labels — and are masked before any rule is applied. ``sentence`` is False for a
        fixed fragment (a list separator, a place format), which is exempt only from ``handoff``:
        a fragment is not where a call is handed over. ``shape`` is the bank shape the text came
        from, for the two rules that are scoped to shapes rather than to registers.
        """
        if register not in REGISTERS:
            raise ValueError(f"unknown register {register!r}; expected one of {REGISTERS}")
        read = mask(text, spans)
        found: list[Violation] = []

        def hits(rule_name: str, patterns: Iterable[tuple[str, re.Pattern[str]]]) -> None:
            for token, pattern in patterns:
                if pattern.search(read):
                    found.append(Violation(rule=rule_name, token=token, where=where, text=text))

        for name in ("hedge", "verdict", "grading", "aggregate"):
            rule: _Rule = getattr(self, name)
            if register in rule.applies_to:
                hits(name, rule.patterns)
        if sentence and register in self.handoff.applies_to:
            if not any(pattern.search(read) for _, pattern in self.handoff.patterns):
                found.append(
                    Violation(
                        rule="handoff",
                        token="(none of the handoff phrases)",
                        where=where,
                        text=text,
                    )
                )
        if shape is not None and shape in self.clean_forbidden_in:
            hits("clean_wording", self.clean)
        if shape is not None and shape in self.required:
            wanted = self.required[shape]
            if not any(pattern.search(read) for _, pattern in wanted):
                found.append(
                    Violation(
                        rule="required",
                        token=" | ".join(token for token, _ in wanted),
                        where=where,
                        text=text,
                    )
                )
        return found

    def lint_template(
        self, template: str, register: str, *, where: str, shape: str | None, sentence: bool
    ) -> list[Violation]:
        """Lint a bank variant as written: every ``{placeholder}`` is masked, the words are not."""
        spans = [match.span() for match in _PLACEHOLDER.finditer(template)]
        return self.lint(template, register, where=where, shape=shape, sentence=sentence, spans=spans)


def mask(text: str, spans: Sequence[tuple[int, int]]) -> str:
    """``text`` with exactly the given positions replaced by :data:`MASK`."""
    if not spans:
        return text
    out: list[str] = []
    cursor = 0
    for start, end in sorted(spans):
        if start < cursor or end < start or end > len(text):
            raise ValueError(f"span {(start, end)} is out of order or outside a {len(text)}-char text")
        out.append(text[cursor:start])
        out.append(MASK)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def _word_pattern(token: str) -> re.Pattern[str]:
    words = token.split()
    if not words:
        raise RegisterConfigError("an empty token")
    body = r"\s+".join(re.escape(word) for word in words)
    return re.compile(rf"(?<![\w]){body}(?![\w])", re.IGNORECASE)


def _tokens(entry: Mapping[str, Any], name: str) -> tuple[tuple[str, re.Pattern[str]], ...]:
    tokens = entry.get("tokens")
    if not isinstance(tokens, list) or not tokens or not all(isinstance(t, str) for t in tokens):
        raise RegisterConfigError(f"{name}.tokens must be a non-empty list of strings")
    return tuple((token, _word_pattern(token)) for token in tokens)


def _applies_to(entry: Mapping[str, Any], name: str) -> frozenset[str]:
    applies = entry.get("applies_to")
    if not isinstance(applies, list) or not applies or not set(applies) <= set(REGISTERS):
        raise RegisterConfigError(f"{name}.applies_to must be a non-empty list drawn from {REGISTERS}")
    return frozenset(applies)


def parse_register(payload: Any, *, origin: str = REGISTER_FILENAME) -> Register:
    """Build a :class:`Register` from the parsed YAML, refusing anything malformed by name."""
    if not isinstance(payload, Mapping):
        raise RegisterConfigError(f"{origin}: the top level must be a mapping")
    try:
        rules: dict[str, _Rule] = {}
        for name in ("hedge", "verdict", "handoff", "grading"):
            entry = payload[name]
            rules[name] = _Rule(
                applies_to=_applies_to(entry, name), patterns=_tokens(entry, name)
            )
        aggregate = payload["aggregate"]
        patterns = aggregate.get("patterns")
        if not isinstance(patterns, list) or not patterns:
            raise RegisterConfigError("aggregate.patterns must be a non-empty list")
        rules["aggregate"] = _Rule(
            applies_to=_applies_to(aggregate, "aggregate"),
            patterns=tuple((p, re.compile(p, re.IGNORECASE)) for p in patterns),
        )
        clean = payload["clean_wording"]
        forbidden_in = clean.get("forbidden_in")
        if not isinstance(forbidden_in, list) or not forbidden_in:
            raise RegisterConfigError("clean_wording.forbidden_in must be a non-empty list")
        required_raw = payload.get("required") or {}
        if not isinstance(required_raw, Mapping):
            raise RegisterConfigError("required must map a shape to a list of phrases")
        required = {
            shape: tuple((token, _word_pattern(token)) for token in tokens)
            for shape, tokens in required_raw.items()
        }
    except KeyError as exc:
        raise RegisterConfigError(f"{origin}: missing section {exc.args[0]!r}") from exc
    except RegisterConfigError as exc:
        raise RegisterConfigError(f"{origin}: {exc}") from exc
    except re.error as exc:
        raise RegisterConfigError(f"{origin}: a pattern does not compile: {exc}") from exc
    return Register(
        hedge=rules["hedge"],
        verdict=rules["verdict"],
        handoff=rules["handoff"],
        grading=rules["grading"],
        aggregate=rules["aggregate"],
        clean_forbidden_in=frozenset(forbidden_in),
        clean=_tokens(clean, "clean_wording"),
        required=required,
    )


def load_register(path: Path | str | None = None) -> Register:
    """The word lists from ``path``, or the shipped ``register.yaml``."""
    if path is None:
        return default_register()
    source = Path(path)
    return parse_register(yaml.safe_load(source.read_text(encoding="utf-8")), origin=str(source))


@cache
def default_register() -> Register:
    """The shipped word lists. Read once."""
    shipped = resources.files(__package__).joinpath(REGISTER_FILENAME)
    return parse_register(yaml.safe_load(shipped.read_text(encoding="utf-8")))


__all__ = [
    "MASK",
    "REGISTERS",
    "Register",
    "RegisterConfigError",
    "Violation",
    "default_register",
    "load_register",
    "mask",
    "parse_register",
]
