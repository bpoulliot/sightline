"""Secret scrubbing, applied to every outbound string surface.

Two things are secret in this project and both are easy to leak by accident:

* the Canvas API token, and
* the Canvas base URL, because it identifies the institution.

Sign-in mode (roadmap item 44) adds two more: the shared secret the auth proxy sends, registered
by value like the token, and the identity headers it forwards, registered by **name** with
:func:`register_header` — a header line or a header mapping keeps the name and loses the value,
because the value is a person's identity and differs on every request.

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
_header_patterns: dict[str, re.Pattern[str]] = {}
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


def register_header(name: str | None) -> None:
    """Scrub the value of the header ``name`` wherever it is written beside its name.

    Covers the shapes a header reaches a string in: ``Name: value`` (a raw header line),
    ``'name': 'value'`` and ``"name": "value"`` (a mapping's repr or JSON), ``(b'name', b'value')``
    (a request scope's repr) and ``name=value``.
    Case-insensitive, as header names are. The name survives, so a line still says *which* header
    was there.
    """
    if not name or not name.strip():
        return
    key = name.strip().lower()
    quoted = re.escape(key)
    pattern = re.compile(
        # The name, bare (``Name: v``, ``name=v``) or quoted, bytes included — a request scope
        # holds its headers as ``(b'name', b'value')`` pairs, so a quoted name may also be
        # followed by a comma.
        rf"(?i)(?P<head>b?(?P<q>[\"']){quoted}(?P=q)\s*[:=,]\s*|\b{quoted}\s*[:=]\s*)"
        # The value: a quoted string with its escapes, or a bare run up to a delimiter.
        rf"(?:b?(?P<vq>[\"'])(?:\\.|(?!(?P=vq)).)*(?P=vq)|[^\s,;&}}\]\)]+)"
    )
    with _registry_lock:
        _header_patterns[key] = pattern


def registered_secrets() -> frozenset[str]:
    """The current registry. Exposed for tests; callers should not need it."""
    with _registry_lock:
        return frozenset(_registry)


def clear_registry() -> None:
    """Empty the registry. Used by tests to keep cases independent."""
    with _registry_lock:
        _registry.clear()
        _header_patterns.clear()


def scrub(text: str) -> str:
    """Return ``text`` with every registered secret and secret-shaped run replaced."""
    if not text:
        return text
    result = text
    # Longest first, so a registered base URL is replaced before its own hostname substring.
    for secret in sorted(registered_secrets(), key=len, reverse=True):
        if secret in result:
            result = result.replace(secret, REDACTED)
    with _registry_lock:
        headers = tuple(_header_patterns.values())
    for header in headers:
        result = header.sub(lambda match: match.group("head") + REDACTED, result)
    for pattern in _PATTERNS:
        result = pattern.sub(REDACTED, result)
    return result


def _is_registered_header(key: object) -> bool:
    """Whether a mapping key names a header registered with :func:`register_header`."""
    if isinstance(key, bytes):
        key = key.decode("latin-1", "replace")
    if not isinstance(key, str):
        return False
    with _registry_lock:
        return key.strip().lower() in _header_patterns


def scrub_object(value: object) -> object:
    """Recursively scrub strings inside dicts, lists, tuples and sets.

    A mapping entry whose key is a registered header name loses its whole value, whatever its
    shape — a structured log field ``headers={...}`` puts the name and the value in separate
    strings, where :func:`register_header`'s pattern cannot see them together.
    """
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {
            key: REDACTED if _is_registered_header(key) else scrub_object(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub_object(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_object(item) for item in value)
    if isinstance(value, set):
        return {scrub_object(item) for item in value}
    return value
