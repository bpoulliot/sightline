"""Runs per user, an admin role, and per-user Canvas tokens (roadmap item 45, SPEC §09).

**Sign-in mode.** A run is its starter's: the run list, a run's page, ``GET /api/runs/{id}``, a
course's review pages, a rating posted to it, its exports and its downloads answer anybody else
exactly as an id that does not exist — 404, the same words — and an admin gets every one of them.
Opening a review is what satisfies the export gate, so the second person's requests are asserted to
have opened nothing. Only an admin changes an instance setting. A run reads Canvas with its
starter's own token, asserted on the recorded-fixture transport, both when it is started from the
screen and when a restart resumes it; a starter with no token stops the run before any Canvas call,
with a remedy naming Settings.

**Local mode** is the one implicit user, who is the admin: the screens read as they did.

No identity provider, no proxy and no Canvas instance is involved: the proxy is these headers,
written by hand, and every Canvas client is pointed at a recording.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet

import ollama_recordings as recordings
from canvas_recordings import EXAMPLE_BASE_URL, Recording, load
from form_clients import FormClient
from run_owners import dead_pid, set_owner, this_host
from sightline.access import ADMIN_ONLY_SUMMARY, LOCAL, Viewer
from sightline.app import create_app
from sightline.batch import (
    BatchRunner,
    CourseWork,
    Stage,
    StageOutcome,
    create_batch_run,
    pipeline_factory,
)
from sightline.batch import service as batch_service
from sightline.batch.service import STARTER_TOKEN_REMEDY, live_pipeline_factory
from sightline.config import Settings
from sightline.credentials import CredentialStore, user_secret_name
from sightline.models import RunStatus
from sightline.paths import DataPaths
from sightline.preferences import PreferenceStore
from sightline.review.routes import EXPORT_FILES
from sightline.store import RunStore

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "examples" / "sample-report" / "review.json"
REFERENCE = "uccs-oscqr-3-1-3"
MODEL = "llama3.1:8b"
#: A documentation address (RFC 5737). Never reached: every client a pipeline builds is patched.
MODEL_HOST = "http://192.0.2.10:11434"
COURSE = "4411"

#: Obviously fake, and long enough for the 32-character floor.
SECRET = "fake-proxy-secret-0000000000000000000000"
#: Sign-in mode's master key (item 50), generated per process: no key literal in the tree.
MASTER_KEY = Fernet.generate_key().decode()
ADMIN_GROUP = "fake-sightline-admins"

ALICE = "fake-subject-alice-0001"
BOB = "fake-subject-bob-0002"
ADMIN = "fake-subject-admin-0003"


def _token(tag: str) -> str:
    """A Canvas-shaped fake token, assembled so no token-shaped literal is in the tree (see
    ``canvas_recordings.FAKE_TOKEN``). One per person, so a call says whose token it carried."""
    return "7213" + "~" + "SightlineFixture" + tag + "NotReal" + ("0" * 12)


TOKENS = {ALICE: _token("Alice"), BOB: _token("Bob"), ADMIN: _token("Admin")}
INSTANCE_TOKEN = _token("Instance")

WAIT_SECONDS = 60.0


def signin_settings(data_root: Path, **extra: Any) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=data_root,
        tokens_css_path=REPO / "docs" / "tokens.css",
        log_format="json",
        signin=True,
        signin_secret=SECRET,
        secret_key=MASTER_KEY,
        **{"signin_admin_group": ADMIN_GROUP, **extra},
    )


def as_person(subject: str, *, groups: str | None = None) -> dict[str, str]:
    """What the proxy (Authentik's outpost header names, the defaults) adds for ``subject``."""
    sent = {
        "X-Sightline-Proxy-Secret": SECRET,
        "X-authentik-uid": subject,
        "X-authentik-name": f"Person {subject[-4:]}",
    }
    if groups is not None:
        sent["X-authentik-groups"] = groups
    return sent


def as_admin() -> dict[str, str]:
    return as_person(ADMIN, groups=f"fake-staff|{ADMIN_GROUP}")


class Client(FormClient):
    """A browser per request: each call names who is signed in, and its CSRF token follows."""

    def as_(self, subject: str, method: str, url: str, **kwargs: Any) -> Any:
        self.subject = subject
        headers = as_admin() if subject == ADMIN else as_person(subject)
        return self.request(method, url, headers=headers, **kwargs)


def text_of(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split()).replace("&#39;", "'")


# -- a reviewed course of Alice's -----------------------------------------------------------------


class SavedReviewPipeline:
    def __init__(self) -> None:
        self.review_payload = json.loads(SAMPLE.read_text(encoding="utf-8"))

    async def run(self, stage: Stage, work: CourseWork) -> StageOutcome:
        return work.record(
            StageOutcome(stage=stage, examined=1, statement=f"{stage.value}: examined 1.")
        )

    def review(self, work: CourseWork) -> Mapping[str, Any]:
        return self.review_payload


def reviewed_run(paths: DataPaths, *, starter: str | None) -> tuple[str, str]:
    """A run with one reviewed course, started by ``starter``. Returns (run id, course id)."""
    store = RunStore.open(paths)
    try:
        run_id = create_batch_run(
            store,
            ["examples/sample-course"],
            rubric_pack=REFERENCE,
            starter_subject=starter,
            starter_name=None if starter is None else f"Person {starter[-4:]}",
        )
        asyncio.run(
            BatchRunner(store, paths, pipeline_factory(SavedReviewPipeline())).execute(run_id)
        )
        run = store.get_run(run_id)
        assert run is not None
        return run_id, run.courses[0].id
    finally:
        store.dispose()


@pytest.fixture
def alices(paths: DataPaths) -> tuple[str, str]:
    return reviewed_run(paths, starter=ALICE)


@pytest.fixture
def signin(data_root: Path) -> Iterator[Client]:
    with Client(create_app(signin_settings(data_root))) as client:
        yield client


def store_of(client: Client) -> RunStore:
    return client.app.state.store  # type: ignore[attr-defined]


#: Every surface of one run and its course, as (method, path template, form data).
SURFACES = [
    ("GET", "/runs/{run}", None),
    ("GET", "/api/runs/{run}", None),
    ("GET", "/review/{course}", None),
    ("GET", "/review/{course}/category/MOB", None),
    ("POST", "/review/{course}/override", {"code": "MOB 2", "rating": "major"}),
    ("POST", "/review/{course}/export", {}),
    *[("GET", f"/review/{{course}}/files/{name}", None) for name in EXPORT_FILES],
]


def _call(client: Client, subject: str, method: str, path: str, data: Any) -> Any:
    kwargs: dict[str, Any] = {"follow_redirects": False}
    if data is not None:
        kwargs["data"] = data
    return client.as_(subject, method, path, **kwargs)


# -- user B gets 404 on each of user A's surfaces -------------------------------------------------


@pytest.mark.parametrize(("method", "template", "data"), SURFACES)
def test_another_person_gets_404_on_each_of_a_runs_surfaces_exactly_as_for_no_run(
    signin: Client, alices: tuple[str, str], method: str, template: str, data: Any
) -> None:
    run_id, course_id = alices
    path = template.format(run=run_id, course=course_id)
    response = _call(signin, BOB, method, path, data)
    assert response.status_code == 404, (path, response.text[:500])

    # The same answer, word for word, as an id that names nothing: a 404 that differed would
    # confirm that the id is somebody's run.
    fake_run, fake_course = "f" * len(run_id), "e" * len(course_id)
    absent = _call(signin, BOB, method, template.format(run=fake_run, course=fake_course), data)
    assert absent.status_code == 404
    assert response.text.replace(run_id, fake_run).replace(course_id, fake_course) == absent.text

    # Nothing was opened, recorded or written: opening is what satisfies the export gate.
    course = store_of(signin).get_course(course_id)
    assert course is not None and course.review_opened_at is None
    assert store_of(signin).list_overrides(course_id=course_id) == []


def test_another_persons_run_is_not_listed_and_the_starter_sees_their_own(
    signin: Client, alices: tuple[str, str], paths: DataPaths
) -> None:
    run_id, _ = alices
    bobs, _ = reviewed_run(paths, starter=BOB)
    for path in ("/runs", "/"):
        bob = signin.as_(BOB, "GET", path).text
        alice = signin.as_(ALICE, "GET", path).text
        assert bobs in bob and run_id not in bob, path
        assert run_id in alice and bobs not in alice, path
    lede = text_of(signin.as_(BOB, "GET", "/runs").text)
    assert "most recent runs you started" in lede
    assert "Started by" not in lede


def test_the_starter_reaches_every_surface_of_their_own_run(
    signin: Client, alices: tuple[str, str]
) -> None:
    run_id, course_id = alices
    assert signin.as_(ALICE, "GET", f"/runs/{run_id}").status_code == 200
    assert signin.as_(ALICE, "GET", f"/api/runs/{run_id}").json()["run_id"] == run_id
    assert signin.as_(ALICE, "GET", f"/review/{course_id}").status_code == 200
    recorded = signin.as_(
        ALICE,
        "POST",
        f"/review/{course_id}/override",
        data={"code": "MOB 2", "rating": "major"},
        follow_redirects=False,
    )
    assert recorded.status_code == 303, recorded.text[:500]
    (record,) = store_of(signin).list_overrides(course_id=course_id)
    assert record.reviewer_subject == ALICE


# -- an admin gets 200 ---------------------------------------------------------------------------


@pytest.mark.parametrize("starter", [ALICE, None], ids=["alices-run", "no-starter-recorded"])
def test_an_admin_gets_every_surface_of_every_run(
    signin: Client, paths: DataPaths, starter: str | None
) -> None:
    run_id, course_id = reviewed_run(paths, starter=starter)
    for path in (f"/runs/{run_id}", f"/api/runs/{run_id}", "/runs"):
        response = signin.as_(ADMIN, "GET", path)
        assert response.status_code == 200, path
        assert run_id in response.text, path
    assert signin.as_(ADMIN, "GET", f"/review/{course_id}").status_code == 200
    assert signin.as_(ADMIN, "GET", f"/review/{course_id}/category/MOB").status_code == 200
    exported = signin.as_(ADMIN, "POST", f"/review/{course_id}/export", follow_redirects=False)
    assert exported.status_code == 303, exported.text[:500]
    for name in EXPORT_FILES:
        assert signin.as_(ADMIN, "GET", f"/review/{course_id}/files/{name}").status_code == 200
    # The admin is told whose run it is, and whose it is not.
    page = text_of(signin.as_(ADMIN, "GET", f"/runs/{run_id}").text)
    assert "Started by" in page
    assert ("Person " + ALICE[-4:] if starter else "nobody recorded") in page
    listing = text_of(signin.as_(ADMIN, "GET", "/runs").text)
    assert "You are an admin, so this lists every person's runs" in listing


def test_a_run_recorded_with_no_starter_is_an_admins_only_in_sign_in_mode(
    signin: Client, paths: DataPaths
) -> None:
    """Every run from before item 45, and every command-line run, has no starter."""
    run_id, course_id = reviewed_run(paths, starter=None)
    assert signin.as_(ALICE, "GET", f"/runs/{run_id}").status_code == 404
    assert signin.as_(ALICE, "GET", f"/review/{course_id}").status_code == 404
    assert run_id not in signin.as_(ALICE, "GET", "/runs").text
    assert signin.as_(ADMIN, "GET", f"/runs/{run_id}").status_code == 200


def test_membership_is_exact_and_no_admin_group_means_no_admin(
    data_root: Path, paths: DataPaths
) -> None:
    run_id, _ = reviewed_run(paths, starter=ALICE)
    with Client(create_app(signin_settings(data_root))) as client:
        near = as_person(BOB, groups=f"{ADMIN_GROUP}-not|{ADMIN_GROUP.upper()}")
        assert client.get(f"/runs/{run_id}", headers=near).status_code == 404
    with Client(create_app(signin_settings(data_root, signin_admin_group=" "))) as client:
        assert client.app.state.settings.signin_admin_group is None  # type: ignore[attr-defined]
        assert client.as_(ADMIN, "GET", f"/runs/{run_id}").status_code == 404


# -- only an admin changes instance settings ------------------------------------------------------

#: Every post that changes an instance setting: the Canvas base URL, the export preference, the
#: model host and the model, and installing a model on that host.
INSTANCE_POSTS = [
    ("/settings", {"canvas_base_url": "https://other.example.edu", "canvas_token": "x" * 20}),
    ("/settings/export", {"request_export": "on"}),
    ("/settings/model-host", {"model_host_endpoint": "http://models.example.lan:11434"}),
    ("/settings/model-host/forget", {}),
    ("/settings/model", {"model": MODEL}),
    ("/settings/model/install", {"model": MODEL}),
]


def _instance_state(client: Client) -> tuple[Any, ...]:
    credentials: CredentialStore = client.app.state.credentials  # type: ignore[attr-defined]
    preferences: PreferenceStore = client.app.state.preferences  # type: ignore[attr-defined]
    canvas = credentials.canvas()
    return (
        canvas.base_url,
        canvas.token,
        credentials.canvas_for(BOB).token,
        preferences.request_content_export().enabled,
        preferences.model_host().endpoint,
        preferences.review_model().name,
        tuple(state.as_dict()["model"] for state in client.app.state.pulls.states()),  # type: ignore[attr-defined]
    )


@pytest.mark.parametrize(("path", "data"), INSTANCE_POSTS, ids=[p for p, _ in INSTANCE_POSTS])
def test_a_person_who_is_not_an_admin_cannot_change_an_instance_setting(
    signin: Client, path: str, data: dict[str, str]
) -> None:
    credentials: CredentialStore = signin.app.state.credentials  # type: ignore[attr-defined]
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=INSTANCE_TOKEN)
    preferences: PreferenceStore = signin.app.state.preferences  # type: ignore[attr-defined]
    preferences.set_model_host(MODEL_HOST)
    before = _instance_state(signin)

    refused = signin.as_(BOB, "POST", path, data=data, follow_redirects=False)
    assert refused.status_code == 403, refused.text[:500]
    assert ADMIN_ONLY_SUMMARY in text_of(refused.text)
    # Refused whole: even the token riding along with a base URL was not stored.
    assert _instance_state(signin) == before


def test_an_admin_changes_the_instance_settings(signin: Client) -> None:
    for path, data in INSTANCE_POSTS[:4]:
        response = signin.as_(ADMIN, "POST", path, data=data, follow_redirects=False)
        assert response.status_code == 303, (path, response.text[:500])
    credentials: CredentialStore = signin.app.state.credentials  # type: ignore[attr-defined]
    assert credentials.canvas().base_url == "https://other.example.edu"
    # The admin's token is the admin's own, not the instance's.
    assert credentials.canvas().token is None
    assert credentials.canvas_for(ADMIN).token == "x" * 20
    preferences: PreferenceStore = signin.app.state.preferences  # type: ignore[attr-defined]
    assert preferences.request_content_export().enabled is True


def test_the_settings_screen_offers_a_non_admin_no_instance_form(signin: Client) -> None:
    page = signin.as_(BOB, "GET", "/settings").text
    actions = set(re.findall(r'<form method="post" action="([^"]+)"', page))
    assert actions.isdisjoint({path for path, _ in INSTANCE_POSTS} - {"/settings"})
    assert 'name="canvas_base_url"' not in page
    assert "only an admin changes" in text_of(page)
    admin = signin.as_(ADMIN, "GET", "/settings").text
    assert 'name="canvas_base_url"' in admin
    assert 'action="/settings/model-host"' in admin and 'action="/settings/export"' in admin


# -- each person's own token ----------------------------------------------------------------------


def test_each_person_stores_their_own_token_and_it_is_never_rendered_or_logged(
    signin: Client, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    credentials: CredentialStore = signin.app.state.credentials  # type: ignore[attr-defined]
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=INSTANCE_TOKEN)
    for subject in (ALICE, BOB):
        saved = signin.as_(
            subject,
            "POST",
            "/settings",
            data={"canvas_token": TOKENS[subject]},
            follow_redirects=False,
        )
        assert saved.status_code == 303, saved.text[:500]
    assert credentials.canvas_for(ALICE).token == TOKENS[ALICE]
    assert credentials.canvas_for(BOB).token == TOKENS[BOB]
    assert credentials.canvas_for(ALICE).base_url == EXAMPLE_BASE_URL
    # The instance token is untouched, and a person with no token borrows nobody's.
    assert credentials.canvas().token == INSTANCE_TOKEN
    assert credentials.canvas_for(ADMIN).token is None
    page = signin.as_(ALICE, "GET", "/settings").text
    assert "Your Canvas API token" in page
    for token in (*TOKENS.values(), INSTANCE_TOKEN):
        assert token not in page
    # The row names nobody: a digest of the subject, not the subject.
    store = store_of(signin)
    assert store.get_secret_blob(user_secret_name(ALICE)) is not None
    assert ALICE not in user_secret_name(ALICE)

    forgot = signin.as_(ALICE, "POST", "/settings/forget", follow_redirects=False)
    assert forgot.status_code == 303
    assert credentials.canvas_for(ALICE).token is None
    assert credentials.canvas_for(BOB).token == TOKENS[BOB]
    assert credentials.canvas().token == INSTANCE_TOKEN

    captured = capsys.readouterr()
    logged = captured.out + captured.err + caplog.text
    assert "canvas_user_token_saved" in logged
    for secret in (*TOKENS.values(), INSTANCE_TOKEN, ALICE, BOB):
        assert secret not in logged


def test_start_names_settings_when_the_starter_has_no_token(signin: Client) -> None:
    credentials: CredentialStore = signin.app.state.credentials  # type: ignore[attr-defined]
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=INSTANCE_TOKEN)
    response = signin.as_(
        BOB,
        "POST",
        "/runs",
        data={"course": [COURSE], "rubric": REFERENCE},
        follow_redirects=False,
    )
    assert response.status_code == 409
    words = text_of(response.text)
    assert "You have no Canvas API token stored" in words
    assert "Store your Canvas API token on the Settings screen" in words
    assert store_of(signin).list_runs() == []


# -- a run reads Canvas with its starter's token, before and after a restart ----------------------


class Traffic:
    """Every Canvas client a pipeline builds, on a fresh recording; every request it made."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.recordings: list[Recording] = []

    @property
    def calls(self) -> list[httpx.Request]:
        return [call for recording in self.recordings for call in recording.calls]

    @property
    def bearers(self) -> set[str]:
        return {call.headers["authorization"] for call in self.calls}


@pytest.fixture
def traffic(monkeypatch: pytest.MonkeyPatch) -> Iterator[Traffic]:
    seen = Traffic("course-acquisition")
    host = recordings.only([MODEL])
    host.chat_responses = [recordings.chat_response(MODEL, json.dumps({"findings": []}))]
    canvas_client, model_client = batch_service.CanvasClient, batch_service.OllamaClient

    def build_canvas(base_url, token, **kwargs):  # type: ignore[no-untyped-def]
        recording = load(seen.name)
        seen.recordings.append(recording)
        kwargs.setdefault("transport", recording.transport())
        return canvas_client(base_url, token, **kwargs)

    def build_model(endpoint, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", host.transport())
        return model_client(endpoint, **kwargs)

    monkeypatch.setattr(batch_service, "CanvasClient", build_canvas)
    monkeypatch.setattr(batch_service, "OllamaClient", build_model)
    yield seen


def configure_instance(client: Client, *people: str) -> None:
    credentials: CredentialStore = client.app.state.credentials  # type: ignore[attr-defined]
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=INSTANCE_TOKEN)
    for person in people:
        credentials.save_user_token(person, TOKENS[person])
    preferences: PreferenceStore = client.app.state.preferences  # type: ignore[attr-defined]
    preferences.set_model_host(MODEL_HOST)
    preferences.set_review_model(MODEL)
    # The recording holds reads only; the content-export POST is asserted on the factory below.
    preferences.set_request_content_export(False)


def wait_for(client: Client, subject: str, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        payload = client.as_(subject, "GET", f"/api/runs/{run_id}").json()
        if payload["status"] not in {"pending", "running"}:
            return payload
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} did not settle: {payload['status']}")
        time.sleep(0.05)


def test_a_run_started_from_the_screen_records_its_starter_and_reads_with_their_token(
    signin: Client, traffic: Traffic
) -> None:
    configure_instance(signin, ALICE, BOB)
    response = signin.as_(
        ALICE,
        "POST",
        "/runs",
        data={"course": [COURSE], "rubric": REFERENCE},
        follow_redirects=False,
    )
    assert response.status_code == 303, text_of(response.text)[:2000]
    run_id = response.headers["location"].removeprefix("/runs/")
    payload = wait_for(signin, ALICE, run_id)
    assert payload["status"] == "succeeded", json.dumps(payload)[:2000]

    run = store_of(signin).get_run(run_id)
    assert run is not None and run.starter_subject == ALICE
    assert run.starter_name == "Person " + ALICE[-4:]
    assert traffic.calls, "the run made no Canvas call"
    assert traffic.bearers == {f"Bearer {TOKENS[ALICE]}"}
    # Who started it is not in the run's events, which say what happened, not who.
    events = " ".join(repr(event.data) for event in store_of(signin).events_in_order(run_id))
    assert ALICE not in events and TOKENS[ALICE] not in events
    assert signin.as_(BOB, "GET", f"/runs/{run_id}").status_code == 404


def test_a_run_a_restart_resumes_still_reads_with_its_starters_token(
    data_root: Path, paths: DataPaths, traffic: Traffic
) -> None:
    # The first process: Alice's run is recorded and is running when the process dies.
    with Client(create_app(signin_settings(data_root))) as before:
        configure_instance(before, ALICE, BOB)
        store = store_of(before)
        run_id = create_batch_run(store, [COURSE], rubric_pack=REFERENCE, starter_subject=ALICE)
    store = RunStore.open(paths)
    try:
        set_owner(store, run_id, host=this_host(), pid=dead_pid())
    finally:
        store.dispose()
    assert traffic.calls == []

    # The second process: its startup recovers the orphaned run and resumes it.
    with Client(create_app(signin_settings(data_root))) as after:
        payload = wait_for(after, ALICE, run_id)
        assert payload["status"] == "succeeded", json.dumps(payload)[:2000]
    assert traffic.calls, "the resumed run made no Canvas call"
    assert traffic.bearers == {f"Bearer {TOKENS[ALICE]}"}


async def test_every_canvas_call_of_a_run_carries_its_starters_token_including_the_export_post(
    paths: DataPaths, settings: Settings, monkeypatch: pytest.MonkeyPatch, instant_pacing: Any
) -> None:
    """The factory a run is built by, over the whole fresh-export path (the POST that writes to
    the course's export list included), in one process and then in the next."""
    recordings_made: list[Recording] = []
    canvas_client, acquisition = batch_service.CanvasClient, batch_service.CourseAcquisition

    def build_canvas(base_url, token, **kwargs):  # type: ignore[no-untyped-def]
        recording = load("export-requested")
        recordings_made.append(recording)
        kwargs.setdefault("transport", recording.transport())
        return canvas_client(base_url, token, **kwargs)

    def build_acquisition(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("export_pacing", instant_pacing)
        return acquisition(*args, **kwargs)

    monkeypatch.setattr(batch_service, "CanvasClient", build_canvas)
    monkeypatch.setattr(batch_service, "CourseAcquisition", build_acquisition)

    async def acquire_once(run_id: str) -> None:
        store = RunStore.open(paths)
        try:
            credentials = CredentialStore(store, paths, env_token=INSTANCE_TOKEN)
            preferences = PreferenceStore(store)
            run = store.get_run(run_id)
            assert run is not None
            factory = live_pipeline_factory(
                settings=settings, paths=paths, credentials=credentials, preferences=preferences
            )
            async with factory(run) as pipeline:
                work = CourseWork(reference="4414", course_id="4414", ordinal=0)
                await pipeline.run(Stage.ACQUIRE, work)
        finally:
            store.dispose()

    store = RunStore.open(paths)
    try:
        credentials = CredentialStore(store, paths)
        credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=INSTANCE_TOKEN)
        credentials.save_user_token(ALICE, TOKENS[ALICE])
        PreferenceStore(store).set_request_content_export(True)
        run_id = create_batch_run(store, ["4414"], rubric_pack=REFERENCE, starter_subject=ALICE)
    finally:
        store.dispose()

    await acquire_once(run_id)  # before a restart
    await acquire_once(run_id)  # a new store, a new credential store: the next process
    assert len(recordings_made) == 2
    for recording in recordings_made:
        assert recording.calls
        assert {call.headers["authorization"] for call in recording.calls} == {
            f"Bearer {TOKENS[ALICE]}"
        }
    assert "POST" in recordings_made[0].methods_called


async def test_a_run_whose_starter_has_no_token_stops_naming_settings_and_calls_nothing(
    paths: DataPaths, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[Any] = []
    monkeypatch.setattr(batch_service, "CanvasClient", lambda *a, **k: built.append(a))
    store = RunStore.open(paths)
    try:
        credentials = CredentialStore(store, paths, env_token=INSTANCE_TOKEN)
        credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=INSTANCE_TOKEN)
        preferences = PreferenceStore(store)
        preferences.set_model_host(MODEL_HOST)
        preferences.set_review_model(MODEL)
        run_id = create_batch_run(store, [COURSE], rubric_pack=REFERENCE, starter_subject=BOB)
        factory = live_pipeline_factory(
            settings=settings, paths=paths, credentials=credentials, preferences=preferences
        )
        await BatchRunner(store, paths, factory).execute(run_id)
        run = store.get_run(run_id)
        assert run is not None
        # A missing credential stops the batch: interrupted, not failed — nothing is wrong with
        # the courses (the runner's own rule for every credential it lacks).
        assert run.status == RunStatus.INTERRUPTED, run.status
        events = " ".join(repr(event.data) for event in store.events_in_order(run_id))
        assert STARTER_TOKEN_REMEDY in events
        assert "Settings screen" in STARTER_TOKEN_REMEDY
        assert INSTANCE_TOKEN not in events
    finally:
        store.dispose()
    assert built == [], "no Canvas client was built, so no other token was borrowed"


# -- local mode is unchanged ---------------------------------------------------------------------


def test_local_mode_is_one_user_who_is_the_admin_and_the_screens_read_as_before(
    settings: Settings, paths: DataPaths
) -> None:
    signed_in_run, course_id = reviewed_run(paths, starter=ALICE)
    unowned, _ = reviewed_run(paths, starter=None)
    with FormClient(create_app(settings)) as client:
        # Every run is the local user's, whoever a sign-in mode once recorded.
        listing = client.get("/runs").text
        assert signed_in_run in listing and unowned in listing
        assert "Started by" not in listing
        assert "most recent runs, newest first. Each run's page" in text_of(listing)
        assert client.get(f"/runs/{signed_in_run}").status_code == 200
        assert "Started by" not in client.get(f"/runs/{signed_in_run}").text
        assert client.get(f"/review/{course_id}").status_code == 200
        missing = text_of(client.get("/runs/nope").text)
        assert "This run store has no run nope" in missing

        # The one stored credential is the local user's, and every instance form is there.
        page = client.get("/settings").text
        assert 'name="canvas_base_url"' in page
        assert "Forget the stored credential" in page
        assert "Your Canvas API token" not in page
        for action in ("/settings/export", "/settings/model-host", "/settings/model-host/forget"):
            assert f'action="{action}"' in page, action
        saved = client.post(
            "/settings",
            data={"canvas_base_url": EXAMPLE_BASE_URL, "canvas_token": INSTANCE_TOKEN},
            follow_redirects=False,
        )
        assert saved.status_code == 303
        credentials: CredentialStore = client.app.state.credentials  # type: ignore[attr-defined]
        assert credentials.canvas().token == INSTANCE_TOKEN
        assert credentials.canvas_for(None) == credentials.canvas()
        changed = client.post(
            "/settings/model-host", data={"model_host_endpoint": MODEL_HOST}, follow_redirects=False
        )
        assert changed.status_code == 303
    assert Viewer(subject=None, name=None, admin=True, signed_in=False) == LOCAL
