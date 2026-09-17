"""Model-host failures, each carrying the next move.

The same rule as :mod:`sightline.canvas.errors`: "Error" alone is not acceptable output. Every
failure here carries a ``summary``, a ``remedy`` naming the specific thing to change, and a
``detail`` that is checkable — the status code, the host that answered, the endpoint that was
called.

One difference from the Canvas errors is worth stating. The Ollama endpoint is **not** treated as
a secret. The Canvas base URL is, because it identifies the institution; a model host is a machine
on somebody's own network, and :func:`sightline.logging.model_call` logs its host deliberately so
that a silent model failure is visible in the log rather than only on a screen nobody was
watching. That decision is already made in :mod:`sightline.config`, which registers the Canvas
base URL with the redaction registry and does not register this one.

Three of these map failures that were **measured against the target host** rather than guessed,
and each remedy names what the measurement showed:

``ModelHostOutOfMemoryError``
    Ollama 0.20.6 sizes a request from the model's *declared* context length unless the request
    pins one. On the target host a 3 B model was refused for 52.2 GiB against 48.7 GiB available.
    The remedy is the context pin, not a smaller model.
``ModelThinkingUnsupportedError``
    ``think: true`` against a model with no ``thinking`` capability is a 400, not a silently
    ignored field.
``ModelNotFoundError``
    Both ``/api/show`` and ``/api/chat`` answer 404 ``model '<name>' not found`` for a name that
    is not installed, which is the same shape as a typo.
"""

from __future__ import annotations


class ModelHostError(Exception):
    """Base for every failure this package raises."""

    #: Overridden by subclasses that map to one obvious fix.
    default_remedy = "Check the model host endpoint on the Settings screen."

    def __init__(
        self,
        summary: str,
        *,
        remedy: str | None = None,
        detail: str | None = None,
        status_code: int | None = None,
        endpoint: str | None = None,
    ) -> None:
        super().__init__(summary)
        self.summary = summary
        self.remedy = remedy or self.default_remedy
        self.detail = detail
        self.status_code = status_code
        self.endpoint = endpoint

    def __str__(self) -> str:
        return " ".join(part for part in (self.summary, self.detail, self.remedy) if part)

    def as_dict(self) -> dict[str, object]:
        """The fields a UI renders, so a template need not parse prose out of ``str``."""
        return {
            "summary": self.summary,
            "detail": self.detail,
            "remedy": self.remedy,
            "status_code": self.status_code,
            "endpoint": self.endpoint,
            "kind": type(self).__name__,
        }


class ModelHostNotConfiguredError(ModelHostError):
    """No endpoint. Raised before any request is attempted."""

    default_remedy = (
        "Enter the model host endpoint on the Settings screen, or set OLLAMA_BASE_URL in the "
        "environment. It is the root of an Ollama server, for example "
        "http://model-host.example.lan:11434."
    )


class ModelHostUnreachableError(ModelHostError):
    """Nothing answered: DNS, TCP, TLS or a timeout."""

    default_remedy = (
        "Nothing answered at that address. Check the hostname and port for a typo, check that "
        "Ollama is running on that machine, and check that this machine can reach it — Ollama "
        "binds to 127.0.0.1 by default, so a host on the network needs OLLAMA_HOST=0.0.0.0 set "
        "on the server before anything else can connect."
    )


class ModelHostNotOllamaError(ModelHostError):
    """Something answered, but it was not an Ollama server.

    The usual cause is an endpoint pointing at a reverse proxy, a web UI in front of Ollama, or
    the wrong port entirely.
    """

    default_remedy = (
        "The host answered but did not return Ollama JSON. Check that the endpoint is the root of "
        "the Ollama server itself and not a web UI in front of it, and that the port is Ollama's "
        "own (11434 unless it was changed)."
    )


class ModelNotFoundError(ModelHostError):
    """The host has no model by that name."""

    default_remedy = (
        "The host has no model by that name. Check the spelling including the tag — 'llama3.1' "
        "and 'llama3.1:8b' are different names to Ollama — or install it from the recommended "
        "models on the Settings screen."
    )


class ModelHostOutOfMemoryError(ModelHostError):
    """The host refused the request for want of memory, before generating anything."""

    default_remedy = (
        "The host could not fit this model in memory at the requested context length. Ollama "
        "sizes the allocation from the context window, and a model that declares a 128k window "
        "asks for far more memory than the weights alone: lower the context window, or choose a "
        "smaller model. The size in the message is what was asked for, not what the weights "
        "weigh."
    )


class ModelThinkingUnsupportedError(ModelHostError):
    """Thinking was pinned on for a model that has no ``thinking`` capability."""

    default_remedy = (
        "This model does not support thinking, and Sightline pins the setting explicitly rather "
        "than letting a model default decide it. Either choose a model whose capabilities include "
        "'thinking', or run this call with thinking off."
    )


class ModelHostServerError(ModelHostError):
    """A 5xx from Ollama that is not one of the mapped causes above."""

    default_remedy = (
        "The model host returned a server error, which nothing in Sightline's configuration "
        "changes. Check the Ollama server's own log on that machine — it records why a request "
        "was refused in more detail than the HTTP response carries."
    )


class ModelResponseError(ModelHostError):
    """Ollama returned JSON that did not have the shape this client requires."""

    default_remedy = (
        "The model host answered with JSON of an unexpected shape. This is a Sightline bug or an "
        "Ollama version difference; the endpoint and status are recorded above."
    )


class ModelNotUsableError(ModelHostError):
    """The named model is installed, and its capabilities do not admit it to this work."""

    default_remedy = (
        "Sightline selects models by capability: a model must report 'completion' and 'tools', "
        "and must not report 'embedding'. Choose a model from the usable list on the Settings "
        "screen."
    )


__all__ = [
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
]
