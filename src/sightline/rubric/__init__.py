"""Rubric packs: the lens a course review is reported through.

**Sightline reviews the course. The rubric is only the lens.** Detectors emit facts about course
artifacts; a pack decides how those facts are reported and rated. UCCS OSCQR 3.1.3 is the reference
instrument — not the schema, not a required intermediate, and not something a third-party rubric
gets aligned to. Quality Matters, a departmental checklist or an instrument written last week loads
through here and gets a working review, and no step requires it to be crosswalked to 3.1.3 first.
Sightline can never *ship* a Quality Matters pack, since that instrument is proprietary, so
third-party rubric support is the only way those users exist.

The pieces, in the order they are used:

:mod:`~sightline.rubric.pack`
    The pack format, its committed JSON Schema, and a loader that names the offending path.
:mod:`~sightline.rubric.importer`
    A workbook to a valid pack **with empty bindings**, which is immediately usable rather than a
    stub awaiting authoring.
:mod:`~sightline.rubric.mapping`
    The authored mapping file — the tags, the tiers and the section flags no workbook has a column
    for. **The only thing that differs between the two shipped packs.**
:mod:`~sightline.rubric.shipped`
    Locating the packs this build ships. They are peers; neither is a default.
:mod:`~sightline.rubric.coverage`
    A coverage report about the pack alone: how many of its rows a parser can answer. Computed
    before any course is read, so a gap is visible information rather than silent degradation.
:mod:`~sightline.rubric.project`
    Typed detector findings onto rubric rows, resolved through capability tags, many-to-many in
    both directions, and rated by summing contributing effort and banding the total.
:mod:`~sightline.rubric.routing`
    The contract for rows a parser cannot answer. An interface; roadmap item 10 implements it.
:mod:`~sightline.rubric.states`
    The three coverage states, defined once.
:mod:`~sightline.rubric.effort`
    The effort model: per-defect costs (an uncalibrated placeholder in ``costs.yaml``, overridable
    without a code change), the near-boundary marker, and nothing course-level.

The capability vocabulary itself is not here: it is shared with the detectors and belongs to
neither side, so it lives at :mod:`sightline.capabilities`.
"""

from .coverage import PackCoverage, RowCoverage, pack_coverage
from .effort import (
    EffortConfigError,
    EffortModel,
    NearBoundary,
    default_effort_model,
    load_effort_model,
)
from .importer import Import, ImportReport, RubricImportError, import_csv, import_workbook
from .mapping import MappingError, PackMapping, RowMapping, SectionMapping
from .mapping import load as load_mapping
from .pack import (
    Band,
    Criterion,
    RatingScale,
    RubricPack,
    RubricPackError,
    Section,
    default_rating_scale,
    load,
    loads,
    schema,
    validate,
)
from .project import (
    BindingWarning,
    Contribution,
    Projection,
    RowResult,
    TagIndex,
    project,
    tag_descriptions,
)
from .routing import (
    ExtractionRequest,
    RoutingManifest,
    extraction_requests,
    manifest,
    routes_to_extraction,
    routing_reason,
)
from .shipped import PacksNotFoundError, pack_paths, packs_dir, shipped_pack, shipped_packs
from .states import Coverage, Rating, coverage_of

__all__ = [
    "Band",
    "BindingWarning",
    "Contribution",
    "Coverage",
    "Criterion",
    "EffortConfigError",
    "EffortModel",
    "ExtractionRequest",
    "Import",
    "ImportReport",
    "MappingError",
    "NearBoundary",
    "PackCoverage",
    "PackMapping",
    "PacksNotFoundError",
    "Projection",
    "Rating",
    "RatingScale",
    "RoutingManifest",
    "RowCoverage",
    "RowMapping",
    "RowResult",
    "RubricImportError",
    "RubricPack",
    "RubricPackError",
    "Section",
    "SectionMapping",
    "TagIndex",
    "coverage_of",
    "default_effort_model",
    "default_rating_scale",
    "extraction_requests",
    "import_csv",
    "import_workbook",
    "load",
    "load_mapping",
    "load_effort_model",
    "loads",
    "manifest",
    "pack_coverage",
    "pack_paths",
    "packs_dir",
    "project",
    "routes_to_extraction",
    "routing_reason",
    "schema",
    "shipped_pack",
    "shipped_packs",
    "tag_descriptions",
    "validate",
]
