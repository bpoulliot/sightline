"""Which installed models can do this work, and the statement of why.

**The obvious filter is the wrong one.** "Exclude anything with vision" removes the best model on
the target host: ``/api/show`` reports ``gemma4:e4b`` as
``['completion', 'vision', 'audio', 'tools', 'thinking']`` — it is multimodal, not a vision model.
So the selection is on what the work needs rather than on what a model happens to also be able to
do (SPEC §06):

> **Require ``completion`` and ``tools``; exclude ``embedding``.**

Measured against the target host on 2026-09-17, that rule admits ``gemma4:e4b``,
``llama3.1:8b``, ``llama3.2:3b`` and ``qwen2.5-coder:14b``; it rejects ``llava:13b`` and
``llama3.2-vision:11b`` for having no ``tools``, rejects ``deepseek-r1:8b`` for the same reason
even though it reports ``thinking``, and rejects ``nomic-embed-text`` for being an embedding
model — which leaves that model installed and available for background embedding use, exactly as
intended. Those eight payloads are recorded in ``tests/fixtures/ollama/``.

**A rejection is a statement, not a flag.** Every card renders ``verdict``: what the model
reported and which clause of the rule decided it. A screen that says "unusable" without saying
what it examined is the kind of output the governing design rule of this project exists to
prevent — the reader has to be able to falsify it, and the capability list is the evidence.

Nothing here talks to the host. :mod:`sightline.llm.client` fetches ``/api/show`` and builds these;
this module is pure, which is why the filter is tested against recorded payloads rather than
against whatever happens to be installed on a developer's machine today.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Both are required. ``completion`` because the work is generation, ``tools`` because the
#: extraction layer (item 10) constrains output through a tool schema rather than by asking for
#: JSON and hoping.
REQUIRED_CAPABILITIES: tuple[str, ...] = ("completion", "tools")

#: An embedding model cannot generate. It is excluded from *this* work and deliberately left
#: installed and reachable: ``nomic-embed-text`` is here to be used, just not for review text.
EXCLUDED_CAPABILITIES: tuple[str, ...] = ("embedding",)

#: Capability names Ollama reports that this rule neither requires nor excludes. Listed so the
#: UI can say "also reports vision, audio" as a fact rather than dropping it silently, and so a
#: capability nobody here has heard of is rendered rather than swallowed.
KNOWN_CAPABILITIES: tuple[str, ...] = (
    "completion",
    "tools",
    "thinking",
    "vision",
    "audio",
    "insert",
    "embedding",
)


@dataclass(frozen=True)
class ModelCard:
    """One installed model, as ``/api/tags`` and ``/api/show`` together describe it.

    ``capabilities`` is the only field the filter reads. The rest is what the picker renders, so
    a person choosing between two names is choosing between two described things.
    """

    name: str
    capabilities: tuple[str, ...] = ()
    family: str | None = None
    parameter_size: str | None = None
    quantization: str | None = None
    #: Bytes on the host's disk, from ``/api/tags``. ``None`` when the card was built from
    #: ``/api/show`` alone.
    size_bytes: int | None = None
    #: The model's *declared* context window, which is not the window a request gets — see
    #: :data:`sightline.llm.client.DEFAULT_CONTEXT_TOKENS` for why that difference matters here.
    context_length: int | None = None
    architecture: str | None = None
    #: Minimum Ollama version the model declares, when it declares one. ``gemma4:e4b`` requires
    #: 0.20.0, which is the only reason a correctly-named model can be installed and unloadable.
    requires: str | None = None

    # -- the rule ----------------------------------------------------------------------------

    @property
    def missing(self) -> tuple[str, ...]:
        """Required capabilities this model does not report."""
        return tuple(name for name in REQUIRED_CAPABILITIES if name not in self.capabilities)

    @property
    def excluded_by(self) -> tuple[str, ...]:
        """Excluded capabilities this model does report."""
        return tuple(name for name in EXCLUDED_CAPABILITIES if name in self.capabilities)

    @property
    def usable(self) -> bool:
        """Whether this model can do the review work."""
        return not self.missing and not self.excluded_by

    @property
    def verdict(self) -> str:
        """Why, in one sentence naming what was examined.

        Phrased as a computed statement over the reported capability list, never as a rating of
        the model: a model that is not admitted here is not a worse model, it is a model that
        reports a different set.
        """
        reported = ", ".join(self.capabilities) if self.capabilities else "no capabilities"
        if self.excluded_by:
            return (
                f"Not selected for review work: reports {reported}. "
                f"{_and_list(self.excluded_by)} is excluded — an embedding model cannot generate."
            )
        if self.missing:
            return (
                f"Not selected for review work: reports {reported}. "
                f"Review work requires {_and_list(REQUIRED_CAPABILITIES)}, and this model does "
                f"not report {_and_list(self.missing)}."
            )
        return (
            f"Selected for review work: reports {reported}, which covers the required "
            f"{_and_list(REQUIRED_CAPABILITIES)}."
        )

    # -- described facts ---------------------------------------------------------------------

    @property
    def supports_thinking(self) -> bool:
        """Whether ``think`` may be pinned on for this model at all."""
        return "thinking" in self.capabilities

    @property
    def is_embedding(self) -> bool:
        return "embedding" in self.capabilities

    @property
    def extra_capabilities(self) -> tuple[str, ...]:
        """What it reports beyond the required set — rendered, never used to decide."""
        return tuple(name for name in self.capabilities if name not in REQUIRED_CAPABILITIES)

    @property
    def unknown_capabilities(self) -> tuple[str, ...]:
        """Capabilities this code has never heard of. Rendered rather than swallowed."""
        return tuple(name for name in self.capabilities if name not in KNOWN_CAPABILITIES)

    @property
    def size_label(self) -> str:
        if self.size_bytes is None:
            return "size unknown"
        return f"{self.size_bytes / 1_000_000_000:.1f} GB"

    @property
    def label(self) -> str:
        """The one-line description the picker shows next to the name."""
        parts = [part for part in (self.parameter_size, self.quantization) if part]
        parts.append(self.size_label)
        if self.context_length:
            parts.append(f"{self.context_length:,} token context")
        return ", ".join(parts)

    # -- construction ------------------------------------------------------------------------

    @classmethod
    def from_show(
        cls,
        name: str,
        payload: Mapping[str, Any],
        *,
        size_bytes: int | None = None,
    ) -> ModelCard:
        """Build a card from an ``/api/show`` payload.

        Tolerant on shape and strict on meaning: a payload with no ``capabilities`` key produces
        a card with none, which the filter then rejects with a verdict saying it reported none.
        An older Ollama that does not report capabilities at all is therefore visible as that,
        rather than being quietly admitted or quietly dropped.
        """
        details = payload.get("details")
        details = details if isinstance(details, Mapping) else {}
        info = payload.get("model_info")
        info = info if isinstance(info, Mapping) else {}
        architecture = _string(info.get("general.architecture")) or _string(details.get("family"))
        return cls(
            name=name,
            capabilities=_capabilities(payload.get("capabilities")),
            family=_string(details.get("family")),
            parameter_size=_string(details.get("parameter_size")),
            quantization=_string(details.get("quantization_level")),
            size_bytes=size_bytes,
            context_length=_context_length(info, architecture),
            architecture=architecture,
            requires=_string(payload.get("requires")),
        )


@dataclass(frozen=True)
class Selection:
    """The installed models split by the rule, with the rule stated alongside them."""

    usable: tuple[ModelCard, ...] = ()
    rejected: tuple[ModelCard, ...] = ()

    @property
    def all(self) -> tuple[ModelCard, ...]:
        return self.usable + self.rejected

    @property
    def embedding(self) -> tuple[ModelCard, ...]:
        """Installed embedding models. Rejected for review work and available for embedding."""
        return tuple(card for card in self.rejected if card.is_embedding)

    def get(self, name: str) -> ModelCard | None:
        for card in self.all:
            if card.name == name:
                return card
        return None

    def summary(self) -> str:
        """Counts, in the declarative register of SPEC §08 tier A."""
        installed = len(self.all)
        return (
            f"{len(self.usable)} of {installed} installed "
            f"model{'' if installed == 1 else 's'} can do review work."
        )

    @staticmethod
    def rule() -> str:
        """The filter, in one line, rendered next to the lists it produced."""
        return (
            f"A model is selected when it reports {_and_list(REQUIRED_CAPABILITIES)} and does not "
            f"report {_and_list(EXCLUDED_CAPABILITIES)}. Vision is not a disqualifier: a "
            "multimodal model reports vision alongside the capabilities this work needs."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule(),
            "summary": self.summary(),
            "usable": [_card_dict(card) for card in self.usable],
            "rejected": [_card_dict(card) for card in self.rejected],
        }


def select(cards: Iterable[ModelCard]) -> Selection:
    """Split cards into what can do the work and what cannot, each in the order given."""
    usable: list[ModelCard] = []
    rejected: list[ModelCard] = []
    for card in cards:
        (usable if card.usable else rejected).append(card)
    return Selection(usable=tuple(usable), rejected=tuple(rejected))


def _card_dict(card: ModelCard) -> dict[str, Any]:
    return {
        "name": card.name,
        "capabilities": list(card.capabilities),
        "usable": card.usable,
        "verdict": card.verdict,
        "label": card.label,
        "supports_thinking": card.supports_thinking,
        "context_length": card.context_length,
        "size_bytes": card.size_bytes,
    }


def _capabilities(value: Any) -> tuple[str, ...]:
    """Reported capabilities, normalized and deduplicated, in the order reported."""
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        name = item.strip().lower()
        if name and name not in seen:
            seen.append(name)
    return tuple(seen)


def _context_length(info: Mapping[str, Any], architecture: str | None) -> int | None:
    """The declared context window.

    Ollama keys ``model_info`` by architecture — ``gemma4.context_length``,
    ``llama.context_length``, ``nomic-bert.context_length`` — so there is no fixed key to read.
    The architecture comes from ``general.architecture``, and the fallback scan exists because a
    model whose ``general.architecture`` is missing still has exactly one such key.
    """
    if architecture:
        value = info.get(f"{architecture}.context_length")
        if isinstance(value, int):
            return value
    for key, value in info.items():
        if key.endswith(".context_length") and isinstance(value, int):
            return value
    return None


def _string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _and_list(names: Sequence[str]) -> str:
    """``a``, ``a and b``, ``a, b and c`` — this text is read by people, not parsed."""
    items = [f"'{name}'" for name in names]
    if not items:
        return "nothing"
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


__all__ = [
    "EXCLUDED_CAPABILITIES",
    "KNOWN_CAPABILITIES",
    "REQUIRED_CAPABILITIES",
    "ModelCard",
    "Selection",
    "select",
]
