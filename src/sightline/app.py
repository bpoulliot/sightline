"""The FastAPI application.

One process, one container. Server-rendered HTML with light progressive enhancement — no SPA
framework (SPEC §09). This module builds the app, wires the run store, the credential store and
the background runner into its lifespan, and serves the status page. The settings screen and the
New run screen live in :mod:`sightline.views`, the run-progress screens in
:mod:`sightline.run_screens` (roadmap item 36) and the review screen in :mod:`sightline.review`
(roadmap item 16).

The vocabulary constraint from SPEC §11 applies to everything rendered here: Sightline *reviews*
and *refreshes*. It does not evaluate, score or grade.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .assets import TokensNotFoundError, resolve_tokens_css
from .batch import BatchRunner, live_pipeline_factory
from .config import Settings, get_settings
from .credentials import CredentialStore
from .llm import PullRegistry
from .logging import configure_logging, get_logger
from .preferences import PreferenceStore
from .review import build_review_router
from .run_screens import build_run_router
from .runner import BackgroundRunner
from .store import RunStore
from .views import build_router

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_ROOT / "templates"
STATIC_DIR = PACKAGE_ROOT / "static"

log = get_logger("sightline.app")

#: Determinacy tiers, rendered as the legend on the status page. Kept here rather than in the
#: template so the wording has one home; SPEC §02 is the source.
TIERS: tuple[dict[str, str], ...] = (
    {
        "letter": "A",
        "css": "tier-a",
        "name": "Deterministic",
        "handling": "Computed by a parser. No model. Stated declaratively.",
    },
    {
        "letter": "B",
        "css": "tier-b",
        "name": "Evidenced",
        "handling": "A model locates and quotes the passage. Worded as evidence.",
    },
    {
        "letter": "C",
        "css": "tier-c",
        "name": "Judgment",
        "handling": "Context assembled and the question drafted. Never pre-rated.",
    },
    {
        "letter": "D",
        "css": "tier-d",
        "name": "Out of scope",
        "handling": "Not determinable from course artifacts. Defaults to N/A with a note.",
    },
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Passing ``settings`` is how tests get an isolated data root."""
    resolved = settings or get_settings()
    configure_logging(level=resolved.log_level, fmt=resolved.log_format)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        paths = resolved.paths.ensure()
        store = RunStore.open(paths)
        runner = BackgroundRunner(store)
        credentials = CredentialStore(
            store,
            paths,
            env_base_url=resolved.canvas_base_url,
            env_token=(
                resolved.canvas_api_token.get_secret_value() if resolved.canvas_api_token else None
            ),
            key=(resolved.secret_key.get_secret_value() if resolved.secret_key else None),
        )
        preferences = PreferenceStore(
            store,
            env_request_content_export=resolved.request_content_export_from_env,
            env_model_host_endpoint=resolved.ollama_base_url,
        )
        pulls = PullRegistry()
        # The pipeline a run executes (roadmap item 17). Wired here rather than inside the
        # background runner because what a run should be built from — credentials, the chosen
        # model, the rubric recorded on the run — is the application's knowledge, not the task
        # scheduler's. Until this line existed, submitting a run raised RunnerNotConfiguredError,
        # which was the honest state of a project with no pipeline.
        batch = BatchRunner(
            store,
            paths,
            live_pipeline_factory(
                settings=resolved,
                paths=paths,
                credentials=credentials,
                preferences=preferences,
            ),
        )
        runner.handler = batch.handle
        application.state.settings = resolved
        application.state.paths = paths
        application.state.store = store
        application.state.runner = runner
        application.state.credentials = credentials
        application.state.preferences = preferences
        application.state.pulls = pulls
        application.state.batch = batch
        interrupted = runner.recover()
        # A restart continues the batch it was in the middle of (SPEC §04). One task, in creation
        # order: the courses are reviewed sequentially against one model host either way.
        resumed = runner.resume_all()
        log.info(
            "startup",
            version=__version__,
            data_root=str(paths.root),
            canvas_configured=resolved.canvas_configured,
            request_content_export=preferences.request_content_export().enabled,
            ollama_configured=preferences.model_host().configured,
            ollama_concurrency=resolved.ollama_concurrency,
            escalation_configured=resolved.escalation_configured,
            interrupted_runs=len(interrupted),
            resumed_runs=len(resumed),
            batch_cap=resolved.batch_cap,
        )
        try:
            yield
        finally:
            # Installs are cancelled before the store closes: a pull task that outlived the
            # process would be a download nobody can see the progress of.
            await pulls.shutdown()
            await runner.shutdown()
            store.dispose()
            log.info("shutdown", version=__version__)

    application = FastAPI(
        title="Sightline",
        version=__version__,
        summary="An automated first pass over an online course against a review rubric.",
        lifespan=lifespan,
    )
    application.state.settings = resolved

    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    templates.env.globals["app_version"] = __version__
    application.include_router(build_router(templates))
    application.include_router(build_run_router(templates))
    application.include_router(build_review_router(templates))

    # The tokens live outside the package on purpose (see sightline.assets). Serving them from a
    # dedicated route rather than the static mount keeps the "one source of colour" literal.
    @application.get("/assets/tokens.css", include_in_schema=False)
    async def tokens_css() -> FileResponse:
        return FileResponse(
            resolve_tokens_css(resolved.tokens_css_path),
            media_type="text/css",
            headers={"Cache-Control": "no-cache"},
        )

    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @application.get("/healthz", include_in_schema=True)
    async def healthz(request: Request) -> JSONResponse:
        """Liveness and readiness in one place; the container HEALTHCHECK calls this.

        Every check reports what it examined, not merely that it passed.
        """
        checks: dict[str, Any] = {}

        paths = getattr(request.app.state, "paths", resolved.paths)
        try:
            paths.ensure()
            checks["data_root"] = {"ok": True, "path": str(paths.root)}
        except OSError as exc:
            checks["data_root"] = {"ok": False, "path": str(paths.root), "error": str(exc)}

        store: RunStore | None = getattr(request.app.state, "store", None)
        if store is None:
            checks["run_store"] = {"ok": False, "error": "store not initialised"}
        else:
            try:
                counts = store.counts_by_status()
                checks["run_store"] = {"ok": True, "runs_by_status": counts}
            except Exception as exc:  # pragma: no cover - surfaced, never swallowed
                checks["run_store"] = {"ok": False, "error": str(exc)}

        try:
            tokens = resolve_tokens_css(resolved.tokens_css_path)
            checks["design_tokens"] = {"ok": True, "path": str(tokens)}
        except TokensNotFoundError as exc:
            checks["design_tokens"] = {"ok": False, "error": str(exc)}

        healthy = all(check["ok"] for check in checks.values())
        payload = {"status": "ok" if healthy else "unhealthy", "version": __version__, **checks}
        return JSONResponse(payload, status_code=200 if healthy else 503)

    @application.get("/", include_in_schema=False)
    async def index(request: Request) -> Any:
        store: RunStore | None = getattr(request.app.state, "store", None)
        runs = store.list_runs(limit=10) if store is not None else []
        paths = getattr(request.app.state, "paths", resolved.paths)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "nav": "status",
                "settings_rows": resolved.status_rows(),
                "tiers": TIERS,
                "runs": runs,
                "data_root": str(paths.root),
                "database": str(paths.database),
            },
        )

    return application


app = create_app()
