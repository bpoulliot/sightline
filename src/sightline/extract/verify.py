r"""Quote verification: the mechanical check that stands between a model and a fabricated quote.

SPEC §11 names this the failure that would destroy trust fastest, and it says why: a fabricated
quote looks exactly like the evidence the whole design depends on. A reviewer who checks one
finding, cannot find the sentence on the page, and concludes the tool makes things up has drawn the
correct conclusion from one data point. So the mitigation is not a prompt and not a temperature —
it is this module, and every quote passes through it before anything renders it.

**The rule.** A returned quote is accepted only if it is a contiguous substring of the document the
model was shown, after normalization. Not found means discarded and logged. There is no third
outcome and no confidence threshold.

**What is normalized, and why each one is not a loosening.** Each of these folds two spellings of
the *same characters* together. None of them can turn one word into a different word, which is the
property that matters and the one the tests assert from both directions.

``&amp;`` → ``&``
    Entity decoding. The document the model saw was decoded; a model that re-encodes on the way
    out is describing the same characters.
NFKC
    A ligature, a full-width letter and a compatibility form are the same letters typed twice over.
``’ ‘ “ ”`` → ``' "``, ``– — −`` → ``-``, ``…`` → ``...``
    Typographic folding. Word processors and Canvas's editor substitute these on the way in, and
    models substitute them back on the way out. ``don’t`` and ``don't`` are one word.
whitespace runs → one space
    The passage a model is shown is wrapped; the quote it returns is not. Line breaks in the
    middle of a sentence are the layout, not the words.
case
    Models re-capitalise the first word of a sentence they quote from mid-sentence. Folding case
    for the *match* costs nothing, because of the next paragraph.

**The accepted quote is the document's text, never the model's string.** Matching happens in
normalized space; what is kept is the source span the match landed on, sliced out of the document
exactly as it was written. So a model that straightened a curly apostrophe or re-cased a word does
not get its version rendered — the reviewer sees what the course says, character for character, and
can search the page for it. This is what makes the normalizations above safe to have at all.

**Two things are deliberately NOT rescued.**

*Ellipsis.* ``Submit your draft … by Friday`` is a model splicing two passages that may sit
paragraphs apart, and the spliced sentence can assert something neither half does. A quote
containing an ellipsis is rejected as any other non-matching quote is.

*Anything approximate.* No edit distance, no token overlap, no longest-common-substring. A near
miss is the most dangerous case there is, because the word that changed is the word that carries
the meaning — ``three assignments`` for ``four assignments``, ``may`` for ``must``. Fuzzy matching
would accept exactly those.
"""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass

#: Entity references, with the semicolon required. A bare ``&amp`` is ambiguous and left alone.
_ENTITY = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")

#: Typographic variants folded to their ASCII spelling. Same characters, different keyboard.
_FOLD: dict[str, str] = {
    "‘": "'",  # left single quotation mark
    "’": "'",  # right single quotation mark, and the apostrophe every editor substitutes
    "‚": "'",
    "‛": "'",
    "′": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "″": '"',
    "‐": "-",
    "‑": "-",
    "‒": "-",
    "–": "-",  # en dash
    "—": "-",  # em dash
    "―": "-",
    "−": "-",  # minus sign
    "…": "...",  # ellipsis, folded so that the rejection below sees one spelling
    " ": " ",  # no-break space
    " ": " ",
    " ": " ",
    "​": "",  # zero-width space
    "‌": "",
    "‍": "",
    "﻿": "",
}

#: Matching pairs a model wraps a quote in. Unwrapped once, and only when the wrapped form does not
#: match — a document whose own sentence carries the marks still matches first.
_WRAPPERS = (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’"))

_WHITESPACE = re.compile(r"\s")


@dataclass(frozen=True, slots=True)
class Normalized:
    """Normalized text, with every character mapped back to the span it came from."""

    text: str
    #: Source index each normalized character starts at.
    starts: tuple[int, ...]
    #: Source index each normalized character ends at, exclusive. Differs from ``start + 1`` where
    #: one normalized character came from an entity or a run of whitespace.
    ends: tuple[int, ...]

    def span(self, start: int, end: int) -> tuple[int, int]:
        """The source span a normalized ``[start, end)`` slice came from."""
        if start >= end:
            raise ValueError("an empty span has no source text")
        return self.starts[start], self.ends[end - 1]


@dataclass(frozen=True, slots=True)
class Match:
    """Where a verified quote sits in the source, and what the source actually says there."""

    #: Source text at the match, exactly as written. **This is what gets rendered.**
    text: str
    start: int
    end: int
    #: How many places in the document the quote matches. More than one is not an error; it means
    #: a reviewer searching the page will find several, and a later item may want to say so.
    occurrences: int = 1
    #: True when the quote matched only after a wrapping pair of quotation marks was removed.
    unwrapped: bool = False


def normalize(text: str) -> Normalized:
    """Fold the spellings listed in the module docstring, keeping a map back to the source."""
    chars: list[str] = []
    starts: list[int] = []
    ends: list[int] = []

    for raw, start, end in _decoded(text):
        folded = _FOLD.get(raw)
        if folded is None:
            folded = unicodedata.normalize("NFKC", raw)
            folded = "".join(_FOLD.get(char, char) for char in folded)
        for char in folded.casefold():
            chars.append(char)
            starts.append(start)
            ends.append(end)

    # Whitespace last, so that a no-break space folded to a space above collapses with its
    # neighbours rather than surviving as a second space.
    out_chars: list[str] = []
    out_starts: list[int] = []
    out_ends: list[int] = []
    index = 0
    while index < len(chars):
        if _WHITESPACE.match(chars[index]):
            run_start = starts[index]
            run_end = ends[index]
            while index + 1 < len(chars) and _WHITESPACE.match(chars[index + 1]):
                index += 1
                run_end = ends[index]
            out_chars.append(" ")
            out_starts.append(run_start)
            out_ends.append(run_end)
        else:
            out_chars.append(chars[index])
            out_starts.append(starts[index])
            out_ends.append(ends[index])
        index += 1

    # Strip the edges, keeping the map aligned with what is left.
    first = 0
    last = len(out_chars)
    while first < last and out_chars[first] == " ":
        first += 1
    while last > first and out_chars[last - 1] == " ":
        last -= 1

    return Normalized(
        text="".join(out_chars[first:last]),
        starts=tuple(out_starts[first:last]),
        ends=tuple(out_ends[first:last]),
    )


def _locate(
    quote: str, source: str, *, normalized_source: Normalized | None = None
) -> Match | None:
    """The span ``quote`` occupies in ``source``, or ``None``. Carries no text; :func:`verify` does.

    Private because a :class:`Match` with an empty ``text`` is a trap for anything that renders
    one. There is one public way to check a quote and it returns the source's own words.
    """
    haystack = normalized_source if normalized_source is not None else normalize(source)
    if not haystack.text:
        return None

    match = _search(quote, haystack)
    if match is not None:
        return match

    for opening, closing in _WRAPPERS:
        stripped = quote.strip()
        if len(stripped) > 2 and stripped.startswith(opening) and stripped.endswith(closing):
            match = _search(stripped[1:-1], haystack)
            if match is not None:
                return Match(
                    text=match.text,
                    start=match.start,
                    end=match.end,
                    occurrences=match.occurrences,
                    unwrapped=True,
                )
            break
    return None


def _search(quote: str, haystack: Normalized) -> Match | None:
    needle = normalize(quote).text
    if not needle:
        return None
    position = haystack.text.find(needle)
    if position < 0:
        return None
    occurrences = haystack.text.count(needle)
    start, end = haystack.span(position, position + len(needle))
    return Match(text="", start=start, end=end, occurrences=occurrences)


def verify(quote: str, source: str, *, normalized_source: Normalized | None = None) -> Match | None:
    """Check one quote against one document, and return what the document says where it matched.

    ``normalized_source`` is the same document already normalized, which the runner passes so that
    a passage is normalized once per call rather than once per returned quote.
    """
    match = _locate(quote, source, normalized_source=normalized_source)
    if match is None:
        return None
    return Match(
        text=source[match.start : match.end],
        start=match.start,
        end=match.end,
        occurrences=match.occurrences,
        unwrapped=match.unwrapped,
    )


def _decoded(text: str) -> list[tuple[str, int, int]]:
    """``(character, source start, source end)`` for every character, entities decoded."""
    out: list[tuple[str, int, int]] = []
    cursor = 0
    for entity in _ENTITY.finditer(text or ""):
        for index in range(cursor, entity.start()):
            out.append((text[index], index, index + 1))
        decoded = html.unescape(entity.group(0))
        if decoded == entity.group(0):
            # Not a reference the standard knows. Left exactly as written.
            for index in range(entity.start(), entity.end()):
                out.append((text[index], index, index + 1))
        else:
            for char in decoded:
                out.append((char, entity.start(), entity.end()))
        cursor = entity.end()
    for index in range(cursor, len(text or "")):
        out.append((text[index], index, index + 1))
    return out


__all__ = ["Match", "Normalized", "normalize", "verify"]
