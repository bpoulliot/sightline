"""Turning one artifact into the text a model is shown, and the text its quotes are checked in.

**One document per call, and the document is one artifact.** An extractor never sends a whole
course and never asks the model which page a sentence came from. It sends one page, one assignment,
one syllabus, and the location of anything the model finds is therefore established by the harness
rather than claimed by the model. That is the cheapest anti-fabrication measure in the item: the
model cannot invent an artifact it was never given.

**What is shown is what is checked.** :attr:`Passage.text` is both the content of the prompt and
the string every returned quote is matched against. There is no second rendering, because two
renderings is how a quote comes back matching what the model saw and failing to match what the
reviewer is told to search for.

**Split, never truncate.** A page longer than the word budget becomes several passages, each with
its own call. Truncating it instead would be the silent under-detection of SPEC §11 in its purest
form: the passage that answers the criterion sits in the half nobody sent, the model correctly
returns nothing, and the row reads as clean. Every passage says which part of how many it is, and
the artifact is counted as examined once however many passages it took.

**The block rendering checks itself.** Blocks are the leaf block-level elements of the body, which
is what gives a quote a source line and a heading to sit under. A body whose text does not all live
in leaf blocks — bare text directly inside a ``div`` that also holds paragraphs — would lose those
words, so the word count of the blocks is compared against the scanner's own count of the body, and
a passage that does not account for every word is rebuilt from the whole body text instead. Losing
the line numbers is a small cost; showing the model a document with words missing from it is not a
cost anybody would notice, which is exactly why it is guarded rather than trusted.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

from ..detect.html_scan import HEADING_TAGS, INLINE_TAGS, Scan, normalize_space, scan
from ..model import Artifact
from .verify import Match, Normalized, normalize

#: Words of course text per call. The context window is pinned at 8192 tokens (SPEC §06); at the
#: usual ~0.75 words per token that is about 6 100 words for everything — instructions, document
#: and output together. 900 words of document is roughly 1 200 tokens, which leaves the instructions
#: their few hundred and the model several thousand to answer in. Deliberately conservative: a
#: passage that overruns the window is answered from a truncated document by a host that does not
#: say it truncated one.
WORD_BUDGET = 900


@dataclass(frozen=True, slots=True)
class Block:
    """One run of text as the page lays it out, with where it came from."""

    text: str
    #: Offsets into :attr:`Passage.text`.
    start: int
    end: int
    #: 1-based source line of the markup this block was written on. ``None`` when the passage had
    #: to be rebuilt from the whole body and the line is no longer known.
    line: int | None = None
    tag: str | None = None
    #: The nearest heading above this block, as written. ``None`` above the first heading.
    heading: str | None = None


@dataclass(frozen=True)
class Passage:
    """One document as sent to the model, and as quotes are verified against."""

    artifact: Artifact
    text: str
    blocks: tuple[Block, ...]
    #: 1-based part number and total, for an artifact that did not fit in one call.
    part: int = 1
    parts: int = 1
    #: True when the blocks did not account for every word and the whole body text was used.
    rebuilt: bool = False
    normalized: Normalized = field(default_factory=lambda: normalize(""))

    @property
    def words(self) -> int:
        return len(self.text.split())

    @property
    def label(self) -> str:
        """How the passage is named in a prompt and in a log line."""
        if self.parts > 1:
            return f"{self.artifact.title} (part {self.part} of {self.parts})"
        return self.artifact.title

    def block_at(self, offset: int) -> Block | None:
        """The block an offset into :attr:`text` falls in."""
        for block in self.blocks:
            if block.start <= offset < block.end:
                return block
        return None

    def within(self, match: Match) -> str | None:
        """A reviewer's words for where in the artifact a verified quote sits.

        Computed from where the quote was actually found — the heading above it, or the part
        number when the artifact took several calls. Never the model's own account of where it
        looked: see :mod:`sightline.extract.base` for the measurement behind that.
        """
        block = self.block_at(match.start)
        heading = block.heading if block is not None else None
        if heading:
            return f"under “{heading}”"
        if self.parts > 1:
            return f"part {self.part} of {self.parts}"
        return None

    def line_of(self, match: Match) -> int | None:
        block = self.block_at(match.start)
        return block.line if block is not None else None


def passages_for(artifact: Artifact, *, budget: int = WORD_BUDGET) -> tuple[Passage, ...]:
    """Every passage one artifact becomes. Empty when the artifact carries no text at all."""
    html = artifact.body_html or ""
    page = scan(html)
    blocks = _blocks(page)
    rebuilt = False

    if _words(blocks) < page.words:
        # The guard in the module docstring. Rebuild from the body text the scanner read, which is
        # complete by construction, and give up the line numbers rather than give up words.
        blocks = [(page.text, None, None, None)] if page.text else []
        rebuilt = True

    if not blocks:
        return ()

    return tuple(_chunked(artifact, blocks, budget=budget, rebuilt=rebuilt))


def _chunked(
    artifact: Artifact,
    blocks: Sequence[tuple[str, int | None, str | None, str | None]],
    *,
    budget: int,
    rebuilt: bool,
) -> Iterator[Passage]:
    groups: list[list[tuple[str, int | None, str | None, str | None]]] = []
    current: list[tuple[str, int | None, str | None, str | None]] = []
    words = 0
    for entry in blocks:
        entry_words = len(entry[0].split())
        if current and words + entry_words > budget:
            groups.append(current)
            current = []
            words = 0
        current.append(entry)
        words += entry_words
    if current:
        groups.append(current)

    total = len(groups)
    for index, group in enumerate(groups, start=1):
        built: list[Block] = []
        cursor = 0
        parts: list[str] = []
        for text, line, tag, heading in group:
            parts.append(text)
            built.append(
                Block(
                    text=text,
                    start=cursor,
                    end=cursor + len(text),
                    line=line,
                    tag=tag,
                    heading=heading,
                )
            )
            cursor += len(text) + 1  # the newline the blocks are joined with
        body = "\n".join(parts)
        yield Passage(
            artifact=artifact,
            text=body,
            blocks=tuple(built),
            part=index,
            parts=total,
            rebuilt=rebuilt,
            normalized=normalize(body),
        )


def _blocks(page: Scan) -> list[tuple[str, int | None, str | None, str | None]]:
    """Leaf block-level elements, in document order, each under the heading above it.

    A *leaf* block is one with no block-level descendant carrying text, so ``<div><p>a</p></div>``
    contributes ``a`` once rather than twice. Inline elements are not blocks of their own — an
    ``<em>`` inside a sentence stays inside the sentence, which is why this reads each element's
    full ``text`` rather than its ``own_text``.
    """
    holds_a_block = _ancestors_of_blocks(page)
    out: list[tuple[str, int | None, str | None, str | None]] = []
    heading: str | None = None
    for element in page.elements:
        if element.tag in INLINE_TAGS or not element.text:
            continue
        if element.ordinal in holds_a_block:
            continue
        text = normalize_space(element.text)
        if not text:
            continue
        out.append((text, element.line, element.tag, heading))
        if element.tag in HEADING_TAGS:
            heading = text
    return out


def _ancestors_of_blocks(page: Scan) -> set[int]:
    """Ordinals of every element that has a block-level descendant carrying text."""
    marked: set[int] = set()
    for element in page.elements:
        if element.tag not in INLINE_TAGS and element.text:
            marked.update(element.ancestors)
    return marked


def _words(blocks: Sequence[tuple[str, int | None, str | None, str | None]]) -> int:
    return sum(len(text.split()) for text, _line, _tag, _heading in blocks)


__all__ = ["Block", "Passage", "WORD_BUDGET", "passages_for"]
