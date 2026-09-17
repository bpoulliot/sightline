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
anything lets a client call the wrong endpoint and still pass. That applies to the one ``POST``
Sightline makes exactly as it applies to a ``GET``: a recording matches on method first, so an
unrecorded write is refused rather than quietly answered. An interaction may also declare the
``form`` body it expects, which is how a fixture proves ``export_type=common_cartridge`` was
actually sent rather than merely that *something* was posted.

An interaction may record a **sequence** of responses rather than one, which is how a poll is
recorded: the same request answered ``exporting`` and then ``exported``. Once the sequence is
exhausted the last response keeps being returned, because that is what Canvas does — a finished
export stays finished however many times it is read.

A fixture can carry an archive. Canvas answers a content-export download with zip bytes, which
JSON cannot hold, so the response is declared as ``zip``: a mapping of member name to member text,
and the transport builds the archive from it deterministically. The alternative — a base64 blob —
would be opaque to every guard in ``tests/test_canvas_fixtures.py``, and those guards are the only
reason it is safe to commit fixtures to a public repository at all. A reviewer can read what is in
this export.
"""

from __future__ import annotations

import io
import json
import zipfile
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
    #: How far through each interaction's response sequence the replay has got.
    cursor: dict[int, int] = field(default_factory=dict)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    @property
    def paths_called(self) -> list[str]:
        return [request.url.path for request in self.calls]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for position, interaction in enumerate(self.interactions):
            if self._matches(interaction, request):
                return _build_response(self._next_response(position, interaction), request)
        recorded = ", ".join(
            f"{item.get('method', 'GET')} {item['path']}" for item in self.interactions
        )
        raise UnrecordedRequest(
            f"fixture '{self.name}' has no recording for "
            f"{request.method} {request.url.path}?{request.url.query.decode()}. "
            f"It records: {recorded}"
        )

    def _next_response(self, position: int, interaction: dict[str, Any]) -> dict[str, Any]:
        """The next response for an interaction, holding at the last one in a sequence."""
        sequence = interaction.get("responses")
        if sequence is None:
            return dict(interaction["response"])
        index = min(self.cursor.get(position, 0), len(sequence) - 1)
        self.cursor[position] = index + 1
        return dict(sequence[index])

    @property
    def methods_called(self) -> list[str]:
        return [request.method for request in self.calls]

    @staticmethod
    def _matches(interaction: dict[str, Any], request: httpx.Request) -> bool:
        if interaction.get("method", "GET").upper() != request.method.upper():
            return False
        if interaction["path"] != request.url.path:
            return False
        expected_form = interaction.get("form")
        if expected_form is not None:
            # A recorded body is matched exactly. A fixture that accepted any body would let the
            # client ask Canvas for the wrong kind of export and still pass.
            posted = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            if posted != {key: str(value) for key, value in expected_form.items()}:
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
    if "zip" in spec:
        return httpx.Response(
            spec.get("status", 200),
            content=build_archive(spec["zip"]),
            headers=headers,
            request=request,
        )
    return httpx.Response(
        spec.get("status", 200),
        text=spec.get("text", ""),
        headers=headers,
        request=request,
    )


def build_archive(members: dict[str, str]) -> bytes:
    """A zip of ``name -> text``, byte-identical on every build.

    Deterministic on purpose: members in sorted order and one fixed timestamp, so two runs of the
    suite produce the same digest and the extraction directory — which is named after that digest
    — does not move between runs.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name in sorted(members):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            bundle.writestr(info, members[name])
    return buffer.getvalue()


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
