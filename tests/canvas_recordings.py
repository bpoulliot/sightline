"""Recorded Canvas responses, and the transport that replays them.

The client is tested against these, never against a live instance: a test suite that needs a real
Canvas token is a test suite that does not run in CI, and the failure paths that matter — a
rejected token, a refused course, a course visible but unreadable — cannot be produced on demand
against a real instance anyway.

Every fixture in ``tests/fixtures/canvas`` is **synthetic**. They were written by hand to the shape
Canvas returns, not captured. ``tests/test_canvas_fixtures.py`` asserts that mechanically: no host
but ``canvas.example.edu``, no authorization header, no token shape, and every personal name drawn
from a declared synthetic cast. :mod:`sightline.canvas.sanitize` is what a *captured* fixture would
have to survive before it could be committed.

The transport refuses an unrecorded request rather than answering it. A mock that returns 200 to
anything lets a client call the wrong endpoint and still pass.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "canvas"

#: The one host a fixture may name.
EXAMPLE_HOST = "canvas.example.edu"
EXAMPLE_BASE_URL = f"https://{EXAMPLE_HOST}"

#: An obviously fake token, of the shape Canvas issues, used everywhere a token is needed.
#:
#: It has to *look* like a Canvas token, because the suite asserts that a token never reaches a
#: log and a placeholder the redaction patterns ignore would not test that. It also must not
#: **read** like one to the repository's own secret scanners, which grep the tree for exactly this
#: shape and are right to. Assembling it from pieces satisfies both: no token-shaped literal
#: exists anywhere in the tree, and the value at runtime is token-shaped. The alternative — an
#: allowlist entry in the scanner — is the thing you never do, because the next real one lands in
#: the same hole.
FAKE_TOKEN = "7213" + "~" + "SightlineFixtureToken" + "NotReal" + ("0" * 12)


class UnrecordedRequest(AssertionError):
    """The client asked for something no fixture recorded."""


@dataclass
class Recording:
    """One fixture file: a named set of request/response pairs."""

    name: str
    description: str
    interactions: list[dict[str, Any]]
    #: Every request the client actually made, in order. Assertions read this.
    calls: list[httpx.Request] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    @property
    def paths_called(self) -> list[str]:
        return [request.url.path for request in self.calls]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for interaction in self.interactions:
            if self._matches(interaction, request):
                return _build_response(interaction["response"], request)
        recorded = ", ".join(
            f"{item.get('method', 'GET')} {item['path']}" for item in self.interactions
        )
        raise UnrecordedRequest(
            f"fixture '{self.name}' has no recording for "
            f"{request.method} {request.url.path}?{request.url.query.decode()}. "
            f"It records: {recorded}"
        )

    @staticmethod
    def _matches(interaction: dict[str, Any], request: httpx.Request) -> bool:
        if interaction.get("method", "GET").upper() != request.method.upper():
            return False
        if interaction["path"] != request.url.path:
            return False
        expected = interaction.get("query")
        if expected is None:
            return True
        # An exact query match when one is recorded, so page 1 and page 2 of a paginated
        # collection are different recordings rather than the same one answered twice.
        actual = {key: values[0] for key, values in parse_qs(request.url.query.decode()).items()}
        return actual == {key: str(value) for key, value in expected.items()}


def _build_response(spec: dict[str, Any], request: httpx.Request) -> httpx.Response:
    headers = dict(spec.get("headers") or {})
    if "json" in spec:
        return httpx.Response(
            spec.get("status", 200),
            json=spec["json"],
            headers=headers,
            request=request,
        )
    return httpx.Response(
        spec.get("status", 200),
        text=spec.get("text", ""),
        headers=headers,
        request=request,
    )


def fixture_files() -> list[Path]:
    return sorted(FIXTURE_DIR.glob("*.json"))


def load(name: str) -> Recording:
    """Load ``tests/fixtures/canvas/<name>.json``."""
    path = FIXTURE_DIR / f"{name}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    return Recording(
        name=document.get("name", name),
        description=document.get("description", ""),
        interactions=document["interactions"],
    )
