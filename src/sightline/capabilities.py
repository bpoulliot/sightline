"""The capability-tag vocabulary: the one thing detectors and rubric packs both know about.

**Why this module belongs to neither side.** A detector declares a capability tag and never names
a criterion. A pack binds a row to tags and never names a detector. Both therefore need the
vocabulary, and neither owns it — so it sits at the top of the package, importable from
:mod:`sightline.detect` and :mod:`sightline.rubric` alike, and importing nothing from either. That
import direction is load-bearing: the detector interface validates a tag at class-definition time,
which it could not do if the vocabulary lived downstream of it.

**The data file is the vocabulary; this module only reads it.** ``capabilities.yaml`` sits beside
this file, hand-authored and commented, and adding a concept is an edit to it rather than to code.
It is read through :mod:`importlib.resources`, so it travels with the package into a wheel and into
the container image rather than being found by walking up from ``__file__`` to a repository that may
not be there.

**Validation is asymmetric, deliberately** (the item 04 decision, recorded in ``docs/ROADMAP.md``):

* :func:`require` — what the detector interface calls. An unknown tag raises
  :class:`UnknownCapabilityTag`. We write the detectors, so a typo in one is our bug and it stops
  the build.
* :func:`known` — what the pack loader calls. An unknown tag is reported and the row it was bound
  to degrades to ``NO_DETERMINISTIC_COVERAGE``. Packs are third-party, and an uploaded rubric must
  never be able to hard-fail Sightline.

**A tag with no detector behind it is an ordinary entry here.** The vocabulary names concepts; which
of them any detector implements is computed from the registry at runtime (see
:class:`sightline.rubric.project.TagIndex`). That is what makes ``NOT_YET_IMPLEMENTED`` a state the
data model can carry rather than a gap nobody notices.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

#: The data file, beside this module.
VOCABULARY_FILENAME = "capabilities.yaml"

#: What a tag may be spelled with. Lowercase, underscores, no numbering, no punctuation — a tag is
#: read by a non-programmer in a pack file and typed by hand.
TAG_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


class CapabilityVocabularyError(RuntimeError):
    """The vocabulary file itself is wrong. A packaging or authoring bug, never a user's."""


class UnknownCapabilityTag(ValueError):
    """A detector declared a tag the vocabulary does not carry.

    Raised for detectors only. A *pack* naming an unknown tag is reported instead — see the module
    docstring for why the two directions are not symmetrical.
    """

    def __init__(self, tag: str, *, declared_by: str, suggestions: Iterable[str] = ()) -> None:
        self.tag = tag
        self.declared_by = declared_by
        self.suggestions = tuple(suggestions)
        hint = f" Did you mean {', '.join(self.suggestions)}?" if self.suggestions else ""
        super().__init__(
            f"{declared_by} declares the capability tag {tag!r}, which is not in the vocabulary "
            f"at sightline/{VOCABULARY_FILENAME}.{hint} Add the concept to that file, or use an "
            "existing tag. A detector is not allowed to invent one: a tag nothing recognises is a "
            "row no pack can bind, and it fails silently rather than loudly."
        )


@dataclass(frozen=True, kw_only=True, slots=True)
class Capability:
    """One course-quality concept, in the words a reviewer would use."""

    tag: str
    group: str
    #: What a finding under this tag is about. A sentence, not a label.
    summary: str

    def to_dict(self) -> dict[str, str]:
        return {"tag": self.tag, "group": self.group, "summary": self.summary}


@dataclass(frozen=True, kw_only=True, slots=True)
class Vocabulary:
    """The closed set of capability tags, and the groups they are filed under."""

    version: int
    #: Group name -> what the group covers. Present so fifty tags can be browsed by a human.
    groups: Mapping[str, str]
    #: Tag -> capability, in tag order.
    capabilities: Mapping[str, Capability]

    def __contains__(self, tag: object) -> bool:
        return isinstance(tag, str) and tag in self.capabilities

    def __len__(self) -> int:
        return len(self.capabilities)

    @property
    def tags(self) -> tuple[str, ...]:
        return tuple(self.capabilities)

    def get(self, tag: str) -> Capability | None:
        return self.capabilities.get(tag)

    def by_group(self) -> Mapping[str, tuple[Capability, ...]]:
        grouped: dict[str, list[Capability]] = {name: [] for name in self.groups}
        for capability in self.capabilities.values():
            grouped.setdefault(capability.group, []).append(capability)
        return {name: tuple(entries) for name, entries in grouped.items()}

    def suggestions(self, tag: str, *, limit: int = 3) -> tuple[str, ...]:
        """Tags that look like ``tag``, for an error message worth reading.

        Substring either way round, which catches the mistakes actually made — a plural, a prefix,
        a near-miss on a compound name — without pulling in a string-distance library for it.
        """
        needle = (tag or "").strip().lower()
        if not needle:
            return ()
        near = [
            candidate
            for candidate in self.capabilities
            if needle in candidate or candidate in needle
        ]
        if not near:
            # Fall back to the leading word, either way round, which is what catches a plural or a
            # differently-compounded name: `tables` -> `table_semantics`.
            head = needle.split("_", 1)[0]
            near = [
                candidate
                for candidate in self.capabilities
                if candidate.startswith(head) or head.startswith(candidate.split("_", 1)[0])
            ]
        return tuple(near[:limit])

    def statement(self) -> str:
        """Counts, declaratively — what the vocabulary holds, per group."""
        parts = ", ".join(
            f"{len(entries)} {name}" for name, entries in self.by_group().items() if entries
        )
        return f"{len(self)} capability tag(s) in {len(self.groups)} group(s): {parts}."


def _parse(payload: Any, *, origin: str) -> Vocabulary:
    if not isinstance(payload, dict):
        raise CapabilityVocabularyError(f"{origin}: the vocabulary must be a mapping")

    version = payload.get("version")
    if not isinstance(version, int):
        raise CapabilityVocabularyError(f"{origin}: 'version' must be an integer")

    raw_groups = payload.get("groups")
    if not isinstance(raw_groups, dict) or not raw_groups:
        raise CapabilityVocabularyError(f"{origin}: 'groups' must be a non-empty mapping")
    groups = {str(name): str(text) for name, text in raw_groups.items()}

    raw_tags = payload.get("tags")
    if not isinstance(raw_tags, list) or not raw_tags:
        raise CapabilityVocabularyError(f"{origin}: 'tags' must be a non-empty list")

    capabilities: dict[str, Capability] = {}
    for position, entry in enumerate(raw_tags):
        where = f"{origin}: tags[{position}]"
        if not isinstance(entry, dict):
            raise CapabilityVocabularyError(f"{where} must be a mapping")
        tag = entry.get("tag")
        if not isinstance(tag, str) or not TAG_PATTERN.match(tag):
            raise CapabilityVocabularyError(f"{where}: 'tag' must be lower_snake_case, got {tag!r}")
        if tag in capabilities:
            raise CapabilityVocabularyError(f"{where}: {tag!r} is declared twice")
        group = entry.get("group")
        if group not in groups:
            raise CapabilityVocabularyError(
                f"{where}: group {group!r} is not one of {sorted(groups)}"
            )
        summary = entry.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise CapabilityVocabularyError(f"{where}: 'summary' is required and must be text")
        capabilities[tag] = Capability(tag=tag, group=str(group), summary=" ".join(summary.split()))

    return Vocabulary(
        version=version,
        groups=groups,
        capabilities=dict(sorted(capabilities.items())),
    )


def parse_vocabulary(text: str, *, origin: str = "<string>") -> Vocabulary:
    """Parse a vocabulary from YAML text. Used by the suite to check the parser itself."""
    return _parse(yaml.safe_load(text), origin=origin)


@lru_cache(maxsize=1)
def vocabulary() -> Vocabulary:
    """The shipped vocabulary, read once.

    Cached because the detector interface consults it for every detector class defined, which
    happens at import time.
    """
    source = resources.files(__package__).joinpath(VOCABULARY_FILENAME)
    return parse_vocabulary(source.read_text(encoding="utf-8"), origin=VOCABULARY_FILENAME)


def known(tag: str) -> bool:
    """Whether ``tag`` is in the vocabulary. The soft check — what the pack loader uses."""
    return tag in vocabulary()


def require(tag: str, *, declared_by: str) -> str:
    """Return ``tag`` if the vocabulary carries it; raise otherwise. The detector-side check."""
    if not isinstance(tag, str) or not tag:
        raise UnknownCapabilityTag(str(tag), declared_by=declared_by)
    current = vocabulary()
    if tag not in current:
        raise UnknownCapabilityTag(
            tag, declared_by=declared_by, suggestions=current.suggestions(tag)
        )
    return tag


__all__ = [
    "TAG_PATTERN",
    "VOCABULARY_FILENAME",
    "Capability",
    "CapabilityVocabularyError",
    "UnknownCapabilityTag",
    "Vocabulary",
    "known",
    "parse_vocabulary",
    "require",
    "vocabulary",
]
