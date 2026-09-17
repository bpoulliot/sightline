"""The one schema every extractor returns, and the parser that refuses anything else.

**The schema is fixed and it is small.** A response is an object with one key, ``findings``, whose
value is a list of objects with exactly ``quote`` and ``location`` and nothing else. There is no
field for a rating, a score, a band, a confidence or a summary, and that is the point: a model
cannot put a verdict in a shape that has nowhere to put one (SPEC §01).

**An empty list is a valid and expected answer.** Most documents do not bear on most criteria, and
abstention has to be as easy to emit as a finding — most miscalibration in a small model comes from
being structurally unwilling to return nothing. So ``{"findings": []}`` parses to a complete,
successful result, and every prompt says so in as many words.

**Violations are rejected, never coerced.** A response carrying a ``rating`` key is not a response
with an extra field to drop; it is a model doing something other than what it was asked, and
quietly deleting the key hides that. Same for a bare list, a string where an object belongs, a
finding missing its location. All of them raise :class:`SchemaViolation`, which the runner records
against the passage as a failure — a failure that reads as a failure and never as "no evidence
found".

**Two transport conventions are unwrapped, and only two.** A response may arrive as the JSON alone
or wrapped in a single fenced code block, because models emit the fence whatever the instruction
says. Nothing else is rescued: no hunting for the first ``{`` in a paragraph of prose, because the
first object in a paragraph of prose is as likely to be part of the reasoning as the answer, and
accepting it is how reasoning becomes evidence.

**The same schema is sent to the host as a structured-output constraint.** Ollama takes a JSON
schema in ``format`` and constrains generation to it, measured working on the target host. That
makes conforming output the common case — and changes nothing here. The host is not trusted to
have honoured it: :func:`parse_response` validates every response it is given, including one from
a host that promised to constrain it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The JSON schema sent to the host in ``format``. Also the documentation of what is accepted:
#: :func:`parse_response` enforces exactly this and is written out rather than generated from it,
#: because a validator generated from the same literal proves only that the literal equals itself.
RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "location": {"type": "string"},
                },
                "required": ["quote", "location"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

#: A fenced block, with or without a language tag. The only wrapper unwrapped before parsing.
_FENCE = re.compile(r"\A\s*```[A-Za-z0-9_+-]*\s*\n(?P<body>.*?)\n?\s*```\s*\Z", re.DOTALL)

#: The longest a quote may be. A model that returns a whole page has stopped quoting and started
#: copying, and a "quote" the length of the document is not evidence a reviewer can check at a
#: glance. Generous enough for a long policy paragraph.
MAX_QUOTE_CHARS = 1500

#: The longest the model's own location string may be. It is never rendered (see
#: :mod:`sightline.extract.base`), so this is only a guard against a paragraph arriving in it.
MAX_LOCATION_CHARS = 200


class SchemaViolation(ValueError):
    """A response did not conform. Carries where it went wrong, in the response's own terms."""

    def __init__(self, reason: str, *, path: str = "response", excerpt: str | None = None) -> None:
        self.reason = reason
        self.path = path
        self.excerpt = excerpt
        super().__init__(f"{path}: {reason}")


@dataclass(frozen=True, slots=True)
class RawFinding:
    """One finding exactly as the model returned it, before anything has been verified.

    Nothing downstream may render either of these. ``quote`` is a claim about the document until
    :mod:`sightline.extract.verify` finds it in the document, and ``location`` is never verified
    at all — see :mod:`sightline.extract.base` for why it is recorded and not shown.
    """

    quote: str
    location: str


def parse_response(content: str) -> tuple[RawFinding, ...]:
    """The findings in one model response, or :class:`SchemaViolation`.

    Returns an empty tuple for ``{"findings": []}``, which is a successful answer and not a
    failure. The caller tells the two apart by whether this raised, never by whether the result
    is empty.
    """
    payload = _load(content)
    if not isinstance(payload, Mapping):
        raise SchemaViolation(
            f"expected an object with a 'findings' key, got {_kind(payload)}",
            excerpt=_excerpt(content),
        )
    extra = sorted(key for key in payload if key != "findings")
    if extra:
        raise SchemaViolation(
            f"carries {', '.join(repr(key) for key in extra)} beside 'findings'. "
            "The schema has no field for anything but located quotes, and dropping a key the "
            "model chose to add would hide that it added one.",
            excerpt=_excerpt(content),
        )
    if "findings" not in payload:
        raise SchemaViolation("has no 'findings' key", excerpt=_excerpt(content))
    findings = payload["findings"]
    if isinstance(findings, (str, bytes)) or not isinstance(findings, Sequence):
        raise SchemaViolation(
            f"'findings' must be a list, got {_kind(findings)}",
            path="response.findings",
            excerpt=_excerpt(content),
        )
    return tuple(_finding(entry, index) for index, entry in enumerate(findings))


def _finding(entry: Any, index: int) -> RawFinding:
    path = f"response.findings[{index}]"
    if not isinstance(entry, Mapping):
        raise SchemaViolation(f"expected an object, got {_kind(entry)}", path=path)
    keys = sorted(entry)
    if keys != ["location", "quote"]:
        missing = [key for key in ("quote", "location") if key not in entry]
        extra = [key for key in keys if key not in ("quote", "location")]
        parts = []
        if missing:
            parts.append(f"is missing {', '.join(repr(key) for key in missing)}")
        if extra:
            parts.append(f"carries {', '.join(repr(key) for key in extra)}")
        raise SchemaViolation(" and ".join(parts), path=path)
    quote = entry["quote"]
    location = entry["location"]
    if not isinstance(quote, str):
        raise SchemaViolation(f"'quote' must be a string, got {_kind(quote)}", path=path)
    if not isinstance(location, str):
        raise SchemaViolation(f"'location' must be a string, got {_kind(location)}", path=path)
    if not quote.strip():
        raise SchemaViolation("'quote' is empty", path=path)
    if len(quote) > MAX_QUOTE_CHARS:
        raise SchemaViolation(
            f"'quote' is {len(quote)} characters, over the {MAX_QUOTE_CHARS}-character limit. "
            "A quote that long is a copy of the document rather than a passage in it.",
            path=path,
        )
    if len(location) > MAX_LOCATION_CHARS:
        raise SchemaViolation(
            f"'location' is {len(location)} characters, over the {MAX_LOCATION_CHARS}-character "
            "limit.",
            path=path,
        )
    return RawFinding(quote=quote, location=location.strip())


def _load(content: str) -> Any:
    text = (content or "").strip()
    if not text:
        raise SchemaViolation("the response was empty")
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group("body").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaViolation(
            f"is not JSON ({exc.msg} at line {exc.lineno} column {exc.colno})",
            excerpt=_excerpt(content),
        ) from exc


def _kind(value: Any) -> str:
    return {
        type(None): "null",
        bool: "a boolean",
        int: "a number",
        float: "a number",
        str: "a string",
        list: "a list",
        dict: "an object",
    }.get(type(value), type(value).__name__)


def _excerpt(content: str, limit: int = 160) -> str:
    text = " ".join((content or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


__all__ = [
    "MAX_LOCATION_CHARS",
    "MAX_QUOTE_CHARS",
    "RESPONSE_SCHEMA",
    "RawFinding",
    "SchemaViolation",
    "parse_response",
]
