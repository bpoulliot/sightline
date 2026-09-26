"""The rubric pack: its schema, its model, and the loader that refuses a bad one by path.

**Sightline reviews the course. The rubric is only the lens.** Detectors emit facts about course
artifacts — "12 images have no alt text, at these locations". A pack decides how those facts are
*reported and rated*. UCCS OSCQR 3.1.3 is the reference instrument, not the schema and not a
required intermediate: Quality Matters, an internal departmental checklist, or an instrument
written last week loads through this module unchanged and gets a working review. Nothing here
requires a pack to be an OSCQR derivative, to carry an OPEN SUNY number, or to be crosswalked to
3.1.3, and if some future step would require that, the step is wrong.

**Three things live in the pack, and one thing deliberately does not.**

*In:* the rows with the instrument's own identifiers; the sections, each with an optional-render
flag; and the effort **bands**, because ≤ ½ hour / ½–2 hours / 2 hours+ are printed on the
instrument itself (SPEC §07). *Out:* the per-defect costs that are summed into those bands. Those
are one global effort model calibrated once (:mod:`sightline.rubric.effort`), because sixty-eight
per-row cost tables will not converge against a handful of completed reviews.

**Bindings bind to capability TAGS, not detector ids**, and zero of them is normal. A pack is
therefore an artifact about course-quality concepts, readable by a non-programmer, portable across
detector refactors, and free of Sightline's internal numbering. See
:mod:`sightline.capabilities` for the vocabulary and for why a *pack* naming an unknown tag is a
reported warning while a *detector* doing the same is a hard failure.

**Every lookup keys on the row code, never on position.** :meth:`RubricPack.criterion` is the only
accessor, and it takes a code. The reference instrument skips index 6 inside its assessment
category — it runs 1, 2, 3, 4, 5, 7 — so an index-walking lookup misaligns criteria silently,
which is the quietest way to report a finding against the wrong row.

**What the loader is strict about, and what it is not.** Structure is checked against the JSON
Schema committed beside this module, and a failure names the offending path (``$.criteria[3].tier``)
because a schema error that says only "invalid" costs an afternoon on a 68-row file. Unknown
capability tags are *not* a structural error — they are collected as warnings at projection time.
Round-tripping keeps the bindings exactly as authored so nothing is lost by loading a pack on a
build that does not recognise one of its tags.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from ..detect.base import Tier

#: The JSON Schema, beside this module and committed. Read through importlib.resources so it
#: travels with the package rather than being found by walking up to a repository.
SCHEMA_FILENAME = "pack.schema.json"

#: Internal band names. The pack supplies the instrument's own label for each.
BAND_RATINGS = ("minor", "moderate", "major")


class RubricPackError(ValueError):
    """A pack is structurally invalid. Carries the path of what is wrong."""

    def __init__(self, message: str, *, origin: str, path: str = "$") -> None:
        self.origin = origin
        self.path = path
        super().__init__(f"{origin}: {path}: {message}")


@dataclass(frozen=True, kw_only=True, slots=True)
class Band:
    """One effort band, as printed on the instrument.

    ``up_to_hours`` is an *inclusive* ceiling — "1/2 hour or less" includes a half hour — and is
    ``None`` on the open-ended top band.
    """

    rating: str
    label: str
    up_to_hours: float | None
    #: The range as the instrument prints it, quoted rather than derived.
    range_label: str | None = None

    def __post_init__(self) -> None:
        # Sufficiently Present is "no qualifying defects found", not a range of hours, so no band
        # may carry it. Refused here rather than only in the schema, because the banding
        # arithmetic returning a clean reading for a row nobody examined is the collapse
        # SPEC §11 names — and a Band built in code never passes through the schema.
        if self.rating not in BAND_RATINGS:
            raise ValueError(
                f"a band's rating must be one of {BAND_RATINGS}, not {self.rating!r}; "
                "Sufficiently Present is the no-qualifying-defects outcome and has no band"
            )

    def holds(self, hours: float) -> bool:
        return self.up_to_hours is None or hours <= self.up_to_hours

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rating": self.rating,
            "label": self.label,
            "up_to_hours": self.up_to_hours,
        }
        if self.range_label is not None:
            payload["range_label"] = self.range_label
        return payload


@dataclass(frozen=True, kw_only=True, slots=True)
class RatingScale:
    """What this instrument calls each outcome, and where its bands fall."""

    sufficiently_present: str = "Sufficiently Present"
    not_applicable: str = "Not Applicable"
    no_response: str = "No Response"
    bands: tuple[Band, ...] = ()

    def band_for(self, hours: float) -> Band:
        """The band a rolled-up cost falls in. Lowest band that still holds it."""
        for band in self.bands:
            if band.holds(hours):
                return band
        return self.bands[-1]

    def band_named(self, rating: str) -> Band | None:
        for band in self.bands:
            if band.rating == rating:
                return band
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sufficiently_present": self.sufficiently_present,
            "not_applicable": self.not_applicable,
            "no_response": self.no_response,
            "bands": [band.to_dict() for band in self.bands],
        }


@dataclass(frozen=True, kw_only=True, slots=True)
class Section:
    """One block of the instrument.

    ``optional`` is the flag that lets a block be switched off — a mobile-standards section that
    does not apply to every course, say. ``enabled`` is whether *this* pack has it switched on, and
    the loader refuses ``enabled: false`` on a section that has not declared itself optional, so a
    mandatory block cannot be dropped by a typo in a YAML file.
    """

    code: str
    title: str
    optional: bool = False
    enabled: bool = True
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "title": self.title}
        if self.optional:
            payload["optional"] = True
        if not self.enabled:
            payload["enabled"] = False
        if self.note is not None:
            payload["note"] = self.note
        return payload


@dataclass(frozen=True, kw_only=True, slots=True)
class Criterion:
    """One row of the instrument.

    ``bindings`` is held **exactly as authored**, including a tag this build does not recognise.
    Dropping it here would make loading a pack on one build and saving it on another lose data, and
    the degradation it causes is a fact about a *run* rather than about the file.
    """

    code: str
    section: str
    text: str
    tier: Tier
    #: The instrument's own priority label, or ``None``. Null is ordinary — see the module
    #: docstring of :mod:`sightline.rubric.project` for what ordering does with it.
    priority: str | None = None
    bindings: tuple[str, ...] = ()
    note: str | None = None
    #: The source instrument's own identifiers, verbatim.
    source_ids: Mapping[str, str | float | int | None] = field(default_factory=dict)

    @property
    def bound(self) -> bool:
        return bool(self.bindings)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "section": self.section,
            "text": self.text,
            "tier": self.tier.value,
        }
        if self.priority is not None:
            payload["priority"] = self.priority
        if self.bindings:
            payload["bindings"] = list(self.bindings)
        if self.note is not None:
            payload["note"] = self.note
        if self.source_ids:
            payload["source_ids"] = dict(self.source_ids)
        return payload


@dataclass(frozen=True, kw_only=True, slots=True)
class PackSource:
    """Where the rows came from. Filenames only: a path can name somebody's home directory."""

    title: str | None = None
    workbook: str | None = None
    sheet: str | None = None
    imported_at: dt.date | None = None
    key_source: str | None = None
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.title is not None:
            payload["title"] = self.title
        if self.workbook is not None:
            payload["workbook"] = self.workbook
        if self.sheet is not None:
            payload["sheet"] = self.sheet
        if self.imported_at is not None:
            payload["imported_at"] = self.imported_at.isoformat()
        if self.key_source is not None:
            payload["key_source"] = self.key_source
        if self.notes:
            payload["notes"] = list(self.notes)
        return payload


@dataclass(frozen=True, kw_only=True, slots=True)
class RubricPack:
    """One rubric, as data. The lens a review is reported through."""

    schema_version: int
    id: str
    name: str
    version: str
    description: str | None = None
    source: PackSource = field(default_factory=PackSource)
    priority_order: tuple[str, ...] = ()
    rating_scale: RatingScale = field(default_factory=RatingScale)
    sections: tuple[Section, ...] = ()
    criteria: tuple[Criterion, ...] = ()

    # -- lookup, always by code --------------------------------------------------------------

    def criterion(self, code: str) -> Criterion | None:
        """The row with this code. The only accessor, and it never takes a position."""
        return self._by_code().get(code)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(criterion.code for criterion in self.criteria)

    def section(self, code: str) -> Section | None:
        for section in self.sections:
            if section.code == code:
                return section
        return None

    def _by_code(self) -> Mapping[str, Criterion]:
        return {criterion.code: criterion for criterion in self.criteria}

    # -- what renders ------------------------------------------------------------------------

    def rendered_sections(self) -> tuple[Section, ...]:
        """Sections switched on, in instrument order."""
        return tuple(section for section in self.sections if section.enabled)

    def omitted_sections(self) -> tuple[Section, ...]:
        """Sections switched off. Reported rather than dropped: an omission is information."""
        return tuple(section for section in self.sections if not section.enabled)

    def rendered_criteria(self) -> tuple[Criterion, ...]:
        """Rows in switched-on sections, in instrument order."""
        live = {section.code for section in self.rendered_sections()}
        return tuple(criterion for criterion in self.criteria if criterion.section in live)

    def omitted_criteria(self) -> tuple[Criterion, ...]:
        live = {section.code for section in self.rendered_sections()}
        return tuple(criterion for criterion in self.criteria if criterion.section not in live)

    def with_section_enabled(self, code: str, enabled: bool) -> RubricPack:
        """A copy with one section switched on or off.

        Refuses to switch off a section that has not declared itself optional, for the reason the
        loader does: a block a course is actually reviewed against must not be droppable.
        """
        section = self.section(code)
        if section is None:
            raise KeyError(f"{self.id} declares no section {code!r}")
        if not enabled and not section.optional:
            raise RubricPackError(
                f"section {code!r} is not optional, so it cannot be switched off. Only a section "
                "that declares 'optional: true' may be; anything else would drop rows the course "
                "is being reviewed against.",
                origin=self.id,
                path=f"$.sections[{self.sections.index(section)}]",
            )
        replaced = tuple(
            Section(
                code=entry.code,
                title=entry.title,
                optional=entry.optional,
                enabled=enabled if entry.code == code else entry.enabled,
                note=entry.note,
            )
            for entry in self.sections
        )
        return RubricPack(
            schema_version=self.schema_version,
            id=self.id,
            name=self.name,
            version=self.version,
            description=self.description,
            source=self.source,
            priority_order=self.priority_order,
            rating_scale=self.rating_scale,
            sections=replaced,
            criteria=self.criteria,
        )

    # -- ordering ----------------------------------------------------------------------------

    def priority_rank(self, priority: str | None) -> int:
        """Where a priority label sorts. A null priority sorts after every labelled one.

        Null is not an absence to be filled in: the reference instrument's mobile block genuinely
        carries no flag, and inventing "Important" for it would put nine unprioritised rows in the
        middle of an action plan on the strength of a default.
        """
        if priority is None:
            return len(self.priority_order) + 1
        try:
            return self.priority_order.index(priority)
        except ValueError:
            return len(self.priority_order)

    # -- serialization -----------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The pack as plain data, in the schema's own shape. Round-trips through :func:`load`."""
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "version": self.version,
        }
        if self.description is not None:
            payload["description"] = self.description
        source = self.source.to_dict()
        if source:
            payload["source"] = source
        if self.priority_order:
            payload["priority_order"] = list(self.priority_order)
        payload["rating_scale"] = self.rating_scale.to_dict()
        payload["sections"] = [section.to_dict() for section in self.sections]
        payload["criteria"] = [criterion.to_dict() for criterion in self.criteria]
        return payload

    def to_yaml(self) -> str:
        """YAML, block style, keys in schema order.

        Comments are not preserved and are not meant to be: a hand-authored pack is edited by
        hand, and this exists for the importer's output and for diffing a re-import.
        """
        return yaml.safe_dump(
            self.to_dict(),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
            width=100,
        )

    def statement(self) -> str:
        """Counts, declaratively."""
        omitted = self.omitted_criteria()
        parts = [
            f"{self.name} {self.version}",
            f"{len(self.criteria)} criteria in {len(self.sections)} section(s)",
        ]
        if omitted:
            names = ", ".join(section.code for section in self.omitted_sections())
            parts.append(f"{len(omitted)} omitted with the {names} section(s) switched off")
        bound = sum(1 for criterion in self.rendered_criteria() if criterion.bound)
        parts.append(f"{bound} of {len(self.rendered_criteria())} rendered row(s) carry a binding")
        return "; ".join(parts) + "."


# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def schema() -> Mapping[str, Any]:
    """The committed JSON Schema."""
    source = resources.files(__package__).joinpath(SCHEMA_FILENAME)
    return json.loads(source.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(schema())


def validate(payload: Any, *, origin: str = "<pack>") -> None:
    """Check ``payload`` against the schema; raise naming the offending path.

    Errors are sorted by path and the first is raised, because an error naming the deepest thing
    wrong is the one worth reading; the count of the rest goes in the message so a file with
    fourteen problems does not look like a file with one.
    """
    errors = sorted(_validator().iter_errors(payload), key=lambda error: list(error.absolute_path))
    if not errors:
        return
    first = errors[0]
    more = f" ({len(errors) - 1} further problem(s) in this file)" if len(errors) > 1 else ""
    raise RubricPackError(f"{first.message}{more}", origin=origin, path=first.json_path)


def loads(text: str, *, origin: str = "<string>") -> RubricPack:
    """Parse, validate and build a pack from YAML text."""
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RubricPackError(f"not valid YAML: {exc}", origin=origin) from exc
    if payload is None:
        raise RubricPackError("the file is empty", origin=origin)
    validate(payload, origin=origin)
    return _build(payload, origin=origin)


def load(path: str | Path) -> RubricPack:
    """Load a pack from a file. The filename is the origin in every error raised."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise RubricPackError(f"cannot be read: {exc}", origin=target.name) from exc
    return loads(text, origin=target.name)


def _build(payload: Mapping[str, Any], *, origin: str) -> RubricPack:
    """Build the model from schema-valid data, then check what a schema cannot express."""
    sections = tuple(
        Section(
            code=str(entry["code"]),
            title=str(entry["title"]),
            optional=bool(entry.get("optional", False)),
            enabled=bool(entry.get("enabled", True)),
            note=_text_or_none(entry.get("note")),
        )
        for entry in payload["sections"]
    )
    _check_sections(sections, origin=origin)

    criteria = tuple(
        Criterion(
            code=str(entry["code"]),
            section=str(entry["section"]),
            text=str(entry["text"]),
            tier=Tier(entry["tier"]),
            priority=_text_or_none(entry.get("priority")),
            bindings=tuple(str(tag) for tag in entry.get("bindings", ())),
            note=_text_or_none(entry.get("note")),
            source_ids=dict(entry.get("source_ids", {})),
        )
        for entry in payload["criteria"]
    )
    _check_criteria(criteria, sections, origin=origin)

    scale = _scale(payload.get("rating_scale"), origin=origin)
    return RubricPack(
        schema_version=int(payload["schema_version"]),
        id=str(payload["id"]),
        name=str(payload["name"]),
        version=str(payload["version"]),
        description=_text_or_none(payload.get("description")),
        source=_source(payload.get("source"), origin=origin),
        priority_order=tuple(str(label) for label in payload.get("priority_order", ())),
        rating_scale=scale,
        sections=sections,
        criteria=criteria,
    )


def _check_sections(sections: Sequence[Section], *, origin: str) -> None:
    seen: set[str] = set()
    for position, section in enumerate(sections):
        path = f"$.sections[{position}]"
        if section.code in seen:
            raise RubricPackError(
                f"section code {section.code!r} is declared twice", origin=origin, path=path
            )
        seen.add(section.code)
        if not section.enabled and not section.optional:
            raise RubricPackError(
                f"section {section.code!r} sets 'enabled: false' without 'optional: true'. Only a "
                "section that declares itself optional may be switched off; anything else silently "
                "drops rows the course is being reviewed against.",
                origin=origin,
                path=path,
            )


def _check_criteria(
    criteria: Sequence[Criterion], sections: Sequence[Section], *, origin: str
) -> None:
    declared = {section.code for section in sections}
    seen: set[str] = set()
    for position, criterion in enumerate(criteria):
        path = f"$.criteria[{position}]"
        if criterion.code in seen:
            raise RubricPackError(
                f"criterion code {criterion.code!r} is used twice. Codes are what every lookup "
                "keys on, so a duplicate makes one of the two rows unreachable.",
                origin=origin,
                path=path,
            )
        seen.add(criterion.code)
        if criterion.section not in declared:
            raise RubricPackError(
                f"criterion {criterion.code!r} is in section {criterion.section!r}, which this "
                f"pack does not declare. Declared: {', '.join(sorted(declared))}.",
                origin=origin,
                path=f"{path}.section",
            )


def _scale(payload: Any, *, origin: str) -> RatingScale:
    if payload is None:
        return default_rating_scale()
    bands_raw = payload.get("bands")
    if bands_raw is None:
        bands = default_rating_scale().bands
    else:
        bands = tuple(
            Band(
                rating=str(entry["rating"]),
                label=str(entry["label"]),
                up_to_hours=(None if entry["up_to_hours"] is None else float(entry["up_to_hours"])),
                range_label=_text_or_none(entry.get("range_label")),
            )
            for entry in bands_raw
        )
        _check_bands(bands, origin=origin)
    defaults = RatingScale()
    return RatingScale(
        sufficiently_present=str(
            payload.get("sufficiently_present", defaults.sufficiently_present)
        ),
        not_applicable=str(payload.get("not_applicable", defaults.not_applicable)),
        no_response=str(payload.get("no_response", defaults.no_response)),
        bands=bands,
    )


def _check_bands(bands: Sequence[Band], *, origin: str) -> None:
    previous: float | None = None
    for position, band in enumerate(bands):
        path = f"$.rating_scale.bands[{position}]"
        if band.up_to_hours is None:
            if position != len(bands) - 1:
                raise RubricPackError(
                    "only the last band may be open-ended", origin=origin, path=path
                )
            continue
        if previous is not None and band.up_to_hours <= previous:
            raise RubricPackError(
                f"bands must ascend: {band.up_to_hours} does not exceed {previous}",
                origin=origin,
                path=f"{path}.up_to_hours",
            )
        previous = band.up_to_hours
    if bands and bands[-1].up_to_hours is not None:
        raise RubricPackError(
            "the last band must be open-ended ('up_to_hours': null). A closed top band leaves a "
            "rolled-up cost above it with no rating at all.",
            origin=origin,
            path=f"$.rating_scale.bands[{len(bands) - 1}].up_to_hours",
        )
    seen = [band.rating for band in bands]
    if len(set(seen)) != len(seen):
        raise RubricPackError(
            f"a band rating is repeated: {seen}", origin=origin, path="$.rating_scale.bands"
        )


def _source(payload: Any, *, origin: str) -> PackSource:
    if payload is None:
        return PackSource()
    raw_date = payload.get("imported_at")
    imported: dt.date | None
    if raw_date is None:
        imported = None
    elif isinstance(raw_date, dt.datetime):
        imported = raw_date.date()
    elif isinstance(raw_date, dt.date):
        imported = raw_date
    else:
        try:
            imported = dt.date.fromisoformat(str(raw_date))
        except ValueError as exc:
            raise RubricPackError(
                f"'imported_at' is not an ISO date: {raw_date!r}",
                origin=origin,
                path="$.source.imported_at",
            ) from exc
    return PackSource(
        title=_text_or_none(payload.get("title")),
        workbook=_text_or_none(payload.get("workbook")),
        sheet=_text_or_none(payload.get("sheet")),
        imported_at=imported,
        key_source=_text_or_none(payload.get("key_source")),
        notes=tuple(str(note) for note in payload.get("notes", ())),
    )


def default_rating_scale() -> RatingScale:
    """The reference instrument's own bands, which are printed on it (SPEC §07).

    Not a guess and not a calibration. On sheet ``Instructional Designer`` of
    ``rubrics/source/UCCS OSCQR 3.1.3.xlsx``, row 3 names the ratings — `S3` "Sufficiently
    Present", `T3` "Minor Revision", `U3` "Moderate Revision", `V3` "Major Revision" — and row 4
    prints the ranges beneath them after `R4` "Estimated time needed for revision:": `T4` "1/2 hour
    or less", `U4` "1/2-2 hours", `V4` "2+ hours". `S4` is empty: Sufficiently Present has no band,
    because it means "no qualifying defects found" rather than zero hours. The RSI instrument prints
    the same three on its rows 6–7, and `CONFIG!$H$15:$K$19`'s point estimates fall inside these
    ranges — a corroboration only, since `CONFIG` is never a source (SPEC §03). A pack that prints
    different ranges declares its own.
    """
    return RatingScale(
        bands=(
            Band(
                rating="minor",
                label="Minor Revision",
                up_to_hours=0.5,
                range_label="1/2 hour or less",
            ),
            Band(
                rating="moderate",
                label="Moderate Revision",
                up_to_hours=2.0,
                range_label="1/2-2 hours",
            ),
            Band(rating="major", label="Major Revision", up_to_hours=None, range_label="2+ hours"),
        )
    )


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


def iter_criteria(packs: Iterable[RubricPack]) -> Iterator[tuple[RubricPack, Criterion]]:
    for pack in packs:
        for criterion in pack.criteria:
            yield pack, criterion


__all__ = [
    "BAND_RATINGS",
    "SCHEMA_FILENAME",
    "Band",
    "Criterion",
    "PackSource",
    "RatingScale",
    "RubricPack",
    "RubricPackError",
    "Section",
    "default_rating_scale",
    "iter_criteria",
    "load",
    "loads",
    "schema",
    "validate",
]
