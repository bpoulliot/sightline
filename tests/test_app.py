"""The HTTP surface: health, the status page, and the palette."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sightline import __version__
from sightline.app import create_app
from sightline.config import Settings
from sightline.models import RunStatus
from sightline.store import RunStore


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_healthz_reports_what_it_examined(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"] == __version__
    for check in ("data_root", "run_store", "design_tokens"):
        assert payload[check]["ok"] is True, payload[check]
    assert payload["run_store"]["runs_by_status"] == {}


def test_healthz_fails_loudly_when_the_palette_is_missing(
    settings: Settings, tmp_path: Path
) -> None:
    broken = settings.model_copy(update={"tokens_css_path": tmp_path / "absent.css"})
    with TestClient(create_app(broken), raise_server_exceptions=False) as broken_client:
        response = broken_client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["design_tokens"]["ok"] is False


def test_startup_creates_the_data_root(settings: Settings) -> None:
    assert not settings.data_dir.exists()
    with TestClient(create_app(settings)):
        pass
    assert settings.data_dir.is_dir()
    assert (settings.data_dir / "runs").is_dir()


def test_index_renders_and_reads_the_palette(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert '<link rel="stylesheet" href="/assets/tokens.css">' in body
    assert "Determinacy tiers" in body
    assert "No runs yet." in body


def test_index_never_evaluates_or_grades(client: TestClient) -> None:
    """A product requirement, not a nicety (SPEC §11): the vocabulary is review and refresh."""
    body = client.get("/").text.lower()
    for forbidden in (" grade", "grading", "evaluation", "score", "pass/fail"):
        assert forbidden not in body.replace("does not grade", ""), forbidden


def test_index_shows_configuration_presence_but_no_values(settings: Settings) -> None:
    configured = settings.model_copy(
        update={"canvas_base_url": "https://canvas.example.edu"},
    )
    with TestClient(create_app(configured)) as configured_client:
        body = configured_client.get("/").text
    assert "CANVAS_BASE_URL" in body
    assert "canvas.example.edu" not in body


def test_tokens_css_is_served_verbatim(client: TestClient, repo_root: Path) -> None:
    """One source of colour: the bytes on the wire are the bytes in docs/tokens.css."""
    response = client.get("/assets/tokens.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.content == (repo_root / "docs" / "tokens.css").read_bytes()


def test_component_stylesheet_is_served(client: TestClient) -> None:
    response = client.get("/static/app.css")
    assert response.status_code == 200
    assert "--accent" in response.text


def test_runs_appear_on_the_status_page(settings: Settings) -> None:
    application = create_app(settings)
    with TestClient(application) as test_client:
        store: RunStore = application.state.store
        run_id = store.create_run(["101", "202"], rubric_pack="oscqr-3.1.3")
        store.set_run_status(run_id, RunStatus.RUNNING)
        body = test_client.get("/").text
    assert run_id[:8] in body
    assert "oscqr-3.1.3" in body
    assert "No runs yet." not in body


def test_interrupted_runs_are_recovered_on_startup(settings: Settings) -> None:
    first = create_app(settings)
    with TestClient(first) as test_client:
        store: RunStore = first.state.store
        run_id = store.create_run(["101"], rubric_pack="oscqr-3.1.3")
        store.set_run_status(run_id, RunStatus.RUNNING)
        assert test_client.get("/healthz").json()["run_store"]["runs_by_status"] == {"running": 1}

    with TestClient(create_app(settings)) as second_client:
        counts = second_client.get("/healthz").json()["run_store"]["runs_by_status"]
    assert counts == {"interrupted": 1}


def test_the_application_can_actually_run_a_batch(client: TestClient) -> None:
    """Before item 17 there was no pipeline, and submitting a run raised. There is one now."""
    from sightline.batch import BatchRunner

    runner = client.app.state.runner
    assert runner.handler is not None
    assert isinstance(client.app.state.batch, BatchRunner)
