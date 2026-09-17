"""One lenient pass over a course body, shared by every HTML detector.

Seven of the ten accessibility detectors read the same markup. Parsing it once and handing every
detector the same tree is not only cheaper — it is what makes them *agree*. Two parsers disagreeing
about whether an unclosed ``<p>`` ended is how one detector reports three images and another
reports four over the same page, and a reviewer cannot falsify either number.

The parser is :mod:`html.parser` from the standard library, for the reason acquisition chose it:
course HTML is malformed in every interesting way and ``HTMLParser`` never raises on it. Two
inherited lessons are encoded below rather than rediscovered.

**``iframe`` must not be a CDATA element.** ``HTMLParser`` treats it as one, so a single unclosed
``<iframe>`` swallows the rest of the document and everything after it silently disappears. That is
the silent under-detection of SPEC §11 arriving through the parser, so
:attr:`_TreeParser.CDATA_CONTENT_ELEMENTS` is narrowed to ``script`` and ``style``, which really do
contain ``<`` that is not markup.

**An element is located, not just found.** Every :class:`Element` carries the literal start tag as
written and the line it was written on, because a finding has to quote something a reviewer can
search the page for.

**Text runs are joined the way the page reads, not the way the markup concatenates.** The scan used
to build its text with ``"".join(...)``, so ``<h2>Course schedule</h2><p>Week 1</p>`` read back as
``Course scheduleWeek 1``: a word-boundary regex failed at the seam and a word count was short by
one per seam. D04 reported a course that has a schedule as having none — wrong output, no error.
The rule now is :data:`INLINE_TAGS` and it is stated once, here: a separator is written wherever the
rendered page would break one run of text from the next, and nowhere else.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser

#: Elements that never have an end tag. Closed the moment they open, so a page full of ``<br>``
#: does not nest every later element inside the first one.
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: Tags whose text is markup-adjacent rather than prose. Excluded from word counts so that a page
#: carrying a stylesheet does not read as a page carrying text.
_NON_PROSE = frozenset({"script", "style"})

#: Tags whose boundaries fall *inside* a run of text rather than between two runs. A browser renders
#: ``<em>un</em>likely`` as the single word "unlikely", so no separator is written where one of
#: these opens or closes, and a word count over the text counts one word there.
#:
#: Everything not in this set breaks the run and takes a separator: every block element, ``<br>``,
#: a replaced object like ``<img>`` or a form control that a reader sees as a thing between two
#: pieces of text, and any tag this set has never heard of. Unknown tags separate deliberately —
#: fusing two words invents a third word that matches nothing and is invisible (SPEC §11), while
#: separating two words that a browser would have run together leaves both of them findable.
#:
#: This is the text-level semantics of HTML plus the presentational tags a decade of course content
#: is written in; it is not a guess about which CSS ``display`` a course's stylesheet sets.
INLINE_TAGS = frozenset(
    {
        "a",
        "abbr",
        "acronym",
        "b",
        "bdi",
        "bdo",
        "big",
        "cite",
        "code",
        "data",
        "del",
        "dfn",
        "em",
        "font",
        "i",
        "ins",
        "kbd",
        "mark",
        "nobr",
        "q",
        "rp",
        "rt",
        "ruby",
        "s",
        "samp",
        "small",
        "span",
        "strike",
        "strong",
        "sub",
        "sup",
        "time",
        "tt",
        "u",
        "var",
        "wbr",
    }
)

HEADING_TAGS: tuple[str, ...] = ("h1", "h2", "h3", "h4", "h5", "h6")

#: Elements that render text in bold or italic. ``b`` and ``i`` are presentational and ``strong``
#: and ``em`` are semantic; for "is this paragraph entirely bold" the distinction does not matter.
EMPHASIS_TAGS = frozenset({"b", "strong", "i", "em", "u", "mark"})

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Element:
    """One element in document order, with everything a finding needs to cite it."""

    tag: str
    attrs: Mapping[str, str]
    #: Position in document order across every element. Also this element's identity within a
    #: scan: parents and children are named by ordinal.
    ordinal: int
    #: 1-based position among elements of the same tag, so a finding can say "image 3".
    index: int
    line: int
    #: The start tag exactly as it was written, which is what a finding quotes.
    raw: str
    parent: int | None
    #: Ancestor ordinals, outermost first.
    ancestors: tuple[int, ...]
    #: Ancestor tag names, outermost first.
    ancestor_tags: tuple[str, ...]
    child_ordinals: tuple[int, ...]
    #: Text belonging to this element and everything inside it, whitespace-normalized, with a
    #: separator at every seam that is not an :data:`INLINE_TAGS` one.
    text: str
    #: Text written directly inside this element, excluding any nested element's text. Separated by
    #: the same rule, so ``<div>a<p>b</p>c</div>`` owns ``a c`` and not ``ac``.
    own_text: str

    @property
    def words(self) -> int:
        return len(self.text.split())

    def attr(self, name: str) -> str | None:
        """An attribute's value, or ``None`` when the attribute is not present.

        Distinguishing absent from empty is the whole of D21: ``alt`` missing and ``alt=""`` are
        different facts about an image and get different findings.
        """
        return self.attrs.get(name)

    def has(self, name: str) -> bool:
        return name in self.attrs

    def descends_from(self, *tags: str) -> bool:
        return any(tag in self.ancestor_tags for tag in tags)


@dataclass(frozen=True, slots=True)
class Scan:
    """Everything one pass over a body produced."""

    elements: tuple[Element, ...]
    #: The contents of every ``<style>`` block, in document order.
    stylesheets: tuple[str, ...]
    #: Prose text of the whole body, whitespace-normalized, read the way the page renders:
    #: separated at every seam that is not an :data:`INLINE_TAGS` one. ``<h2>Course
    #: schedule</h2><p>Week 1</p>`` reads ``Course schedule Week 1``, and ``<em>un</em>likely``
    #: reads ``unlikely``. Anything matching vocabulary with ``\b`` depends on both halves.
    text: str
    #: Text runs that sit outside any anchor, each with the line it started on. What D23 reads to
    #: find a URL written out as prose, which Canvas turns into a link when it renders.
    unlinked_runs: tuple[tuple[int, str], ...]

    @property
    def words(self) -> int:
        return len(self.text.split())

    def of(self, *tags: str) -> tuple[Element, ...]:
        wanted = {tag.lower() for tag in tags}
        return tuple(element for element in self.elements if element.tag in wanted)

    def at(self, ordinal: int) -> Element:
        return self.elements[ordinal]

    def children(self, element: Element) -> tuple[Element, ...]:
        return tuple(self.elements[ordinal] for ordinal in element.child_ordinals)

    def ancestors(self, element: Element) -> tuple[Element, ...]:
        return tuple(self.elements[ordinal] for ordinal in element.ancestors)


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    raw: str
    line: int
    parent: int | None
    ancestors: tuple[int, ...]
    ordinal: int
    children: list[int] = field(default_factory=list)
    own: list[str] = field(default_factory=list)
    all_text: list[str] = field(default_factory=list)


class _TreeParser(HTMLParser):
    #: Narrowed from the default, which includes ``iframe``, ``noembed``, ``noframes`` and ``xmp``.
    #: An unclosed ``<iframe>`` under the default swallows every element after it on the page.
    CDATA_CONTENT_ELEMENTS = ("script", "style")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.nodes: list[_Node] = []
        self.stylesheets: list[str] = []
        self.text_parts: list[str] = []
        #: ``(line, text)`` for every run of text that sits outside an anchor.
        self.unlinked: list[tuple[int, str]] = []
        self._open: list[int] = []

    # -- structure -------------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered not in INLINE_TAGS:
            self._seam()
        node = _Node(
            tag=lowered,
            attrs={name.lower(): (value if value is not None else "") for name, value in attrs},
            raw=self.get_starttag_text() or f"<{lowered}>",
            line=self.getpos()[0],
            parent=self._open[-1] if self._open else None,
            ancestors=tuple(self._open),
            ordinal=len(self.nodes),
        )
        self.nodes.append(node)
        if node.parent is not None:
            self.nodes[node.parent].children.append(node.ordinal)
        if lowered not in VOID_TAGS:
            self._open.append(node.ordinal)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self._close(tag.lower())

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered not in INLINE_TAGS:
            self._seam()
        self._close(lowered)

    def _close(self, tag: str) -> None:
        # Leniently: an end tag with no matching open tag is discarded rather than unwinding the
        # document, and closing an outer element closes what is still open inside it.
        for position in range(len(self._open) - 1, -1, -1):
            if self.nodes[self._open[position]].tag == tag:
                del self._open[position:]
                return

    # -- text ------------------------------------------------------------------------------

    def _seam(self) -> None:
        """Write a separator into every text stream that is open at a rendered break.

        Into the innermost open element's own text as well as into every ancestor's, because
        ``own_text`` fuses at a seam exactly as the body text does: ``<div>a<p>b</p>c</div>`` owns
        ``a`` and ``c`` with nothing between them. A separator written where there is no text yet,
        or where no text follows, is removed by :func:`normalize_space` at the end of the parse.

        Not into :attr:`unlinked`, which keeps its runs apart already and stamps each with the line
        it started on — a separator there would be a run with no text and no line of its own.
        """
        if self._open:
            self.nodes[self._open[-1]].own.append(" ")
        for ordinal in self._open:
            self.nodes[ordinal].all_text.append(" ")
        self.text_parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._open and self.nodes[self._open[-1]].tag == "style":
            self.stylesheets.append(data)
            return
        if self._open and self.nodes[self._open[-1]].tag in _NON_PROSE:
            return
        if self._open:
            self.nodes[self._open[-1]].own.append(data)
        for ordinal in self._open:
            self.nodes[ordinal].all_text.append(data)
        self.text_parts.append(data)
        if not any(self.nodes[ordinal].tag == "a" for ordinal in self._open):
            self.unlinked.append((self.getpos()[0], data))


def scan(html: str) -> Scan:
    """Parse one body. Never raises on malformed markup, because course bodies are."""
    parser = _TreeParser()
    parser.feed(html or "")
    parser.close()

    counts: dict[str, int] = {}
    elements: list[Element] = []
    for node in parser.nodes:
        counts[node.tag] = counts.get(node.tag, 0) + 1
        elements.append(
            Element(
                tag=node.tag,
                attrs=dict(node.attrs),
                ordinal=node.ordinal,
                index=counts[node.tag],
                line=node.line,
                raw=node.raw,
                parent=node.parent,
                ancestors=node.ancestors,
                ancestor_tags=tuple(parser.nodes[ordinal].tag for ordinal in node.ancestors),
                child_ordinals=tuple(node.children),
                text=normalize_space("".join(node.all_text)),
                own_text=normalize_space("".join(node.own)),
            )
        )
    return Scan(
        elements=tuple(elements),
        stylesheets=tuple(parser.stylesheets),
        text=normalize_space("".join(parser.text_parts)),
        unlinked_runs=tuple(
            (line, normalize_space(run)) for line, run in parser.unlinked if normalize_space(run)
        ),
    )


def normalize_space(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def heading_level(tag: str) -> int | None:
    """``h3`` -> 3. ``None`` for anything that is not a heading."""
    return int(tag[1]) if tag in HEADING_TAGS else None


def iter_headings(page: Scan) -> Iterator[Element]:
    return (element for element in page.elements if element.tag in HEADING_TAGS)


def excerpt(text: str, limit: int = 120) -> str:
    """A readable fragment for a *measured value*: whitespace collapsed, truncation marked.

    Not for evidence. Collapsing whitespace changes the bytes, and a reviewer who searches the page
    for a quote that was tidied up will not find it. Use :func:`verbatim` for anything presented as
    what the course says.
    """
    trimmed = normalize_space(text)
    if len(trimmed) <= limit:
        return trimmed
    return trimmed[: limit - 1].rstrip() + "…"


def verbatim(text: str, limit: int = 200) -> str:
    """Source text exactly as written, truncated only, with the truncation marked.

    The whole design rests on a finding being falsifiable in seconds (SPEC §01), which means the
    quote in it has to be findable in the page. So nothing is normalized here — not the spacing
    inside a tag, not a line break inside an attribute. What is shortened says that it was.
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def tag_counts(page: Scan, tags: Sequence[str]) -> dict[str, int]:
    wanted = {tag.lower() for tag in tags}
    tally = dict.fromkeys(sorted(wanted), 0)
    for element in page.elements:
        if element.tag in wanted:
            tally[element.tag] += 1
    return tally


__all__ = [
    "EMPHASIS_TAGS",
    "HEADING_TAGS",
    "INLINE_TAGS",
    "VOID_TAGS",
    "Element",
    "Scan",
    "excerpt",
    "heading_level",
    "iter_headings",
    "normalize_space",
    "scan",
    "tag_counts",
    "verbatim",
]
