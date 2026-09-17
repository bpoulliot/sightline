"""The Ollama client, against recorded payloads and forced failures.

Three groups of assertions matter more than the rest, and each of them encodes something that was
**measured against the target host** rather than assumed — see :mod:`sightline.llm.client` for the
measurements themselves.

* Every call pins a context window, because Ollama sizes its allocation from a model's declared
  window otherwise and refuses models that would comfortably fit.
* The thinking pin is capability-aware: sent for a model that reports ``thinking``, omitted
  entirely for one that does not, and refused before the request when somebody asks for it on a
  model that cannot.
* Every call logs its latency and its token counts. The earlier local-AI pipeline this project
  keeps referring to failed *silently*; a test that only checked the return value would let that
  happen again.
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

import ollama_recordings as recordings
from sightline.llm.client import (
    DEFAULT_CONTEXT_TOKENS,
    OllamaClient,
    Thinking,
    host_of,
    normalize_endpoint,
)
from sightline.llm.errors import (
    ModelHostError,
    ModelHostNotConfiguredError,
    ModelHostNotOllamaError,
    ModelHostOutOfMemoryError,
    ModelHostServerError,
    ModelHostUnreachableError,
    ModelNotFoundError,
    ModelNotUsableError,
    ModelThinkingUnsupportedError,
)
from sightline.logging import configure_logging

ENDPOINT = recordings.EXAMPLE_ENDPOINT


@pytest.fixture
def host() -> recordings.FakeHost:
    return recordings.load()


def client_for(host: recordings.FakeHost, **kwargs: object) -> OllamaClient:
    return OllamaClient(ENDPOINT, transport=host.transport(), **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    """Capture the bytes the formatter actually renders, not an intercepted record."""
    stream = io.StringIO()
    configure_logging(level="DEBUG", fmt="json", stream=stream)
    yield stream
    configure_logging(level="INFO", fmt="json")


def _events(captured: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (raw.strip() for raw in captured.splitlines())
        if line.startswith("{")
    ]


# ---------------------------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------------------------


def test_an_endpoint_is_trimmed_to_scheme_host_and_port() -> None:
    assert normalize_endpoint("http://model-host.example.lan:11434/api/tags/") == ENDPOINT
    assert normalize_endpoint("  http://model-host.example.lan:11434  ") == ENDPOINT


def test_a_scheme_is_assumed_rather_than_failing() -> None:
    """http, not https: Ollama serves plain HTTP and a TLS error names the wrong problem."""
    assert normalize_endpoint("model-host.example.lan:11434") == ENDPOINT


def test_an_unusable_endpoint_fails_with_a_remedy() -> None:
    for value in ("", "   ", None, "http://", "not a url", "http://two words:11434"):
        with pytest.raises(ModelHostNotConfiguredError) as caught:
            normalize_endpoint(value)  # type: ignore[arg-type]
        assert caught.value.remedy


def test_the_host_label_keeps_the_port() -> None:
    assert host_of(ENDPOINT) == recordings.EXAMPLE_HOST
    assert host_of("http://model-host.example.lan") == "model-host.example.lan"
    assert host_of(None) is None


# ---------------------------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------------------------


async def test_version_reads_the_hosts_own_answer(host: recordings.FakeHost) -> None:
    async with client_for(host) as client:
        assert await client.version() == recordings.RECORDED_VERSION


async def test_a_host_that_is_not_ollama_says_so(host: recordings.FakeHost) -> None:
    host.failures["/api/version"] = (200, {"nginx": "hello"})
    async with client_for(host) as client:
        with pytest.raises(ModelHostNotOllamaError):
            await client.version()


async def test_a_host_answering_html_says_so(host: recordings.FakeHost) -> None:
    host.failures["/api/version"] = (200, "<html>a web ui</html>")
    async with client_for(host) as client:
        with pytest.raises(ModelHostNotOllamaError) as caught:
            await client.version()
    assert "not JSON" in str(caught.value)


async def test_installed_reads_tags_then_show_for_every_model(host: recordings.FakeHost) -> None:
    """/api/tags does not report capabilities, so there is no shortcut past /api/show."""
    async with client_for(host) as client:
        selection = await client.installed()

    assert host.paths_called[0] == "/api/tags"
    assert sorted(host.models_shown) == sorted(host.show)
    assert [card.name for card in selection.usable] == [
        "gemma4:e4b",
        "llama3.1:8b",
        "llama3.2:3b",
        "qwen2.5-coder:14b",
    ]
    assert selection.get("gemma4:e4b").size_bytes == 9608350718  # type: ignore[union-attr]


async def test_a_tags_answer_with_no_models_array_is_a_failure(
    host: recordings.FakeHost,
) -> None:
    host.failures["/api/tags"] = (200, {"something": "else"})
    async with client_for(host) as client:
        with pytest.raises(ModelHostNotOllamaError):
            await client.installed()


async def test_a_model_that_is_not_installed_is_named_in_the_error(
    host: recordings.FakeHost,
) -> None:
    async with client_for(host) as client:
        with pytest.raises(ModelNotFoundError) as caught:
            await client.card("nothing-installed:1b")
    assert "nothing-installed:1b" in str(caught.value)
    assert caught.value.status_code == 404


# ---------------------------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------------------------


async def test_health_reports_host_version_and_usable_models(host: recordings.FakeHost) -> None:
    async with client_for(host) as client:
        report = await client.health()

    assert report.ok
    assert report.host == recordings.EXAMPLE_HOST
    assert report.version == recordings.RECORDED_VERSION
    assert recordings.EXAMPLE_HOST in report.summary()
    assert recordings.RECORDED_VERSION in report.summary()
    assert "4 of 8 installed models can do review work" in report.summary()
    assert [check.name for check in report.checks] == [
        "Host answered",
        "Models listed",
        "Usable models",
    ]


async def test_health_degrades_with_a_message_when_nothing_answers() -> None:
    """The one thing this call exists to report is failure, so it must not raise."""
    transport = recordings.raising_transport(httpx.ConnectError("no route to host"))
    async with OllamaClient(ENDPOINT, transport=transport) as client:
        report = await client.health()

    assert not report.ok
    assert report.version is None
    assert report.host == recordings.EXAMPLE_HOST
    assert report.checks[0].status == "failed"
    assert report.remedy
    assert "OLLAMA_HOST" in report.remedy
    assert recordings.EXAMPLE_HOST in report.summary()


async def test_health_on_a_timeout_names_the_host() -> None:
    transport = recordings.raising_transport(httpx.ReadTimeout("too slow"))
    async with OllamaClient(ENDPOINT, transport=transport) as client:
        report = await client.health()
    assert not report.ok
    assert "did not answer in time" in report.checks[0].detail


async def test_health_fails_the_usable_check_when_nothing_qualifies() -> None:
    """A host with only vision and embedding models is reachable and cannot do the work."""
    host = recordings.only(["llava:13b", "nomic-embed-text:latest"])
    async with client_for(host) as client:
        report = await client.health()

    assert not report.ok
    assert report.version == recordings.RECORDED_VERSION
    assert report.checks[-1].name == "Usable models"
    assert report.checks[-1].status == "failed"
    assert "completion" in (report.remedy or "")
    assert report.rejected


async def test_health_reports_a_listing_failure_separately(host: recordings.FakeHost) -> None:
    host.failures["/api/tags"] = (500, {"error": "something broke"})
    async with client_for(host) as client:
        report = await client.health()
    assert [check.name for check in report.checks] == ["Host answered", "Models listed"]
    assert report.checks[1].status == "failed"


async def test_health_as_dict_carries_the_models_and_the_rule(host: recordings.FakeHost) -> None:
    async with client_for(host) as client:
        payload = (await client.health()).as_dict()
    assert payload["ok"] is True
    assert payload["context_tokens"] == DEFAULT_CONTEXT_TOKENS
    assert payload["concurrency"] == 1
    assert len(payload["models"]["usable"]) == 4
    assert "vision is not a disqualifier" in payload["models"]["rule"].lower()


# ---------------------------------------------------------------------------------------------
# The context pin
# ---------------------------------------------------------------------------------------------


async def test_every_call_pins_a_context_window(host: recordings.FakeHost) -> None:
    """Without this the target host refuses a 2 GB model for 52.2 GiB. Measured, not guessed."""
    host.chat_responses = [recordings.chat_response("llama3.1:8b", "ok")]
    async with client_for(host) as client:
        await client.chat(
            "llama3.1:8b",
            [{"role": "user", "content": "hi"}],
            thinking=Thinking.OFF,
            operation="probe",
        )
    posted = host.chat_payloads()[0]
    assert posted["options"]["num_ctx"] == DEFAULT_CONTEXT_TOKENS
    assert posted["options"]["temperature"] == 0.0
    assert posted["stream"] is False


async def test_the_context_pin_is_configurable_per_client_and_per_call(
    host: recordings.FakeHost,
) -> None:
    host.chat_responses = [recordings.chat_response("llama3.1:8b", "ok")] * 2
    async with client_for(host, context_tokens=4096) as client:
        result = await client.chat(
            "llama3.1:8b",
            [{"role": "user", "content": "hi"}],
            thinking=Thinking.OFF,
            operation="probe",
        )
        assert result.context_tokens == 4096
        await client.chat(
            "llama3.1:8b",
            [{"role": "user", "content": "hi"}],
            thinking=Thinking.OFF,
            operation="probe",
            context_tokens=1024,
        )
    assert [call["options"]["num_ctx"] for call in host.chat_payloads()] == [4096, 1024]


async def test_being_refused_for_memory_names_the_context_window(
    host: recordings.FakeHost,
) -> None:
    """The host's own words, mapped to the remedy that actually fixes it."""
    host.failures["/api/chat"] = (
        500,
        {"error": "model requires more system memory (52.2 GiB) than is available (48.7 GiB)"},
    )
    async with client_for(host) as client:
        with pytest.raises(ModelHostOutOfMemoryError) as caught:
            await client.chat(
                "llama3.1:8b",
                [{"role": "user", "content": "hi"}],
                thinking=Thinking.OFF,
                operation="probe",
            )
    assert "52.2 GiB" in str(caught.value)
    assert "context window" in caught.value.remedy


# ---------------------------------------------------------------------------------------------
# The thinking pin
# ---------------------------------------------------------------------------------------------


async def test_thinking_on_is_sent_for_a_model_that_supports_it(
    host: recordings.FakeHost,
) -> None:
    host.chat_responses = [
        recordings.chat_response("gemma4:e4b", "ok", thinking="Thinking Process: ...")
    ]
    async with client_for(host) as client:
        result = await client.chat(
            "gemma4:e4b",
            [{"role": "user", "content": "hi"}],
            thinking=Thinking.ON,
            operation="extract",
        )
    assert host.chat_payloads()[0]["think"] is True
    assert result.thinking_pinned is True
    assert result.content == "ok"
    assert result.thinking == "Thinking Process: ..."


async def test_thinking_off_is_sent_for_a_model_that_supports_it(
    host: recordings.FakeHost,
) -> None:
    host.chat_responses = [recordings.chat_response("gemma4:e4b", "ok")]
    async with client_for(host) as client:
        result = await client.chat(
            "gemma4:e4b",
            [{"role": "user", "content": "hi"}],
            thinking=Thinking.OFF,
            operation="extract",
        )
    assert host.chat_payloads()[0]["think"] is False
    assert result.thinking_pinned is False


async def test_thinking_is_omitted_entirely_for_a_model_without_it(
    host: recordings.FakeHost,
) -> None:
    """Omission is not false: the field would claim a decision the model cannot act on."""
    host.chat_responses = [recordings.chat_response("llama3.1:8b", "ok")]
    async with client_for(host) as client:
        result = await client.chat(
            "llama3.1:8b",
            [{"role": "user", "content": "hi"}],
            thinking=Thinking.OFF,
            operation="extract",
        )
    assert "think" not in host.chat_payloads()[0]
    assert result.thinking_pinned is None


async def test_asking_for_thinking_on_a_model_without_it_fails_before_the_request(
    host: recordings.FakeHost,
) -> None:
    """The host answers 400 for this. Failing first names the model in the remedy, not the wire."""
    async with client_for(host) as client:
        with pytest.raises(ModelThinkingUnsupportedError) as caught:
            await client.chat(
                "llama3.1:8b",
                [{"role": "user", "content": "hi"}],
                thinking=Thinking.ON,
                operation="extract",
            )
    assert "/api/chat" not in host.paths_called
    assert "llama3.1:8b" in caught.value.summary
    assert "completion, tools" in (caught.value.detail or "")


async def test_the_hosts_own_thinking_refusal_is_mapped_too(host: recordings.FakeHost) -> None:
    """Mapped as well as pre-empted: a model whose capabilities change is not a 'refused' page."""
    host.failures["/api/chat"] = (400, {"error": '"llama3.2:3b" does not support thinking'})
    host.chat_responses = [recordings.chat_response("gemma4:e4b", "ok")]
    async with client_for(host) as client:
        with pytest.raises(ModelThinkingUnsupportedError):
            await client.chat(
                "gemma4:e4b",
                [{"role": "user", "content": "hi"}],
                thinking=Thinking.ON,
                operation="extract",
            )


# ---------------------------------------------------------------------------------------------
# Logging — the silent-failure guard
# ---------------------------------------------------------------------------------------------


async def test_every_call_logs_latency_and_token_counts(
    host: recordings.FakeHost, log_stream: io.StringIO
) -> None:
    host.chat_responses = [
        recordings.chat_response("gemma4:e4b", "ok", prompt_eval_count=20, eval_count=24)
    ]
    async with client_for(host) as client:
        await client.chat(
            "gemma4:e4b",
            [{"role": "user", "content": "hi"}],
            thinking=Thinking.ON,
            operation="extract",
        )

    events = [event for event in _events(log_stream.getvalue()) if event["event"] == "model_call"]
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "ok"
    assert event["operation"] == "extract"
    assert event["model"] == "gemma4:e4b"
    assert event["endpoint_host"] == "model-host.example.lan"
    assert event["prompt_tokens"] == 20
    assert event["completion_tokens"] == 24
    assert event["total_tokens"] == 44
    assert event["tokens_reported"] is True
    assert event["thinking"] == "on"
    assert event["think_sent"] is True
    assert event["context_tokens"] == DEFAULT_CONTEXT_TOKENS
    assert isinstance(event["latency_ms"], int | float)


async def test_a_host_that_reports_no_token_counts_logs_that_as_a_fact(
    host: recordings.FakeHost, log_stream: io.StringIO
) -> None:
    """An absent field looks like a field nobody needed; an explicit false is a question asked."""
    host.chat_responses = [
        recordings.chat_response("llama3.1:8b", "ok", prompt_eval_count=None, eval_count=None)
    ]
    async with client_for(host) as client:
        result = await client.chat(
            "llama3.1:8b",
            [{"role": "user", "content": "hi"}],
            thinking=Thinking.OFF,
            operation="extract",
        )

    event = [e for e in _events(log_stream.getvalue()) if e["event"] == "model_call"][0]
    assert event["tokens_reported"] is False
    assert result.total_tokens is None


async def test_a_failed_call_is_logged_as_an_error_and_reraised(
    host: recordings.FakeHost, log_stream: io.StringIO
) -> None:
    host.failures["/api/chat"] = (500, {"error": "something broke"})
    async with client_for(host) as client:
        with pytest.raises(ModelHostServerError):
            await client.chat(
                "llama3.1:8b",
                [{"role": "user", "content": "hi"}],
                thinking=Thinking.OFF,
                operation="extract",
            )

    event = [e for e in _events(log_stream.getvalue()) if e["event"] == "model_call"][0]
    assert event["status"] == "error"
    assert event["error_type"] == "ModelHostServerError"


async def test_durations_are_converted_out_of_nanoseconds(host: recordings.FakeHost) -> None:
    host.chat_responses = [recordings.chat_response("llama3.1:8b", "ok")]
    async with client_for(host) as client:
        result = await client.chat(
            "llama3.1:8b",
            [{"role": "user", "content": "hi"}],
            thinking=Thinking.OFF,
            operation="probe",
        )
    assert result.total_duration_ms == pytest.approx(3706.63, abs=0.01)
    assert result.load_duration_ms == pytest.approx(3590.52, abs=0.01)
    assert result.done_reason == "stop"


# ---------------------------------------------------------------------------------------------
# Capability enforcement at call time
# ---------------------------------------------------------------------------------------------


async def test_a_rejected_model_is_refused_with_its_verdict(host: recordings.FakeHost) -> None:
    async with client_for(host) as client:
        with pytest.raises(ModelNotUsableError) as caught:
            await client.chat(
                "llava:13b",
                [{"role": "user", "content": "hi"}],
                thinking=Thinking.OFF,
                operation="extract",
            )
    assert "tools" in (caught.value.detail or "")
    assert "/api/chat" not in host.paths_called


async def test_the_usability_check_can_be_waived_deliberately(
    host: recordings.FakeHost,
) -> None:
    """An embedding model still cannot chat; what is waived is the *filter*, not the host."""
    host.chat_responses = [recordings.chat_response("llava:13b", "a description")]
    async with client_for(host) as client:
        result = await client.chat(
            "llava:13b",
            [{"role": "user", "content": "hi"}],
            thinking=Thinking.OFF,
            operation="probe",
            require_usable=False,
        )
    assert result.content == "a description"


async def test_a_card_can_be_passed_to_avoid_the_extra_show_call(
    host: recordings.FakeHost,
) -> None:
    host.chat_responses = [recordings.chat_response("llama3.1:8b", "ok")]
    async with client_for(host) as client:
        selection = await client.installed()
        card = selection.get("llama3.1:8b")
        assert card is not None
        before = len(host.models_shown)
        await client.chat(
            card,
            [{"role": "user", "content": "hi"}],
            thinking=Thinking.OFF,
            operation="probe",
        )
    assert len(host.models_shown) == before


# ---------------------------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------------------------


async def test_concurrency_defaults_to_one() -> None:
    host = recordings.load()
    async with client_for(host) as client:
        assert client.concurrency == 1


async def test_concurrency_is_capped_at_the_configured_number() -> None:
    """48-way concurrency against capacity that cannot sustain it is what killed the last one."""
    in_flight = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return httpx.Response(200, json={"version": recordings.RECORDED_VERSION})

    transport = httpx.MockTransport(handler)
    async with OllamaClient(ENDPOINT, transport=transport, concurrency=2) as client:
        await asyncio.gather(*(client.version() for _ in range(8)))
    assert peak == 2


async def test_a_concurrency_below_one_is_raised_to_one() -> None:
    host = recordings.load()
    async with client_for(host, concurrency=0) as client:
        assert client.concurrency == 1


# ---------------------------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------------------------


async def test_a_pull_yields_every_line_the_host_sends(host: recordings.FakeHost) -> None:
    host.pull_lines = recordings.PULL_LINES
    async with client_for(host) as client:
        progress = [line async for line in client.pull("llama3.2:3b")]

    assert [line.status for line in progress] == [line["status"] for line in recordings.PULL_LINES]
    assert progress[-1].done
    assert progress[1].percent == 0.0
    assert progress[2].percent == 50.0
    assert progress[3].percent == 100.0


async def test_a_line_without_numbers_has_no_percentage(host: recordings.FakeHost) -> None:
    """A bar reading 0% while a manifest is fetched tells the reader something false."""
    host.pull_lines = [{"status": "pulling manifest"}]
    async with client_for(host) as client:
        progress = [line async for line in client.pull("llama3.2:3b")]
    assert progress[0].percent is None


async def test_a_failure_inside_a_pull_stream_is_raised(host: recordings.FakeHost) -> None:
    """Ollama reports a failed pull inside a 200. Unmapped, it would look like a finished one."""
    host.pull_lines = [
        {"status": "pulling manifest"},
        {"error": "model 'nope:1b' not found"},
    ]
    async with client_for(host) as client:
        with pytest.raises(ModelNotFoundError):
            _ = [line async for line in client.pull("nope:1b")]


async def test_a_pull_against_an_unreachable_host_says_so() -> None:
    transport = recordings.raising_transport(httpx.ConnectError("no route"))
    async with OllamaClient(ENDPOINT, transport=transport) as client:
        with pytest.raises(ModelHostUnreachableError):
            _ = [line async for line in client.pull("llama3.2:3b")]


async def test_a_pull_refused_outright_maps_to_an_error(host: recordings.FakeHost) -> None:
    host.failures["/api/pull"] = (500, {"error": "disk full"})
    async with client_for(host) as client:
        with pytest.raises(ModelHostServerError):
            _ = [line async for line in client.pull("llama3.2:3b")]


def test_nothing_in_the_package_calls_the_delete_endpoint() -> None:
    """``/api/delete`` exists and is deliberately not wired up (SPEC §06).

    Asserted mechanically rather than trusted to review: the reason not to have it is that a
    review tool which can remove somebody's models eventually removes somebody's models, and that
    reason does not weaken when somebody is adding an unrelated method in six months.
    """
    package = Path(__file__).resolve().parents[1] / "src" / "sightline"
    offenders = []
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # String *constants*, so the prose in this package explaining the decision does not
            # trip the guard that enforces it.
            if isinstance(node, ast.Constant) and node.value == "/api/delete":
                offenders.append(f"{path.relative_to(package)}:{node.lineno}")
    assert offenders == []


# ---------------------------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------------------------


async def test_an_unknown_route_is_not_reported_as_a_missing_model(
    host: recordings.FakeHost,
) -> None:
    host.failures["/api/tags"] = (404, {"error": "404 page not found"})
    async with client_for(host) as client:
        with pytest.raises(ModelHostNotOllamaError):
            await client.installed()


async def test_a_refusal_that_is_not_mapped_still_carries_a_remedy(
    host: recordings.FakeHost,
) -> None:
    host.failures["/api/chat"] = (422, {"error": "something specific and new"})
    async with client_for(host) as client:
        with pytest.raises(ModelHostError) as caught:
            await client.chat(
                "llama3.1:8b",
                [{"role": "user", "content": "hi"}],
                thinking=Thinking.OFF,
                operation="probe",
            )
    assert "something specific and new" in (caught.value.detail or "")
    assert caught.value.remedy


def test_an_error_renders_for_a_template_without_parsing_prose() -> None:
    error = ModelNotFoundError("gone:1b is not installed.", detail="POST /api/show -> HTTP 404")
    payload = error.as_dict()
    assert payload["kind"] == "ModelNotFoundError"
    assert payload["summary"] and payload["remedy"] and payload["detail"]
    assert error.summary in str(error)


def test_a_repr_carries_no_more_than_the_host() -> None:
    client = OllamaClient(ENDPOINT)
    assert repr(client) == f"<OllamaClient host='{recordings.EXAMPLE_HOST}' concurrency=1>"
