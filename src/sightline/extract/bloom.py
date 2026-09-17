"""The task-verb table. A lookup, in code, that a reviewer can read in a minute and argue with.

This is the third step of the pattern SPEC §01 sets out for every subjective criterion, and it is
the step that is deliberately *not* a model:

1. The model extracts the sentences stating what a learner must produce.
2. The verb is taken from each sentence **here**, by a word match against the table below.
3. The distribution is counted **here**, by arithmetic.
4. A human reads it.

The model extracted, a lookup table judged, a human decides. Nothing in this file asks a model
anything, and nothing in it produces a rating.

**A DISTRIBUTION IS NOT A SCORE, AND A HIGHER LEVEL IS NOT A BETTER ONE.** A first-semester course
whose activities sit at *remember* and *understand* is doing what a first-semester course does. The
criterion this feeds asks a human whether the balance fits the course's purpose, and the artifact
they are handed is a count with every item linked to the sentence it came from — never an average,
never a label, never a judgement of a course or of the person who built it.

**A verb that is not in the table is reported, not dropped.** :func:`classify` returns ``None`` and
the sentence is still carried, counted under "no table verb". A silent drop would understate the
number of activities, and an understated count on a page that looks complete is the silent
under-detection SPEC §11 is about — the same reason a detector reports what it examined.

**The table is the revised (Anderson and Krathwohl) taxonomy's six levels**, with the verbs that
actually appear in course text rather than the whole published list. It is small on purpose: every
entry is one a reviewer can be shown and disagree with. Order matters in one place only — where a
sentence carries verbs from two levels, the *first* one in the sentence is taken, because that is
the one stating the task ("analyze the two proposals and list your conclusions").
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The six levels, lowest to highest as the taxonomy orders them. "Lowest" and "highest" are the
#: taxonomy's words for cognitive complexity and carry no judgement about a course.
LEVELS: tuple[str, ...] = ("remember", "understand", "apply", "analyze", "evaluate", "create")

#: Verbs per level. Both spellings of anything that has two, because a course written in either is
#: the same course and matching only one of them would report the other as having no task verb.
TABLE: dict[str, tuple[str, ...]] = {
    "remember": (
        "define",
        "describe",  # in its "state what it is" sense; the taxonomy puts it here
        "identify",
        "label",
        "list",
        "match",
        "name",
        "recall",
        "recognise",
        "recognize",
        "record",
        "repeat",
        "state",
    ),
    "understand": (
        "classify",
        "discuss",
        "explain",
        "illustrate",
        "interpret",
        "outline",
        "paraphrase",
        "restate",
        "review",
        "summarise",
        "summarize",
        "translate",
    ),
    "apply": (
        "apply",
        "calculate",
        "carry out",
        "compute",
        "demonstrate",
        "employ",
        "execute",
        "implement",
        "practise",
        "practice",
        "solve",
        "use",
    ),
    "analyze": (
        "analyse",
        "analyze",
        "break down",
        "categorise",
        "categorize",
        "compare",
        "contrast",
        "differentiate",
        "distinguish",
        "examine",
        "investigate",
        "organise",
        "organize",
    ),
    "evaluate": (
        "appraise",
        "argue",
        "assess",
        "critique",
        "defend",
        "evaluate",
        "judge",
        "justify",
        "rank",
        "rate",
        "recommend",
        "select",
    ),
    "create": (
        "author",
        "build",
        "compose",
        "construct",
        "create",
        "design",
        "develop",
        "devise",
        "draft",
        "formulate",
        "generate",
        "plan",
        "produce",
        "propose",
        "write",
    ),
}

#: Forms the rules below get wrong. Listed rather than inferred, because a rule that turns "write"
#: into "writed" matches nothing and reports a sentence as having no task verb.
IRREGULAR: dict[str, tuple[str, ...]] = {
    "write": ("wrote", "written", "writing"),
    "build": ("built", "building"),
    "draw": ("drew", "drawn", "drawing"),
    "choose": ("chose", "chosen", "choosing"),
    "plan": ("planned", "planning"),
    "carry out": ("carried out", "carries out", "carrying out"),
    "break down": ("broke down", "broken down", "breaks down", "breaking down"),
}


@dataclass(frozen=True, slots=True)
class VerbMatch:
    """The task verb found in one sentence, and where in the sentence it was."""

    #: The word as written in the sentence, e.g. ``analyzing``.
    written: str
    #: The table entry it matched, e.g. ``analyze``.
    verb: str
    level: str
    #: Offset into the text that was classified.
    offset: int


def classify(text: str) -> VerbMatch | None:
    """The first table verb in ``text``, or ``None`` when it carries none.

    ``None`` is an ordinary answer. "Read chapter three" states a task with no verb in the table,
    and reporting it as *create* because "read" was not found would be worse than saying so.
    """
    match = _pattern().search(text or "")
    if match is None:
        return None
    written = match.group(0)
    return VerbMatch(
        written=written,
        verb=_forms()[written.lower()],
        level=_levels()[_forms()[written.lower()]],
        offset=match.start(),
    )


def level_of(verb: str) -> str | None:
    """The level a verb sits at, or ``None`` when the table does not carry it."""
    return _levels().get(verb.lower())


def distribution(levels: list[str | None]) -> dict[str, int]:
    """Counts per level, in taxonomy order, with every level present even at zero.

    Zeros are kept because "0 evaluate, 0 create" is the observation a reviewer is being shown;
    omitting an empty level would leave them counting which ones are missing.
    """
    tally = dict.fromkeys(LEVELS, 0)
    unclassified = 0
    for level in levels:
        if level is None:
            unclassified += 1
        else:
            tally[level] = tally.get(level, 0) + 1
    tally["no table verb"] = unclassified
    return tally


def _inflections(verb: str) -> tuple[str, ...]:
    """Every written form of one table entry. Regular rules, plus :data:`IRREGULAR`."""
    forms = {verb}
    head, _, tail = verb.partition(" ")
    suffix = f" {tail}" if tail else ""
    if head.endswith("e"):
        stem = head[:-1]
        forms.update({f"{head}s{suffix}", f"{head}d{suffix}", f"{stem}ing{suffix}"})
    elif len(head) > 2 and head.endswith("y") and head[-2] not in "aeiou":
        stem = head[:-1]
        forms.update({f"{stem}ies{suffix}", f"{stem}ied{suffix}", f"{head}ing{suffix}"})
    else:
        forms.update({f"{head}s{suffix}", f"{head}ed{suffix}", f"{head}ing{suffix}"})
    forms.update(IRREGULAR.get(verb, ()))
    return tuple(sorted(forms))


def _forms() -> dict[str, str]:
    """Every written form, mapped to its table entry."""
    global _FORMS
    if _FORMS is None:
        built: dict[str, str] = {}
        for verbs in TABLE.values():
            for verb in verbs:
                for form in _inflections(verb):
                    built.setdefault(form, verb)
        _FORMS = built
    return _FORMS


def _levels() -> dict[str, str]:
    global _VERB_LEVELS
    if _VERB_LEVELS is None:
        _VERB_LEVELS = {verb: level for level, verbs in TABLE.items() for verb in verbs}
    return _VERB_LEVELS


def _pattern() -> re.Pattern[str]:
    global _PATTERN
    if _PATTERN is None:
        # Longest first, so "carry out" wins over "carry" would if it were in the table, and
        # "break down" over "break".
        forms = sorted(_forms(), key=lambda form: (-len(form), form))
        _PATTERN = re.compile(r"\b(?:" + "|".join(re.escape(form) for form in forms) + r")\b", re.I)
    return _PATTERN


_FORMS: dict[str, str] | None = None
_VERB_LEVELS: dict[str, str] | None = None
_PATTERN: re.Pattern[str] | None = None


__all__ = ["IRREGULAR", "LEVELS", "TABLE", "VerbMatch", "classify", "distribution", "level_of"]
