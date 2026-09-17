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
    Per-defect costs, as a labelled placeholder. Roadmap item 12 owns the real one.

The capability vocabulary itself is not here: it is shared with the detectors and belongs to
neither side, so it lives at :mod:`sightline.capabilities`.
"""

from .coverage import PackCoverage, RowCoverage, pack_coverage
from .effort import EffortModel, placeholder_effort_model
from .importer import Import, ImportReport, RubricImportError, import_csv, import_workbook
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
from .states import Coverage, Rating, coverage_of

__all__ = [
    "Band",
    "BindingWarning",
    "Contribution",
    "Coverage",
    "Criterion",
    "EffortModel",
    "ExtractionRequest",
    "Import",
    "ImportReport",
    "PackCoverage",
    "Projection",
    "Rating",
    "RatingScale",
    "RoutingManifest",
    "RowCoverage",
    "RowResult",
    "RubricImportError",
    "RubricPack",
    "RubricPackError",
    "Section",
    "TagIndex",
    "coverage_of",
    "default_rating_scale",
    "extraction_requests",
    "import_csv",
    "import_workbook",
    "load",
    "loads",
    "manifest",
    "pack_coverage",
    "placeholder_effort_model",
    "project",
    "routes_to_extraction",
    "routing_reason",
    "schema",
    "tag_descriptions",
    "validate",
]
