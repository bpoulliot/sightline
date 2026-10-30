"""Assembling the real pipeline for one run out of what this deployment is configured with.

The batch runner takes a factory rather than a pipeline because the pipeline holds an HTTP client
per run and because what it should be built from is only known once the run exists — the rubric
pack is recorded on the run, and the credentials are whatever is in force at the moment the run
starts rather than when the process booted.

**Everything missing fails as a stated condition of the run, not as a stack trace.** No Canvas
credentials, no model host, a pack id nothing on disk answers to: each raises
:class:`~sightline.batch.stages.BatchAborted` carrying what to do about it, which the runner
records and the summary repeats. A batch that starts and then throws a ``KeyError`` sixteen times
is the shape this avoids.

**Document bytes reach the detectors through the data root** (roadmap item 26). Each acquired
document records the unpacked export its files live in, and the pipeline is handed the data root so
it can build each course's :class:`~sightline.detect.sources.FileSource` from that record. A course
acquired without an export records none, and its documents are reported as not read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from ..acquire.service import CourseAcquisition
from ..canvas.client import CanvasClient
from ..config import Settings
from ..credentials import CredentialError, CredentialStore
from ..detect.links import HttpLinkResolver
from ..llm.client import OllamaClient
from ..logging import get_logger
from ..models import Run
from ..paths import DataPaths
from ..preferences import PreferenceStore
from ..rubric.accepted import BindingsError, ResolvedPack, resolve_pack
from ..rubric.effort import load_effort_model
from ..rubric.shipped import PacksNotFoundError
from .pipeline import CanvasSource, CoursePipeline, ReviewPipeline
from .runner import PipelineFactory
from .stages import BatchAborted

log = get_logger("sightline.batch.service")

#: Said whenever a run cannot start for want of credentials. One string, because the two places
#: that say it are two ways of being unconfigured and not two different problems.
CANVAS_REMEDY = (
    "Enter the Canvas URL and an API token on the Settings screen, then start the run again."
)


def live_pipeline_factory(
    *,
    settings: Settings,
    paths: DataPaths,
    credentials: CredentialStore,
    preferences: PreferenceStore,
    model_endpoint: str | None = None,
    model_name: str | None = None,
) -> PipelineFactory:
    """The factory the application wires into its background runner.

    ``model_endpoint`` and ``model_name`` override the stored preferences **for this factory
    only**, which is what a command-line flag should do: a flag typed once is an answer about this
    run, not a change to what the deployment is configured with.
    """

    @asynccontextmanager
    async def build(run: Run) -> AsyncIterator[CoursePipeline]:
        resolved = resolved_pack(run, paths)
        canvas = _canvas(credentials)
        endpoint = model_endpoint or preferences.model_host().endpoint
        model = model_name or preferences.review_model().name
        links = HttpLinkResolver.from_httpx() if settings.resolve_links else None
        export_preference = preferences.request_content_export()

        async with CanvasClient(canvas.base_url, canvas.token) as client:
            model_client = (
                OllamaClient(
                    endpoint,
                    concurrency=settings.ollama_concurrency,
                    context_tokens=settings.ollama_context_tokens,
                )
                if endpoint
                else None
            )
            try:
                yield ReviewPipeline(
                    source=CanvasSource(
                        CourseAcquisition(
                            client,
                            paths,
                            request_export=export_preference.enabled,
                        )
                    ),
                    pack=resolved.pack,
                    bindings=resolved.digest,
                    client=model_client,
                    model=model,
                    # Each course's bytes come from the export its own acquisition recorded.
                    data_paths=paths,
                    links=links,
                    effort=load_effort_model(settings.effort_costs),
                )
            finally:
                if model_client is not None:
                    await model_client.aclose()

    return build


def resolved_pack(run: Run, paths: DataPaths) -> ResolvedPack:
    """The pack ``run`` uses: the shipped pack with the bindings the run recorded (item 22).

    By the run's recorded digest, never the bindings in force now, so a run resumed after somebody
    accepted or reverted bindings carries on with the ones it started with.
    """
    try:
        return resolve_pack(run.rubric_pack, paths, digest=run.bindings_digest)
    except (KeyError, PacksNotFoundError) as exc:
        raise BatchAborted(
            f"This run was recorded against rubric pack {run.rubric_pack!r}, which is not on "
            f"disk. {exc}",
            remedy=(
                "Start the run again against a rubric this build ships, or point "
                "SIGHTLINE_RUBRIC_PACKS_DIR at the directory holding the packs."
            ),
        ) from exc
    except BindingsError as exc:
        raise BatchAborted(
            f"This run was recorded with accepted bindings that cannot be read back: {exc}.",
            remedy=(
                "Start the run again; a new run uses the bindings in force on the rubric's page."
            ),
        ) from exc


def _canvas(credentials: CredentialStore):  # noqa: ANN202 - CanvasCredentials
    try:
        canvas = credentials.canvas()
    except CredentialError as exc:
        raise BatchAborted(
            f"Sightline has no Canvas credentials, so no course could be acquired. {exc}",
            remedy=CANVAS_REMEDY,
        ) from exc
    if not canvas.complete:
        raise BatchAborted(
            "Sightline has no complete Canvas credentials, so no course could be acquired.",
            remedy=CANVAS_REMEDY,
        )
    return canvas


__all__ = ["live_pipeline_factory", "resolved_pack"]
