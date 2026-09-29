"""The sample course, rendered, with located evidence for a handful of rows.

The sample course carries no recorded extraction, and this suite never calls a model. So the rows
item 15 has to render as Tier B and Tier C evidence are answered by a **scripted stand-in** through
the real extraction path: :func:`sightline.extract.runner.answer_requests`, the real prompt, the
real schema check and the real verification step. The stand-in only ever returns a sentence that
occurs in the passage it was sent, so every quote below is accepted by verification on its own
merits; a sentence that did not occur would be discarded and never rendered, as in production.

The sentences are the sample course's own synthetic text, chosen by hand. They are what a model
*could* locate, not what one did — which is the point of a stand-in, and why it lives in the suite
and not in ``examples/``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import httpx
import ollama_recordings as recordings

from sightline.acquire import load_bundle
from sightline.detect import DetectionReport, run_detectors
from sightline.extract.runner import RowExtraction, answer_requests
from sightline.llm.client import OllamaClient
from sightline.model import CourseDocument
from sightline.rubric import Projection, project, shipped_packs
from sightline.rubric.routing import manifest

MODEL = "llama3.1:8b"

#: The reference pack's code -> the sentences the stand-in locates for it, wherever they occur.
#: GSOV 10 quotes a syllabus that says "Grades" — the lint must read it as course text, not as
#: Sightline's vocabulary. COAL 7 and OREP 9 are answered with nothing, on purpose.
STAND_IN: Mapping[str, tuple[str, ...]] = {
    "GSOV 10": (
        "Grades are posted in the Canvas gradebook.",
        "Late work is accepted at the instructor's discretion.",
    ),
    "ENIN 4": ("I will respond to messages as soon as I can.",),
    "ENIN 6": (
        "Reply to at least two classmates.",
        "Submit your lab report as a PDF.",
        "Reply to one classmate who took the other position.",
    ),
    "COAL 7": (),
    "OREP 9": (),
}


def sample_document(repo_root: Path) -> CourseDocument:
    return load_bundle(repo_root / "examples" / "sample-course")


def sample_projection(document: CourseDocument, pack_id: str) -> tuple[Projection, DetectionReport]:
    report = run_detectors(document)
    return project(shipped_packs()[pack_id], report), report


def _document_text(content: str) -> str:
    """The passage between the prompt's ``---`` fences — the only text a quote may come from."""
    parts = content.split("---\n")
    return parts[1] if len(parts) >= 3 else ""


def _stand_in_host(texts: Mapping[str, tuple[str, ...]]) -> httpx.MockTransport:
    recorded = recordings.only([MODEL])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/api/chat":
            recorded.calls.append(request)
            return recorded._handle(request)
        payload = json.loads(request.content)
        content = payload["messages"][1]["content"]
        passage = _document_text(content)
        quotes = [
            {"quote": sentence, "location": "stand-in"}
            for criterion, sentences in texts.items()
            if criterion in content
            for sentence in sentences
            if sentence in passage
        ]
        body = recordings.chat_response(MODEL, json.dumps({"findings": quotes}))
        return httpx.Response(200, json=body, request=request)

    return httpx.MockTransport(handler)


async def stand_in_extractions(
    projection: Projection, document: CourseDocument, codes: Mapping[str, tuple[str, ...]] = STAND_IN
) -> dict[str, RowExtraction]:
    """Answer the routed rows named in ``codes`` through the real extraction path.

    The stand-in recognises a row by its criterion *text*, which the routing contract puts in the
    prompt; the code never reaches a prompt (item 10), so it is mapped to text here.
    """
    routing = manifest(projection.rows)
    wanted = [request for request in routing.requests if request.criterion in codes]
    by_text = {request.text: codes[request.criterion] for request in wanted}
    async with OllamaClient(recordings.EXAMPLE_ENDPOINT, transport=_stand_in_host(by_text)) as client:
        answered = await answer_requests(wanted, document, client, MODEL)
    return {entry.criterion: entry for entry in answered}
