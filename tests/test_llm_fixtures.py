"""What the Ollama fixtures are allowed to contain.

These payloads are **captured from a real host**, unlike the Canvas fixtures, which are synthetic.
That is a defensible difference and not a relaxation: an ``/api/show`` payload is metadata the
model's authors published — a capability list, a parameter count, a quantization level — and it
carries nothing about the machine it was read from. This module is what turns that sentence from a
claim into a check, because on a public repository the claim is only as good as the thing that
enforces it.

The guard is a **key allowlist**, not a search for known-bad strings. A blocklist finds the leak
it was written for; an allowlist finds the one nobody thought of, because a field that is not on
the list fails whether or not anybody knew it existed.
"""

from __future__ import annotations

import json
import re

import pytest

import ollama_recordings as recordings
from sightline.llm.selection import ModelCard

#: Every key an ``/api/show`` payload may carry in a fixture. The recording deliberately drops
#: the licence text, the modelfile, the prompt template and the tensor list: thousands of lines
#: that prove nothing about a capability filter, and four more places for something to hide.
ALLOWED_SHOW_KEYS = {"capabilities", "details", "model_info", "requires"}

ALLOWED_DETAIL_KEYS = {
    "parent_model",
    "format",
    "family",
    "families",
    "parameter_size",
    "quantization_level",
}

ALLOWED_TAG_KEYS = {"name", "model", "size"}

#: Anything that could be an address, a credential or a person.
FORBIDDEN = re.compile(
    r"(192\.168\.|10\.\d+\.|172\.(1[6-9]|2\d|3[01])\.|localhost|bearer\s|@[\w.-]+\.\w+"
    r"|\.edu\b|\.lan\b|/home/|/Users/)",
    re.IGNORECASE,
)


@pytest.mark.parametrize("path", recordings.fixture_files(), ids=lambda path: path.name)
def test_a_fixture_carries_no_address_credential_or_person(path) -> None:
    text = path.read_text(encoding="utf-8")
    found = FORBIDDEN.findall(text)
    assert found == [], f"{path.name} contains {found}"


@pytest.mark.parametrize("path", recordings.fixture_files(), ids=lambda path: path.name)
def test_a_fixture_carries_only_the_keys_this_client_reads(path) -> None:
    document = json.loads(path.read_text(encoding="utf-8"))
    for name, payload in document["show"].items():
        assert set(payload) <= ALLOWED_SHOW_KEYS, f"{name} carries {set(payload)}"
        assert set(payload.get("details") or {}) <= ALLOWED_DETAIL_KEYS
        for key in payload.get("model_info") or {}:
            assert key == "general.architecture" or key.endswith(".context_length"), key
    for entry in document["tags"]["models"]:
        assert set(entry) <= ALLOWED_TAG_KEYS


@pytest.mark.parametrize("path", recordings.fixture_files(), ids=lambda path: path.name)
def test_a_fixture_says_where_it_came_from(path) -> None:
    """A captured fixture that does not say it was captured is one nobody re-checks."""
    document = json.loads(path.read_text(encoding="utf-8"))
    assert "Recorded from" in document["description"]
    assert "2026-09-17" in document["description"]


def test_the_recording_covers_every_shape_the_filter_has_to_decide() -> None:
    """Multimodal, vision-only, embedding — the three the item names — plus tools-only."""
    host = recordings.load()
    cards = [ModelCard.from_show(name, payload) for name, payload in host.show.items()]
    shapes = {
        "multimodal": [
            c for c in cards if "vision" in c.capabilities and "tools" in c.capabilities
        ],
        "vision only": [c for c in cards if "vision" in c.capabilities and c.missing],
        "embedding": [c for c in cards if c.is_embedding],
        "tools only": [c for c in cards if c.usable and "vision" not in c.capabilities],
        "thinking": [c for c in cards if c.supports_thinking],
    }
    for label, matches in shapes.items():
        assert matches, f"no recorded model covers the {label} case"


def test_the_transport_refuses_a_request_nothing_recorded() -> None:
    """A mock that answers anything lets a client call the wrong endpoint and still pass."""
    import httpx

    host = recordings.load()
    with pytest.raises(recordings.UnrecordedRequest):
        httpx.Client(transport=host.transport(), base_url="http://x").get("/api/generate")
