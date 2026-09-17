"""D21–D30: the accessibility cluster.

These ten are the credibility wedge. They are fully deterministic, they cover the checks a reviewer
currently does by hand over every page of every course, and they are the first thing a sceptical
faculty member will try to falsify. So every finding here carries the markup exactly as it was
written and a location precise enough to search for.

**The scope boundary, which none of these crosses.** Sightline checks that alt text *exists*, is not
a filename, and is not empty on an image nothing marks decorative. It does not judge whether alt
text is good, and it sends no image to a vision model (SPEC §11). The same restraint runs through
the rest: D25 reports a ratio and the background it assumed, not a verdict about a colour scheme;
D30 reports that a PDF has no tag tree, not that a document is inaccessible.

**Two of these are tier B, and the difference is in the wording, not in who computed it.** D26 and
D29 look for signals that *suggest* a problem a person has to confirm — colour doing work no other
cue does, a page whose content lives inside its pictures. They locate and quote a passage and stop
there. SPEC §02 puts them at tier B for that reason, and a parser that quotes is a stricter form of
the same contract than a model that quotes, because a parser cannot invent the quote.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from ..model import Artifact, CourseFile, Embed
from .base import Body, Detector, Finding, Severity, Survey, Tier
from .documents import inspect_docx, inspect_pdf, inspect_pptx
from .html_scan import (
    EMPHASIS_TAGS,
    HEADING_TAGS,
    Element,
    Scan,
    excerpt,
    heading_level,
    scan,
    verbatim,
)
from .styles import (
    ASSUMED_BACKGROUND,
    MINIMUM_POINTS,
    Colour,
    StyleContext,
    Stylesheet,
    contrast_ratio,
    declarations,
    legacy_font_declarations,
    required_ratio,
    summarise_ratio,
    typeface,
)

#: The collections whose artifacts carry a body. Declared by every HTML detector so that a zero
#: count is checked against what acquisition actually managed to read.
BODY_COLLECTIONS: tuple[str, ...] = (
    "pages",
    "assignments",
    "quizzes",
    "discussions",
    "announcements",
)

#: The background a ratio falls back to. Built rather than parsed so that nothing at import time
#: depends on a parse succeeding.
_WHITE = Colour(255, 255, 255, written=ASSUMED_BACKGROUND)


class HtmlDetector(Detector, abstract=True):
    """Shared walk for the detectors that read course markup.

    One parse per body, handed to whichever detector asked for it. Two detectors disagreeing about
    whether an unclosed tag ended is how one reports three images and another four over the same
    page, and neither number can then be checked.
    """

    collections = BODY_COLLECTIONS

    def pages(self, survey: Survey) -> Iterator[tuple[Body, Scan]]:
        for body in survey.bodies():
            yield body, scan(body.html)


# ---------------------------------------------------------------------------------------------
# D21 — image alt text
# ---------------------------------------------------------------------------------------------

#: Past this, a screen reader reads a paragraph where a label was meant. It is the widely used
#: convention rather than a standard, and the finding quotes the length it found alongside it.
MAX_ALT_LENGTH = 125

_IMAGE_EXTENSIONS = ("jpg", "jpeg", "png", "gif", "bmp", "webp", "svg", "tif", "tiff", "heic")
_FILENAME_ALT = re.compile(
    r"^[\w\-. ()%]+\.(?:" + "|".join(_IMAGE_EXTENSIONS) + r")$", re.IGNORECASE
)
#: Names and sizes that mark an image as furniture rather than content.
_SPACER_WORDS = ("spacer", "blank", "pixel", "shim", "clear", "transparent", "divider", "bullet")
_SPACER_PIXELS = 4


class ImageAltText(HtmlDetector):
    id = "D21"
    title = (
        "Images with no alt text, with a filename used as alt text, with alt text long enough to "
        "read as a paragraph, or marked decorative in a way that contradicts itself"
    )
    tier = Tier.A
    capability_tag = "alt_text"
    capability_description = (
        "Whether each image carries alt text at all, and whether what it carries is a real "
        "description rather than a filename, an empty string on a meaningful image, or a paragraph."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for body, page in self.pages(survey):
            images = page.of("img")
            survey.counted(len(images))
            for image in images:
                yield from self._one(survey, body.artifact, image)

    def _one(self, survey: Survey, artifact: Artifact, image: Element) -> Iterator[Finding]:
        location = survey.location(artifact, within=f"image {image.index}", line=image.line)
        evidence = verbatim(image.raw)
        alt = image.attr("alt")
        decorative = _marked_decorative(image)

        if alt is None:
            if decorative:
                yield survey.finding(
                    code="decorative_image_without_empty_alt",
                    severity=Severity.ADVISORY,
                    location=location,
                    observed="no alt attribute, marked decorative",
                    evidence=evidence,
                )
                return
            yield survey.finding(
                code="alt_missing",
                severity=Severity.BARRIER,
                location=location,
                observed="no alt attribute",
                evidence=evidence,
                src=image.attr("src") or "",
            )
            return

        if not alt.strip():
            if decorative or _looks_like_furniture(image):
                return
            yield survey.finding(
                code="alt_empty_and_not_marked_decorative",
                severity=Severity.FRICTION,
                location=location,
                observed='alt=""',
                evidence=evidence,
                note=(
                    "An empty alt hides the image from a screen reader. That is correct for "
                    "decoration and this image is not marked as decoration."
                ),
            )
            return

        if decorative:
            yield survey.finding(
                code="decorative_image_with_alt_text",
                severity=Severity.ADVISORY,
                location=location,
                observed=f'role="presentation" with alt="{excerpt(alt, 60)}"',
                evidence=evidence,
            )
        if _alt_is_filename(alt, image.attr("src")):
            yield survey.finding(
                code="alt_is_a_filename",
                severity=Severity.BARRIER,
                location=location,
                observed=f'alt="{excerpt(alt, 60)}"',
                evidence=evidence,
            )
        elif len(alt) > MAX_ALT_LENGTH:
            yield survey.finding(
                code="alt_over_long",
                severity=Severity.ADVISORY,
                location=location,
                observed=f"{len(alt)} characters of alt text",
                evidence=verbatim(alt, 160),
                limit=MAX_ALT_LENGTH,
            )


def _marked_decorative(image: Element) -> bool:
    role = (image.attr("role") or "").strip().lower()
    hidden = (image.attr("aria-hidden") or "").strip().lower()
    return role in {"presentation", "none"} or hidden == "true"


def _looks_like_furniture(image: Element) -> bool:
    """A spacer or rule, which carries no meaning and is correctly given an empty alt."""
    source = (image.attr("src") or "").lower()
    if any(word in source for word in _SPACER_WORDS):
        return True
    for name in ("width", "height"):
        value = (image.attr(name) or "").strip().rstrip("px")
        if value.isdigit() and int(value) <= _SPACER_PIXELS:
            return True
    return False


def _alt_is_filename(alt: str, src: str | None) -> bool:
    text = alt.strip()
    if _FILENAME_ALT.match(text):
        return True
    if not src:
        return False
    basename = src.split("?", 1)[0].rstrip("/").rpartition("/")[2]
    stem = basename.rpartition(".")[0] or basename
    return bool(basename) and text.lower() in {basename.lower(), stem.lower()}


# ---------------------------------------------------------------------------------------------
# D22 — heading structure
# ---------------------------------------------------------------------------------------------

#: Below this, a page is a note and needs no headings. Above it, a page with none is a wall of text
#: with no way through it for anyone navigating by heading.
HEADINGS_EXPECTED_ABOVE_WORDS = 150

#: Bold text this long is a sentence, not a label somebody used as a heading.
MAX_PSEUDO_HEADING_CHARS = 100

_BLOCK_PARENTS = frozenset({"p", "div"})


class HeadingStructure(HtmlDetector):
    id = "D22"
    title = (
        "Pages with no headings, headings that skip a level, empty headings, and bold text used "
        "where a heading was meant"
    )
    tier = Tier.A
    capability_tag = "heading_structure"
    capability_description = (
        "Whether pages use real heading elements, in order and without gaps, rather than bold text "
        "standing in for one."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for body, page in self.pages(survey):
            headings = page.of(*HEADING_TAGS)
            survey.counted(len(headings))
            yield from self._levels(survey, body.artifact, page, headings)
            yield from self._pseudo(survey, body.artifact, page)

    def _levels(
        self, survey: Survey, artifact: Artifact, page: Scan, headings: Sequence[Element]
    ) -> Iterator[Finding]:
        if not headings:
            if page.words >= HEADINGS_EXPECTED_ABOVE_WORDS:
                yield survey.finding(
                    code="headings_absent",
                    severity=Severity.FRICTION,
                    location=survey.location(artifact),
                    observed=f"{page.words} words, no heading element",
                    evidence=verbatim(page.text, 160),
                )
            return

        previous: int | None = None
        for heading in headings:
            level = heading_level(heading.tag)
            if level is None:  # pragma: no cover - of() only returns heading tags
                continue
            location = survey.location(
                artifact, within=f"{heading.tag} at line {heading.line}", line=heading.line
            )
            if not heading.text:
                yield survey.finding(
                    code="heading_empty",
                    severity=Severity.FRICTION,
                    location=location,
                    observed=f"<{heading.tag}> with no text in it",
                    evidence=verbatim(heading.raw),
                )
            if level == 1:
                yield survey.finding(
                    code="heading_h1_inside_body",
                    severity=Severity.ADVISORY,
                    location=location,
                    observed=f"<h1>{excerpt(heading.text, 60)}</h1> inside the body",
                    evidence=verbatim(heading.raw),
                    note=(
                        "Canvas renders the page's own title as its first-level heading, so a "
                        "second one appears in the outline."
                    ),
                )
            if previous is not None and level > previous + 1:
                yield survey.finding(
                    code="heading_level_skipped",
                    severity=Severity.FRICTION,
                    location=location,
                    observed=f"h{previous} followed by h{level}",
                    evidence=verbatim(heading.raw),
                    skipped=[f"h{missing}" for missing in range(previous + 1, level)],
                )
            previous = level

    def _pseudo(self, survey: Survey, artifact: Artifact, page: Scan) -> Iterator[Finding]:
        for element in page.elements:
            if element.tag not in _BLOCK_PARENTS or element.own_text:
                continue
            children = page.children(element)
            if len(children) != 1 or children[0].tag not in EMPHASIS_TAGS:
                continue
            label = children[0].text
            if not label or len(label) > MAX_PSEUDO_HEADING_CHARS:
                continue
            yield survey.finding(
                code="bold_text_used_as_a_heading",
                severity=Severity.FRICTION,
                location=survey.location(
                    artifact, within=f"line {element.line}", line=element.line
                ),
                observed=f"<{element.tag}> containing only <{children[0].tag}>{label}</...>",
                evidence=verbatim(children[0].raw + label),
            )


# ---------------------------------------------------------------------------------------------
# D23 — link text
# ---------------------------------------------------------------------------------------------

#: Labels that say nothing about where the link goes. A screen reader lists a page's links out of
#: context, and a list of eight "click here" entries is a list of eight identical destinations.
VAGUE_LINK_TEXT = frozenset(
    {
        "click",
        "click here",
        "click here for more",
        "click here to read",
        "click this link",
        "continue",
        "details",
        "download",
        "follow this link",
        "go",
        "here",
        "learn more",
        "link",
        "more",
        "more info",
        "more information",
        "read more",
        "see here",
        "this",
        "this link",
        "this page",
    }
)

_URL_IN_TEXT = re.compile(r"(?:https?://|www\.)[^\s<>\"']{4,}", re.IGNORECASE)
_BARE_HOST = re.compile(r"^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+){1,}(?:/\S*)?$")
_FILENAME = re.compile(r"\.[A-Za-z0-9]{2,5}$")
_DOCUMENT_SUFFIXES = frozenset(
    {
        ".doc",
        ".docx",
        ".gif",
        ".htm",
        ".html",
        ".jpeg",
        ".jpg",
        ".mp3",
        ".mp4",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".txt",
        ".xls",
        ".xlsx",
        ".zip",
    }
)


class LinkText(HtmlDetector):
    id = "D23"
    title = (
        "Links labelled 'click here' or with a raw web address, links with no text at all, the "
        "same label pointing at different places, and addresses typed into the page as prose"
    )
    tier = Tier.A
    capability_tag = "link_text"
    capability_description = (
        "Whether a link's own text says where it goes, and whether the same label is reused for "
        "different destinations."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for body, page in self.pages(survey):
            anchors = [element for element in page.of("a") if element.has("href")]
            bare = list(_bare_urls(page))
            survey.counted(len(anchors) + len(bare))
            yield from self._labels(survey, body.artifact, page, anchors)
            yield from self._duplicates(survey, body.artifact, anchors)
            for line, url in bare:
                yield survey.finding(
                    code="web_address_written_as_prose",
                    severity=Severity.FRICTION,
                    location=survey.location(body.artifact, within=f"line {line}", line=line),
                    observed=url,
                    evidence=url,
                    note=(
                        "Canvas turns this into a link when it renders the page, and its label is "
                        "then the address itself."
                    ),
                )

    def _labels(
        self, survey: Survey, artifact: Artifact, page: Scan, anchors: Sequence[Element]
    ) -> Iterator[Finding]:
        for anchor in anchors:
            href = (anchor.attr("href") or "").strip()
            label = anchor.text or _image_alt_inside(page, anchor)
            location = survey.location(artifact, within=f"link {anchor.index}", line=anchor.line)
            evidence = verbatim(anchor.raw + label + "</a>")
            if not label:
                yield survey.finding(
                    code="link_without_text",
                    severity=Severity.BARRIER,
                    location=location,
                    observed=f"a link to {excerpt(href, 60)} with nothing to read",
                    evidence=evidence,
                )
                continue
            normalized = _normalize_label(label)
            if normalized in VAGUE_LINK_TEXT:
                yield survey.finding(
                    code="link_text_says_nothing",
                    severity=Severity.FRICTION,
                    location=location,
                    observed=f'"{label}"',
                    evidence=evidence,
                    href=href,
                )
            elif _label_is_an_address(label):
                yield survey.finding(
                    code="link_text_is_a_web_address",
                    severity=Severity.FRICTION,
                    location=location,
                    observed=f'"{excerpt(label, 80)}"',
                    evidence=evidence,
                )

    def _duplicates(
        self, survey: Survey, artifact: Artifact, anchors: Sequence[Element]
    ) -> Iterator[Finding]:
        grouped: dict[str, list[Element]] = {}
        for anchor in anchors:
            label = _normalize_label(anchor.text)
            if label:
                grouped.setdefault(label, []).append(anchor)
        for label, group in grouped.items():
            targets = sorted({(anchor.attr("href") or "").strip() for anchor in group})
            if len(group) < 2 or len(targets) < 2:
                continue
            first = group[0]
            yield survey.finding(
                code="same_label_different_destinations",
                severity=Severity.FRICTION,
                location=survey.location(artifact, within=f"link {first.index}", line=first.line),
                observed=f'"{label}" used {len(group)} times for {len(targets)} destinations',
                evidence=verbatim(first.raw + first.text + "</a>"),
                destinations=targets,
            )


def _bare_urls(page: Scan) -> Iterator[tuple[int, str]]:
    for line, run in page.unlinked_runs:
        for match in _URL_IN_TEXT.finditer(run):
            yield line, match.group(0).rstrip(".,;:)")


def _normalize_label(text: str) -> str:
    return re.sub(r"[\s ]+", " ", text).strip().strip(".,:;!?\"'“”‘’ ").lower()


def _label_is_an_address(label: str) -> bool:
    text = label.strip()
    if _URL_IN_TEXT.match(text):
        return True
    if not _BARE_HOST.match(text):
        return False
    # A filename is a legitimate label for a file. A hostname is not a legitimate label for a page.
    suffix = _FILENAME.search(text.split("/", 1)[0])
    return not (suffix and suffix.group(0).lower() in _DOCUMENT_SUFFIXES)


def _image_alt_inside(page: Scan, anchor: Element) -> str:
    for child in page.elements:
        if child.tag == "img" and anchor.ordinal in child.ancestors:
            return (child.attr("alt") or "").strip()
    return ""


# ---------------------------------------------------------------------------------------------
# D24 — table semantics
# ---------------------------------------------------------------------------------------------


class TableSemantics(HtmlDetector):
    id = "D24"
    title = (
        "Tables of data with no header cells, header cells with no scope, tables with no caption, "
        "and tables used to position things on the page rather than to hold data"
    )
    tier = Tier.A
    capability_tag = "table_semantics"
    capability_description = (
        "Whether tables of data declare their header cells and scope and carry a caption, and "
        "whether a table is being used to lay out a page instead."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for body, page in self.pages(survey):
            tables = page.of("table")
            survey.counted(len(tables))
            for table in tables:
                yield from self._one(survey, body.artifact, page, table)

    def _one(
        self, survey: Survey, artifact: Artifact, page: Scan, table: Element
    ) -> Iterator[Finding]:
        shape = _table_shape(page, table)
        location = survey.location(artifact, within=f"table {table.index}", line=table.line)
        evidence = verbatim(table.raw)

        if shape.headers == 0 and shape.looks_like_layout:
            yield survey.finding(
                code="table_positions_content",
                severity=Severity.FRICTION,
                location=location,
                observed=f"{shape.rows} row(s) × {shape.columns} column(s), no header cells",
                evidence=evidence,
                reason=shape.layout_reason,
            )
            return

        if shape.headers == 0:
            yield survey.finding(
                code="table_without_header_cells",
                severity=Severity.BARRIER,
                location=location,
                observed=f"{shape.rows} row(s) × {shape.columns} column(s), no <th>",
                evidence=evidence,
            )
        elif shape.headers_without_scope and shape.rows > 1 and shape.columns > 1:
            yield survey.finding(
                code="header_cells_without_scope",
                severity=Severity.FRICTION,
                location=location,
                observed=(
                    f"{shape.headers_without_scope} of {shape.headers} header cell(s) carry no "
                    "scope attribute"
                ),
                evidence=evidence,
            )

        if not shape.has_caption:
            yield survey.finding(
                code="table_without_caption",
                severity=Severity.ADVISORY,
                location=location,
                observed="no <caption> element",
                evidence=evidence,
            )
        if table.has("summary"):
            yield survey.finding(
                code="table_uses_summary_attribute",
                severity=Severity.ADVISORY,
                location=location,
                observed=f'summary="{excerpt(table.attr("summary") or "", 60)}"',
                evidence=evidence,
                note="The summary attribute was dropped from HTML5; a caption replaces it.",
            )


@dataclass(frozen=True, slots=True)
class _TableShape:
    rows: int
    columns: int
    headers: int
    headers_without_scope: int
    has_caption: bool
    nested: bool
    looks_like_layout: bool
    layout_reason: str


def _table_shape(page: Scan, table: Element) -> _TableShape:
    own = [
        element
        for element in page.elements
        if table.ordinal in element.ancestors and _nearest_table(page, element) == table.ordinal
    ]
    rows = [element for element in own if element.tag == "tr"]
    headers = [element for element in own if element.tag == "th"]
    columns = 0
    for row in rows:
        cells = sum(
            1 for element in own if element.tag in {"td", "th"} and element.parent == row.ordinal
        )
        columns = max(columns, cells)
    nested = any(
        element.tag == "table" and table.ordinal in element.ancestors for element in page.elements
    )
    role = (table.attr("role") or "").strip().lower()
    borderless = (table.attr("border") or "").strip() == "0"

    reason = ""
    if role in {"presentation", "none"}:
        reason = 'the table declares role="presentation"'
    elif len(rows) <= 1:
        reason = "the table has a single row"
    elif columns <= 1:
        reason = "the table has a single column"
    elif nested:
        reason = "the table holds another table"
    elif borderless and not headers:
        reason = 'the table declares border="0" and has no header cells'

    return _TableShape(
        rows=len(rows),
        columns=columns,
        headers=len(headers),
        headers_without_scope=sum(1 for header in headers if not header.has("scope")),
        has_caption=any(element.tag == "caption" for element in own),
        nested=nested,
        looks_like_layout=bool(reason),
        layout_reason=reason,
    )


def _nearest_table(page: Scan, element: Element) -> int | None:
    for ordinal in reversed(element.ancestors):
        if page.at(ordinal).tag == "table":
            return ordinal
    return None


# ---------------------------------------------------------------------------------------------
# Styles in force — shared by D25, D26 and D28
# ---------------------------------------------------------------------------------------------


def style_contexts(page: Scan) -> tuple[dict[int, StyleContext], Stylesheet]:
    """The declarations in force on every element, folded down through its ancestors.

    Not a rendering engine and not offered as one: it merges a ``<style>`` block's flat rules, the
    ``style`` attribute and the legacy ``<font>`` element, in that order. What it could not resolve
    it keeps, so a detector can report the gap instead of reporting nothing.
    """
    sheet = Stylesheet.parse(page.stylesheets)
    contexts: dict[int, StyleContext] = {}
    for element in page.elements:
        inherited = contexts[element.parent] if element.parent is not None else StyleContext()
        declared: dict[str, str] = {}
        declared.update(sheet.matching(element.tag, element.attrs))
        declared.update(declarations(element.attr("style")))
        if element.tag == "font":
            declared.update(legacy_font_declarations(element.attrs))
        contexts[element.ordinal] = inherited.merged_with(
            declared, bold_tag=element.tag in {"b", "strong"}
        )
    return contexts, sheet


# ---------------------------------------------------------------------------------------------
# D25 — contrast
# ---------------------------------------------------------------------------------------------


class ContrastRatio(HtmlDetector):
    id = "D25"
    title = (
        "Text whose declared colour does not reach the contrast against its background that the "
        "rubric asks for, reported with the background it was measured against"
    )
    tier = Tier.A
    capability_tag = "colour_contrast"
    capability_description = (
        "The contrast ratio of each declared text colour against the background it was measured "
        "against, in course pages."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for body, page in self.pages(survey):
            contexts, sheet = style_contexts(page)
            measured = 0
            seen: dict[tuple[str, str], int] = {}
            first: dict[tuple[str, str], Element] = {}
            for element in page.elements:
                if not element.own_text:
                    continue
                context = contexts[element.ordinal]
                if context.colour is None:
                    continue
                measured += 1
                background = context.background or _WHITE
                ratio = contrast_ratio(context.colour, background)
                needed = required_ratio(context.points, bold=context.bold)
                if ratio >= needed:
                    continue
                key = (context.colour.hex, background.hex)
                seen[key] = seen.get(key, 0) + 1
                first.setdefault(key, element)
            survey.counted(measured)

            for key, occurrences in seen.items():
                element = first[key]
                context = contexts[element.ordinal]
                background = context.background or _WHITE
                assumed = context.background is None
                ratio = contrast_ratio(context.colour, background) if context.colour else 0.0
                needed = required_ratio(context.points, bold=context.bold)
                yield survey.finding(
                    code="contrast_below_threshold",
                    severity=Severity.BARRIER if ratio < 3.0 else Severity.FRICTION,
                    location=survey.location(
                        body.artifact, within=f"line {element.line}", line=element.line
                    ),
                    observed=(
                        f"{summarise_ratio(ratio)} for {key[0]} on {key[1]}, "
                        f"{summarise_ratio(needed)} needed"
                    ),
                    evidence=verbatim(element.own_text, 100),
                    background_assumed=assumed,
                    background=key[1],
                    foreground=key[0],
                    occurrences=occurrences,
                    note=(
                        "The page declares no background here, so the ratio is measured against "
                        f"{ASSUMED_BACKGROUND}, which is what Canvas's content area uses."
                        if assumed
                        else ""
                    ),
                )

            if sheet.unsupported:
                survey.undetermined(
                    survey.location(body.artifact),
                    reason=(
                        f"{len(sheet.unsupported)} style rule(s) on this page use selectors "
                        "Sightline does not resolve, so any colour they set was not measured: "
                        + ", ".join(sorted(set(sheet.unsupported))[:5])
                    ),
                    remedy="Check those rules by hand, or move the colours onto the elements.",
                )


# ---------------------------------------------------------------------------------------------
# D26 — colour carrying meaning on its own (tier B)
# ---------------------------------------------------------------------------------------------

#: Colour names that turn up in instructions. Narrower than the CSS list on purpose: "silver" and
#: "olive" in prose are almost never an instruction, and a false quote costs a reviewer more than
#: a missed one does here, where the tier already says a person decides.
_INSTRUCTION_COLOURS = (
    "red",
    "green",
    "blue",
    "yellow",
    "orange",
    "purple",
    "pink",
    "grey",
    "gray",
)
_COLOUR_INSTRUCTION = re.compile(
    r"\b(?:in|shown in|marked in|highlighted in|coloured|colored|coded)\s+"
    r"(?:" + "|".join(_INSTRUCTION_COLOURS) + r")\b"
    r"|\b(?:" + "|".join(_INSTRUCTION_COLOURS) + r")\s+"
    r"(?:text|items?|rows?|columns?|cells?|links?|boxes?|entries|headings?|words?|sections?)\b",
    re.IGNORECASE,
)
_SENTENCE = re.compile(r"[^.!?]*[.!?]|[^.!?]+$")
_OTHER_CUE_TAGS = frozenset({"b", "strong", "i", "em", "u", "mark", "abbr"})


class ColourAsSoleCarrier(HtmlDetector):
    id = "D26"
    title = (
        "Passages where colour appears to be doing work on its own — prose that tells a learner to "
        "look for a colour, and coloured text that carries no second cue"
    )
    tier = Tier.B
    capability_tag = "colour_as_sole_cue"
    capability_description = (
        "Passages where colour appears to be the only thing carrying a distinction, including "
        "prose that tells a learner to look for one."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for body, page in self.pages(survey):
            contexts, _ = style_contexts(page)
            sentences = [
                sentence.strip() for sentence in _SENTENCE.findall(page.text) if sentence.strip()
            ]
            coloured = [
                element
                for element in page.elements
                if element.own_text and _sets_own_colour(page, element, contexts)
            ]
            survey.counted(len(sentences) + len(coloured))

            for sentence in sentences:
                match = _COLOUR_INSTRUCTION.search(sentence)
                if not match:
                    continue
                yield survey.finding(
                    code="instructions_refer_to_a_colour",
                    severity=Severity.FRICTION,
                    location=survey.location(body.artifact),
                    observed=f'"{match.group(0)}"',
                    evidence=verbatim(sentence, 200),
                    note=(
                        "Quoted for a reviewer to judge: whether anything other than the colour "
                        "marks these items is not readable from the page."
                    ),
                )

            for element in coloured:
                if not _is_fragment(page, element) or _has_second_cue(page, element):
                    continue
                context = contexts[element.ordinal]
                yield survey.finding(
                    code="coloured_text_carries_no_second_cue",
                    severity=Severity.ADVISORY,
                    location=survey.location(
                        body.artifact, within=f"line {element.line}", line=element.line
                    ),
                    observed=(
                        f'"{excerpt(element.own_text, 60)}" set in '
                        f"{context.colour.written if context.colour else ''}"
                    ),
                    evidence=verbatim(element.raw + element.own_text),
                )


def _sets_own_colour(page: Scan, element: Element, contexts: Mapping[int, StyleContext]) -> bool:
    context = contexts[element.ordinal]
    if context.colour is None:
        return False
    parent = contexts[element.parent] if element.parent is not None else StyleContext()
    return parent.colour is None or parent.colour.hex != context.colour.hex


def _is_fragment(page: Scan, element: Element) -> bool:
    """Whether this coloured run sits inside a larger block that is not coloured with it.

    A whole paragraph in one colour is a style choice. A few words in a different colour inside a
    paragraph is the shape where colour is carrying the meaning.
    """
    if element.parent is None:
        return False
    parent = page.at(element.parent)
    return bool(parent.text) and len(parent.text) > len(element.text)


def _has_second_cue(page: Scan, element: Element) -> bool:
    if element.tag in _OTHER_CUE_TAGS or element.descends_from(*_OTHER_CUE_TAGS):
        return True
    if any(child.tag in _OTHER_CUE_TAGS for child in page.children(element)):
        return True
    declared = declarations(element.attr("style"))
    if declared.get("text-decoration", "").strip():
        return True
    weight = declared.get("font-weight", "").strip().lower()
    return weight in {"bold", "bolder"} or (weight.isdigit() and int(weight) >= 600)


# ---------------------------------------------------------------------------------------------
# D27 — captions on media
# ---------------------------------------------------------------------------------------------

MEDIA_EXTENSIONS = (
    ".mp4",
    ".m4v",
    ".mov",
    ".avi",
    ".mkv",
    ".webm",
    ".mp3",
    ".m4a",
    ".wav",
    ".flac",
)
CAPTION_EXTENSIONS = (".vtt", ".srt", ".sbv", ".dfxp", ".ttml")


class CaptionTrack(HtmlDetector):
    id = "D27"
    title = (
        "Audio and video with no caption track beside it, and hosted players whose caption state "
        "the course itself does not record"
    )
    tier = Tier.A
    capability_tag = "captions_and_transcripts"
    capability_description = (
        "Whether a caption track accompanies each audio or video element, and where a hosted "
        "player's caption state cannot be read from the course."
    )
    collections = ("embeds", "files")

    def examine(self, survey: Survey) -> Iterable[Finding]:
        seen = 0
        for embed in survey.embeds():
            seen += 1
            yield from self._embed(survey, embed)
        files = list(survey.files())
        captions = {_stem(item.filename) for item in files if item.extension in CAPTION_EXTENSIONS}
        media = [item for item in files if item.extension in MEDIA_EXTENSIONS]
        seen += len(media)
        survey.counted(seen)
        for item in media:
            if _stem(item.filename) in captions:
                continue
            yield survey.finding(
                code="media_file_without_a_caption_file",
                severity=Severity.FRICTION,
                location=survey.location(item),
                observed=f"{item.filename} with no caption file of the same name",
                evidence=item.filename,
                looked_for=[f"{_stem(item.filename)}{suffix}" for suffix in CAPTION_EXTENSIONS],
            )

    def _embed(self, survey: Survey, embed: Embed) -> Iterator[Finding]:
        if embed.has_caption_track:
            return
        container = survey.container_of(embed)
        location = survey.location(container or embed, within=f"{embed.tag} embed")
        source = embed.src or "(no source attribute)"
        if embed.tag in {"video", "audio"}:
            yield survey.finding(
                code="media_without_caption_track",
                severity=Severity.BARRIER,
                location=location,
                observed=f'<{embed.tag}> with no <track kind="captions">',
                evidence=source,
                container=embed.container_id,
                provider=embed.provider or "",
            )
            return
        yield survey.finding(
            code="player_captions_not_evidenced",
            severity=Severity.FRICTION,
            location=location,
            observed=f"<{embed.tag}> pointing at {excerpt(source, 80)}",
            evidence=source,
            container=embed.container_id,
            provider=embed.provider or "",
            note=(
                "Nothing on the page records whether this player carries captions. Open it and "
                "check; the course files do not answer it."
            ),
        )


def _stem(filename: str) -> str:
    return filename.rpartition(".")[0].lower() or filename.lower()


# ---------------------------------------------------------------------------------------------
# D28 — typeface and size
# ---------------------------------------------------------------------------------------------

DOCUMENT_TYPE_EXTENSIONS = (".docx",)


class TypeSizeAndFamily(HtmlDetector):
    id = "D28"
    title = (
        "Text declared below the minimum size the rubric asks for, and typefaces that are not "
        "sans-serif, in course pages and in the Word documents attached to them"
    )
    tier = Tier.A
    capability_tag = "type_size_and_family"
    capability_description = (
        "Declared text sizes below the minimum and typefaces outside the family asked for, in "
        "course pages and in attached word-processor documents."
    )
    collections = (*BODY_COLLECTIONS, "files")

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for body, page in self.pages(survey):
            yield from self._markup(survey, body, page)
        yield from self._documents(survey)

    def _markup(self, survey: Survey, body: Body, page: Scan) -> Iterator[Finding]:
        contexts, _ = style_contexts(page)
        small: dict[str, tuple[Element, float]] = {}
        faces: dict[str, tuple[Element, str]] = {}
        declared = 0
        for element in page.elements:
            if not element.own_text:
                continue
            context = contexts[element.ordinal]
            if context.points is None and context.face is None:
                continue
            declared += 1
            if context.points is not None and context.points < MINIMUM_POINTS:
                written = context.points_written or f"{context.points:g}pt"
                small.setdefault(written, (element, context.points))
            if context.face is not None and context.face.kind in {"serif", "decorative"}:
                faces.setdefault(context.face.written, (element, context.face.kind))
        survey.counted(declared)

        for written, (element, points) in small.items():
            yield survey.finding(
                code="type_smaller_than_the_minimum",
                severity=Severity.FRICTION,
                location=survey.location(
                    body.artifact, within=f"line {element.line}", line=element.line
                ),
                observed=f"font-size: {written}, which resolves to {points:g}pt",
                evidence=verbatim(element.own_text, 100),
                minimum_points=MINIMUM_POINTS,
            )
        for written, (element, kind) in faces.items():
            yield survey.finding(
                code="typeface_is_not_sans_serif",
                severity=Severity.FRICTION if kind == "decorative" else Severity.ADVISORY,
                location=survey.location(
                    body.artifact, within=f"line {element.line}", line=element.line
                ),
                observed=f"font-family: {written} ({kind})",
                evidence=verbatim(element.own_text, 100),
                family_kind=kind,
            )

    def _documents(self, survey: Survey) -> Iterator[Finding]:
        for item in survey.files(*DOCUMENT_TYPE_EXTENSIONS):
            survey.counted()
            data = survey.read(item)
            if data is None:
                _no_bytes(survey, item)
                continue
            report = inspect_docx(data)
            if not report.readable:
                survey.undetermined(
                    survey.location(item),
                    reason=f"the document's type could not be read: {report.reason}",
                )
                continue
            if report.default_points is not None and report.default_points < MINIMUM_POINTS:
                yield survey.finding(
                    code="document_type_smaller_than_the_minimum",
                    severity=Severity.FRICTION,
                    location=survey.location(item),
                    observed=f"the document's default style is {report.default_points:g}pt",
                    evidence=item.filename,
                    minimum_points=MINIMUM_POINTS,
                )
            classified = typeface(report.default_face)
            if classified is not None and classified.kind in {"serif", "decorative"}:
                yield survey.finding(
                    code="document_typeface_is_not_sans_serif",
                    severity=Severity.ADVISORY,
                    location=survey.location(item),
                    observed=f"the document's default typeface is {report.default_face}",
                    evidence=item.filename,
                    family_kind=classified.kind,
                )


# ---------------------------------------------------------------------------------------------
# D29 — text rendered as an image (tier B)
# ---------------------------------------------------------------------------------------------

#: Words in a filename that name a document rather than a picture. An image called
#: ``schedule-table.png`` is a table somebody exported as a picture often enough to be worth
#: quoting; whether it is one is a reviewer's call, which is what makes this tier B.
DOCUMENT_WORDS = (
    "agenda",
    "calendar",
    "checklist",
    "handout",
    "instructions",
    "outline",
    "rubric",
    "scan",
    "schedule",
    "screenshot",
    "slide",
    "syllabus",
    "table",
    "timeline",
    "transcript",
    "worksheet",
)

#: Below this many words of prose per image, a page is mostly pictures.
WORDS_PER_IMAGE = 30
#: One picture on a short page is a figure. Two or more is a pattern worth quoting.
IMAGES_FOR_DENSITY = 2


class TextRenderedAsImage(HtmlDetector):
    id = "D29"
    title = (
        "Images that look like a document rendered as a picture, and pages carrying more picture "
        "than prose"
    )
    tier = Tier.B
    capability_tag = "text_as_image"
    capability_description = (
        "Images whose pixel density suggests a page of text rendered as a picture, and pages "
        "carrying more picture than prose."
    )
    collections = (*BODY_COLLECTIONS, "files")

    def examine(self, survey: Survey) -> Iterable[Finding]:
        # The course's file list is part of what this reads, not a lookup on the side: an image's
        # strongest signal is its *name*, and the name lives in the file list rather than in the
        # markup. Walking it here is what makes the examined count say so.
        list(survey.files())
        for body, page in self.pages(survey):
            images = page.of("img")
            survey.counted(len(images))
            for image in images:
                yield from self._named(survey, body, image)
            if len(images) >= IMAGES_FOR_DENSITY and page.words < WORDS_PER_IMAGE * len(images):
                yield survey.finding(
                    code="page_carries_more_picture_than_prose",
                    severity=Severity.FRICTION,
                    location=survey.location(body.artifact),
                    observed=f"{len(images)} image(s) and {page.words} word(s) of text",
                    evidence=verbatim(page.text, 160),
                    images=len(images),
                    words=page.words,
                )

    def _named(self, survey: Survey, body: Body, image: Element) -> Iterator[Finding]:
        source = image.attr("src") or ""
        item = survey.find_file(source)
        haystack = f"{item.filename if item else source} {image.attr('alt') or ''}".lower()
        matched = [word for word in DOCUMENT_WORDS if word in haystack]
        if not matched:
            return
        target: Artifact = item or body.artifact
        yield survey.finding(
            code="image_named_like_a_document",
            severity=Severity.FRICTION,
            location=survey.location(
                target, within=f"image {image.index} on {body.artifact.title}"
            ),
            observed=f"{item.filename if item else excerpt(source, 60)} names {', '.join(matched)}",
            evidence=verbatim(image.raw),
            appears_on=body.artifact.canvas_path,
            words_matched=matched,
            note=(
                "Quoted for a reviewer to open: whether this picture holds text a learner needs "
                "is not readable from the page, and Sightline does not look inside images."
            ),
        )


# ---------------------------------------------------------------------------------------------
# D30 — document accessibility
# ---------------------------------------------------------------------------------------------

LEGACY_OFFICE_EXTENSIONS = (".doc", ".ppt", ".xls")
INSPECTED_EXTENSIONS = (".pdf", ".docx", ".pptx", *LEGACY_OFFICE_EXTENSIONS)


class DocumentAccessibility(Detector):
    id = "D30"
    title = (
        "Attached documents: PDFs with no tag tree or no text layer, Word files that use no "
        "heading styles, and slide decks with missing or repeated titles or timed transitions"
    )
    tier = Tier.A
    capability_tag = "document_accessibility"
    capability_description = (
        "Whether an attached PDF carries a tag tree and a text layer, whether a word-processor "
        "file uses heading styles, and whether a slide deck has unique titles and no timed "
        "transitions."
    )
    collections = ("files",)

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for item in survey.files(*INSPECTED_EXTENSIONS):
            survey.counted()
            if item.extension in LEGACY_OFFICE_EXTENSIONS:
                survey.undetermined(
                    survey.location(item),
                    reason=(
                        f"{item.filename} is in a pre-2007 binary Office format, which carries "
                        "none of the structure Sightline reads"
                    ),
                    remedy="Save it as .docx or .pptx and attach that instead.",
                )
                continue
            data = survey.read(item)
            if data is None:
                _no_bytes(survey, item)
                continue
            if item.extension == ".pdf":
                yield from self._pdf(survey, item, data)
            elif item.extension == ".docx":
                yield from self._docx(survey, item, data)
            else:
                yield from self._pptx(survey, item, data)

    def _pdf(self, survey: Survey, item: CourseFile, data: bytes) -> Iterator[Finding]:
        report = inspect_pdf(data)
        location = survey.location(item)
        if not report.readable:
            survey.undetermined(
                location,
                reason=f"{item.filename} could not be read: {report.reason}",
                remedy=(
                    "Open it in Acrobat and check the tag tree by hand, or re-export it from the "
                    "document it came from."
                ),
            )
            return
        if not report.tagged:
            yield survey.finding(
                code="pdf_without_a_tag_tree",
                severity=Severity.BARRIER,
                location=location,
                observed=f"{report.pages} page(s), no structure tree object in the file",
                evidence=item.filename,
                marked=report.marked,
            )
        if report.pages and not report.has_text_layer:
            yield survey.finding(
                code="pdf_without_a_text_layer",
                severity=Severity.BARRIER,
                location=location,
                observed=(
                    f"{report.pages} page(s) and {report.images} image(s), no font or text "
                    "operator in the file"
                ),
                evidence=item.filename,
                note="A page of pixels. Nothing in it can be read aloud, searched or copied.",
            )
        if not report.has_language:
            yield survey.finding(
                code="pdf_without_a_declared_language",
                severity=Severity.ADVISORY,
                location=location,
                observed="no /Lang entry in the document catalog",
                evidence=item.filename,
            )

    def _docx(self, survey: Survey, item: CourseFile, data: bytes) -> Iterator[Finding]:
        report = inspect_docx(data)
        location = survey.location(item)
        if not report.readable:
            survey.undetermined(
                location, reason=f"{item.filename} could not be read: {report.reason}"
            )
            return
        if not report.uses_heading_styles:
            yield survey.finding(
                code="document_without_heading_styles",
                severity=Severity.FRICTION,
                location=location,
                observed="no paragraph uses a built-in heading style",
                evidence=item.filename,
            )
        if report.tables and report.tables_with_header_rows < report.tables:
            yield survey.finding(
                code="document_table_without_a_repeating_header_row",
                severity=Severity.FRICTION,
                location=location,
                observed=(
                    f"{report.tables - report.tables_with_header_rows} of {report.tables} "
                    "table(s) mark no header row"
                ),
                evidence=item.filename,
            )
        if report.images_without_alt:
            yield survey.finding(
                code="document_image_without_alt_text",
                severity=Severity.BARRIER,
                location=location,
                observed=(
                    f"{report.images_without_alt} of {report.images} image(s) carry no alt text"
                ),
                evidence=item.filename,
            )

    def _pptx(self, survey: Survey, item: CourseFile, data: bytes) -> Iterator[Finding]:
        report = inspect_pptx(data)
        location = survey.location(item)
        if not report.readable:
            survey.undetermined(
                location, reason=f"{item.filename} could not be read: {report.reason}"
            )
            return
        if report.slides_without_title:
            yield survey.finding(
                code="slide_without_a_title",
                severity=Severity.BARRIER,
                location=location,
                observed=(
                    f"{len(report.slides_without_title)} of {report.slides} slide(s) have no "
                    "title placeholder with text in it"
                ),
                evidence=item.filename,
                slides=list(report.slides_without_title),
            )
        if report.duplicate_titles:
            yield survey.finding(
                code="slide_titles_repeat",
                severity=Severity.FRICTION,
                location=location,
                observed=f"{len(report.duplicate_titles)} title(s) used on more than one slide",
                evidence=", ".join(report.duplicate_titles[:5]),
                titles=list(report.duplicate_titles),
            )
        if report.slides_advancing_automatically:
            yield survey.finding(
                code="slide_advances_on_a_timer",
                severity=Severity.FRICTION,
                location=location,
                observed=(
                    f"{len(report.slides_advancing_automatically)} slide(s) advance without a "
                    "click"
                ),
                evidence=item.filename,
                slides=list(report.slides_advancing_automatically),
            )
        if report.slides_off_layout:
            yield survey.finding(
                code="slide_built_outside_a_layout",
                severity=Severity.FRICTION,
                location=location,
                observed=(
                    f"{len(report.slides_off_layout)} slide(s) use no layout placeholders, so "
                    "their reading order is the order the shapes were drawn"
                ),
                evidence=item.filename,
                slides=list(report.slides_off_layout),
            )
        if report.images_without_alt:
            yield survey.finding(
                code="document_image_without_alt_text",
                severity=Severity.BARRIER,
                location=location,
                observed=(
                    f"{report.images_without_alt} of {report.images} image(s) carry no alt text"
                ),
                evidence=item.filename,
            )


def _no_bytes(survey: Survey, item: CourseFile) -> None:
    survey.undetermined(
        survey.location(item),
        reason=(
            f"{item.filename} is listed in the course but its bytes were not acquired, so nothing "
            "inside it was examined"
        ),
        remedy=(
            "Ask Canvas to build a content export for this course, then run the review again; "
            "the export is where document bytes come from."
        ),
    )


__all__ = [
    "BODY_COLLECTIONS",
    "CAPTION_EXTENSIONS",
    "DOCUMENT_WORDS",
    "HEADINGS_EXPECTED_ABOVE_WORDS",
    "MAX_ALT_LENGTH",
    "MEDIA_EXTENSIONS",
    "VAGUE_LINK_TEXT",
    "CaptionTrack",
    "ColourAsSoleCarrier",
    "ContrastRatio",
    "DocumentAccessibility",
    "HeadingStructure",
    "HtmlDetector",
    "ImageAltText",
    "LinkText",
    "TableSemantics",
    "TextRenderedAsImage",
    "TypeSizeAndFamily",
    "style_contexts",
]
