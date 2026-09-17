"""The local-model layer: an Ollama client, a capability filter, and a curated install list.

Named ``llm`` rather than ``model`` or ``models`` because both of those are taken and mean
something else here — :mod:`sightline.model` is the normalized *document* model every detector
reads, and :mod:`sightline.models` is the run store's schema. A third meaning of the word in one
package would be a trap for whoever reads an import line next.

Start with :mod:`sightline.llm.selection` for the capability rule and why the obvious version of
it is wrong, and :mod:`sightline.llm.client` for the three behaviours of the target host that were
measured rather than assumed.
"""

from __future__ import annotations

from .client import (
    DEFAULT_CONTEXT_TOKENS,
    ChatResult,
    HostCheck,
    HostReport,
    OllamaClient,
    PullProgress,
    Thinking,
    host_of,
    normalize_endpoint,
)
from .errors import (
    ModelHostError,
    ModelHostNotConfiguredError,
    ModelHostNotOllamaError,
    ModelHostOutOfMemoryError,
    ModelHostServerError,
    ModelHostUnreachableError,
    ModelNotFoundError,
    ModelNotUsableError,
    ModelResponseError,
    ModelThinkingUnsupportedError,
)
from .manifest import Manifest, ManifestError, Recommendation, load_manifest
from .pulls import PullRegistry, PullState
from .selection import (
    EXCLUDED_CAPABILITIES,
    REQUIRED_CAPABILITIES,
    ModelCard,
    Selection,
    select,
)

__all__ = [
    "DEFAULT_CONTEXT_TOKENS",
    "EXCLUDED_CAPABILITIES",
    "REQUIRED_CAPABILITIES",
    "ChatResult",
    "HostCheck",
    "HostReport",
    "Manifest",
    "ManifestError",
    "ModelCard",
    "ModelHostError",
    "ModelHostNotConfiguredError",
    "ModelHostNotOllamaError",
    "ModelHostOutOfMemoryError",
    "ModelHostServerError",
    "ModelHostUnreachableError",
    "ModelNotFoundError",
    "ModelNotUsableError",
    "ModelResponseError",
    "ModelThinkingUnsupportedError",
    "OllamaClient",
    "PullProgress",
    "PullRegistry",
    "PullState",
    "Recommendation",
    "Selection",
    "Thinking",
    "host_of",
    "load_manifest",
    "normalize_endpoint",
    "select",
]
