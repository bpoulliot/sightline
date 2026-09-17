"""The curated manifest of models worth installing, and what it is allowed to claim.

Ollama has no library-search endpoint, so "what could I install?" cannot be answered by asking the
host. It is answered by :mod:`sightline.llm.recommended` — ``recommended.yaml`` next to this
module — which is a short hand-written list rather than a browser over every published model
(SPEC §06).

**The manifest never decides whether a model is usable.** That is
:mod:`sightline.llm.selection`'s job and it reads ``/api/show`` and nothing else. What an entry
carries is an *expectation*, and :meth:`Recommendation.confirmation` states the difference between
the expectation and what the host actually reported once the model is installed. An entry that
turns out to be wrong is therefore visible as a stated difference on the settings screen, which is
the checkable-artifact rule applied to our own data file: a claim a reader can falsify in seconds.

The file's own header carries the provenance of every number in it, including the probe of the
community catalogue that SPEC §06 prescribes as the freshness test. It is worth reading before
adding an entry.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .selection import ModelCard, Selection

#: Ships inside the package, so an installed wheel has it and no data directory is involved.
MANIFEST_PATH = Path(__file__).resolve().parent / "recommended.yaml"

#: ``api-show`` — the capability list was read from a running host and is recorded in
#: ``tests/fixtures/ollama/``. ``expected`` — nobody here has run it, and every surface says so.
EVIDENCE_VALUES = ("api-show", "expected")

ROLES = ("review", "embedding")


class ManifestError(ValueError):
    """The manifest file is not the shape this module requires."""


@dataclass(frozen=True)
class Recommendation:
    """One model worth installing, and the provenance of everything claimed about it."""

    name: str
    role: str
    download_bytes: int
    expected_capabilities: tuple[str, ...]
    evidence: str
    why: str

    @property
    def verified(self) -> bool:
        """Whether the capability list was read from a host rather than expected."""
        return self.evidence == "api-show"

    @property
    def size_label(self) -> str:
        return f"{self.download_bytes / 1_000_000_000:.1f} GB"

    @property
    def evidence_note(self) -> str:
        """What is known about this entry's capabilities, and how."""
        if self.verified:
            return (
                "Capabilities read from /api/show on a running host and recorded in the test "
                "suite. Sightline still reads them again after installing."
            )
        return (
            "Capabilities are expected, not measured here. Sightline reads them from /api/show "
            "after installing and reports any difference."
        )

    @property
    def expected_usable(self) -> bool:
        """Whether this entry is *expected* to pass the filter. Never used to decide anything."""
        return ModelCard(name=self.name, capabilities=self.expected_capabilities).usable

    def confirmation(self, card: ModelCard | None, *, checked: bool = True) -> str:
        """The expectation against what the host reported, as a statement of the difference.

        ``checked`` is the difference between *the host says this is not installed* and *nobody
        has asked the host yet*. Collapsing those two into one sentence would put a claim about
        somebody's machine on the screen that nothing had verified.
        """
        expected = ", ".join(self.expected_capabilities) or "nothing"
        if not checked:
            return f"The model host has not been checked. Expected to report {expected}."
        if card is None:
            return f"Not installed. Expected to report {expected}."
        actual = ", ".join(card.capabilities) or "nothing"
        if tuple(card.capabilities) == self.expected_capabilities:
            return f"Installed. Reports {actual}, which is what this entry expected."
        missing = [name for name in self.expected_capabilities if name not in card.capabilities]
        extra = [name for name in card.capabilities if name not in self.expected_capabilities]
        differences = []
        if missing:
            differences.append(f"does not report {', '.join(missing)}")
        if extra:
            differences.append(f"also reports {', '.join(extra)}")
        return (
            f"Installed. Reports {actual}; this entry expected {expected} — it "
            f"{' and '.join(differences)}."
        )


@dataclass(frozen=True)
class Manifest:
    """The whole list, with the provenance of its numbers."""

    version: int
    sizes_read_from: str
    sizes_read_on: str
    models: tuple[Recommendation, ...]

    def __len__(self) -> int:
        return len(self.models)

    def __iter__(self) -> Any:
        return iter(self.models)

    def get(self, name: str) -> Recommendation | None:
        for entry in self.models:
            if entry.name == name:
                return entry
        return None

    @property
    def for_review(self) -> tuple[Recommendation, ...]:
        return tuple(entry for entry in self.models if entry.role == "review")

    @property
    def for_embedding(self) -> tuple[Recommendation, ...]:
        return tuple(entry for entry in self.models if entry.role == "embedding")

    def not_installed(self, selection: Selection | None) -> tuple[Recommendation, ...]:
        """Entries the host does not already have, in manifest order."""
        installed = {card.name for card in selection.all} if selection else set()
        return tuple(entry for entry in self.models if entry.name not in installed)

    def rows(self, selection: Selection | None) -> list[dict[str, Any]]:
        """What the settings screen renders: each entry beside what the host reported for it.

        ``selection`` of ``None`` means the host has not been checked this page load, which is
        rendered as that rather than as "not installed".
        """
        checked = selection is not None
        cards = {card.name: card for card in (selection.all if selection else ())}
        return [
            {
                "entry": entry,
                "card": cards.get(entry.name),
                "installed": entry.name in cards if checked else None,
                "checked": checked,
                "confirmation": entry.confirmation(cards.get(entry.name), checked=checked),
            }
            for entry in self.models
        ]

    @property
    def provenance(self) -> str:
        return (
            f"Download sizes read from {self.sizes_read_from} on {self.sizes_read_on} by summing "
            "each model's manifest layers. The download is not the memory it needs to run."
        )


def parse_manifest(document: Any) -> Manifest:
    """Validate and build. Every rejection names the entry and the field."""
    if not isinstance(document, Mapping):
        raise ManifestError("The manifest must be a mapping at the top level.")
    raw = document.get("models")
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes) or not raw:
        raise ManifestError("The manifest must carry a non-empty 'models' list.")
    return Manifest(
        version=_int_field(document, "version"),
        sizes_read_from=_text_field(document, "sizes_read_from"),
        sizes_read_on=str(document.get("sizes_read_on") or ""),
        models=tuple(_entry(item, position) for position, item in enumerate(raw)),
    )


@lru_cache(maxsize=1)
def load_manifest(path: Path | None = None) -> Manifest:
    """The shipped manifest. Cached: it is a committed file, not state."""
    target = path or MANIFEST_PATH
    try:
        document = yaml.safe_load(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError(f"Could not read the model manifest at {target}.") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"The model manifest at {target} is not valid YAML: {exc}") from exc
    return parse_manifest(document)


def _entry(item: Any, position: int) -> Recommendation:
    where = f"models[{position}]"
    if not isinstance(item, Mapping):
        raise ManifestError(f"{where} is not a mapping.")
    name = _text_field(item, "name", where=where)
    if ":" not in name:
        # 'llama3.1' and 'llama3.1:8b' are different names to Ollama, and the untagged form
        # resolves to whatever the registry calls latest — which is not a thing to install on
        # somebody's behalf without saying which one.
        raise ManifestError(f"{where}: '{name}' has no tag. Every entry names an exact tag.")
    role = _text_field(item, "role", where=where)
    if role not in ROLES:
        raise ManifestError(f"{where}: role '{role}' is not one of {', '.join(ROLES)}.")
    evidence = _text_field(item, "evidence", where=where)
    if evidence not in EVIDENCE_VALUES:
        raise ManifestError(
            f"{where}: evidence '{evidence}' is not one of {', '.join(EVIDENCE_VALUES)}."
        )
    size = _int_field(item, "download_bytes", where=where)
    if size <= 0:
        raise ManifestError(f"{where}: download_bytes must be a positive number of bytes.")
    return Recommendation(
        name=name,
        role=role,
        download_bytes=size,
        expected_capabilities=_capabilities(item.get("expected_capabilities"), where),
        evidence=evidence,
        why=_text_field(item, "why", where=where),
    )


def _capabilities(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes) or not value:
        raise ManifestError(f"{where}: expected_capabilities must be a non-empty list.")
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ManifestError(f"{where}: every expected capability must be a name.")
        names.append(item.strip().lower())
    return tuple(names)


def _text_field(source: Mapping[str, Any], key: str, *, where: str = "the manifest") -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{where}: '{key}' must be a non-empty string.")
    return value.strip()


def _int_field(source: Mapping[str, Any], key: str, *, where: str = "the manifest") -> int:
    value = source.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ManifestError(f"{where}: '{key}' must be a whole number.")
    return value


def installed_names(cards: Iterable[ModelCard]) -> set[str]:
    return {card.name for card in cards}


__all__ = [
    "EVIDENCE_VALUES",
    "MANIFEST_PATH",
    "Manifest",
    "ManifestError",
    "Recommendation",
    "load_manifest",
    "parse_manifest",
]
