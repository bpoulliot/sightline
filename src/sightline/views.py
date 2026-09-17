"""The settings screen and the course-resolution screen.

Server-rendered HTML with light progressive enhancement, no SPA framework (SPEC §09). Both screens
work with JavaScript switched off: every action is a form post that re-renders the page with the
result. The JSON endpoints exist for the suite and for later items, not because the pages need
them.

Two rules shape what is rendered here and they pull in opposite directions.

*Presence, never values, on a status surface.* The status page reports that a setting is
configured and never what it is (see :meth:`sightline.config.Settings.status_rows`), because on a
public-repo project a screenshot of a status page is as publishable as the repository.

*The connection test must say which host answered.* A test that cannot name the host it reached is
not a test, it is a light. So the settings screen renders the host — in the transient result of an
action the user just asked for, on the screen where they typed it. It stays out of the persistent
status table, out of the logs and out of stored run events, which redaction handles.

The model-host section does not call the host when the page is rendered, and that is deliberate.
Listing models costs one request per installed model, and an unreachable host would make the
settings page itself hang for the connection timeout — on the one screen somebody opens *because*
the endpoint is wrong. So the check is a button, exactly like the Canvas one, and an install's
progress is re-read by reloading rather than by a page that refreshes itself under whatever
somebody is typing into the form above it.

There is no CSRF token and no login. Sightline is one process on a designer's machine reviewing
courses they already have access to; adding a session layer for a single-user local tool would be
security theatre with a maintenance cost.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .batch import progress_of
from .canvas import (
    REQUIRED_PERMISSIONS,
    REQUIRED_SCOPES,
    CanvasClient,
    CanvasError,
    ConnectionCheck,
    ConnectionReport,
    CourseRef,
    RefBatch,
    parse_course_ref_csv,
    parse_course_refs,
)
from .config import DEFAULT_BATCH_CAP
from .credentials import CanvasCredentials, CredentialError, CredentialStore
from .llm import (
    HostReport,
    ModelHostError,
    ModelHostNotConfiguredError,
    OllamaClient,
    PullRegistry,
    load_manifest,
)
from .logging import get_logger
from .preferences import (
    ExportPreference,
    ModelHostPreference,
    PreferenceStore,
    ReviewModelPreference,
)
from .store import RunStore

log = get_logger("sightline.views")

#: How many courses one resolution request will look up in Canvas. The batch cap is sixteen
#: (SPEC §09); parsing is unlimited so the user can see everything they pasted, and the surplus is
#: reported as "parsed but not looked up" rather than dropped. Taken from the cap rather than
#: spelled again: two numbers that have to match are a number that will not.
MAX_LOOKUPS = DEFAULT_BATCH_CAP

#: Cap on an uploaded CSV. Large enough for any realistic course list, small enough that a wrong
#: file fails as a stated limit rather than as a memory error.
MAX_CSV_BYTES = 2 * 1024 * 1024


def build_router(templates: Jinja2Templates) -> APIRouter:
    """Build the settings and course routers against the shared template environment."""
    router = APIRouter()

    # -- settings ----------------------------------------------------------------------------

    @router.get("/settings", include_in_schema=False)
    async def settings_page(request: Request, saved: str | None = None) -> Any:
        return _render_settings(request, templates, saved=saved)

    @router.post("/settings", include_in_schema=False)
    async def save_settings(
        request: Request,
        canvas_base_url: Annotated[str, Form()] = "",
        canvas_token: Annotated[str, Form()] = "",
    ) -> Any:
        store = _credentials(request)
        try:
            # A blank token field means "leave the stored token alone", not "erase it". Erasing
            # is the separate Forget action, because a form that wipes a working token whenever
            # somebody edits the URL is a form that teaches people not to touch it.
            normalized = store.save_canvas(
                base_url=canvas_base_url, token=canvas_token.strip() or None
            )
        except CredentialError as exc:
            return _render_settings(request, templates, credential_error=exc)
        saved = "normalized" if normalized and normalized != canvas_base_url.strip() else "yes"
        return RedirectResponse(f"/settings?saved={saved}", status_code=303)

    @router.post("/settings/export", include_in_schema=False)
    async def save_export_setting(
        request: Request,
        request_export: Annotated[str, Form()] = "",
    ) -> Any:
        """Turn the fresh-export request on or off.

        An unchecked checkbox is not submitted at all, so absence is what "off" looks like in a
        form post. That is why this is its own action rather than a field on the credential form:
        a checkbox merged into that form would read as "off" every time somebody saved a token.
        """
        enabled = bool(request_export)
        _preferences(request).set_request_content_export(enabled)
        return RedirectResponse(
            f"/settings?saved={'export_on' if enabled else 'export_off'}", status_code=303
        )

    @router.post("/settings/forget", include_in_schema=False)
    async def forget_settings(request: Request) -> Any:
        _credentials(request).clear_canvas()
        return RedirectResponse("/settings?saved=forgotten", status_code=303)

    @router.post("/settings/test", include_in_schema=False)
    async def test_settings(
        request: Request,
        course_reference: Annotated[str, Form()] = "",
    ) -> Any:
        report, error = await _run_connection_test(request, course_reference)
        return _render_settings(request, templates, report=report, credential_error=error)

    @router.post("/api/canvas/test")
    async def test_canvas_api(request: Request, course_reference: str | None = None) -> Any:
        """The connection test as JSON. Same code path as the button, no second implementation."""
        report, error = await _run_connection_test(request, course_reference or "")
        if report is not None:
            return JSONResponse(report.as_dict(), status_code=200 if report.ok else 502)
        assert error is not None  # noqa: S101 - one of the two is always set
        return JSONResponse(
            {"ok": False, "host": None, "summary": error.summary, "remedy": error.remedy},
            status_code=400,
        )

    # -- the model host ------------------------------------------------------------------------

    @router.post("/settings/model-host", include_in_schema=False)
    async def save_model_host(
        request: Request,
        model_host_endpoint: Annotated[str, Form()] = "",
    ) -> Any:
        try:
            _preferences(request).set_model_host(model_host_endpoint)
        except ModelHostError as exc:
            return _render_settings(request, templates, model_error=exc)
        return RedirectResponse("/settings?saved=model_host", status_code=303)

    @router.post("/settings/model-host/forget", include_in_schema=False)
    async def forget_model_host(request: Request) -> Any:
        _preferences(request).clear_model_host()
        return RedirectResponse("/settings?saved=model_host_forgotten", status_code=303)

    @router.post("/settings/model-host/check", include_in_schema=False)
    async def check_model_host(request: Request) -> Any:
        report, error = await _run_host_check(request)
        return _render_settings(request, templates, host_report=report, model_error=error)

    @router.post("/settings/model", include_in_schema=False)
    async def choose_model(
        request: Request,
        model: Annotated[str, Form()] = "",
    ) -> Any:
        try:
            _preferences(request).set_review_model(model)
        except ModelHostError as exc:
            return _render_settings(request, templates, model_error=exc)
        return RedirectResponse("/settings?saved=model_chosen", status_code=303)

    @router.post("/settings/model/install", include_in_schema=False)
    async def install_model(
        request: Request,
        model: Annotated[str, Form()] = "",
    ) -> Any:
        """Start an install and return to the page, which then shows its progress.

        The response does not wait for the download. A request that waited would be a request
        that times out on a 9 GB model, and the task outliving the request is the whole reason
        :class:`sightline.llm.PullRegistry` exists.
        """
        try:
            endpoint = _model_endpoint(request)
        except ModelHostError as exc:
            return _render_settings(request, templates, model_error=exc)
        name = model.strip()
        if not name:
            return _render_settings(
                request,
                templates,
                model_error=ModelHostError(
                    "No model was named, so nothing was installed.",
                    remedy="Choose one of the recommended models and submit again.",
                ),
            )
        settings = request.app.state.settings
        _pulls(request).start(
            name,
            lambda: OllamaClient(
                endpoint,
                concurrency=settings.ollama_concurrency,
                context_tokens=settings.ollama_context_tokens,
            ),
        )
        return RedirectResponse("/settings?saved=installing#models", status_code=303)

    @router.post("/api/models/check")
    async def check_model_host_api(request: Request) -> Any:
        """The health check as JSON. Same code path as the button, no second implementation."""
        report, error = await _run_host_check(request)
        if report is not None:
            return JSONResponse(report.as_dict(), status_code=200 if report.ok else 502)
        assert error is not None  # noqa: S101 - one of the two is always set
        return JSONResponse(
            {"ok": False, "host": None, "summary": error.summary, "remedy": error.remedy},
            status_code=400,
        )

    @router.get("/api/models/installs")
    async def model_installs(request: Request) -> Any:
        """Every install this process has started, running or finished."""
        return JSONResponse({"installs": [state.as_dict() for state in _pulls(request).states()]})

    # -- runs --------------------------------------------------------------------------------

    @router.get("/api/runs/{run_id}")
    async def run_progress(request: Request, run_id: str) -> JSONResponse:
        """One run, per course and per stage, with every failure carried (SPEC §09).

        JSON and not a screen, deliberately. The run-progress *screen* is part of the review UI
        (roadmap item 16), which is where the question of how a batch and a review sit together
        gets decided; item 17 owes that screen its data, not a second design for it. Until then
        this endpoint and ``python -m sightline.batch status`` are the surfaces, and both report
        what each stage examined rather than a percentage nobody can check.
        """
        store: RunStore | None = getattr(request.app.state, "store", None)
        progress = progress_of(store, run_id) if store is not None else None
        if progress is None:
            return JSONResponse({"error": f"no run {run_id!r} in this run store"}, status_code=404)
        return JSONResponse(progress.to_dict())

    # -- courses -----------------------------------------------------------------------------

    @router.get("/courses", include_in_schema=False)
    async def courses_page(request: Request) -> Any:
        return _render_courses(request, templates)

    @router.post("/courses", include_in_schema=False)
    async def resolve_courses(
        request: Request,
        references: Annotated[str, Form()] = "",
        upload: UploadFile | None = None,
    ) -> Any:
        credentials = _credentials(request).canvas()
        host = credentials.host
        batches: list[tuple[str, RefBatch]] = []

        if references.strip():
            batches.append(("Pasted references", parse_course_refs(references, expected_host=host)))

        if upload is not None and upload.filename:
            content = await upload.read()
            if len(content) > MAX_CSV_BYTES:
                return _render_courses(
                    request,
                    templates,
                    upload_error=(
                        f"{upload.filename} is {len(content) // 1024} KB. The limit is "
                        f"{MAX_CSV_BYTES // 1024} KB — that is far more than a course list needs, "
                        "so this is probably the wrong file."
                    ),
                )
            batches.append(
                (
                    upload.filename,
                    parse_course_ref_csv(content, expected_host=host, filename=upload.filename),
                )
            )

        if not batches:
            return _render_courses(
                request,
                templates,
                upload_error=(
                    "Nothing to resolve. Paste one or more course URLs or ids, or choose a CSV "
                    "file, then submit."
                ),
            )

        lookups = await _look_up(credentials, _accepted(batches))
        return _render_courses(request, templates, batches=batches, lookups=lookups)

    return router


# ---------------------------------------------------------------------------------------------
# Shared work
# ---------------------------------------------------------------------------------------------


def _pulls(request: Request) -> PullRegistry:
    registry: PullRegistry | None = getattr(request.app.state, "pulls", None)
    if registry is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the install registry is not initialised")
    return registry


def _model_endpoint(request: Request) -> str:
    """The endpoint in force, or the failure that says none is configured."""
    preference = _preferences(request).model_host()
    if not preference.endpoint:
        raise ModelHostNotConfiguredError("No model host endpoint is configured.")
    return preference.endpoint


async def _run_host_check(request: Request) -> tuple[HostReport | None, ModelHostError | None]:
    """Health, from the endpoint in force. Never raises for a host that is simply down."""
    try:
        endpoint = _model_endpoint(request)
    except ModelHostError as exc:
        return None, exc
    settings = request.app.state.settings
    try:
        client = OllamaClient(
            endpoint,
            concurrency=settings.ollama_concurrency,
            context_tokens=settings.ollama_context_tokens,
        )
    except ModelHostError as exc:
        # A stored endpoint is normalized on the way in; the environment fallback is not, so a
        # container started with a malformed OLLAMA_BASE_URL reaches here.
        return None, exc
    async with client:
        return await client.health(), None


def _credentials(request: Request) -> CredentialStore:
    store: CredentialStore | None = getattr(request.app.state, "credentials", None)
    if store is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the credential store is not initialised")
    return store


def _preferences(request: Request) -> PreferenceStore:
    store: PreferenceStore | None = getattr(request.app.state, "preferences", None)
    if store is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the preference store is not initialised")
    return store


async def _run_connection_test(
    request: Request, course_reference: str
) -> tuple[ConnectionReport | None, CredentialError | None]:
    try:
        credentials = _credentials(request).canvas()
    except CredentialError as exc:
        return None, exc
    try:
        async with CanvasClient(credentials.base_url, credentials.token) as client:
            return (
                await client.test_connection(course_reference=course_reference.strip() or None),
                None,
            )
    except CanvasError as exc:
        # Raised before any request when nothing is configured. Rendered as a one-check report so
        # the screen has one shape rather than two.
        return (
            ConnectionReport(
                host=credentials.host,
                checks=(
                    ConnectionCheck(
                        name="Configuration",
                        status="failed",
                        detail=" ".join(filter(None, (exc.summary, exc.detail))),
                        remedy=exc.remedy,
                    ),
                ),
            ),
            None,
        )


def _accepted(batches: list[tuple[str, RefBatch]]) -> list[CourseRef]:
    """Every accepted reference across the batches, deduplicated, in the order given."""
    seen: set[str] = set()
    ordered: list[CourseRef] = []
    for _label, batch in batches:
        for ref in batch.accepted:
            if ref.course_id not in seen:
                seen.add(ref.course_id)
                ordered.append(ref)
    return ordered


async def _look_up(credentials: CanvasCredentials, refs: list[CourseRef]) -> list[dict[str, Any]]:
    """Resolve each reference against Canvas, reporting every outcome including "not attempted".

    Parsing already succeeded for everything here; this is the part that needs a token. When
    there is none, every row says so rather than the section quietly disappearing.
    """
    if not refs:
        return []
    if not credentials.complete:
        return [
            {
                "ref": ref,
                "state": "not attempted",
                "detail": "No Canvas credential is configured, so nothing was looked up.",
                "remedy": "Enter a base URL and token on the Settings screen.",
            }
            for ref in refs
        ]

    try:
        client = CanvasClient(credentials.base_url, credentials.token)
    except CanvasError as exc:
        # A stored base URL is normalized on the way in, but the environment fallback is not: a
        # container started with a malformed CANVAS_BASE_URL reaches here. Reporting it per row
        # keeps the screen's shape; letting it escape would be a 500 with the remedy in a log.
        return [
            {
                "ref": ref,
                "state": "not attempted",
                "detail": " ".join(filter(None, (exc.summary, exc.detail))),
                "remedy": exc.remedy,
            }
            for ref in refs
        ]

    results: list[dict[str, Any]] = []
    async with client:
        for position, ref in enumerate(refs):
            if position >= MAX_LOOKUPS:
                results.append(
                    {
                        "ref": ref,
                        "state": "not attempted",
                        "detail": (
                            f"Parsed, but not looked up: a batch is capped at {MAX_LOOKUPS} "
                            "courses."
                        ),
                        "remedy": "Submit the rest separately.",
                    }
                )
                continue
            try:
                resolved = await client.resolve_course(ref)
            except CanvasError as exc:
                results.append(
                    {
                        "ref": ref,
                        "state": "failed",
                        "detail": " ".join(filter(None, (exc.summary, exc.detail))),
                        "remedy": exc.remedy,
                    }
                )
                continue
            results.append(
                {
                    "ref": ref,
                    "state": "resolved",
                    "course": resolved.course,
                    "access": resolved.access,
                    "detail": resolved.access.detail,
                    "remedy": resolved.access.remedy,
                }
            )
    return results


# ---------------------------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------------------------

#: What the ``saved`` query parameter means, spelled out rather than rendered as a raw token.
_SAVED_MESSAGES = {
    "yes": "Saved.",
    "normalized": (
        "Saved. The base URL was trimmed to its scheme and host — Canvas API calls are made "
        "against the root of the instance, not a page inside it."
    ),
    "forgotten": (
        "The stored Canvas credential has been deleted. Any value in the environment still "
        "applies."
    ),
    "export_on": (
        "Saved. Sightline will ask Canvas to build a fresh export of each course it reviews. The "
        "export appears in that course's export list; no course content is changed."
    ),
    "export_off": (
        "Saved. Sightline will not ask Canvas to build an export. It will read an export the "
        "course already has, and report the steps to create one when there is none."
    ),
    "model_host": (
        "Saved. The endpoint was trimmed to its scheme, host and port. Nothing has been called "
        "yet — use Check the model host to see which host answers and what it can do."
    ),
    "model_host_forgotten": (
        "The stored model host endpoint has been deleted. Any value in the environment still "
        "applies."
    ),
    "model_chosen": "Saved. Review work will be sent to this model.",
    "installing": (
        "The install has started and is running in the background. Reload this page to see how "
        "far it has got; closing the tab does not stop it."
    ),
}


def _render_settings(
    request: Request,
    templates: Jinja2Templates,
    *,
    saved: str | None = None,
    report: ConnectionReport | None = None,
    credential_error: CredentialError | None = None,
    host_report: HostReport | None = None,
    model_error: ModelHostError | None = None,
) -> Any:
    credentials: CanvasCredentials | None = None
    error = credential_error
    try:
        credentials = _credentials(request).canvas()
    except CredentialError as exc:
        error = error or exc
    preferences = _preferences(request)
    export: ExportPreference = preferences.request_content_export()
    model_host: ModelHostPreference = preferences.model_host()
    review_model: ReviewModelPreference = preferences.review_model()
    selection = host_report.selection if host_report else None
    # None when the host has not been checked this page load, which the template renders as that
    # rather than as "the chosen model is missing".
    chosen_card = selection.get(review_model.name) if selection and review_model.name else None
    manifest = load_manifest()
    settings = request.app.state.settings
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "nav": "settings",
            "credentials": credentials,
            "presence": credentials.presence() if credentials else [],
            "saved_message": _SAVED_MESSAGES.get(saved or ""),
            "report": report,
            "credential_error": error,
            "scopes": REQUIRED_SCOPES,
            "permissions": REQUIRED_PERMISSIONS,
            "export": export,
            "writes": [scope for scope in REQUIRED_SCOPES if scope.writes],
            "model_host": model_host,
            "review_model": review_model,
            "review_model_card": chosen_card,
            "host_report": host_report,
            "model_error": model_error,
            "manifest": manifest,
            "manifest_rows": manifest.rows(selection),
            "installs": _pulls(request).states(),
            "concurrency": settings.ollama_concurrency,
            "context_tokens": settings.ollama_context_tokens,
        },
    )


def _render_courses(
    request: Request,
    templates: Jinja2Templates,
    *,
    batches: list[tuple[str, RefBatch]] | None = None,
    lookups: list[dict[str, Any]] | None = None,
    upload_error: str | None = None,
) -> Any:
    credentials: CanvasCredentials | None = None
    try:
        credentials = _credentials(request).canvas()
    except CredentialError:
        credentials = None
    return templates.TemplateResponse(
        request=request,
        name="courses.html",
        context={
            "nav": "courses",
            "batches": batches or [],
            "lookups": lookups or [],
            "upload_error": upload_error,
            "canvas_ready": bool(credentials and credentials.complete),
            "max_lookups": MAX_LOOKUPS,
        },
    )
