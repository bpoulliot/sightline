"""Secret scrubbing, applied to every outbound string surface.

Two things are secret in this project and both are easy to leak by accident:

* the Canvas API token, and
* the Canvas base URL, because it identifies the institution.

Redaction is therefore not a logging nicety. It runs over log events, over rendered error
messages, and over anything else that could reach a terminal, an HTTP response or a file. The
registry below is populated by :class:`sightline.config.Settings` at construction time, so a value
that arrives from the environment is scrubbed without any call site having to remember it exists.
"""

from __future__ import annotations

import re
from threading import Lock

REDACTED = "[redacted]"

# Shapes that are secret regardless of whether anything registered them. These are deliberately
# tight: a pattern that fires on ordinary prose makes logs unreadable and trains people to ignore
# the redaction, which is worse than not having it.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Canvas personal access tokens: a short numeric prefix, a tilde, then a long alphanumeric body.
    re.compile(r"\b\d{3,6}~[A-Za-z0-9]{20,}\b"),
    # Authorization headers, however they are spelled.
    re.compile(r"(?i)\b(bearer|token)\s+[A-Za-z0-9\-._~+/]{16,}=*"),
    # Query-string credentials. ``verifier`` is Canvas's: a file or content-export download link
    # carries one, and it is a bearer credential written into a URL — anyone holding the link can
    # fetch the file. ``X-Amz-Signature`` is the same thing on the storage back end Canvas
    # redirects to, which nothing registers because nothing configures it.
    re.compile(
        r"(?i)\b(access_token|api_key|apikey|token|password|verifier|x-amz-signature)=[^\s&\"']+"
    ),
    # OpenAI-compatible keys.
    re.compile(r"\bsk-[A-Za-z0-9\-_]{16,}\b"),
    # The origin of any absolute URL. The registry covers the configured Canvas host, and it
    # cannot cover the storage back end Canvas redirects a download to: that host differs per
    # deployment, nothing configures it, and the HTTP client logs it by name. A pattern reaches
    # it where registration cannot. The path survives the substitution, so a log line still says
    # what was fetched — only where from is removed, which is the part that identifies an
    # institution.
    re.compile(r"(?i)\bhttps?://[^/\s\"'<>]+"),
)

_registry: set[str] = set()
_registry_lock = Lock()

# Registering a short string would blank out ordinary words, so there is a floor.
_MIN_REGISTERED_LENGTH = 6


def register_secret(value: str | None) -> None:
    """Add a literal value to the scrub registry.

    Short or empty values are ignored: redacting a three-character string would corrupt unrelated
    text and hide nothing useful.
    """
    if not value:
        return
    text = str(value).strip()
    if len(text) < _MIN_REGISTERED_LENGTH:
        return
    with _registry_lock:
        _registry.add(text)


def registered_secrets() -> frozenset[str]:
    """The current registry. Exposed for tests; callers should not need it."""
    with _registry_lock:
        return frozenset(_registry)


def clear_registry() -> None:
    """Empty the registry. Used by tests to keep cases independent."""
    with _registry_lock:
        _registry.clear()


def scrub(text: str) -> str:
    """Return ``text`` with every registered secret and secret-shaped run replaced."""
    if not text:
        return text
    result = text
    # Longest first, so a registered base URL is replaced before its own hostname substring.
    for secret in sorted(registered_secrets(), key=len, reverse=True):
        if secret in result:
            result = result.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        result = pattern.sub(REDACTED, result)
    return result


def scrub_object(value: object) -> object:
    """Recursively scrub strings inside dicts, lists, tuples and sets."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {key: scrub_object(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_object(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_object(item) for item in value)
    if isinstance(value, set):
        return {scrub_object(item) for item in value}
    return value
