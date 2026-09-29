"""The mapping file — the one thing that differs between two shipped rubric packs.

**This module is the whole of item 04's claim, made testable.** Item 04 said *adding a rubric
costs a mapping file, not code*. Item 05 shipped two packs from two structurally unalike workbooks
— ten sheets against one, a category column against section headings sitting in the index column,
a composite key and a priority flag against neither — and the only thing that differs between them
is a file in this format. No reader was added, no branch on the instrument, no ``if`` on a
filename.

WHAT A MAPPING FILE IS. A workbook carries the criterion text and the instrument's own
identifiers, and it carries nothing at all about what Sightline can compute: no rubric workbook has
a column naming a detector, and none ever will. So an import produces a pack with **empty
bindings**, which is a finished and usable pack (its rows route to the extraction layer), and
everything an author knows *beyond the file* lives here:

* which capability tags each row is about,
* what determinacy tier each row is (SPEC §02), which the workbook cannot say either,
* whether a section may be switched off,
* the pack's identity, so regenerating it is one command with no arguments to remember.

WHY IT IS NOT JUST HAND-EDITED INTO THE PACK. The workbooks get revised (SPEC §11). If the
bindings live only in the generated file, the first revision either loses them or turns
regeneration into a merge. Keeping them in a separate authored file makes regeneration a command —
``scripts/build-packs.sh`` — and makes the revision *loud*: a mapping naming a row code the import
did not produce raises, because a criterion that has been renumbered or removed under a binding is
exactly the drift SPEC §11 is about and the worst possible outcome is for it to pass quietly.

WHY IT KEYS ON THE ROW CODE. The code is the pack's own identifier and the one a person reads —
``ACD 8``, not a spreadsheet row. A mapping keyed on position would misalign the moment a row moved,
and the reference instrument's assessment block already runs 1, 2, 3, 4, 5, **7**.

WHAT IT MAY NOT CONTAIN. Detector ids — a mapping binds concepts, so it survives a detector
refactor — and any other instrument's numbering. There is no crosswalk here in either direction:
the RSI instrument's criterion 8 binds to ``link_text`` because that is what its own text is about,
not because it matches a row of the reference instrument.

An unknown capability tag is **not** rejected here. That is deliberate and it is the asymmetry
item 04 settled: a pack binding a tag this build does not recognise is a reported warning whose row
degrades, never a hard failure, and validating it here would put a second, stricter rule in front
of the first. ``tests/test_rubric_packs.py`` asserts that the two shipped mappings use only known
tags, which catches our own typos without making the format stricter than the pack format it feeds.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..detect.base import Tier

#: Top-level keys a mapping file may carry. Anything else is a typo and is refused: a misspelled
#: ``critera:`` block would otherwise import as an unmapped pack and look like a successful run.
TOP_LEVEL_KEYS = frozenset(
    {
        "pack",
        "name",
        "version",
        "description",
        "source_title",
        "imported_at",
        "join_key_column",
        "sheet",
        "sections",
        "criteria",
    }
)

ROW_KEYS = frozenset({"tier", "bindings", "note"})
SECTION_KEYS = frozenset({"title", "optional", "enabled", "note"})


class _DuplicateKeyError(Exception):
    """A key written twice inside one YAML mapping. Internal: :func:`loads` turns it into a
    :class:`MappingError` naming the key and the line."""

    def __init__(self, key: object, line: int) -> None:
        super().__init__(f"{key!r} at line {line}")
        self.key = key
        self.line = line


class _StrictLoader(yaml.SafeLoader):
    """``yaml.SafeLoader``, except that a key written twice is refused rather than overwritten.

    **YAML permits a duplicated key and PyYAML keeps the last one silently.** In a hand-edited
    mapping file 68 rows long that is the worst kind of wrongness: writing ``ACD 5:`` a second time
    — a paste into the wrong block, a row moved between categories — drops the first entry with its
    tier, its bindings and its note, and the pack then imports, validates and ships with a row
    nobody meant to change. Nothing downstream can notice, because the row is present and the
    counts are right.

    The format already refuses a tag listed twice inside one row, for the same reason. This closes
    the same hole one level up, and it applies to every mapping in the file rather than only the
    row codes: a duplicated ``criteria:`` block is the same mistake at the top level.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in seen
                seen.add(key)
            except TypeError:
                continue  # an unhashable key; the base class reports that one properly
            if duplicate:
                raise _DuplicateKeyError(key, key_node.start_mark.line + 1)
        return super().construct_mapping(node, deep=deep)


class MappingError(ValueError):
    """A mapping file could not be read, or disagrees with the workbook it was written against."""

    def __init__(self, message: str, *, origin: str) -> None:
        super().__init__(f"{origin}: {message}")
        self.origin = origin


@dataclass(frozen=True, kw_only=True, slots=True)
class RowMapping:
    """What an author knows about one row that the workbook does not say."""

    tier: Tier | None = None
    bindings: tuple[str, ...] = ()
    note: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class SectionMapping:
    """An override for one section of the instrument. ``optional`` is item 08's render flag."""

    title: str | None = None
    optional: bool | None = None
    enabled: bool | None = None
    note: str | None = None


@dataclass(frozen=True, kw_only=True, slots=True)
class PackMapping:
    """One authored mapping file, parsed.

    Every field is optional except the rows themselves, because the format has to serve an
    instrument that carries none of the reference one's identifiers. A mapping that says nothing
    but ``criteria: {}`` is legal and produces exactly what a bare import produces.
    """

    origin: str
    pack_id: str | None = None
    name: str | None = None
    version: str | None = None
    description: str | None = None
    source_title: str | None = None
    sheet: str | None = None
    #: Fixed in the file rather than taken from the clock, so regenerating a pack is byte-identical
    #: and a diff shows a change to the instrument rather than a change of date.
    imported_at: dt.date | None = None
    #: The column whose value identifies a row across the workbook's sheets and across a re-import.
    #: Declared rather than guessed — see :mod:`sightline.rubric.importer` for why the reference
    #: instrument's category-and-index pair is not one.
    join_key_column: str | None = None
    sections: Mapping[str, SectionMapping] = field(default_factory=dict)
    criteria: Mapping[str, RowMapping] = field(default_factory=dict)

    def statement(self) -> str:
        bound = sum(1 for row in self.criteria.values() if row.bindings)
        tags = {tag for row in self.criteria.values() for tag in row.bindings}
        return (
            f"{self.origin}: {len(self.criteria)} row(s) mapped, {bound} of them bound to "
            f"{len(tags)} distinct capability tag(s); {len(self.sections)} section override(s)."
        )


def loads(text: str, *, origin: str = "<mapping>") -> PackMapping:
    """Parse a mapping file from YAML text."""
    try:
        payload = yaml.load(text, Loader=_StrictLoader)
    except _DuplicateKeyError as exc:
        raise MappingError(
            f"{exc.key!r} is written twice (line {exc.line}). YAML keeps only the last one, so "
            "the earlier block — its tier, its bindings, its note — would be dropped without a "
            "word. Merge the two into one.",
            origin=origin,
        ) from exc
    except yaml.YAMLError as exc:
        raise MappingError(f"not valid YAML: {exc}", origin=origin) from exc
    if payload is None:
        raise MappingError("the file is empty", origin=origin)
    if not isinstance(payload, dict):
        raise MappingError("the top level must be a mapping of keys to values", origin=origin)

    unknown = sorted(set(payload) - TOP_LEVEL_KEYS)
    if unknown:
        raise MappingError(
            f"unrecognised top-level key(s): {', '.join(unknown)}. Known keys are "
            f"{', '.join(sorted(TOP_LEVEL_KEYS))}.",
            origin=origin,
        )

    return PackMapping(
        origin=origin,
        pack_id=_text(payload.get("pack"), "pack", origin=origin),
        name=_text(payload.get("name"), "name", origin=origin),
        version=_version(payload.get("version"), origin=origin),
        description=_text(payload.get("description"), "description", origin=origin),
        source_title=_text(payload.get("source_title"), "source_title", origin=origin),
        sheet=_text(payload.get("sheet"), "sheet", origin=origin),
        imported_at=_date(payload.get("imported_at"), origin=origin),
        join_key_column=_column(payload.get("join_key_column"), origin=origin),
        sections=_sections(payload.get("sections"), origin=origin),
        criteria=_criteria(payload.get("criteria"), origin=origin),
    )


def load(path: str | Path) -> PackMapping:
    """Read a mapping file. The filename is the origin in every error message."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise MappingError(f"cannot be read: {exc}", origin=target.name) from exc
    return loads(text, origin=target.name)


def dumps(mapping: PackMapping) -> str:
    """A mapping as YAML text that :func:`loads` reads back to the same mapping.

    Written for roadmap item 22, whose accepted binding proposals come out as a mapping file so
    that they become a pack through ``import --map`` — the path that is already tested — rather
    than through a second writer of packs. Only what the format carries is written: an authored
    file's comments are not data, and a mapping produced here has none of them.

    Every row code is written as a string key and every field in a fixed order, so two dumps of one
    mapping are byte-identical and a diff between two of them shows only what changed.
    """
    payload: dict[str, Any] = {}
    for key, value in (
        ("pack", mapping.pack_id),
        ("name", mapping.name),
        ("version", mapping.version),
        ("description", mapping.description),
        ("source_title", mapping.source_title),
        ("imported_at", mapping.imported_at),
        ("sheet", mapping.sheet),
        ("join_key_column", mapping.join_key_column),
    ):
        if value is not None:
            payload[key] = value
    if mapping.sections:
        payload["sections"] = {
            str(code): _section_payload(section) for code, section in mapping.sections.items()
        }
    payload["criteria"] = {
        str(code): _row_payload(row) for code, row in mapping.criteria.items()
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100)


def _section_payload(section: SectionMapping) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("title", "optional", "enabled", "note"):
        value = getattr(section, key)
        if value is not None:
            out[key] = value
    return out


def _row_payload(row: RowMapping) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if row.tier is not None:
        out["tier"] = row.tier.value
    out["bindings"] = list(row.bindings)
    if row.note is not None:
        out["note"] = row.note
    return out


# ---------------------------------------------------------------------------------------------
# Field readers. Each one names the offending key, because a mapping file is edited by hand.
# ---------------------------------------------------------------------------------------------


def _text(value: Any, key: str, *, origin: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MappingError(f"{key} must be a non-empty string", origin=origin)
    return value.strip()


def _version(value: Any, *, origin: str) -> str | None:
    """A version is a string. ``3.1.3`` is not a number and ``2026.1`` must not become ``2026.1``.

    YAML reads an unquoted ``2026.1`` as a float, which then renders as ``2026.1`` by luck and as
    something else the moment a version ends in a zero. Refusing it here is a one-line fix in the
    mapping file and saves a pack whose version silently changed.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    raise MappingError(
        f"version must be quoted as a string, not read as {type(value).__name__}: an unquoted "
        "2026.10 becomes 2026.1",
        origin=origin,
    )


def _date(value: Any, *, origin: str) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value.strip())
        except ValueError as exc:
            raise MappingError(f"imported_at is not a date: {value!r}", origin=origin) from exc
    raise MappingError(f"imported_at must be a date, not {type(value).__name__}", origin=origin)


def _column(value: Any, *, origin: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip().isalpha():
        raise MappingError(
            f"join_key_column must be a spreadsheet column letter such as F, not {value!r}",
            origin=origin,
        )
    return value.strip().upper()


def _sections(value: Any, *, origin: str) -> Mapping[str, SectionMapping]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MappingError("sections must be a mapping of section code to overrides", origin=origin)
    out: dict[str, SectionMapping] = {}
    for code, entry in value.items():
        label = f"sections[{code!r}]"
        if not isinstance(entry, dict):
            raise MappingError(f"{label} must be a mapping", origin=origin)
        unknown = sorted(set(entry) - SECTION_KEYS)
        if unknown:
            raise MappingError(
                f"{label} has unrecognised key(s): {', '.join(unknown)}", origin=origin
            )
        out[str(code)] = SectionMapping(
            title=_text(entry.get("title"), f"{label}.title", origin=origin),
            optional=_flag(entry.get("optional"), f"{label}.optional", origin=origin),
            enabled=_flag(entry.get("enabled"), f"{label}.enabled", origin=origin),
            note=_text(entry.get("note"), f"{label}.note", origin=origin),
        )
    return out


def _flag(value: Any, key: str, *, origin: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise MappingError(f"{key} must be true or false", origin=origin)
    return value


def _criteria(value: Any, *, origin: str) -> Mapping[str, RowMapping]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MappingError(
            "criteria must be a mapping of row code to what is known about that row", origin=origin
        )
    out: dict[str, RowMapping] = {}
    for code, entry in value.items():
        label = f"criteria[{code!r}]"
        if entry is None:
            out[str(code)] = RowMapping()
            continue
        if not isinstance(entry, dict):
            raise MappingError(f"{label} must be a mapping", origin=origin)
        unknown = sorted(set(entry) - ROW_KEYS)
        if unknown:
            raise MappingError(
                f"{label} has unrecognised key(s): {', '.join(unknown)}. Known keys are "
                f"{', '.join(sorted(ROW_KEYS))}.",
                origin=origin,
            )
        out[str(code)] = RowMapping(
            tier=_tier(entry.get("tier"), label, origin=origin),
            bindings=_bindings(entry.get("bindings"), label, origin=origin),
            note=_text(entry.get("note"), f"{label}.note", origin=origin),
        )
    return out


def _tier(value: Any, label: str, *, origin: str) -> Tier | None:
    if value is None:
        return None
    try:
        return Tier(str(value).strip().upper())
    except ValueError as exc:
        letters = ", ".join(tier.value for tier in Tier)
        raise MappingError(
            f"{label}.tier is {value!r}; it must be one of {letters} (SPEC §02)", origin=origin
        ) from exc


def _bindings(value: Any, label: str, *, origin: str) -> tuple[str, ...]:
    """Capability tags, in the order written. An unknown tag is NOT refused here — see the header.

    Duplicates are refused, because a tag listed twice would contribute its detector's effort
    twice into a sum that is then banded, and a rating that doubles on a copy-paste is the sort of
    arithmetic error nobody notices.
    """
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, list | tuple):
        raise MappingError(
            f"{label}.bindings must be a list of capability tags, even for one tag", origin=origin
        )
    tags: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise MappingError(
                f"{label}.bindings holds {item!r}, which is not a tag", origin=origin
            )
        tag = item.strip()
        if tag in tags:
            raise MappingError(f"{label}.bindings lists {tag!r} twice", origin=origin)
        tags.append(tag)
    return tuple(tags)


__all__ = [
    "MappingError",
    "PackMapping",
    "RowMapping",
    "SectionMapping",
    "dumps",
    "load",
    "loads",
]
