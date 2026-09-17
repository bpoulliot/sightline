"""Recorded Ollama responses, and the transport that replays them.

The client is tested against these, never against a live host: a test suite that needs a GPU is a
test suite that does not run in CI, and the failures that matter here — an unreachable host, a
model refused for memory, a thinking pin the model rejects — cannot be produced on demand against
a working one anyway.

Unlike ``tests/fixtures/canvas``, these fixtures are **captured, not synthetic**, and that is
safe for a public repository for a reason worth stating: an ``/api/show`` payload is metadata the
model's own authors published — a capability list, a parameter count, a quantization level. It
names no host, no person and no course. ``tests/test_llm_fixtures.py`` asserts that mechanically
rather than leaving it to the eye, exactly as the Canvas fixture guard does.

The transport refuses an unrecorded request rather than answering it. A mock that returns 200 to
anything lets a client call the wrong endpoint and still pass.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "ollama"

#: The version recorded on the host these payloads came from.
RECORDED_VERSION = "0.20.6"

#: An endpoint no fixture may deviate from, so a test cannot accidentally assert on a real one.
EXAMPLE_ENDPOINT = "http://model-host.example.lan:11434"
EXAMPLE_HOST = "model-host.example.lan:11434"


class UnrecordedRequest(AssertionError):
    """The client asked for something no fixture recorded."""


@dataclass
class FakeHost:
    """An Ollama host assembled from recorded payloads, plus whatever a test adds.

    ``show`` and ``tags`` come from the recording. ``chat_responses`` and ``pull_lines`` are set
    per test, because a generation is what a test is asserting about rather than something to
    capture once.
    """

    show: dict[str, dict[str, Any]] = field(default_factory=dict)
    tags: list[dict[str, Any]] = field(default_factory=list)
    version: str = RECORDED_VERSION
    chat_responses: list[dict[str, Any]] = field(default_factory=list)
    pull_lines: list[dict[str, Any]] = field(default_factory=list)
    #: ``path -> (status, body)``. A forced failure wins over the recording, which is how a 404,
    #: a 500 and Ollama's own error text get exercised without a broken host to hand.
    failures: dict[str, tuple[int, Any]] = field(default_factory=dict)
    #: Raised instead of answering, for the unreachable-host paths.
    raises: Exception | None = None
    #: Every request the client actually made, in order. Assertions read this.
    calls: list[httpx.Request] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    @property
    def paths_called(self) -> list[str]:
        return [request.url.path for request in self.calls]

    @property
    def models_shown(self) -> list[str]:
        return [
            json.loads(request.content)["model"]
            for request in self.calls
            if request.url.path == "/api/show"
        ]

    def chat_payloads(self) -> list[dict[str, Any]]:
        """The bodies actually posted to ``/api/chat`` — what the thinking pin is asserted on."""
        return [
            json.loads(request.content) for request in self.calls if request.url.path == "/api/chat"
        ]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.raises is not None:
            raise self.raises
        path = request.url.path
        if path in self.failures:
            status, body = self.failures[path]
            if isinstance(body, str):
                return httpx.Response(status, text=body, request=request)
            return httpx.Response(status, json=body, request=request)
        if path == "/api/version":
            return httpx.Response(200, json={"version": self.version}, request=request)
        if path == "/api/tags":
            return httpx.Response(200, json={"models": self.tags}, request=request)
        if path == "/api/show":
            name = json.loads(request.content).get("model")
            payload = self.show.get(name)
            if payload is None:
                return httpx.Response(
                    404, json={"error": f"model '{name}' not found"}, request=request
                )
            return httpx.Response(200, json=payload, request=request)
        if path == "/api/chat":
            if not self.chat_responses:
                raise UnrecordedRequest("no chat response is recorded for this test")
            index = min(len(self.chat_payloads()) - 1, len(self.chat_responses) - 1)
            return httpx.Response(200, json=self.chat_responses[index], request=request)
        if path == "/api/pull":
            body = "".join(json.dumps(line) + "\n" for line in self.pull_lines)
            return httpx.Response(200, text=body, request=request)
        raise UnrecordedRequest(
            f"nothing is recorded for {request.method} {path}. "
            "This host answers /api/version, /api/tags, /api/show, /api/chat and /api/pull."
        )


def load(name: str = "installed-models") -> FakeHost:
    """Load ``tests/fixtures/ollama/<name>.json`` as a host."""
    document = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return FakeHost(
        show=dict(document["show"]),
        tags=list(document["tags"]["models"]),
    )


def only(names: Sequence[str], source: str = "installed-models") -> FakeHost:
    """A host carrying just the named models, for tests about one shape at a time."""
    full = load(source)
    return FakeHost(
        show={name: full.show[name] for name in names},
        tags=[entry for entry in full.tags if entry["name"] in names],
    )


def chat_response(
    model: str,
    content: str,
    *,
    thinking: str | None = None,
    prompt_eval_count: int | None = 32,
    eval_count: int | None = 12,
    done_reason: str = "stop",
) -> dict[str, Any]:
    """A ``/api/chat`` body of the shape the recorded host returns."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if thinking is not None:
        message["thinking"] = thinking
    body: dict[str, Any] = {
        "model": model,
        "created_at": "2026-09-17T00:00:00.000000000Z",
        "message": message,
        "done": True,
        "done_reason": done_reason,
        "total_duration": 3_706_634_094,
        "load_duration": 3_590_521_637,
        "prompt_eval_duration": 66_552_683,
        "eval_duration": 38_895_032,
    }
    if prompt_eval_count is not None:
        body["prompt_eval_count"] = prompt_eval_count
    if eval_count is not None:
        body["eval_count"] = eval_count
    return body


#: The lines a real pull emits, in the order the host emits them.
PULL_LINES: list[dict[str, Any]] = [
    {"status": "pulling manifest"},
    {
        "status": "pulling dde5aa3fc5ff",
        "digest": "sha256:dde5aa3fc5ff",
        "total": 2_019_377_376,
        "completed": 0,
    },
    {
        "status": "pulling dde5aa3fc5ff",
        "digest": "sha256:dde5aa3fc5ff",
        "total": 2_019_377_376,
        "completed": 1_009_688_688,
    },
    {
        "status": "pulling dde5aa3fc5ff",
        "digest": "sha256:dde5aa3fc5ff",
        "total": 2_019_377_376,
        "completed": 2_019_377_376,
    },
    {"status": "verifying sha256 digest"},
    {"status": "writing manifest"},
    {"status": "success"},
]


def raising_transport(exc: Exception) -> httpx.MockTransport:
    """A transport that fails the way an unreachable host fails."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise exc

    return httpx.MockTransport(handler)


def fixture_files() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


__all__: list[str] = [
    "EXAMPLE_ENDPOINT",
    "EXAMPLE_HOST",
    "PULL_LINES",
    "RECORDED_VERSION",
    "FakeHost",
    "UnrecordedRequest",
    "chat_response",
    "fixture_files",
    "load",
    "only",
    "raising_transport",
]
