"""The settings screen and the New run screen: resolving courses, then starting a run on them.

Server-rendered HTML with light progressive enhancement, no SPA framework (SPEC §09). Both screens
work with JavaScript switched off: every action is a form post that re-renders the page with the
result. The JSON endpoints exist for the suite and for later items, not because the pages need
them.

The New run screen is the course screen of item 02 extended rather than duplicated (roadmap item
36): ``POST /courses`` resolves what was pasted, and the courses Canvas resolved come back with a
Start form — a rubric picker and the reasons a run cannot start now, from
:mod:`sightline.new_run` — that posts to ``/runs``. A started run is followed on its own page,
served by :mod:`sightline.run_screens`. Bundles stay on the command line.

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

Every form here carries a CSRF token, in both modes (roadmap item 44, :mod:`sightline.csrf`): a
local tool listens on a port any page in the same browser can post to. There is still no login
in the app. In sign-in mode the person signs in at a proxy in front of it
(:mod:`sightline.signin`), and the Reviewer section of Settings gives way to the signed-in name.

In sign-in mode each person's Canvas token is their own (roadmap item 45, :mod:`sightline.access`):
the token field on Settings stores the signed-in person's, the course lookup, the connection test
and Start use it, and a run records who started it. The instance settings — the Canvas base URL,
the export preference, the model host and the model, and installing a model on that host — are
changed by an admin only; anybody else sees their state and no form, and a post is refused.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .access import ADMIN_ONLY_SUMMARY, Viewer, admin_only_remedy, viewer, visible_run
from .batch import BatchTooLargeError, create_batch_run, progress_of
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
from .new_run import Blocker, blockers, chosen_courses, is_queued, queue_notice, rubric_choices
from .paths import DataPaths
from .preferences import (
    REVIEWER_MAX,
    ExportPreference,
    ModelHostPreference,
    PreferenceStore,
    ReviewerNameError,
    ReviewModelPreference,
)
from .review.identity import current_reviewer
from .rubric.accepted import BindingsError, resolve_pack
from .run_screens import payload_of, queue_positions
from .runner import BackgroundRunner
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

#: A course name carried through the Start form is only ever echoed back onto the page; this keeps
#: a tampered form from echoing back a novel.
MAX_LABEL = 200


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
        canvas_base_url: Annotated[str | None, Form()] = None,
        canvas_token: Annotated[str, Form()] = "",
    ) -> Any:
        store = _credentials(request)
        who = viewer(request)
        if who.signed_in:
            return _save_signed_in(request, templates, who, canvas_base_url, canvas_token)
        canvas_base_url = canvas_base_url or ""
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
        if (refused := _admin_only(request, templates)) is not None:
            return refused
        enabled = bool(request_export)
        _preferences(request).set_request_content_export(enabled)
        return RedirectResponse(
            f"/settings?saved={'export_on' if enabled else 'export_off'}", status_code=303
        )

    @router.post("/settings/reviewer", include_in_schema=False)
    async def save_reviewer(
        request: Request,
        reviewer: Annotated[str, Form()] = "",
    ) -> Any:
        """Set the name a rating recorded on the review screen is recorded as (item 16, Q1)."""
        if request.app.state.settings.signin:
            return _reviewer_is_signed_in(request, templates)
        try:
            _preferences(request).set_reviewer(reviewer)
        except ReviewerNameError as exc:
            return _render_settings(request, templates, reviewer_error=exc)
        return RedirectResponse("/settings?saved=reviewer#reviewer", status_code=303)

    @router.post("/settings/reviewer/forget", include_in_schema=False)
    async def forget_reviewer(request: Request) -> Any:
        if request.app.state.settings.signin:
            return _reviewer_is_signed_in(request, templates)
        _preferences(request).clear_reviewer()
        return RedirectResponse("/settings?saved=reviewer_forgotten#reviewer", status_code=303)

    @router.post("/settings/forget", include_in_schema=False)
    async def forget_settings(request: Request) -> Any:
        who = viewer(request)
        if who.signed_in:
            # Signed in, the stored credential this forgets is the person's own token; the base
            # URL is the instance's, which an admin clears by saving the field empty.
            assert who.subject is not None  # noqa: S101 - a signed-in viewer has a subject
            _credentials(request).clear_user_token(who.subject)
            return RedirectResponse("/settings?saved=token_forgotten", status_code=303)
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
        if (refused := _admin_only(request, templates)) is not None:
            return refused
        try:
            _preferences(request).set_model_host(model_host_endpoint)
        except ModelHostError as exc:
            return _render_settings(request, templates, model_error=exc)
        return RedirectResponse("/settings?saved=model_host", status_code=303)

    @router.post("/settings/model-host/forget", include_in_schema=False)
    async def forget_model_host(request: Request) -> Any:
        if (refused := _admin_only(request, templates)) is not None:
            return refused
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
        if (refused := _admin_only(request, templates)) is not None:
            return refused
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

        An admin's action in sign-in mode: an install changes what the shared model host holds.
        """
        if (refused := _admin_only(request, templates)) is not None:
            return refused
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

        The data of the run-progress screen (``/runs/{run_id}``, :mod:`sightline.run_screens`,
        roadmap item 36), which renders it server-side and whose optional script polls this to
        know when to re-read the page. ``python -m sightline.batch status`` prints the same. All
        three report what each stage examined rather than a percentage nobody can check.

        In sign-in mode a run somebody else started answers exactly as a run that does not exist
        (roadmap item 45).
        """
        store: RunStore | None = getattr(request.app.state, "store", None)
        visible = store is not None and visible_run(request, store, run_id) is not None
        progress = progress_of(store, run_id) if store is not None and visible else None
        if progress is None:
            return JSONResponse({"error": f"no run {run_id!r} in this run store"}, status_code=404)
        # A queued run carries how many runs are ahead of it (roadmap item 46), so the page that
        # polls this reloads when it moves up.
        ahead = queue_positions(request).get(run_id) if is_queued(progress.status) else None
        return JSONResponse(payload_of(progress.to_dict(), ahead))

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
        credentials = _canvas(request)
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

    @router.post("/runs", include_in_schema=False)
    async def start_run(request: Request) -> Any:
        """Record a batch over the chosen courses and start it in the background.

        Every check the Start form showed is made again here, because a page rendered a minute ago
        is not evidence about now — another run may have started since. Nothing between the checks
        and :meth:`BackgroundRunner.submit` awaits, so no other request can start one in between.

        A run started while another is active, or while others wait, is recorded queued and left
        to the runner's queue (roadmap item 46); it starts when every run ahead of it has ended.
        """
        form = await request.form()
        courses = chosen_courses([str(value) for value in form.getlist("course")])
        labels = {
            course: str(form.get(f"label-{course}", ""))[:MAX_LABEL].strip() for course in courses
        }
        rubric = str(form.get("rubric", "")).strip()
        refusals = _start_refusals(request, courses, rubric)
        if refusals:
            return _render_courses(
                request,
                templates,
                chosen=[(course, labels[course]) for course in courses],
                rubric=rubric,
                refusals=refusals,
                status=409,
            )
        try:
            # The starter's bindings in force for the rubric now (roadmap items 22 and 48): only
            # confirmed ones, their own, and the run records which, so its reports read them back.
            who = viewer(request)
            digest = resolve_pack(rubric, _paths(request), starter=who.subject).digest
            queued = _runner(request).must_queue()
            run_id = create_batch_run(
                _store(request),
                courses,
                rubric_pack=rubric,
                bindings_digest=digest,
                cap=request.app.state.settings.batch_cap,
                # Whose run it is (roadmap item 45): it reads Canvas with their token, and only
                # they or an admin see it. Nobody in local mode.
                starter_subject=who.subject,
                starter_name=who.name,
                queued=queued,
            )
        except BindingsError as exc:
            return _render_courses(
                request,
                templates,
                chosen=[(course, labels[course]) for course in courses],
                rubric=rubric,
                refusals=[
                    Blocker(
                        summary=f"The bindings accepted for this rubric cannot be read: {exc}.",
                        remedy="Its page says what was found.",
                        href=f"/rubrics/{rubric}",
                        link="Rubric",
                    )
                ],
                status=409,
            )
        except BatchTooLargeError as exc:
            return _render_courses(
                request,
                templates,
                chosen=[(course, labels[course]) for course in courses],
                rubric=rubric,
                refusals=[Blocker(summary=exc.summary, remedy=exc.remedy)],
                status=409,
            )
        if queued:
            _runner(request).start_queue()
        else:
            _runner(request).submit(run_id)
        log.info(
            "run_started_from_screen",
            run_id=run_id,
            courses=len(courses),
            rubric=rubric,
            queued=queued,
        )
        return RedirectResponse(f"/runs/{run_id}", status_code=303)

    return router


# ---------------------------------------------------------------------------------------------
# Shared work
# ---------------------------------------------------------------------------------------------


def _store(request: Request) -> RunStore:
    store: RunStore | None = getattr(request.app.state, "store", None)
    if store is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the run store is not initialised")
    return store


def _runner(request: Request) -> BackgroundRunner:
    runner: BackgroundRunner | None = getattr(request.app.state, "runner", None)
    if runner is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the background runner is not initialised")
    return runner


def _paths(request: Request) -> DataPaths:
    paths: DataPaths | None = getattr(request.app.state, "paths", None)
    if paths is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the data root is not initialised")
    return paths


def _start_blockers(request: Request) -> list[Blocker]:
    return blockers(
        store=_store(request),
        runner=_runner(request),
        credentials=_credentials(request),
        preferences=_preferences(request),
        proposing=getattr(request.app.state, "proposing", None),
        viewer=viewer(request),
    )


def _start_refusals(request: Request, courses: list[str], rubric: str) -> list[Blocker]:
    """Why this Start cannot go ahead: the form's own faults first, then the screen's blockers."""
    refusals: list[Blocker] = []
    if not courses:
        refusals.append(
            Blocker(
                summary="No course was chosen, so no run was started.",
                remedy="Tick at least one of the courses Canvas resolved, then press Start.",
            )
        )
    else:
        try:
            host = _canvas(request).host
        except CredentialError:
            host = None  # reported below as a blocker; the ids still parse without a host
        parsed = parse_course_refs("\n".join(courses), expected_host=host)
        if [ref.course_id for ref in parsed.accepted] != courses:
            refusals.append(
                Blocker(
                    summary=(
                        "The form carried something that is not a course id, so no run was "
                        "started."
                    ),
                    remedy="Resolve the courses again on this screen and start from its results.",
                )
            )
    if not any(choice.id == rubric for choice in rubric_choices(rubric)):
        available = ", ".join(choice.id for choice in rubric_choices()) or "none"
        refusals.append(
            Blocker(
                summary=f"No shipped rubric has the id {rubric!r}, so no run was started.",
                remedy=f"Choose one of the rubrics listed: {available}.",
            )
        )
    return refusals + _start_blockers(request)


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


def _canvas(request: Request) -> CanvasCredentials:
    """The Canvas credential this request acts with: the signed-in person's own token (item 45).

    Local mode has one user, whose credential is the one stored credential, as before.
    """
    return _credentials(request).canvas_for(viewer(request).subject)


def _admin_only(request: Request, templates: Jinja2Templates) -> Any:
    """A refusal page when this request may not change an instance setting; ``None`` when it may.

    403, not 404: the setting exists and its state is on the page; what is refused is the change.
    """
    if viewer(request).admin:
        return None
    return templates.TemplateResponse(
        request=request,
        name="review_message.html",
        context={
            "nav": "settings",
            "summary": ADMIN_ONLY_SUMMARY,
            "remedy": admin_only_remedy(request.app.state.settings.signin_admin_group),
            "back": "Back to Settings",
            "back_href": "/settings",
        },
        status_code=403,
    )


def _save_signed_in(
    request: Request,
    templates: Jinja2Templates,
    who: Viewer,
    base_url: str | None,
    token: str,
) -> Any:
    """The Canvas form in sign-in mode: the base URL is the instance's, the token the person's.

    The base URL field is on an admin's form only. A post that carries it from anybody else is
    refused whole, before the token is touched, so a refused post changes nothing.
    """
    store = _credentials(request)
    assert who.subject is not None  # noqa: S101 - a signed-in viewer has a subject
    if base_url is not None and (refused := _admin_only(request, templates)) is not None:
        return refused
    normalized: str | None = None
    try:
        if base_url is not None:
            normalized = store.save_canvas(base_url=base_url)
    except CredentialError as exc:
        return _render_settings(request, templates, credential_error=exc, status=400)
    if token.strip():
        store.save_user_token(who.subject, token)
    if base_url is None and not token.strip():
        return RedirectResponse("/settings", status_code=303)
    saved = "normalized" if normalized and normalized != (base_url or "").strip() else "yes"
    return RedirectResponse(f"/settings?saved={saved}", status_code=303)


def _preferences(request: Request) -> PreferenceStore:
    store: PreferenceStore | None = getattr(request.app.state, "preferences", None)
    if store is None:  # pragma: no cover - only reachable without the app lifespan
        raise RuntimeError("the preference store is not initialised")
    return store


async def _run_connection_test(
    request: Request, course_reference: str
) -> tuple[ConnectionReport | None, CredentialError | None]:
    try:
        credentials = _canvas(request)
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
    "token_forgotten": (
        "Your stored Canvas token has been deleted. A run you start cannot read Canvas until you "
        "store one again, and neither can a run of yours that resumes after a restart."
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
    "reviewer": "Saved. A rating recorded on the review screen is now recorded with this name.",
    "reviewer_forgotten": (
        "The reviewer name has been deleted. The review screen records no rating until one is set "
        "again; ratings already recorded keep their name."
    ),
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
    reviewer_error: ReviewerNameError | None = None,
    status: int = 200,
) -> Any:
    credentials: CanvasCredentials | None = None
    error = credential_error
    try:
        credentials = _canvas(request)
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
            # Shown beside the section it is about, which the redirect scrolls to.
            "saved_section": "reviewer" if (saved or "").startswith("reviewer") else None,
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
            "reviewer": preferences.reviewer(),
            "reviewer_error": reviewer_error,
            "reviewer_max": REVIEWER_MAX,
            # Sign-in mode (item 44): the Reviewer field gives way to the signed-in name.
            "signed_in": current_reviewer(request) if settings.signin else None,
            # Item 45: who may change the instance settings. Always true in local mode.
            "admin": viewer(request).admin,
            "admin_remedy": admin_only_remedy(settings.signin_admin_group),
        },
        status_code=status,
    )


def _reviewer_is_signed_in(request: Request, templates: Jinja2Templates) -> Any:
    """A post to the Reviewer setting in sign-in mode, where there is no such setting."""
    return templates.TemplateResponse(
        request=request,
        name="review_message.html",
        context={
            "nav": "settings",
            "summary": (
                "Nothing was saved: in sign-in mode a rating is recorded as the person signed in, "
                "and there is no Reviewer setting to change."
            ),
            "remedy": "Your name comes from the sign-in service; change it there.",
            "back": "Back to Settings",
            "back_href": "/settings",
        },
        status_code=409,
    )


def _render_courses(
    request: Request,
    templates: Jinja2Templates,
    *,
    batches: list[tuple[str, RefBatch]] | None = None,
    lookups: list[dict[str, Any]] | None = None,
    upload_error: str | None = None,
    chosen: list[tuple[str, str]] | None = None,
    rubric: str | None = None,
    refusals: list[Blocker] | None = None,
    status: int = 200,
) -> Any:
    credentials: CanvasCredentials | None = None
    try:
        credentials = _canvas(request)
    except CredentialError:
        credentials = None
    start = _start_panel(request, lookups or [], chosen=chosen, rubric=rubric, refusals=refusals)
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
            "start": start,
        },
        status_code=status,
    )


def _start_panel(
    request: Request,
    lookups: list[dict[str, Any]],
    *,
    chosen: list[tuple[str, str]] | None,
    rubric: str | None,
    refusals: list[Blocker] | None,
) -> dict[str, Any] | None:
    """The Start form, or ``None`` when there is no course to start a run on.

    Offered for the courses Canvas resolved, ticked where the token can read the content. A course
    that resolved but whose content cannot be read is offered unticked: its acquisition would fail,
    and the lookup table above says why. After a refused Start the form comes back with what was
    posted, so nobody has to resolve the courses a second time.
    """
    if chosen is not None:
        courses = [
            {"id": course, "label": label or f"Course {course}", "checked": True, "note": None}
            for course, label in chosen
        ]
    else:
        courses = [
            {
                "id": row["ref"].course_id,
                "label": row["course"].label,
                "checked": row["access"].readable,
                "note": None if row["access"].readable else row["access"].detail,
            }
            for row in lookups
            if row["state"] == "resolved"
        ]
    if not courses and not refusals:
        return None
    # A refused Start already carries the current blockers; reading them twice would list each
    # one twice.
    blocked = refusals if refusals is not None else _start_blockers(request)
    return {
        "courses": courses,
        "rubrics": rubric_choices(rubric, paths=_paths(request), viewer=viewer(request)),
        "blockers": blocked,
        "refused": refusals is not None,
        # Roadmap item 46: not a refusal. A run started now waits behind the ones ahead.
        "queue": queue_notice(_store(request), _runner(request), viewer=viewer(request)),
        "cap": request.app.state.settings.batch_cap,
        "requests_export": _preferences(request).request_content_export().enabled,
    }
