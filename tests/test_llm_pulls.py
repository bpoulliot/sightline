"""Installing a model as a background task, and what its state says while it runs.

The item asks for progress in the UI. What that needs from this layer is that a pull outlives the
request that started it, that its state is readable at any moment, and — the part that is easy to
get wrong — that a finished pull stays in the list. A success that vanishes is indistinguishable
from one that never started, and a failure that vanishes is the silent failure this whole project
keeps guarding against.
"""

from __future__ import annotations

import asyncio

import httpx

import ollama_recordings as recordings
from sightline.llm.client import OllamaClient
from sightline.llm.pulls import PullRegistry

ENDPOINT = recordings.EXAMPLE_ENDPOINT


def factory_for(host: recordings.FakeHost):
    return lambda: OllamaClient(ENDPOINT, transport=host.transport())


async def drain(registry: PullRegistry) -> None:
    """Wait for every task the registry is running, without reaching into its internals."""
    for _ in range(500):
        if not registry.running:
            return
        await asyncio.sleep(0.001)
    raise AssertionError("an install never finished")


async def test_a_successful_install_records_every_line_it_saw() -> None:
    host = recordings.load()
    host.pull_lines = recordings.PULL_LINES
    registry = PullRegistry()

    state = registry.start("llama3.2:3b", factory_for(host))
    assert state.running
    await drain(registry)

    finished = registry.get("llama3.2:3b")
    assert finished is not None
    assert finished.succeeded
    assert not finished.failed
    assert finished.status == "success"
    assert finished.percent == 100.0
    assert len(finished.digests_seen) == 1
    assert finished.finished_at is not None


async def test_a_finished_install_stays_in_the_list() -> None:
    host = recordings.load()
    host.pull_lines = recordings.PULL_LINES
    registry = PullRegistry()
    registry.start("llama3.2:3b", factory_for(host))
    await drain(registry)
    assert [state.model for state in registry.states()] == ["llama3.2:3b"]


async def test_a_failed_install_records_the_failure_and_its_remedy() -> None:
    host = recordings.load()
    host.failures["/api/pull"] = (500, {"error": "disk full"})
    registry = PullRegistry()

    registry.start("qwen3:30b", factory_for(host))
    await drain(registry)

    state = registry.get("qwen3:30b")
    assert state is not None
    assert state.failed
    assert not state.succeeded
    assert state.error is not None
    assert state.error["remedy"]
    assert state.detail == state.error["summary"]


async def test_a_failure_reported_inside_the_stream_is_a_failure() -> None:
    """Ollama reports a bad name inside a 200. Unmapped it would read as a finished install."""
    host = recordings.load()
    host.pull_lines = [{"status": "pulling manifest"}, {"error": "model 'nope:1b' not found"}]
    registry = PullRegistry()

    registry.start("nope:1b", factory_for(host))
    await drain(registry)

    state = registry.get("nope:1b")
    assert state is not None
    assert state.failed
    assert state.error is not None
    assert state.error["kind"] == "ModelNotFoundError"


async def test_starting_the_same_model_twice_does_not_start_a_second_download() -> None:
    started = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal started
        started += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, text='{"status": "success"}\n')

    def factory() -> OllamaClient:
        return OllamaClient(ENDPOINT, transport=httpx.MockTransport(slow))

    registry = PullRegistry()
    first = registry.start("llama3.2:3b", factory)
    second = registry.start("llama3.2:3b", factory)
    assert first is second
    await drain(registry)
    assert started == 1


async def test_installing_a_model_again_after_it_finished_is_allowed() -> None:
    host = recordings.load()
    host.pull_lines = recordings.PULL_LINES
    registry = PullRegistry()

    first = registry.start("llama3.2:3b", factory_for(host))
    await drain(registry)
    second = registry.start("llama3.2:3b", factory_for(host))
    assert second is not first
    await drain(registry)
    assert registry.get("llama3.2:3b") is second


async def test_shutdown_cancels_a_running_install_and_says_so() -> None:
    """Nothing may outlive the process, and the record has to show why it stopped."""

    async def never(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200, text="")

    registry = PullRegistry()
    registry.start(
        "qwen3:30b", lambda: OllamaClient(ENDPOINT, transport=httpx.MockTransport(never))
    )
    await asyncio.sleep(0.01)
    await registry.shutdown()

    state = registry.get("qwen3:30b")
    assert state is not None
    assert state.status == "cancelled"
    assert state.error is not None
    assert "again" in state.error["remedy"]
    assert not registry.running


async def test_progress_is_json_serialisable_for_the_api() -> None:
    host = recordings.load()
    host.pull_lines = recordings.PULL_LINES
    registry = PullRegistry()
    registry.start("llama3.2:3b", factory_for(host))
    await drain(registry)

    payload = registry.states()[0].as_dict()
    assert payload["model"] == "llama3.2:3b"
    assert payload["succeeded"] is True
    assert payload["percent"] == 100.0
    assert payload["layers_seen"] == 1
    assert payload["started_at"] and payload["finished_at"]


async def test_a_pull_with_no_numbers_yet_has_no_percentage() -> None:
    host = recordings.load()
    host.pull_lines = [{"status": "pulling manifest"}, {"status": "success"}]
    registry = PullRegistry()
    registry.start("llama3.2:3b", factory_for(host))
    await drain(registry)

    state = registry.get("llama3.2:3b")
    assert state is not None
    assert state.percent is None
    assert state.detail == "success"


async def test_a_stream_that_ends_without_success_is_not_a_success() -> None:
    """Nothing raised, and the host never said it finished. That is not an installed model.

    Reporting it as installed because no exception escaped is the silent wrongness this layer
    exists to prevent: the failure would surface later, somewhere else, as a model that is not
    there.
    """
    host = recordings.load()
    host.pull_lines = recordings.PULL_LINES[:3]  # cut off mid-download
    registry = PullRegistry()

    registry.start("qwen3:30b", factory_for(host))
    await drain(registry)

    state = registry.get("qwen3:30b")
    assert state is not None
    assert not state.succeeded
    assert state.failed
    assert state.error is not None
    assert state.error["kind"] == "Incomplete"
    assert "without the host reporting success" in state.error["summary"]
