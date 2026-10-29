"""Item 36: the New run Start form and the run-progress screens.

Nothing here reaches a Canvas instance or a model host. A run started from the screen executes the
application's own pipeline against recorded Canvas responses (``tests/fixtures/canvas``) and a
recorded model host (``tests/ollama_recordings.py``), both patched in where the pipeline builds
its clients; the course-resolution step reads the same recordings through the views' client. The
other tests swap the background runner's handler for a fixture pipeline, which is how a stage is
made to fail, or to wait, on demand.

Every assertion is about what a person reads on the page: per course and per stage, never a
percentage; every failure with its remedy; a reviewed course linked to its review; an unopened
course's export shown closed with the gate's own reason.
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import ollama_recordings as recordings
from canvas_recordings import EXAMPLE_BASE_URL, FAKE_TOKEN, Recording, load
from sightline import views
from sightline.app import create_app
from sightline.batch import (
    DEFAULT_RUBRIC_PACK,
    BatchRunner,
    CourseWork,
    Stage,
    StageError,
    StageOutcome,
    pipeline_factory,
)
from sightline.batch import service as batch_service
from sightline.config import Settings
from sightline.models import Run, RunStatus
from sightline.preferences import PreferenceStore
from sightline.rubric.shipped import shipped_packs
from sightline.store import RunStore

REPO = Path(__file__).resolve().parents[1]
SAMPLE_REVIEW = REPO / "examples" / "sample-report" / "review.json"
MODEL = "llama3.1:8b"
#: A documentation address (RFC 5737). Never reached: every client the pipeline builds is patched.
MODEL_HOST = "http://192.0.2.10:11434"
COURSE = "4411"

#: The longest a test waits for a background run before failing.
WAIT_SECONDS = 60.0


# -- the application ------------------------------------------------------------------------------


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def lookups(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Recording]]:
    """Point every CanvasClient the course screen builds at a recorded fixture."""
    state: dict[str, Recording] = {"recording": load("happy")}
    original = views.CanvasClient

    def build(base_url, token, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", state["recording"].transport())
        return original(base_url, token, **kwargs)

    monkeypatch.setattr(views, "CanvasClient", build)
    yield state


def store_of(client: TestClient) -> RunStore:
    return client.app.state.store  # type: ignore[attr-defined]


def preferences_of(client: TestClient) -> PreferenceStore:
    return client.app.state.preferences  # type: ignore[attr-defined]


def configure_canvas(client: TestClient) -> None:
    response = client.post(
        "/settings",
        data={"canvas_base_url": EXAMPLE_BASE_URL, "canvas_token": FAKE_TOKEN},
        follow_redirects=False,
    )
    assert response.status_code == 303


def configure_model(client: TestClient) -> None:
    preferences = preferences_of(client)
    preferences.set_model_host(MODEL_HOST)
    preferences.set_review_model(MODEL)


def configure_everything(client: TestClient) -> None:
    configure_canvas(client)
    configure_model(client)


def start(client: TestClient, *courses: str, rubric: str = DEFAULT_RUBRIC_PACK) -> Any:
    data: dict[str, Any] = {"course": list(courses), "rubric": rubric}
    for course in courses:
        data[f"label-{course}"] = f"Sample Course {course}"
    return client.post("/runs", data=data, follow_redirects=False)


def run_id_of(response: Any) -> str:
    assert response.status_code == 303, response.text[:2000]
    location = response.headers["location"]
    assert location.startswith("/runs/")
    return location.removeprefix("/runs/")


def wait_for(client: TestClient, run_id: str, *, settled: bool = True) -> dict[str, Any]:
    """Poll the progress endpoint, as the page's script does, until the run settles."""
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        payload = client.get(f"/api/runs/{run_id}").json()
        if settled and payload["status"] not in {"pending", "running"}:
            return payload
        if not settled and payload["status"] == "running":
            return payload
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} did not settle: {payload['status']}")
        time.sleep(0.05)


def section(html: str, element_id: str) -> str:
    """The markup of one ``<section id=...>``, which on these pages never nests another."""
    match = re.search(rf'<section[^>]*\bid="{re.escape(element_id)}"', html)
    assert match, f"no section {element_id!r}"
    end = html.index("</section>", match.start())
    return html[match.start() : end]


def text_of(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split()).replace("&#39;", "'")


# -- fixture pipelines ----------------------------------------------------------------------------


class SavedReviewPipeline:
    """Five stages that each examine two things, and the committed sample review."""

    def __init__(self, *, fail_at: Stage | None = None, gate: threading.Event | None = None):
        self.fail_at = fail_at
        self.gate = gate
        self.review_payload = json.loads(SAMPLE_REVIEW.read_text(encoding="utf-8"))

    async def run(self, stage: Stage, work: CourseWork) -> StageOutcome:
        if self.gate is not None and stage is Stage.ACQUIRE:
            while not self.gate.is_set():
                await asyncio.sleep(0.01)
        if stage is self.fail_at:
            raise StageError(
                stage,
                "The detectors could not open the course's documents.",
                remedy="Re-export the course from Canvas and start the run again.",
            )
        return work.record(
            StageOutcome(stage=stage, examined=2, statement=f"{stage.title}: 2 examined.")
        )

    def review(self, work: CourseWork) -> Mapping[str, Any]:
        return self.review_payload


def use_pipeline(client: TestClient, pipeline: SavedReviewPipeline) -> None:
    app = client.app
    batch = BatchRunner(
        app.state.store,  # type: ignore[attr-defined]
        app.state.paths,  # type: ignore[attr-defined]
        pipeline_factory(pipeline),
    )
    app.state.runner.handler = batch.handle  # type: ignore[attr-defined]


# -- the New run screen ---------------------------------------------------------------------------


def test_resolved_courses_come_back_with_a_start_form_and_a_rubric_picker(
    client: TestClient, lookups: dict[str, Recording]
) -> None:
    configure_everything(client)
    page = client.post("/courses", data={"references": "4410"}).text
    form = section(page, "start")
    assert 'action="/runs"' in form
    assert re.search(r'name="course" value="4410"[^>]*checked', form)
    assert "Sample Course 4410" in form
    for pack in shipped_packs().values():
        assert f'value="{pack.id}"' in form
    assert re.search(rf'name="rubric" value="{DEFAULT_RUBRIC_PACK}"[^>]*checked', form)
    assert len(re.findall(r'name="rubric"[^>]*checked', form)) == 1
    assert "disabled" not in form
    # Starting writes to the course's export list when the setting is on, and the form says so.
    assert "fresh content export" in text_of(form)


def test_the_screen_is_called_new_run_and_no_longer_says_nothing_is_started(
    client: TestClient,
) -> None:
    page = client.get("/courses").text
    assert '<h1 id="h-resolve">New run</h1>' in page
    assert "Nothing is started here" not in page
    assert "--bundle" in page
    assert 'href="/runs"' in page


def test_no_start_form_before_anything_is_resolved(client: TestClient) -> None:
    assert 'id="start"' not in client.get("/courses").text


def test_start_is_refused_without_a_model_host_and_names_settings(
    client: TestClient, lookups: dict[str, Recording]
) -> None:
    configure_canvas(client)
    page = client.post("/courses", data={"references": "4410"}).text
    form = section(page, "start")
    assert "No model host is set" in form
    assert re.search(r'<button type="submit"[^>]*disabled', form)

    response = start(client, "4410")
    assert response.status_code == 409
    body = text_of(section(response.text, "start"))
    assert "No run was started." in body
    assert "No model host is set" in body
    assert "Settings screen" in body
    assert store_of(client).list_runs() == []


def test_start_is_refused_without_a_chosen_model(client: TestClient) -> None:
    configure_canvas(client)
    preferences_of(client).set_model_host(MODEL_HOST)
    response = start(client, COURSE)
    assert response.status_code == 409
    assert "No model is chosen for review work" in text_of(response.text)
    assert store_of(client).list_runs() == []


def test_start_is_refused_without_a_canvas_credential(client: TestClient) -> None:
    configure_model(client)
    response = start(client, COURSE)
    assert response.status_code == 409
    assert "No Canvas credential is set" in text_of(response.text)
    assert store_of(client).list_runs() == []


def test_a_refused_start_keeps_what_was_posted(client: TestClient) -> None:
    configure_canvas(client)
    response = start(client, COURSE)
    form = section(response.text, "start")
    assert re.search(rf'name="course" value="{COURSE}"[^>]*checked', form)
    assert f"Sample Course {COURSE}" in form


def test_start_refuses_an_unknown_rubric_and_a_value_that_is_not_a_course(
    client: TestClient,
) -> None:
    configure_everything(client)
    response = start(client, COURSE, rubric="no-such-pack")
    assert response.status_code == 409
    assert "No shipped rubric has the id" in text_of(response.text)
    response = start(client, "https://elsewhere.example.org/courses/1")
    assert response.status_code == 409
    assert "not a course id" in text_of(response.text)
    response = client.post("/runs", data={"rubric": DEFAULT_RUBRIC_PACK}, follow_redirects=False)
    assert response.status_code == 409
    assert "No course was chosen" in text_of(response.text)
    assert store_of(client).list_runs() == []


def test_the_cap_is_the_batch_runners_and_its_message_is_shown(settings: Settings) -> None:
    capped = settings.model_copy(update={"batch_cap": 2})
    with TestClient(create_app(capped)) as capped_client:
        configure_everything(capped_client)
        response = start(capped_client, "4410", "4411", "4412")
        assert response.status_code == 409
        body = text_of(response.text)
        assert "3 courses were given and the cap for one run is 2." in body
        assert "Split them across runs." in body
        assert store_of(capped_client).list_runs() == []


def test_a_second_start_is_refused_while_one_is_active(client: TestClient) -> None:
    configure_everything(client)
    gate = threading.Event()
    use_pipeline(client, SavedReviewPipeline(gate=gate))
    try:
        first = run_id_of(start(client, COURSE))
        wait_for(client, first, settled=False)
        assert client.app.state.runner.active_run_ids == [first]  # type: ignore[attr-defined]

        response = start(client, "4410")
        assert response.status_code == 409
        body = text_of(response.text)
        assert f"Run {first[:8]} is running" in body
        assert f'href="/runs/{first}"' in response.text
        assert len(store_of(client).list_runs()) == 1

        # The run page says it is going, and carries the optional script.
        page = client.get(f"/runs/{first}").text
        assert f'data-run-poll="/api/runs/{first}"' in page
        assert "/static/run-progress.js" in page
    finally:
        gate.set()
    assert wait_for(client, first)["status"] == "succeeded"
    # Once it has finished, a start goes ahead.
    second = run_id_of(start(client, "4410"))
    assert wait_for(client, second)["status"] == "succeeded"


def test_a_run_another_process_is_running_refuses_a_start_too(client: TestClient) -> None:
    """``python -m sightline.batch run`` is another process: it shows only in the store."""
    configure_everything(client)
    store = store_of(client)
    elsewhere = store.create_run(["4410"], rubric_pack=DEFAULT_RUBRIC_PACK)
    store.set_run_status(elsewhere, RunStatus.RUNNING)
    assert client.app.state.runner.active_run_ids == []  # type: ignore[attr-defined]

    response = start(client, COURSE)
    assert response.status_code == 409
    assert f"Run {elsewhere[:8]} is running" in text_of(response.text)
    assert len(store.list_runs()) == 1


# -- a run started from the screen, against recorded Canvas and model responses -------------------


@pytest.fixture
def recorded_pipeline(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """The application's own pipeline, with its Canvas and model clients on recordings."""
    canvas = load("course-acquisition")
    host = recordings.only([MODEL])
    host.chat_responses = [recordings.chat_response(MODEL, json.dumps({"findings": []}))]
    canvas_client, model_client = batch_service.CanvasClient, batch_service.OllamaClient

    def build_canvas(base_url, token, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", canvas.transport())
        return canvas_client(base_url, token, **kwargs)

    def build_model(endpoint, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", host.transport())
        return model_client(endpoint, **kwargs)

    monkeypatch.setattr(batch_service, "CanvasClient", build_canvas)
    monkeypatch.setattr(batch_service, "OllamaClient", build_model)
    yield {"canvas": canvas, "host": host}


def test_a_run_started_from_the_screen_renders_per_course_and_per_stage(
    client: TestClient, recorded_pipeline: dict[str, Any]
) -> None:
    configure_everything(client)
    # The recording holds the course's existing (empty) export list and no write: with the
    # setting off, acquisition makes only reads, and the recording refuses anything else.
    preferences_of(client).set_request_content_export(False)

    run_id = run_id_of(start(client, COURSE))
    payload = wait_for(client, run_id)
    assert payload["status"] == "succeeded", json.dumps(payload, indent=2)[:3000]
    assert set(recorded_pipeline["canvas"].methods_called) == {"GET"}
    assert "/api/chat" in recorded_pipeline["host"].paths_called

    page = client.get(f"/runs/{run_id}").text
    body = text_of(page)
    assert "Completed" in body
    assert payload["statement"] in body
    course = section(page, "course-1")
    for stage in payload["courses"][0]["stages"]:
        assert stage["state"] == "done"
        row = re.search(
            rf'<tr data-stage="{stage["stage"]}" data-state="done">(.*?)</tr>', course, re.S
        )
        assert row, stage["stage"]
        cells = text_of(row.group(1))
        assert str(stage["examined"]) in cells
        assert Stage(stage["stage"]).describes in cells
        assert text_of(stage["statement"]) in cells
    # Counts, never a percentage or a bar.
    assert "%" not in text_of(course)
    assert "<progress" not in page and "progressbar" not in page
    # No live banner and no script once a run is over.
    assert "data-run-poll" not in page


def test_a_reviewed_course_links_to_its_review_and_its_export_is_closed_until_opened(
    client: TestClient,
) -> None:
    configure_everything(client)
    use_pipeline(client, SavedReviewPipeline())
    run_id = run_id_of(start(client, COURSE))
    payload = wait_for(client, run_id)
    course_id = payload["courses"][0]["course_id"]

    course = section(client.get(f"/runs/{run_id}").text, "course-1")
    assert f'href="/review/{course_id}"' in course
    assert re.search(r'<button type="button" class="button" disabled[^>]*>Export closed', course)
    words = text_of(course)
    assert "Nobody has opened the review of course 1 in this run" in words
    assert "Sightline does not generate a report nobody has read." in words
    # Rendering the run page opened nothing.
    assert store_of(client).get_course(course_id).review_opened_at is None

    assert client.get(f"/review/{course_id}").status_code == 200
    course = section(client.get(f"/runs/{run_id}").text, "course-1")
    assert "Export closed" not in course
    assert f'href="/review/{course_id}#export"' in course
    assert "Return to the review" in course


def test_a_failed_stage_renders_its_remedy_and_the_course_has_no_review_link(
    client: TestClient,
) -> None:
    configure_everything(client)
    use_pipeline(client, SavedReviewPipeline(fail_at=Stage.DETECT))
    run_id = run_id_of(start(client, COURSE, "4410"))
    payload = wait_for(client, run_id)
    assert payload["status"] == "failed"
    detect = payload["courses"][0]["stages"][1]
    assert detect["state"] == "failed"
    assert detect["summary"] == "The detectors could not open the course's documents."
    assert detect["remedy"] == "Re-export the course from Canvas and start the run again."
    assert detect["statement"] == f"{detect['summary']} {detect['remedy']}"

    page = client.get(f"/runs/{run_id}").text
    assert "Failed" in text_of(section(page, "course-1"))
    course = section(page, "course-1")
    failed = re.search(r'<tr data-stage="detect" data-state="failed">(.*?)</tr>', course, re.S)
    assert failed
    assert "The detectors could not open the course's documents." in text_of(failed.group(1))
    assert "Re-export the course from Canvas and start the run again." in text_of(failed.group(1))
    assert 'class="check-remedy"' in failed.group(1)
    # Later stages are pending, not left out.
    for later in ("project", "extract", "score"):
        assert f'data-stage="{later}" data-state="pending"' in course
    assert "/review/" not in course
    words = text_of(course)
    assert "Export closed" in words
    assert "Nobody has opened the review of course 1" in words
    assert "it failed before one was written" in words
    # The stage table says "Roll up effort", never the stored stage name.
    assert ">score<" not in course.lower()


def test_partial_reads_as_completed_with_failures(client: TestClient) -> None:
    store = store_of(client)
    run_id = store.create_run(["4410", "4411"], rubric_pack=DEFAULT_RUBRIC_PACK)
    store.set_run_status(run_id, RunStatus.PARTIAL)
    page = text_of(client.get(f"/runs/{run_id}").text)
    assert "Completed with failures" in page
    finished = store.get_run(run_id)
    assert finished is not None and finished.finished_at is not None
    stamp = finished.finished_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    assert f"Finished {stamp}" in page
    assert "Not recorded" not in page
    assert "Completed with failures" in text_of(client.get("/runs").text)


def test_a_finished_run_with_no_finish_time_reads_not_recorded(client: TestClient) -> None:
    # What a run recorded before item 42 looks like: terminal, with no finish time. Built
    # directly, because the store's own write now always records one.
    store = store_of(client)
    run_id = store.create_run(["4410", "4411"], rubric_pack=DEFAULT_RUBRIC_PACK)
    with store.session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        run.status = RunStatus.PARTIAL
        run.finished_at = None
    page = text_of(client.get(f"/runs/{run_id}").text)
    assert "Completed with failures" in page
    assert "Finished Not recorded" in page


def test_an_interrupted_run_names_the_resume_command_and_offers_no_button(
    client: TestClient,
) -> None:
    # What a batch that stopped before its first course leaves behind (BatchRunner.execute): the
    # run interrupted, and the reason in the log against the run rather than any course.
    store = store_of(client)
    run_id = store.create_run([COURSE], rubric_pack=DEFAULT_RUBRIC_PACK)
    store.set_run_status(run_id, RunStatus.INTERRUPTED)
    store.append_event(
        run_id,
        "batch_aborted",
        level="error",
        summary="No model host is configured, so nothing could be extracted.",
        remedy="Set the local model host on the Settings screen.",
    )

    page = client.get(f"/runs/{run_id}").text
    words = text_of(page)
    assert "Interrupted" in words
    assert f"python -m sightline.batch resume {run_id}" in words
    assert "No model host is configured, so nothing could be extracted." in words
    assert "Set the local model host on the Settings screen." in words
    assert "resume" not in " ".join(re.findall(r"<button[^>]*>[^<]*</button>", page)).lower()
    assert "<form" not in section(page, "course-1")


def test_the_run_list_links_every_run_and_an_unknown_run_is_a_404(client: TestClient) -> None:
    store = store_of(client)
    run_id = store.create_run(["4410"], rubric_pack=DEFAULT_RUBRIC_PACK)
    listing = client.get("/runs").text
    assert f'href="/runs/{run_id}"' in listing
    assert run_id[:8] in listing
    assert f'href="/runs/{run_id}"' in client.get("/").text
    missing = client.get("/runs/not-a-run")
    assert missing.status_code == 404
    assert "has no run" in text_of(missing.text)


def test_the_run_log_is_on_the_page_newest_first(client: TestClient) -> None:
    configure_everything(client)
    use_pipeline(client, SavedReviewPipeline())
    run_id = run_id_of(start(client, COURSE))
    wait_for(client, run_id)
    page = client.get(f"/runs/{run_id}").text
    entries = re.findall(r'<td data-label="Entry">(.*?)</td>', page, re.S)
    words = [text_of(entry) for entry in entries]
    assert words[0] == "Run status"
    assert words[-1] == "Run recorded"
    assert "Stage finished" in words


@pytest.mark.parametrize("path", ["/runs", "/courses"])
def test_the_run_screens_never_evaluate_or_grade(client: TestClient, path: str) -> None:
    """A product requirement, not a nicety (SPEC §11): the vocabulary is review and refresh."""
    body = text_of(client.get(path).text).lower().replace("does not grade", "")
    for forbidden in (" grade", "grading", "evaluation", "score", "pass/fail"):
        assert forbidden not in body, forbidden


def test_a_run_page_never_evaluates_or_grades(client: TestClient) -> None:
    configure_everything(client)
    use_pipeline(client, SavedReviewPipeline())
    run_id = run_id_of(start(client, COURSE))
    wait_for(client, run_id)
    # The visible text: the markup carries each stage's stored name in a data attribute.
    body = text_of(client.get(f"/runs/{run_id}").text).lower().replace("does not grade", "")
    for forbidden in (" grade", "grading", "evaluation", "score", "pass/fail"):
        assert forbidden not in body, forbidden


def test_nothing_logs_a_course_reference(
    client: TestClient, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Item 17: a course URL carries the host, which SPEC §11 treats as a secret."""
    configure_everything(client)
    use_pipeline(client, SavedReviewPipeline())
    reference = f"{EXAMPLE_BASE_URL}/courses/{COURSE}"
    run_id = run_id_of(start(client, COURSE))
    wait_for(client, run_id)
    client.get(f"/runs/{run_id}")
    captured = capsys.readouterr()
    logged = captured.out + captured.err + caplog.text
    assert "run_started_from_screen" in logged
    assert reference not in logged
    assert f'"{COURSE}"' not in logged
