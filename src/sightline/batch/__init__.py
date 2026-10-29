"""The batch runner: what a run does, in what order, and what it is not allowed to do.

Roadmap item 17. Five modules and one rule each:

:mod:`sightline.batch.stages`
    The five unattended stages, and what counts as one failing.
:mod:`sightline.batch.pipeline`
    The work each stage does, and the reviewable artifact a course leaves behind.
:mod:`sightline.batch.runner`
    The loop: order, per-course isolation, resume, progress events, the summary.
:mod:`sightline.batch.progress`
    Reading a run back out of the store, per course and per stage.
:mod:`sightline.batch.gate`
    Nothing is exported for a course until a person has opened its review.
"""

from .gate import ExportGate, ReviewNotOpenedError
from .pipeline import (
    BundleSource,
    CanvasSource,
    CoursePipeline,
    CourseWork,
    DocumentSource,
    ReviewPipeline,
    Rollup,
)
from .progress import CourseProgress, RunProgress, StageProgress, StageState, progress_of
from .runner import (
    DEFAULT_RUBRIC_PACK,
    BatchRunner,
    BatchSummary,
    BatchTooLargeError,
    CourseOutcome,
    PipelineFactory,
    create_batch_run,
    fixed_pipeline,
    pipeline_factory,
)
from .service import live_pipeline_factory
from .stages import ORDER, BatchAborted, NothingExamined, Stage, StageError, StageOutcome

__all__ = [
    "DEFAULT_RUBRIC_PACK",
    "ORDER",
    "BatchAborted",
    "BatchRunner",
    "BatchSummary",
    "BatchTooLargeError",
    "BundleSource",
    "CanvasSource",
    "CourseOutcome",
    "CoursePipeline",
    "CourseProgress",
    "CourseWork",
    "DocumentSource",
    "ExportGate",
    "NothingExamined",
    "PipelineFactory",
    "ReviewPipeline",
    "ReviewNotOpenedError",
    "Rollup",
    "RunProgress",
    "Stage",
    "StageError",
    "StageOutcome",
    "StageProgress",
    "StageState",
    "create_batch_run",
    "fixed_pipeline",
    "live_pipeline_factory",
    "pipeline_factory",
    "progress_of",
]
