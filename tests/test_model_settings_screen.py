"""The model host on the settings screen.

Every assertion here is a plain form post, because the screen works with JavaScript switched off
(SPEC §09). The host is answered from recorded payloads through a patched client, never from a
live one.

The screen has one behaviour worth stating before reading the tests: **it does not call the model
host when it renders.** Listing models costs a request per installed model, and an unreachable
host would hang the settings page — the one screen somebody opens *because* the endpoint is wrong.
So the check is a button, and several tests below exist to hold that line.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

import ollama_recordings as recordings
from form_clients import FormClient as TestClient
from sightline import views
from sightline.app import create_app
from sightline.config import Settings

ENDPOINT = recordings.EXAMPLE_ENDPOINT


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def host(monkeypatch: pytest.MonkeyPatch) -> Iterator[recordings.FakeHost]:
    """Point every OllamaClient the views build at the recorded host."""
    recorded = recordings.load()
    original = views.OllamaClient

    def build(base_url, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", recorded.transport())
        return original(base_url, **kwargs)

    monkeypatch.setattr(views, "OllamaClient", build)
    yield recorded


def configure(client: TestClient, endpoint: str = ENDPOINT) -> None:
    response = client.post(
        "/settings/model-host",
        data={"model_host_endpoint": endpoint},
        follow_redirects=False,
    )
    assert response.status_code == 303


# ---------------------------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------------------------


def test_the_section_renders_with_nothing_configured(client: TestClient) -> None:
    body = client.get("/settings").text
    assert "Local model host" in body
    assert "OLLAMA_BASE_URL" in body
    assert "No model is chosen yet." in body


def test_saving_an_endpoint_normalizes_and_reports_it(client: TestClient) -> None:
    configure(client, "http://model-host.example.lan:11434/api/tags")
    body = client.get("/settings?saved=model_host").text
    assert ENDPOINT in body
    assert "trimmed to its scheme, host and port" in body


def test_a_malformed_endpoint_is_refused_with_a_remedy(client: TestClient) -> None:
    response = client.post("/settings/model-host", data={"model_host_endpoint": "nonsense here"})
    assert response.status_code == 200
    assert "not a usable model host endpoint" in response.text
    assert "http://model-host.example.lan:11434" in response.text


def test_forgetting_the_endpoint_falls_back_to_the_environment(
    data_root, repo_root, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        _env_file=None,
        data_dir=data_root,
        tokens_css_path=repo_root / "docs" / "tokens.css",
        ollama_base_url="http://from-the-environment.example.lan:11434",
    )
    with TestClient(create_app(settings)) as client:
        configure(client)
        assert ENDPOINT in client.get("/settings").text
        client.post("/settings/model-host/forget", follow_redirects=False)
        body = client.get("/settings").text
        assert "from-the-environment.example.lan" in body
        assert "environment" in body


def test_the_page_does_not_call_the_host_when_it_renders(
    client: TestClient, host: recordings.FakeHost
) -> None:
    """An unreachable host must not be able to hang the screen that fixes the endpoint."""
    configure(client)
    client.get("/settings")
    assert host.calls == []


# ---------------------------------------------------------------------------------------------
# The health check
# ---------------------------------------------------------------------------------------------


def test_the_check_reports_host_version_and_usable_models(
    client: TestClient, host: recordings.FakeHost
) -> None:
    configure(client)
    body = client.post("/settings/model-host/check").text

    assert recordings.EXAMPLE_HOST in body
    assert f"Ollama {recordings.RECORDED_VERSION}" in body
    assert "4 of 8 installed models can do review work" in body
    assert "gemma4:e4b" in body


def test_the_check_states_why_a_model_was_not_selected(
    client: TestClient, host: recordings.FakeHost
) -> None:
    """A model missing from a list is indistinguishable from a model nobody looked at."""
    configure(client)
    body = client.post("/settings/model-host/check").text
    assert "llava:13b" in body
    assert "does not report &#39;tools&#39;" in body or "does not report 'tools'" in body
    assert "nomic-embed-text:latest" in body
    assert "an embedding model cannot generate" in body


def test_the_check_with_no_endpoint_says_what_to_set(client: TestClient) -> None:
    body = client.post("/settings/model-host/check").text
    assert "No model host endpoint is configured." in body
    assert "OLLAMA_BASE_URL" in body


def test_the_check_on_an_unreachable_host_renders_the_remedy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = views.OllamaClient

    def build(base_url, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", recordings.raising_transport(httpx.ConnectError("no route")))
        return original(base_url, **kwargs)

    monkeypatch.setattr(views, "OllamaClient", build)
    configure(client)
    body = client.post("/settings/model-host/check").text
    assert "Could not reach" in body
    assert "OLLAMA_HOST=0.0.0.0" in body


def test_the_json_check_is_the_same_code_path(
    client: TestClient, host: recordings.FakeHost
) -> None:
    configure(client)
    payload = client.post("/api/models/check").json()
    assert payload["ok"] is True
    assert payload["version"] == recordings.RECORDED_VERSION
    assert len(payload["models"]["usable"]) == 4


def test_the_json_check_reports_a_missing_endpoint_as_a_bad_request(
    client: TestClient,
) -> None:
    response = client.post("/api/models/check")
    assert response.status_code == 400
    assert response.json()["remedy"]


# ---------------------------------------------------------------------------------------------
# Choosing a model
# ---------------------------------------------------------------------------------------------


def test_choosing_a_model_stores_it(client: TestClient, host: recordings.FakeHost) -> None:
    configure(client)
    response = client.post("/settings/model", data={"model": "gemma4:e4b"}, follow_redirects=False)
    assert response.status_code == 303
    body = client.get("/settings").text
    assert "gemma4:e4b" in body
    assert (
        "Review work will be sent to this model." in client.get("/settings?saved=model_chosen").text
    )


def test_choosing_nothing_is_refused(client: TestClient) -> None:
    response = client.post("/settings/model", data={"model": "   "})
    assert response.status_code == 200
    assert "No model was chosen." in response.text


def test_a_chosen_model_is_not_claimed_to_be_installed_without_a_check(
    client: TestClient, host: recordings.FakeHost
) -> None:
    """The page has not called the host, so it must not say the model is still there."""
    configure(client)
    client.post("/settings/model", data={"model": "gemma4:e4b"})
    body = client.get("/settings").text
    assert "has not been checked this page load" in body


def test_a_chosen_model_the_host_no_longer_has_is_reported(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chosen here, gone there. Silently falling back to something else would be worse."""
    recorded = recordings.only(["llama3.1:8b"])
    original = views.OllamaClient

    def build(base_url, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("transport", recorded.transport())
        return original(base_url, **kwargs)

    monkeypatch.setattr(views, "OllamaClient", build)
    configure(client)
    client.post("/settings/model", data={"model": "gemma4:e4b"})
    body = client.post("/settings/model-host/check").text
    assert "does not have a model by that name" in body


def test_the_picker_only_offers_models_that_passed_the_filter(
    client: TestClient, host: recordings.FakeHost
) -> None:
    configure(client)
    body = client.post("/settings/model-host/check").text
    picker = body.split("Usable models on")[1].split("Installed, and not used")[0]
    assert 'value="gemma4:e4b"' in picker
    assert 'value="llava:13b"' not in picker
    assert 'value="nomic-embed-text:latest"' not in picker


# ---------------------------------------------------------------------------------------------
# Recommendations and installing
# ---------------------------------------------------------------------------------------------


def test_the_recommendations_render_without_calling_the_host(
    client: TestClient, host: recordings.FakeHost
) -> None:
    body = client.get("/settings").text
    assert "Recommended models" in body
    assert "qwen3:30b" in body
    assert "The model host has not been checked" in body
    assert host.calls == []


def test_recommendations_say_how_each_expectation_was_arrived_at(client: TestClient) -> None:
    body = client.get("/settings").text
    assert "Capabilities read from /api/show" in body
    assert "Capabilities are expected, not measured here" in body


def test_an_installed_recommendation_is_marked_as_installed(
    client: TestClient, host: recordings.FakeHost
) -> None:
    configure(client)
    body = client.post("/settings/model-host/check").text
    row = body.split("<code>gemma4:e4b</code>")[-1]
    assert "installed" in row


def test_installing_starts_a_background_task_and_returns(
    client: TestClient, host: recordings.FakeHost
) -> None:
    """The response does not wait: a request that waited would time out on a 9 GB model."""
    host.pull_lines = recordings.PULL_LINES
    configure(client)
    response = client.post(
        "/settings/model/install", data={"model": "qwen3:30b"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith("#models")

    installs = client.get("/api/models/installs").json()["installs"]
    assert [state["model"] for state in installs] == ["qwen3:30b"]


def test_installing_without_an_endpoint_says_what_to_set(client: TestClient) -> None:
    response = client.post("/settings/model/install", data={"model": "qwen3:30b"})
    assert response.status_code == 200
    assert "No model host endpoint is configured." in response.text


def test_installing_nothing_is_refused(client: TestClient, host: recordings.FakeHost) -> None:
    configure(client)
    response = client.post("/settings/model/install", data={"model": ""})
    assert response.status_code == 200
    assert "No model was named" in response.text


def test_the_screen_offers_no_way_to_delete_a_model(
    client: TestClient, host: recordings.FakeHost
) -> None:
    configure(client)
    body = client.post("/settings/model-host/check").text
    assert "/settings/model/delete" not in body
    assert "deliberately does not call it" in body


def test_the_status_page_reports_the_pinned_context_window(client: TestClient) -> None:
    body = client.get("/").text
    assert "8,192-token context pinned on every call" in body
