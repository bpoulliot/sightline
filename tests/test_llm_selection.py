"""The capability filter, against recorded payloads from a real host.

The acceptance condition for this item is stated as a shape rather than as a name: a multimodal
model is **admitted**, a vision-only model is not, an embedding model is not. Every assertion here
reads the capability list out of ``tests/fixtures/ollama/installed-models.json``, which was
captured from an Ollama 0.20.6 host, so a rule written against a guess about what ``/api/show``
returns cannot pass.
"""

from __future__ import annotations

import pytest

import ollama_recordings as recordings
from sightline.llm.selection import (
    EXCLUDED_CAPABILITIES,
    REQUIRED_CAPABILITIES,
    ModelCard,
    Selection,
    select,
)


@pytest.fixture
def host() -> recordings.FakeHost:
    return recordings.load()


def card_for(host: recordings.FakeHost, name: str) -> ModelCard:
    sizes = {entry["name"]: entry.get("size") for entry in host.tags}
    return ModelCard.from_show(name, host.show[name], size_bytes=sizes.get(name))


# ---------------------------------------------------------------------------------------------
# The three shapes the item names
# ---------------------------------------------------------------------------------------------


def test_a_multimodal_model_is_admitted(host: recordings.FakeHost) -> None:
    """gemma4 reports vision and audio *and* the capabilities the work needs. It is admitted.

    This is the whole point of the rule. "Exclude anything with vision" would throw away the best
    model on the target host, which is why the filter selects on what the work needs instead.
    """
    card = card_for(host, "gemma4:e4b")
    assert card.capabilities == ("completion", "vision", "audio", "tools", "thinking")
    assert card.usable
    assert card.supports_thinking
    assert "vision" in card.extra_capabilities


def test_a_vision_only_model_is_rejected_for_having_no_tools(host: recordings.FakeHost) -> None:
    """llava has vision, and that is not why it is rejected — it has no tools."""
    card = card_for(host, "llava:13b")
    assert card.capabilities == ("completion", "vision")
    assert not card.usable
    assert card.missing == ("tools",)
    assert "'tools'" in card.verdict
    assert card.excluded_by == ()


def test_the_other_vision_only_model_is_rejected_the_same_way(
    host: recordings.FakeHost,
) -> None:
    card = card_for(host, "llama3.2-vision:11b")
    assert not card.usable
    assert card.missing == ("tools",)


def test_an_embedding_model_is_rejected_by_the_exclusion(host: recordings.FakeHost) -> None:
    """nomic-embed-text is excluded, and stays installed and available for embedding use."""
    card = card_for(host, "nomic-embed-text:latest")
    assert card.capabilities == ("embedding",)
    assert not card.usable
    assert card.excluded_by == ("embedding",)
    assert card.is_embedding
    assert "cannot generate" in card.verdict


# ---------------------------------------------------------------------------------------------
# The rest of the recorded host
# ---------------------------------------------------------------------------------------------


def test_thinking_alone_does_not_admit_a_model(host: recordings.FakeHost) -> None:
    """deepseek-r1 reports thinking and no tools. Thinking is not one of the required two."""
    card = card_for(host, "deepseek-r1:8b")
    assert card.capabilities == ("completion", "thinking")
    assert not card.usable
    assert card.missing == ("tools",)
    assert card.supports_thinking


def test_an_unrecognised_capability_is_reported_not_swallowed(host: recordings.FakeHost) -> None:
    """qwen2.5-coder reports 'insert', which the rule neither requires nor excludes."""
    card = card_for(host, "qwen2.5-coder:14b")
    assert "insert" in card.capabilities
    assert card.usable
    assert "insert" in card.verdict


def test_the_whole_recorded_host_splits_as_measured(host: recordings.FakeHost) -> None:
    """The filter over every recorded model, asserted as the names it produced.

    Written as an exact split rather than as counts: a rule that admitted one extra model would
    pass a count assertion for the wrong reason.
    """
    selection = select(card_for(host, name) for name in host.show)
    assert [card.name for card in selection.usable] == [
        "gemma4:e4b",
        "llama3.1:8b",
        "llama3.2:3b",
        "qwen2.5-coder:14b",
    ]
    assert [card.name for card in selection.rejected] == [
        "llava:13b",
        "llama3.2-vision:11b",
        "nomic-embed-text:latest",
        "deepseek-r1:8b",
    ]
    assert [card.name for card in selection.embedding] == ["nomic-embed-text:latest"]


def test_the_rule_is_the_one_the_spec_states() -> None:
    assert REQUIRED_CAPABILITIES == ("completion", "tools")
    assert EXCLUDED_CAPABILITIES == ("embedding",)
    assert "vision" not in REQUIRED_CAPABILITIES + EXCLUDED_CAPABILITIES


# ---------------------------------------------------------------------------------------------
# What a card reports about itself
# ---------------------------------------------------------------------------------------------


def test_a_verdict_names_what_was_examined(host: recordings.FakeHost) -> None:
    """A rejection a reader cannot falsify is the thing this project exists not to emit."""
    for name in host.show:
        card = card_for(host, name)
        for capability in card.capabilities:
            assert capability in card.verdict


def test_context_length_is_read_from_the_architecture_key(host: recordings.FakeHost) -> None:
    """Ollama keys model_info by architecture, so there is no fixed key to read."""
    assert card_for(host, "gemma4:e4b").context_length == 131072
    assert card_for(host, "llava:13b").context_length == 4096
    assert card_for(host, "nomic-embed-text:latest").context_length == 2048


def test_context_length_survives_a_missing_architecture() -> None:
    card = ModelCard.from_show(
        "mystery:1b",
        {"capabilities": ["completion", "tools"], "model_info": {"mystery.context_length": 4096}},
    )
    assert card.context_length == 4096


def test_a_payload_with_no_capabilities_is_rejected_saying_so() -> None:
    """An Ollama too old to report capabilities must be visible as that, not quietly admitted."""
    card = ModelCard.from_show("ancient:7b", {"details": {"family": "llama"}})
    assert card.capabilities == ()
    assert not card.usable
    assert "no capabilities" in card.verdict


def test_capabilities_are_normalized_and_deduplicated() -> None:
    card = ModelCard.from_show(
        "odd:1b", {"capabilities": ["Completion", "tools", "TOOLS", 7, "", "completion"]}
    )
    assert card.capabilities == ("completion", "tools")
    assert card.usable


def test_a_capability_list_of_the_wrong_type_is_empty_not_an_error() -> None:
    assert ModelCard.from_show("odd:1b", {"capabilities": "completion"}).capabilities == ()
    assert ModelCard.from_show("odd:1b", {"capabilities": None}).capabilities == ()


def test_size_and_label_render_without_a_size(host: recordings.FakeHost) -> None:
    assert card_for(host, "gemma4:e4b").size_label == "9.6 GB"
    assert ModelCard(name="x:1b").size_label == "size unknown"
    assert "8.0B" in card_for(host, "gemma4:e4b").label


def test_requires_is_carried_when_a_model_declares_one(host: recordings.FakeHost) -> None:
    """gemma4 declares a minimum Ollama version. Nothing else recorded here does."""
    assert card_for(host, "gemma4:e4b").requires == "0.20.0"
    assert card_for(host, "llama3.1:8b").requires is None


# ---------------------------------------------------------------------------------------------
# The selection as a whole
# ---------------------------------------------------------------------------------------------


def test_an_empty_selection_still_states_the_rule() -> None:
    selection = Selection()
    assert selection.summary() == "0 of 0 installed models can do review work."
    assert "vision is not a disqualifier" in selection.rule().lower()


def test_selection_lookup_finds_rejected_models_too(host: recordings.FakeHost) -> None:
    selection = select(card_for(host, name) for name in host.show)
    assert selection.get("llava:13b") is not None
    assert selection.get("nothing-installed:1b") is None


def test_as_dict_carries_the_verdict_for_every_model(host: recordings.FakeHost) -> None:
    payload = select(card_for(host, name) for name in host.show).as_dict()
    assert payload["summary"] == "4 of 8 installed models can do review work."
    for row in payload["usable"] + payload["rejected"]:
        assert row["verdict"]
        assert isinstance(row["capabilities"], list)
