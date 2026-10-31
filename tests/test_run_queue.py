"""Queue a run behind the active one (roadmap item 46, SPEC §09).

A Start while a run is active records the run *queued* rather than refusing it. Queued runs
execute one at a time, in the order they were started, after the active run — which may be a
command-line run in another process with a live owner (item 43). The run page and the run list
read *Queued, N ahead*. The starter or an admin (item 45) cancels a queued run, and the run records
who did; anybody else is answered 404, as for any run of somebody else's. A restart keeps the queue
and its order. Proposing bindings still refuses rather than queueing, and the queue waits for it.

The runner tests drive :class:`sightline.runner.BackgroundRunner` directly with a handler that
records the order runs execute in and how many execute at once. The screen tests run the app with a
fixture pipeline. Nothing reaches a Canvas instance or a model host.
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

from canvas_recordings import EXAMPLE_BASE_URL, FAKE_TOKEN
from form_clients import FormClient
from run_owners import OTHER_HOST, STALE, dead_pid, live_process, set_owner, this_host
from sightline import app as app_module
from sightline.app import create_app
from sightline.batch import (
    DEFAULT_RUBRIC_PACK,
    BatchRunner,
    CourseWork,
    Stage,
    StageOutcome,
    pipeline_factory,
)
from sightline.batch.__main__ import main as batch_main
from sightline.config import Settings, reset_settings_cache
from sightline.models import RunStatus
from sightline.paths import DataPaths
from sightline.runner import BackgroundRunner
from sightline.store import RunStore

REPO = Path(__file__).resolve().parents[1]
SAMPLE_REVIEW = REPO / "examples" / "sample-report" / "review.json"
PACK = DEFAULT_RUBRIC_PACK
MODEL = "llama3.1:8b"
#: A documentation address (RFC 5737). Never reached: the pipeline is a fixture.
MODEL_HOST = "http://192.0.2.10:11434"

#: The longest a test waits for anything before failing.
WAIT_SECONDS = 30.0
#: How often the queue looks again in these tests, rather than every five seconds.
POLL = 0.02


# -- runner-level: order, waiting, restart --------------------------------------------------------


class Recorder:
    """A handler that records the order runs execute in and the most that executed at once."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.executing = 0
        self.most_at_once = 0
        #: Set, every run goes through; otherwise only the runs named in ``let_through``.
        self.release = asyncio.Event()
        self.let_through: set[str] = set()

    async def __call__(self, run_id: str) -> None:
        self.order.append(run_id)
        self.executing += 1
        self.most_at_once = max(self.most_at_once, self.executing)
        try:
            while not (self.release.is_set() or run_id in self.let_through):
                await asyncio.sleep(0.005)
        finally:
            self.executing -= 1


async def until(condition: Any, *, what: str) -> None:
    deadline = time.monotonic() + WAIT_SECONDS
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.01)


def status(store: RunStore, run_id: str) -> RunStatus:
    run = store.get_run(run_id)
    assert run is not None
    return RunStatus(run.status)


def queue(store: RunStore, count: int) -> list[str]:
    """``count`` runs recorded queued, oldest first."""
    return [store.create_run([f"44{index}"], rubric_pack=PACK, queued=True) for index in range(count)]


def test_queued_runs_execute_one_at_a_time_in_the_order_they_were_started(paths: DataPaths) -> None:
    async def scenario() -> None:
        store = RunStore.open(paths)
        recorder = Recorder()
        runner = BackgroundRunner(store, recorder, poll_seconds=POLL)
        try:
            active = store.create_run(["4400"], rubric_pack=PACK)
            runner.submit(active)
            await until(lambda: recorder.order == [active], what="the active run")
            waiting = queue(store, 3)
            runner.start_queue()
            await asyncio.sleep(0.1)
            assert recorder.order == [active]
            assert runner.queue_positions() == {waiting[0]: 1, waiting[1]: 2, waiting[2]: 3}
            assert all(status(store, run_id) is RunStatus.QUEUED for run_id in waiting)

            recorder.release.set()
            await until(lambda: len(recorder.order) == 4, what="every queued run")
            await until(
                lambda: all(status(store, r) is RunStatus.SUCCEEDED for r in waiting),
                what="the queued runs to finish",
            )
            assert recorder.order == [active, *waiting]
            assert recorder.most_at_once == 1
            assert runner.queue_positions() == {}
        finally:
            await runner.shutdown()
            store.dispose()

    asyncio.run(scenario())


def test_one_run_executes_at_a_time_however_the_queue_is_woken(paths: DataPaths) -> None:
    """Each run is released in turn, and at every moment exactly one is executing."""

    async def scenario() -> None:
        store = RunStore.open(paths)
        recorder = Recorder()
        runner = BackgroundRunner(store, recorder, poll_seconds=POLL)
        try:
            waiting = queue(store, 3)
            runner.start_queue()
            for index, run_id in enumerate(waiting):
                await until(lambda run_id=run_id: run_id in recorder.order, what=f"run {index}")
                await asyncio.sleep(0.05)
                assert recorder.executing == 1
                assert runner.queue_positions() == {
                    later: position for position, later in enumerate(waiting[index + 1 :], start=1)
                }
                recorder.let_through.add(run_id)
                await until(
                    lambda run_id=run_id: status(store, run_id) is RunStatus.SUCCEEDED,
                    what="it to finish",
                )
            assert recorder.order == waiting and recorder.most_at_once == 1
        finally:
            recorder.release.set()
            await runner.shutdown()
            store.dispose()

    asyncio.run(scenario())


def test_a_restart_keeps_the_queue_and_its_order(paths: DataPaths) -> None:
    """The run the process was executing resumes first; then the queue, oldest first."""
    store = RunStore.open(paths)
    active = store.create_run(["4400"], rubric_pack=PACK)
    # Its process has gone: the app was stopped while the run was going.
    set_owner(store, active, host=this_host(), pid=dead_pid())
    waiting = queue(store, 2)
    store.dispose()

    async def after_restart() -> None:
        restarted = RunStore.open(paths)
        recorder = Recorder()
        runner = BackgroundRunner(restarted, recorder, poll_seconds=POLL)
        try:
            assert runner.recover() == [active]
            assert all(status(restarted, run_id) is RunStatus.QUEUED for run_id in waiting)
            assert runner.resume_all() == [active]
            runner.start_queue()
            assert runner.queue_positions() == {waiting[0]: 1, waiting[1]: 2}
            await until(lambda: recorder.order == [active], what="the resumed run")
            await asyncio.sleep(0.1)
            assert recorder.order == [active]
            recorder.release.set()
            await until(lambda: len(recorder.order) == 3, what="the queue")
            assert recorder.order == [active, *waiting]
            assert recorder.most_at_once == 1
        finally:
            await runner.shutdown()
            restarted.dispose()

    asyncio.run(after_restart())


def test_shutdown_leaves_a_queued_run_queued(paths: DataPaths) -> None:
    async def scenario() -> None:
        store = RunStore.open(paths)
        recorder = Recorder()
        runner = BackgroundRunner(store, recorder, poll_seconds=POLL)
        active = store.create_run(["4400"], rubric_pack=PACK)
        runner.submit(active)
        (waiting,) = queue(store, 1)
        runner.start_queue()
        await until(lambda: recorder.order == [active], what="the active run")
        await runner.shutdown()
        assert status(store, active) is RunStatus.INTERRUPTED
        assert status(store, waiting) is RunStatus.QUEUED
        store.dispose()

    asyncio.run(scenario())


def test_the_queue_waits_behind_a_live_command_line_run_on_another_host(paths: DataPaths) -> None:
    async def scenario() -> None:
        store = RunStore.open(paths)
        recorder = Recorder()
        recorder.release.set()
        runner = BackgroundRunner(store, recorder, poll_seconds=POLL)
        try:
            elsewhere = store.create_run(["4400"], rubric_pack=PACK)
            set_owner(store, elsewhere, host=OTHER_HOST, pid=7)
            (waiting,) = queue(store, 1)
            runner.start_queue()
            await asyncio.sleep(0.2)
            assert recorder.order == []
            assert runner.ahead_of_queue() == [elsewhere]
            assert runner.queue_positions() == {waiting: 1}
            assert runner.must_queue()

            # Its heartbeat goes stale: the process is gone, and the queue goes on unwoken.
            set_owner(store, elsewhere, host=OTHER_HOST, pid=7, heartbeat_age=STALE)
            await until(lambda: recorder.order == [waiting], what="the queued run")
        finally:
            await runner.shutdown()
            store.dispose()

    asyncio.run(scenario())


def test_the_queue_waits_behind_a_live_command_line_run_on_this_host(paths: DataPaths) -> None:
    async def scenario() -> None:
        store = RunStore.open(paths)
        recorder = Recorder()
        recorder.release.set()
        runner = BackgroundRunner(store, recorder, poll_seconds=POLL)
        try:
            elsewhere = store.create_run(["4400"], rubric_pack=PACK)
            (waiting,) = queue(store, 1)
            with live_process() as pid:
                set_owner(store, elsewhere, host=this_host(), pid=pid)
                runner.start_queue()
                await asyncio.sleep(0.2)
                assert recorder.order == []
                assert runner.queue_positions() == {waiting: 1}
            await until(lambda: recorder.order == [waiting], what="the queued run")
        finally:
            await runner.shutdown()
            store.dispose()

    asyncio.run(scenario())


def test_the_queue_waits_while_bindings_are_proposed(paths: DataPaths) -> None:
    async def scenario() -> None:
        store = RunStore.open(paths)
        recorder = Recorder()
        recorder.release.set()
        runner = BackgroundRunner(store, recorder, poll_seconds=POLL)
        proposing = {"busy": True}
        runner.held_by = lambda: proposing["busy"]
        try:
            (waiting,) = queue(store, 1)
            runner.start_queue()
            await asyncio.sleep(0.2)
            assert recorder.order == []
            proposing["busy"] = False
            await until(lambda: recorder.order == [waiting], what="the queued run")
        finally:
            await runner.shutdown()
            store.dispose()

    asyncio.run(scenario())


def test_a_cancel_and_the_queue_taking_a_run_cannot_both_land(paths: DataPaths) -> None:
    store = RunStore.open(paths)
    taken, cancelled = queue(store, 2)
    assert store.take_queued(taken)
    assert not store.cancel_queued(taken, subject="fake-subject", name="Fake Person")
    assert status(store, taken) is RunStatus.PENDING

    assert store.cancel_queued(cancelled, subject="fake-subject", name="Fake Person")
    assert not store.take_queued(cancelled)
    run = store.get_run(cancelled)
    assert run is not None
    assert RunStatus(run.status) is RunStatus.CANCELLED and run.finished_at is not None
    assert (run.cancelled_by_subject, run.cancelled_by_name) == ("fake-subject", "Fake Person")
    assert {RunStatus(course.status) for course in run.courses} == {RunStatus.CANCELLED}
    # The log says what happened, not who.
    events = [event for event in store.events_in_order(cancelled) if event.event == "run_cancelled"]
    assert len(events) == 1 and "fake-subject" not in json.dumps(events[0].data)
    store.dispose()


def test_resuming_a_queued_run_on_the_command_line_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    data_root: Path,
    repo_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("SIGHTLINE_DATA_DIR", str(data_root))
    monkeypatch.chdir(repo_root)
    reset_settings_cache()
    try:
        store = RunStore.open(DataPaths.from_setting(data_root))
        (waiting,) = queue(store, 1)
        store.dispose()
        assert batch_main(["resume", waiting]) == 2
        assert "is queued in the app" in capsys.readouterr().err
        store = RunStore.open(DataPaths.from_setting(data_root))
        assert status(store, waiting) is RunStatus.QUEUED
        store.dispose()
    finally:
        reset_settings_cache()


# -- the screens, local mode ----------------------------------------------------------------------


class GatedPipeline:
    """Five stages and the committed sample review; a run waits at acquisition for the gate."""

    def __init__(self, gate: threading.Event) -> None:
        self.gate = gate
        self.review_payload = json.loads(SAMPLE_REVIEW.read_text(encoding="utf-8"))

    async def run(self, stage: Stage, work: CourseWork) -> StageOutcome:
        if stage is Stage.ACQUIRE:
            while not self.gate.is_set():
                await asyncio.sleep(0.01)
        return work.record(
            StageOutcome(stage=stage, examined=2, statement=f"{stage.title}: 2 examined.")
        )

    def review(self, work: CourseWork) -> Mapping[str, Any]:
        return self.review_payload


@pytest.fixture
def gate() -> Iterator[threading.Event]:
    released = threading.Event()
    yield released
    released.set()


@pytest.fixture
def client(
    settings: Settings, gate: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> Iterator[FormClient]:
    """The app, its pipeline the gated fixture from the start — so a restart resumes into it."""
    monkeypatch.setattr(
        app_module, "live_pipeline_factory", lambda **_: pipeline_factory(GatedPipeline(gate))
    )
    with FormClient(create_app(settings)) as test_client:
        test_client.app.state.runner.poll_seconds = POLL  # type: ignore[attr-defined]
        configure(test_client)
        yield test_client


def configure(client: FormClient) -> None:
    credentials = client.app.state.credentials  # type: ignore[attr-defined]
    credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
    preferences = client.app.state.preferences  # type: ignore[attr-defined]
    preferences.set_reviewer("Fake Reviewer")
    preferences.set_model_host(MODEL_HOST)
    preferences.set_review_model(MODEL)


def start(client: FormClient, course: str) -> str:
    response = client.post(
        "/runs",
        data={"course": [course], "rubric": PACK, f"label-{course}": f"Sample Course {course}"},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text[:2000]
    return response.headers["location"].removeprefix("/runs/")


def progress(client: FormClient, run_id: str) -> dict[str, Any]:
    return client.get(f"/api/runs/{run_id}").json()


def wait_status(client: FormClient, run_id: str, wanted: str) -> dict[str, Any]:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        payload = progress(client, run_id)
        if payload["status"] == wanted:
            return payload
        if time.monotonic() > deadline:
            raise AssertionError(f"run {run_id} is {payload['status']}, not {wanted}")
        time.sleep(0.02)


def text_of(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split()).replace("&#39;", "'")


def test_start_while_a_run_is_active_queues_and_every_screen_says_how_many_ahead(
    client: FormClient, gate: threading.Event
) -> None:
    first = start(client, "4410")
    wait_status(client, first, "running")

    second = start(client, "4411")
    third = start(client, "4412")
    assert progress(client, second)["status"] == "queued"
    assert progress(client, second)["queue_ahead"] == 1
    assert progress(client, third)["queue_ahead"] == 2
    assert "queue_ahead" not in progress(client, first)

    page = text_of(client.get(f"/runs/{third}").text)
    assert "Queued, 2 ahead" in page
    assert "Cancel this run" in page
    assert "no estimate" in page
    assert not re.search(r"\d\s*(%|minutes?|hours?)\b", page.split("Run log")[0])
    listing = text_of(client.get("/runs").text)
    assert "Queued, 1 ahead" in listing and "Queued, 2 ahead" in listing
    assert "Running" in listing

    gate.set()
    finished = [wait_status(client, run_id, "succeeded") for run_id in (first, second, third)]
    starts = [payload["started_at"] for payload in finished]
    assert starts == sorted(starts)
    # Each started only once the one before it had finished.
    assert finished[0]["finished_at"] <= starts[1] and finished[1]["finished_at"] <= starts[2]


def test_the_start_form_reports_the_queue_before_anything_is_started(
    client: FormClient, gate: threading.Event
) -> None:
    first = start(client, "4410")
    wait_status(client, first, "running")
    start(client, "4411")
    # The form as a refused post re-renders it: here a post with no course, so nothing starts.
    response = client.post("/runs", data={"rubric": PACK}, follow_redirects=False)
    assert response.status_code == 409
    notice = text_of(response.text)
    assert "A run started now is queued." in notice
    assert f"Run {first[:8]} is running, so a run started now is queued behind it: 2 ahead." in notice
    assert "no estimate" in notice


def test_the_other_blockers_still_refuse_while_a_run_is_active(
    client: FormClient, gate: threading.Event
) -> None:
    first = start(client, "4410")
    wait_status(client, first, "running")
    client.app.state.preferences.clear_model_host()  # type: ignore[attr-defined]
    response = client.post(
        "/runs",
        data={"course": ["4411"], "rubric": PACK, "label-4411": "Sample Course 4411"},
        follow_redirects=False,
    )
    assert response.status_code == 409
    assert "No model host is set" in text_of(response.text)
    store: RunStore = client.app.state.store  # type: ignore[attr-defined]
    assert [run.id for run in store.list_runs()] == [first]


def test_the_local_user_cancels_a_queued_run_and_the_page_says_who(
    client: FormClient, gate: threading.Event
) -> None:
    first = start(client, "4410")
    wait_status(client, first, "running")
    second = start(client, "4411")
    third = start(client, "4412")

    response = client.post(f"/runs/{second}/cancel", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/runs/{second}"
    assert progress(client, second)["status"] == "cancelled"
    assert progress(client, third)["queue_ahead"] == 1
    page = text_of(client.get(f"/runs/{second}").text)
    assert "Cancelled by Fake Reviewer, while it was queued" in page
    assert "Cancel this run" not in page

    # Not queued any more, and the running run cannot be cancelled either.
    again = client.post(f"/runs/{second}/cancel", follow_redirects=False)
    assert again.status_code == 409 and "not queued" in text_of(again.text)
    running = client.post(f"/runs/{first}/cancel", follow_redirects=False)
    assert running.status_code == 409
    assert progress(client, first)["status"] == "running"

    gate.set()
    wait_status(client, third, "succeeded")
    assert progress(client, second)["status"] == "cancelled"


def test_proposing_is_refused_while_runs_are_queued(client: FormClient) -> None:
    store: RunStore = client.app.state.store  # type: ignore[attr-defined]
    elsewhere = store.create_run(["4400"], rubric_pack=PACK)
    set_owner(store, elsewhere, host=OTHER_HOST, pid=7)
    queue(store, 1)
    response = client.post(f"/rubrics/{PACK}/propose", follow_redirects=False)
    assert response.status_code == 409
    assert "1 run(s) are queued to use the model host" in text_of(response.text)


def test_a_restarted_app_carries_on_with_its_queue(
    settings: Settings, gate: threading.Event, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore.open(settings.paths.ensure())
    elsewhere = store.create_run(["4400"], rubric_pack=PACK)
    set_owner(store, elsewhere, host=OTHER_HOST, pid=7)
    waiting = queue(store, 2)
    store.dispose()
    monkeypatch.setattr(
        app_module, "live_pipeline_factory", lambda **_: pipeline_factory(GatedPipeline(gate))
    )
    gate.set()
    with FormClient(create_app(settings)) as restarted:
        restarted.app.state.runner.poll_seconds = POLL  # type: ignore[attr-defined]
        assert [progress(restarted, run_id)["queue_ahead"] for run_id in waiting] == [1, 2]
        # The command-line run's process stops beating; the queue goes on, in its order.
        live = restarted.app.state.store  # type: ignore[attr-defined]
        set_owner(live, elsewhere, host=OTHER_HOST, pid=7, heartbeat_age=STALE)
        finished = [wait_status(restarted, run_id, "succeeded") for run_id in waiting]
        assert finished[0]["finished_at"] <= finished[1]["started_at"]


# -- the screens, sign-in mode --------------------------------------------------------------------

SECRET = "fake-proxy-secret-0000000000000000000000"
ADMIN_GROUP = "fake-sightline-admins"
ALICE = "fake-subject-alice-0001"
BOB = "fake-subject-bob-0002"
ADMIN = "fake-subject-admin-0003"


def headers_for(subject: str) -> dict[str, str]:
    sent = {
        "X-Sightline-Proxy-Secret": SECRET,
        "X-authentik-uid": subject,
        "X-authentik-name": f"Person {subject[-4:]}",
    }
    if subject == ADMIN:
        sent["X-authentik-groups"] = f"fake-staff|{ADMIN_GROUP}"
    return sent


class SignedIn(FormClient):
    def as_(self, subject: str, method: str, url: str, **kwargs: Any) -> Any:
        self.subject = subject
        return self.request(method, url, headers=headers_for(subject), **kwargs)


@pytest.fixture
def signed_in(data_root: Path) -> Iterator[SignedIn]:
    settings = Settings(
        _env_file=None,
        data_dir=data_root,
        tokens_css_path=REPO / "docs" / "tokens.css",
        log_format="json",
        signin=True,
        signin_secret=SECRET,
        signin_admin_group=ADMIN_GROUP,
    )
    with SignedIn(create_app(settings)) as test_client:
        yield test_client


def queued_behind_a_live_run(client: SignedIn) -> tuple[RunStore, str, str]:
    """Alice's run, queued behind a command-line run that is going; and Bob's behind hers."""
    store: RunStore = client.app.state.store  # type: ignore[attr-defined]
    elsewhere = store.create_run(["4400"], rubric_pack=PACK)
    set_owner(store, elsewhere, host=OTHER_HOST, pid=7)
    alices = store.create_run(
        ["4411"], rubric_pack=PACK, queued=True, starter_subject=ALICE, starter_name="Person 0001"
    )
    bobs = store.create_run(
        ["4412"], rubric_pack=PACK, queued=True, starter_subject=BOB, starter_name="Person 0002"
    )
    return store, alices, bobs


def test_the_starter_cancels_their_queued_run(signed_in: SignedIn) -> None:
    store, alices, bobs = queued_behind_a_live_run(signed_in)
    page = text_of(signed_in.as_(ALICE, "GET", f"/runs/{alices}").text)
    assert "Queued, 1 ahead" in page
    listing = text_of(signed_in.as_(BOB, "GET", "/runs").text)
    assert "Queued, 2 ahead" in listing and alices[:8] not in listing

    response = signed_in.as_(ALICE, "POST", f"/runs/{alices}/cancel", follow_redirects=False)
    assert response.status_code == 303
    run = store.get_run(alices)
    assert run is not None and RunStatus(run.status) is RunStatus.CANCELLED
    assert (run.cancelled_by_subject, run.cancelled_by_name) == (ALICE, "Person 0001")
    assert "Cancelled by you, while it was queued" in text_of(
        signed_in.as_(ALICE, "GET", f"/runs/{alices}").text
    )
    # An admin reads the name the record keeps.
    assert "Cancelled by Person 0001, while it was queued" in text_of(
        signed_in.as_(ADMIN, "GET", f"/runs/{alices}").text
    )
    # Bob moves up.
    assert "Queued, 1 ahead" in text_of(signed_in.as_(BOB, "GET", f"/runs/{bobs}").text)


def test_an_admin_cancels_somebody_elses_queued_run(signed_in: SignedIn) -> None:
    store, alices, _ = queued_behind_a_live_run(signed_in)
    assert "Cancel this run" in signed_in.as_(ADMIN, "GET", f"/runs/{alices}").text
    response = signed_in.as_(ADMIN, "POST", f"/runs/{alices}/cancel", follow_redirects=False)
    assert response.status_code == 303
    run = store.get_run(alices)
    assert run is not None and RunStatus(run.status) is RunStatus.CANCELLED
    assert (run.cancelled_by_subject, run.cancelled_by_name) == (ADMIN, "Person 0003")
    # The starter is told an admin did it, without the admin's name; the admin reads "you".
    alice_reads = text_of(signed_in.as_(ALICE, "GET", f"/runs/{alices}").text)
    assert "Cancelled by an admin, while it was queued" in alice_reads
    assert "Person 0003" not in alice_reads
    assert "Cancelled by you, while it was queued" in text_of(
        signed_in.as_(ADMIN, "GET", f"/runs/{alices}").text
    )


def test_nobody_else_can_cancel_a_queued_run(signed_in: SignedIn) -> None:
    store, alices, _ = queued_behind_a_live_run(signed_in)
    response = signed_in.as_(BOB, "POST", f"/runs/{alices}/cancel", follow_redirects=False)
    unknown = signed_in.as_(BOB, "POST", "/runs/0123456789abcdef/cancel", follow_redirects=False)
    # Exactly the answer for a run that does not exist: a 404 does not confirm the id is a run.
    assert response.status_code == unknown.status_code == 404
    assert text_of(response.text).replace(alices, "ID") == text_of(unknown.text).replace(
        "0123456789abcdef", "ID"
    )
    run = store.get_run(alices)
    assert run is not None and RunStatus(run.status) is RunStatus.QUEUED
    assert run.cancelled_by_subject is None and run.cancelled_by_name is None
    assert not any(event.event == "run_cancelled" for event in store.events_in_order(alices))
