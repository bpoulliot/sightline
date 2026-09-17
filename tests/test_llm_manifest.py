"""The curated manifest, and the limits on what it is allowed to claim.

The manifest is a committed data file that describes somebody else's software, so it can be wrong
in a way code cannot: a model's capabilities can change under it. These tests hold the line that
makes that survivable — the manifest never decides anything, it only states an expectation, and
every entry says how that expectation was arrived at.
"""

from __future__ import annotations

import pytest
import yaml

import ollama_recordings as recordings
from sightline.llm.manifest import (
    EVIDENCE_VALUES,
    MANIFEST_PATH,
    ManifestError,
    load_manifest,
    parse_manifest,
)
from sightline.llm.selection import ModelCard


@pytest.fixture
def manifest():
    return load_manifest()


# ---------------------------------------------------------------------------------------------
# The shipped file
# ---------------------------------------------------------------------------------------------


def test_the_shipped_manifest_is_a_short_curated_list(manifest) -> None:
    """8-12 candidates sized for the host, not a browser over 170 models (SPEC §06)."""
    assert 8 <= len(manifest) <= 12
    assert len({entry.name for entry in manifest}) == len(manifest)


def test_every_entry_names_an_exact_tag(manifest) -> None:
    """'llama3.1' and 'llama3.1:8b' are different names to Ollama."""
    for entry in manifest:
        assert ":" in entry.name


def test_every_entry_says_where_its_capabilities_came_from(manifest) -> None:
    for entry in manifest:
        assert entry.evidence in EVIDENCE_VALUES
        assert entry.evidence_note
        assert entry.why.strip()


def test_verified_entries_match_the_recorded_payloads(manifest) -> None:
    """An entry claiming it was read from a host must match the payload recorded from one.

    This is the assertion that keeps the manifest honest over time. An entry can say
    ``evidence: api-show`` only if the recording agrees with it, so a capability list edited by
    hand into this file fails here rather than being believed on a settings screen.
    """
    host = recordings.load()
    verified = [entry for entry in manifest if entry.verified]
    assert verified, "at least one entry should be backed by a recording"
    for entry in verified:
        assert entry.name in host.show, f"{entry.name} claims api-show evidence with no recording"
        card = ModelCard.from_show(entry.name, host.show[entry.name])
        assert card.capabilities == entry.expected_capabilities


def test_the_embedding_entry_is_expected_to_fail_the_filter(manifest) -> None:
    """It is on the list to be installed and excluded, which is the point of the exclusion."""
    embedding = manifest.for_embedding
    assert len(embedding) == 1
    assert not embedding[0].expected_usable
    assert "embedding" in embedding[0].expected_capabilities


def test_every_review_entry_is_expected_to_pass_the_filter(manifest) -> None:
    for entry in manifest.for_review:
        assert entry.expected_usable, f"{entry.name} is recommended and not expected to qualify"


def test_a_multimodal_entry_is_on_the_list(manifest) -> None:
    """The list must not quietly re-introduce the exclusion the filter refuses to make."""
    multimodal = [entry for entry in manifest.for_review if "vision" in entry.expected_capabilities]
    assert multimodal
    assert all(entry.expected_usable for entry in multimodal)


def test_sizes_are_real_numbers_with_a_stated_source(manifest) -> None:
    for entry in manifest:
        assert entry.download_bytes > 100_000_000
    assert "registry.ollama.ai" in manifest.provenance
    assert manifest.sizes_read_on


def test_the_file_carries_its_own_provenance() -> None:
    """The header explains the catalogue probe and why this file is hand-written."""
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    assert "ollama-models.zwz.workers.dev" in text
    assert "gemma4" in text


# ---------------------------------------------------------------------------------------------
# Expectation against reality
# ---------------------------------------------------------------------------------------------


def test_confirmation_separates_unchecked_from_not_installed(manifest) -> None:
    """Two different facts. Collapsing them puts an unverified claim on the screen."""
    entry = manifest.for_review[0]
    assert "has not been checked" in entry.confirmation(None, checked=False)
    assert entry.confirmation(None, checked=True).startswith("Not installed.")


def test_confirmation_states_a_difference_rather_than_hiding_it(manifest) -> None:
    entry = manifest.get("llama3.1:8b")
    assert entry is not None
    card = ModelCard(name="llama3.1:8b", capabilities=("completion",))
    sentence = entry.confirmation(card)
    assert "does not report tools" in sentence

    richer = ModelCard(name="llama3.1:8b", capabilities=("completion", "tools", "thinking"))
    assert "also reports thinking" in entry.confirmation(richer)


def test_confirmation_says_so_when_the_expectation_holds(manifest) -> None:
    entry = manifest.get("llama3.1:8b")
    assert entry is not None
    card = ModelCard(name="llama3.1:8b", capabilities=("completion", "tools"))
    assert "is what this entry expected" in entry.confirmation(card)


def test_rows_mark_every_entry_unchecked_when_nothing_was_read(manifest) -> None:
    rows = manifest.rows(None)
    assert len(rows) == len(manifest)
    assert all(row["installed"] is None for row in rows)
    assert all(row["checked"] is False for row in rows)


def test_rows_mark_what_the_host_actually_has(manifest) -> None:
    host = recordings.load()
    from sightline.llm.selection import select

    selection = select(ModelCard.from_show(name, payload) for name, payload in host.show.items())
    rows = {row["entry"].name: row for row in manifest.rows(selection)}
    assert rows["gemma4:e4b"]["installed"] is True
    assert rows["qwen3:30b"]["installed"] is False
    assert manifest.not_installed(selection)
    assert "gemma4:e4b" not in {entry.name for entry in manifest.not_installed(selection)}


# ---------------------------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------------------------


def _document(**overrides) -> dict:
    entry = {
        "name": "example:1b",
        "role": "review",
        "download_bytes": 1_000_000_000,
        "expected_capabilities": ["completion", "tools"],
        "evidence": "expected",
        "why": "An example.",
    }
    entry.update(overrides)
    return {
        "version": 1,
        "sizes_read_from": "registry.ollama.ai",
        "sizes_read_on": "2026-09-17",
        "models": [entry],
    }


def test_a_valid_document_parses() -> None:
    assert len(parse_manifest(_document())) == 1


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"name": "example"}, "has no tag"),
        ({"role": "everything"}, "role"),
        ({"evidence": "somebody said so"}, "evidence"),
        ({"download_bytes": 0}, "positive"),
        ({"download_bytes": "9 GB"}, "whole number"),
        ({"expected_capabilities": []}, "non-empty list"),
        ({"why": ""}, "'why'"),
    ],
)
def test_a_malformed_entry_names_the_entry_and_the_field(overrides, fragment) -> None:
    with pytest.raises(ManifestError) as caught:
        parse_manifest(_document(**overrides))
    assert "models[0]" in str(caught.value)
    assert fragment in str(caught.value)


def test_a_document_with_no_models_is_refused() -> None:
    with pytest.raises(ManifestError):
        parse_manifest({"version": 1, "sizes_read_from": "x", "models": []})
    with pytest.raises(ManifestError):
        parse_manifest(["not", "a", "mapping"])


def test_the_shipped_file_is_valid_yaml_a_person_can_edit() -> None:
    """Loaded with safe_load, and the comments that explain it survive in the file."""
    text = MANIFEST_PATH.read_text(encoding="utf-8")
    assert yaml.safe_load(text)["version"] == 1
    assert text.lstrip().startswith("#")
