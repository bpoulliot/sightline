"""D39–D45: the mobile cluster, and the section a rubric pack may switch off.

**These are nearly free, and that is the reason they exist.** Every one of them reads something the
course has already been parsed for: the embeds acquisition extracted once (SPEC §04), the same
:func:`~sightline.detect.html_scan.scan` the accessibility cluster walks, the file list, the module
structure. No detector here opens a second parser or takes a second pass over a body, so shipping
the mobile cluster costs about what shipping none of it would.

**No criterion is named here, in a docstring or anywhere else.** These detectors produce facts
about small screens and declare capability tags for them; which rows of which instrument receive
those facts is a pack's business and nothing in this module knows it (SPEC §04, and
:mod:`sightline.capabilities`). What follows is about the *detectors*.

**Four of the seven carry value nothing else produces**, and the rest are honest about not doing
so — SPEC §05's mobile table says which is which, and nothing here quietly pads the count.

:class:`EmbedFallbackLink`, :class:`TextFloatedBesideImages`, :class:`FixedPixelWidths` and
:class:`AttachmentFormats` are the four. They read the embed list, the images, the declared widths
and the attachment formats, and no other detector establishes any of it.

:class:`NestedIndentLevels` shares its capability with D24, deliberately, and computes the half
D24 does not. Indentation depth is invisible to a table walker, and layout tables are already
computed by D24, which declares the same ``table_semantics`` tag. A row bound to that tag receives
from both detectors and their effort sums (:mod:`sightline.rubric.project`), so a second
layout-table walk here would report one table twice on one row and double what its fix is priced
at. The one table fact D24 structurally cannot reach — a table *inside* another table, where D24
reports the outer one and nothing reports the inner — is computed here, on the **inner** table, so
the two never name the same element.

:class:`LegacyEmbeds` will mostly find nothing, and that is the expected answer in 2026: Flash has
been dead for years. A detector that finds nothing over a modern course is not a broken detector,
and the result still says how many embeds it opened to establish it.

:class:`RequiredAppAvailability` is Tier C. It finds every place the course sends a learner to
something outside Canvas and records that **whether an app for it is published on both Android and
iOS is not in the course**. That is an :class:`~sightline.detect.base.Undetermined` rather than a
finding, because it is exactly "opened it, could not read the answer" — and it is what Tier C asks
for: the context and the question, with no rating pre-selected (SPEC §02).

**Content chunking is not here.** D35 already computes it; SPEC §05 routes it, and a second
page-length walk would be the same double count described above.

**Whether a course has been tested on real handsets has no detector and is not supposed to have
one.** It is not determinable from course artifacts, so it is Tier D: the pack carries it with a
standing note and the projection leaves it Not Applicable for a human to set.

**The optional-section flag is not implemented here and does not need to be.** Item 04's pack
format already carries ``sections[].optional`` and ``sections[].enabled``
(``rubric/pack.schema.json``), the loader refuses to switch off a section that has not declared
itself optional, and :func:`~sightline.rubric.project.project` renders only
``pack.rendered_criteria()`` and reports the rest as omitted rather than dropping them silently.
That is the principle stated whole: **a rubric is a projection over detector output.** These
detectors run either way; a pack decides whether their facts are reported, and switching a section
off changes what is rendered and nothing about what was examined.

**On severity.** ``BARRIER`` is used once, by :class:`LegacyEmbeds`, for a plugin no current
browser runs. The vocabulary's own wording is about assistive technology because the accessibility
cluster wrote it first; the fact it names is "the content cannot be reached at all", and for a
Flash object that is true for every learner rather than only for one using a screen reader.
Everything else here degrades a route rather than closing it, and is ``FRICTION`` or ``ADVISORY``
accordingly.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping, Sequence

from ..model import Artifact, CourseFile, Embed, Module, ModuleItem
from .accessibility import BODY_COLLECTIONS, HtmlDetector
from .base import Body, Detector, Finding, Severity, Survey, Tier
from .html_scan import Element, Scan, excerpt, verbatim
from .structure import module_items
from .styles import Stylesheet, declarations

# ---------------------------------------------------------------------------------------------
# D39 — an embed with a plain link beside it
# ---------------------------------------------------------------------------------------------


class EmbedFallbackLink(Detector):
    id = "D39"
    title = "Embedded players with no ordinary hyperlink to the same thing beside them"
    tier = Tier.A
    collections = ("embeds",)
    capability_tag = "embed_fallback_link"
    capability_description = (
        "Whether each embedded player is accompanied by a plain link to the same address, which "
        "is the route left to a learner whose device will not play the embed inline."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        seen = 0
        for embed in survey.embeds():
            seen += 1
            if embed.accompanied_by_link:
                continue
            yield self._finding(survey, embed)
        survey.counted(seen)

    def _finding(self, survey: Survey, embed: Embed) -> Finding:
        container = survey.container_of(embed)
        source = embed.src or "(no source attribute)"
        return survey.finding(
            code="embed_without_an_accompanying_link",
            severity=Severity.FRICTION,
            location=survey.location(container or embed, within=f"{embed.tag} embed"),
            observed=(
                f"<{embed.tag}> pointing at {excerpt(source, 80)}, and no link on the same page "
                "points at it"
            ),
            evidence=source,
            container=embed.container_id,
            provider=embed.provider or "",
            note=(
                "Matching is on the host and the resource-identifying part of the address, "
                "because Canvas rewrites a link into a player URL when it renders. A link written "
                "some other way reads here as absent, which is checkable against the page in "
                "seconds."
            ),
        )


# ---------------------------------------------------------------------------------------------
# D40 — levels of indentation
# ---------------------------------------------------------------------------------------------

#: Levels of nesting a narrow screen absorbs without the content losing its left margin. Past this,
#: indentation is a layout scheme rather than ordinary structure: a two-level bulleted list is how
#: prose is written, and a third level inside it is somebody laying out a page with the indent
#: button. Canvas module items carry the same number as an explicit ``indent`` field.
INDENT_LIMIT = 2

#: Elements that indent what is inside them. ``div`` is not one of them: a ``div`` nests for a
#: hundred reasons that have nothing to do with indentation.
INDENTING_TAGS = frozenset({"ul", "ol", "blockquote", "dd", "dl"})


class NestedIndentLevels(HtmlDetector):
    id = "D40"
    title = (
        "Module items and page content indented more levels deep than a narrow screen shows, and "
        "tables nested inside other tables"
    )
    tier = Tier.A
    collections = ("modules", *BODY_COLLECTIONS)
    capability_tag = "table_semantics"
    capability_description = (
        "How many levels deep module items and page content are indented, and where a table sits "
        "inside another table. The layout-table half of the same concept is D24's; this half is "
        "the indentation, which D24 does not read."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        subjects = 0
        for module in survey.modules():
            items = module_items(module)
            subjects += len(items)
            yield from self._module(survey, module, items)
        for body, page in self.pages(survey):
            indenting = [element for element in page.elements if element.tag in INDENTING_TAGS]
            tables = page.of("table")
            subjects += len(indenting) + len(tables)
            yield from self._markup(survey, body, page, indenting)
            yield from self._nested_tables(survey, body, page, tables)
        survey.counted(subjects)

    def _module(
        self, survey: Survey, module: Module, items: Sequence[ModuleItem]
    ) -> Iterator[Finding]:
        for item in items:
            if item.indent <= INDENT_LIMIT:
                continue
            yield survey.finding(
                code="module_item_indented_past_the_limit",
                severity=Severity.FRICTION,
                location=survey.location(item),
                observed=(
                    f"{item.title!r} is indented {item.indent} level(s) in "
                    f"{module.title!r}, past the {INDENT_LIMIT} a narrow screen shows"
                ),
                evidence=item.title,
                indent=item.indent,
                limit=INDENT_LIMIT,
                module=module.title,
            )

    def _markup(
        self, survey: Survey, body: Body, page: Scan, indenting: Sequence[Element]
    ) -> Iterator[Finding]:
        """One finding per over-deep branch, on the first element past the limit.

        Reporting every element inside a deep list would turn one nesting mistake into a dozen
        findings and a dozen times the estimated effort. The outermost element past the limit is
        the one a reviewer edits.
        """
        for element in indenting:
            depth = 1 + sum(1 for tag in element.ancestor_tags if tag in INDENTING_TAGS)
            if depth != INDENT_LIMIT + 1:
                continue
            yield survey.finding(
                code="content_indented_past_the_limit",
                severity=Severity.FRICTION,
                location=survey.location(
                    body.artifact, within=f"{element.tag} {element.index}", line=element.line
                ),
                observed=(
                    f"a <{element.tag}> nested {depth} level(s) deep, past the {INDENT_LIMIT} a "
                    "narrow screen shows"
                ),
                evidence=verbatim(element.raw),
                depth=depth,
                limit=INDENT_LIMIT,
            )

    def _nested_tables(
        self, survey: Survey, body: Body, page: Scan, tables: Sequence[Element]
    ) -> Iterator[Finding]:
        for table in tables:
            if "table" not in table.ancestor_tags:
                continue
            yield survey.finding(
                code="table_nested_inside_a_table",
                severity=Severity.FRICTION,
                location=survey.location(
                    body.artifact, within=f"table {table.index}", line=table.line
                ),
                observed=(
                    f"table {table.index} sits inside another table, "
                    f"{sum(1 for tag in table.ancestor_tags if tag == 'table')} level(s) in"
                ),
                evidence=verbatim(table.raw),
                note=(
                    "Located on the inner table. D24 reads the outer one, so the two do not name "
                    "the same element."
                ),
            )


# ---------------------------------------------------------------------------------------------
# D41 — text floated beside an image
# ---------------------------------------------------------------------------------------------

#: ``float`` values that put content alongside something instead of below it.
_SIDEWAYS = frozenset({"left", "right"})


class TextFloatedBesideImages(HtmlDetector):
    id = "D41"
    title = "Images floated to one side with text running alongside them"
    tier = Tier.A
    capability_tag = "text_float_beside_media"
    capability_description = (
        "Images floated left or right with text in the same block beside them, which a narrow "
        "screen reflows into a column a few words wide."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for body, page in self.pages(survey):
            sheet = Stylesheet.parse(page.stylesheets)
            images = page.of("img")
            survey.counted(len(images))
            for image in images:
                finding = self._one(survey, body, page, sheet, image)
                if finding is not None:
                    yield finding

    def _one(
        self, survey: Survey, body: Body, page: Scan, sheet: Stylesheet, image: Element
    ) -> Finding | None:
        floated = self._floated(page, sheet, image)
        if floated is None:
            return None
        element, side, written = floated
        container = page.at(element.parent) if element.parent is not None else None
        words = container.words if container is not None else page.words
        if not words:
            # A floated image with nothing beside it reflows to its own line anyway. Reporting it
            # would be reporting a declaration rather than an effect.
            return None
        return survey.finding(
            code="text_floated_beside_an_image",
            severity=Severity.FRICTION,
            location=survey.location(body.artifact, within=f"image {image.index}", line=image.line),
            observed=f"{written} with {words} word(s) of text in the same block",
            evidence=verbatim(image.raw),
            side=side,
            words_beside=words,
        )

    def _floated(
        self, page: Scan, sheet: Stylesheet, image: Element
    ) -> tuple[Element, str, str] | None:
        """The element carrying the float, which side, and the declaration as written.

        The image itself, or a wrapper that holds the image and no text — ``<div style="float:
        left"><img></div>`` floats the picture exactly as a float on the ``<img>`` does, and a
        detector that only read the ``img`` would miss the form every WYSIWYG editor writes.
        """
        candidates = [image, *(page.at(ordinal) for ordinal in reversed(image.ancestors))]
        for element in candidates:
            if element is not image and element.text:
                break
            declared = _declared(sheet, element)
            side = (declared.get("float") or "").strip().lower()
            if side in _SIDEWAYS:
                return element, side, f"float: {side}"
            legacy = (element.attr("align") or "").strip().lower()
            if legacy in _SIDEWAYS:
                return element, legacy, f'align="{legacy}"'
        return None


def _declared(sheet: Stylesheet, element: Element) -> dict[str, str]:
    """Declarations in force on one element from the two places a course body carries them.

    Not inherited, deliberately: ``float`` and ``width`` do not inherit in CSS, and folding them
    down an ancestor chain would report a page's wrapper width on every paragraph inside it.
    """
    merged = dict(sheet.matching(element.tag, element.attrs))
    merged.update(declarations(element.attr("style")))
    return merged


# ---------------------------------------------------------------------------------------------
# D42 — widths declared in pixels
# ---------------------------------------------------------------------------------------------

#: The CSS width of a narrow phone in portrait. Anything wider than this overflows the screen and
#: is reported as friction; anything narrower is a fixed width that still fits, and is reported as
#: advisory because the criterion asks for percentages either way.
NARROW_VIEWPORT_PX = 320

#: Units that pin a width to a number of device pixels. ``%``, ``em``, ``rem``, ``vw``, ``auto``
#: and the rest scale with the screen or the text and are not reported.
_FIXED_UNITS: Mapping[str, float] = {
    "px": 1.0,
    "pt": 96 / 72,
    "pc": 16.0,
    "in": 96.0,
    "cm": 96 / 2.54,
    "mm": 9.6 / 2.54,
}

#: Properties that pin a width. ``max-width`` is *not* one of them: ``max-width: 780px`` paired
#: with a percentage width is the responsive idiom, and reporting it would be reporting the fix.
_WIDTH_PROPERTIES = ("width", "min-width")

_LENGTH = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([a-z%]*)\s*$", re.IGNORECASE)


class FixedPixelWidths(HtmlDetector):
    id = "D42"
    title = "Widths declared as a fixed number of pixels rather than as a percentage"
    tier = Tier.A
    capability_tag = "fixed_width_layout"
    capability_description = (
        "Every width a course body pins to a number of pixels, in a style rule, a style attribute "
        "or a width attribute, with the widest one on each element and whether it is wider than a "
        "narrow phone screen."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for body, page in self.pages(survey):
            sheet = Stylesheet.parse(page.stylesheets)
            measured = 0
            for element in page.elements:
                declared = _declared(sheet, element)
                if not _declares_a_width(declared, element):
                    continue
                # Counted here rather than per finding: an element that declares a width in per
                # cent was measured and found to be fine, and a subject count that only counted
                # the pixel ones would report "3 of 3" over a page that got it right twice.
                measured += 1
                widest = _widest_fixed_width(declared, element)
                if widest is None:
                    continue
                pixels, written = widest
                over = pixels > NARROW_VIEWPORT_PX
                yield survey.finding(
                    code="width_fixed_in_pixels",
                    severity=Severity.FRICTION if over else Severity.ADVISORY,
                    location=survey.location(
                        body.artifact,
                        within=f"{element.tag} {element.index}",
                        line=element.line,
                    ),
                    observed=(
                        f"{written}, which is {pixels:g}px"
                        + (
                            f" — wider than a {NARROW_VIEWPORT_PX}px phone screen"
                            if over
                            else f" — inside a {NARROW_VIEWPORT_PX}px phone screen"
                        )
                    ),
                    evidence=verbatim(element.raw),
                    pixels=pixels,
                    viewport_px=NARROW_VIEWPORT_PX,
                )
            survey.counted(measured)


def _declares_a_width(declared: Mapping[str, str], element: Element) -> bool:
    return element.has("width") or any(name in declared for name in _WIDTH_PROPERTIES)


def _widest_fixed_width(declared: Mapping[str, str], element: Element) -> tuple[float, str] | None:
    """The largest fixed width this element declares, and the declaration as written.

    One finding per element rather than one per property: ``style="width: 780px; min-width: 600px"``
    is one thing to change, and pricing it as two would double what the fix is estimated at.
    """
    found: list[tuple[float, str]] = []
    for name in _WIDTH_PROPERTIES:
        pixels = _pixels(declared.get(name))
        if pixels is not None:
            found.append((pixels, f"{name}: {declared[name].strip()}"))
    attribute = element.attr("width")
    if attribute is not None:
        # An HTML ``width`` attribute is a number of pixels unless it ends in a per cent sign.
        # ``width="900"`` and ``width="900px"`` are the same declaration written two ways.
        written = attribute.strip()
        pixels = _pixels(written if written.endswith("px") else f"{written}px")
        if pixels is not None:
            found.append((pixels, f'width="{written}"'))
    return max(found) if found else None


def _pixels(value: str | None) -> float | None:
    """A CSS length in device pixels, or ``None`` when it is not pinned to one."""
    if not value:
        return None
    match = _LENGTH.match(value)
    if match is None:
        return None
    factor = _FIXED_UNITS.get(match.group(2).lower())
    if factor is None:
        return None
    pixels = float(match.group(1)) * factor
    return pixels if pixels > 0 else None


# ---------------------------------------------------------------------------------------------
# D43 — embeds that need a plugin
# ---------------------------------------------------------------------------------------------

#: MIME type fragment -> the technology it names. A plugin declares itself in ``type`` far more
#: reliably than in the file extension, which is why this is read from the markup rather than from
#: the normalized embed: :class:`~sightline.model.Embed` records the source and the provider, and
#: a ``type`` attribute is neither.
_PLUGIN_TYPES: tuple[tuple[str, str], ...] = (
    ("x-shockwave-flash", "Flash"),
    ("futuresplash", "Flash"),
    ("x-java-applet", "a Java applet"),
    ("x-java-bean", "a Java applet"),
    ("silverlight", "Silverlight"),
    ("x-ms-wmp", "Windows Media Player"),
    ("x-pn-realaudio", "RealPlayer"),
    ("vnd.rn-realmedia", "RealPlayer"),
    ("x-director", "Shockwave Director"),
)

#: File extension -> the technology it names, for an embed that declares no type.
_PLUGIN_EXTENSIONS: tuple[tuple[str, str], ...] = (
    (".swf", "Flash"),
    (".fla", "Flash"),
    (".flv", "Flash video"),
    (".dcr", "Shockwave Director"),
    (".jar", "a Java applet"),
    (".class", "a Java applet"),
    (".xap", "Silverlight"),
    (".wmv", "Windows Media Player"),
    (".rm", "RealPlayer"),
    (".ram", "RealPlayer"),
)

#: ActiveX class identifiers. Two of them account for essentially every one in a course body.
_CLASSIDS: tuple[tuple[str, str], ...] = (
    ("d27cdb6e-ae6d-11cf-96b8-444553540000", "Flash"),
    ("8ad9c840-044e-11d1-b3e9-00805f499d93", "a Java applet"),
    ("dfeaf541-f3e1-4c24-acac-99c30715084a", "Silverlight"),
)

#: Technologies no current browser runs at all, as against ones that need a particular player.
_DEAD = frozenset({"Flash", "Flash video", "Shockwave Director", "a Java applet", "Silverlight"})

#: Elements that can load a plugin. ``applet`` is here and is not an embed as far as acquisition is
#: concerned, which is the second reason this detector reads the markup.
_PLUGIN_TAGS = frozenset({"object", "embed", "applet"})


class LegacyEmbeds(HtmlDetector):
    id = "D43"
    title = "Embedded content that needs a browser plugin a phone does not have"
    tier = Tier.A
    capability_tag = "legacy_embeds"
    capability_description = (
        "Embedded content that names a plugin technology — Flash, Java, Silverlight, a "
        "platform-specific media player — in its type, its class identifier or its file "
        "extension."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        for body, page in self.pages(survey):
            candidates = [element for element in page.elements if element.tag in _PLUGIN_TAGS]
            survey.counted(len(candidates))
            for element in candidates:
                named = _plugin_named_by(page, element)
                if named is None:
                    continue
                technology, where = named
                dead = technology in _DEAD
                yield survey.finding(
                    code="embed_needs_a_browser_plugin",
                    severity=Severity.BARRIER if dead else Severity.FRICTION,
                    location=survey.location(
                        body.artifact,
                        within=f"{element.tag} {element.index}",
                        line=element.line,
                    ),
                    observed=(
                        f"<{element.tag}> naming {technology} in its {where}"
                        + (
                            "; no current browser runs it, on a phone or anywhere else"
                            if dead
                            else "; a phone opens it only where that player is installed"
                        )
                    ),
                    evidence=verbatim(element.raw),
                    technology=technology,
                    named_in=where,
                )


def _plugin_named_by(page: Scan, element: Element) -> tuple[str, str] | None:
    """What plugin this element names and where it names it, or ``None``.

    Four places, checked in the order a course body actually carries them. A ``param`` element
    named ``movie`` is one of them, because an ``<object>`` written by an older editor keeps the
    ``.swf`` there and nowhere else.
    """
    if element.tag == "applet":
        return "a Java applet", "tag name"

    declared_type = (element.attr("type") or "").strip().lower()
    for fragment, technology in _PLUGIN_TYPES:
        if fragment in declared_type:
            return technology, "type attribute"

    classid = (element.attr("classid") or "").strip().lower()
    for fragment, technology in _CLASSIDS:
        if fragment in classid:
            return technology, "class identifier"

    sources = [element.attr("data") or "", element.attr("src") or ""]
    sources.extend(
        child.attr("value") or ""
        for child in page.children(element)
        if child.tag == "param" and (child.attr("name") or "").strip().lower() == "movie"
    )
    for source in sources:
        stem = source.split("?", 1)[0].split("#", 1)[0].lower()
        for suffix, technology in _PLUGIN_EXTENSIONS:
            if stem.endswith(suffix):
                return technology, "file extension"
    return None


# ---------------------------------------------------------------------------------------------
# D44 — what formats the course attaches
# ---------------------------------------------------------------------------------------------

#: Formats superseded by something a phone opens more readily, and in several cases by their own
#: successor. A ``.doc`` is not unopenable; it is the one an older desktop suite wrote and the one
#: a phone is likeliest to render badly.
SUPERSEDED_FORMATS: Mapping[str, str] = {
    ".doc": "Word 97–2003",
    ".ppt": "PowerPoint 97–2003",
    ".xls": "Excel 97–2003",
    ".pub": "Microsoft Publisher",
    ".wpd": "WordPerfect",
    ".wps": "Microsoft Works",
    ".pages": "Apple Pages",
    ".key": "Apple Keynote",
    ".numbers": "Apple Numbers",
}

#: Current formats that still need an application a learner may not have on a phone.
APP_FORMATS: Mapping[str, str] = {
    ".docx": "Word",
    ".pptx": "PowerPoint",
    ".xlsx": "Excel",
    ".odt": "a word processor",
    ".odp": "a presentation app",
    ".ods": "a spreadsheet app",
    ".rtf": "a word processor",
}

#: Containers a phone cannot usefully open at all: the learner has to reach a computer first.
ARCHIVE_FORMATS = frozenset({".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"})

#: What the criterion asks attachments to be, where an attachment is necessary at all.
PREFERRED_FORMAT = ".pdf"


class AttachmentFormats(Detector):
    id = "D44"
    title = (
        "Attachments in a format a phone opens only with another application, or cannot open at "
        "all, counted against the ones it opens directly"
    )
    tier = Tier.A
    collections = ("files",)
    capability_tag = "attachment_formats"
    capability_description = (
        "A census of the formats the course attaches: how many are PDF, how many need a separate "
        "application, how many are in a superseded format, and how many are archives."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        files = list(survey.files())
        survey.counted(len(files))
        census = _census(files)
        for item in files:
            extension = item.extension
            if extension in SUPERSEDED_FORMATS:
                yield self._finding(
                    survey,
                    item,
                    census,
                    code="attachment_in_a_superseded_format",
                    severity=Severity.FRICTION,
                    what=f"a {SUPERSEDED_FORMATS[extension]} file",
                )
            elif extension in APP_FORMATS:
                yield self._finding(
                    survey,
                    item,
                    census,
                    code="attachment_needs_another_application",
                    severity=Severity.ADVISORY,
                    what=f"a file that opens in {APP_FORMATS[extension]}",
                )
            elif extension in ARCHIVE_FORMATS:
                yield self._finding(
                    survey,
                    item,
                    census,
                    code="attachment_is_an_archive",
                    severity=Severity.FRICTION,
                    what="an archive, which a phone unpacks only with another application",
                )

    def _finding(
        self,
        survey: Survey,
        item: CourseFile,
        census: Mapping[str, int],
        *,
        code: str,
        severity: Severity,
        what: str,
    ) -> Finding:
        pdfs = census.get(PREFERRED_FORMAT, 0)
        return survey.finding(
            code=code,
            severity=severity,
            location=survey.location(item),
            observed=(
                f"{item.filename} is {what}; {pdfs} of {sum(census.values())} attachment(s) in "
                "the course are PDF"
            ),
            evidence=item.filename,
            extension=item.extension or "",
            census=dict(census),
        )


def _census(files: Sequence[CourseFile]) -> dict[str, int]:
    """Extension -> how many attachments carry it, in extension order.

    Carried verbatim into every finding: "a ``.doc`` attachment" is a fact about one file, and
    "one of nineteen attachments" is what tells a reviewer whether it is an oversight or a habit.
    """
    tally: dict[str, int] = {}
    for item in files:
        key = item.extension or "(no extension)"
        tally[key] = tally.get(key, 0) + 1
    return dict(sorted(tally.items()))


# ---------------------------------------------------------------------------------------------
# D45 — apps the course requires (tier C)
# ---------------------------------------------------------------------------------------------

#: A sentence asking a learner to obtain an app. Deliberately narrow: ``\bapps?\b`` on its own
#: matches "apparatus" and every mention of the word in a syllabus, and a Tier C question raised
#: against a false match is worse than one not raised at all.
_APP_REQUIREMENT = re.compile(
    r"(?:download|install|obtain|purchase|require[sd]?|need(?:s|ed)?|use)\b[^.!?]{0,80}?\bapps?\b",
    re.IGNORECASE,
)

#: How much of the sentence around a match is quoted back.
_QUOTE_LIMIT = 200


class RequiredAppAvailability(Detector):
    id = "D45"
    title = (
        "Places the course sends a learner outside Canvas, where whether the platforms learners "
        "have are supported is a question the course does not answer"
    )
    tier = Tier.C
    collections = ("modules", *BODY_COLLECTIONS)
    capability_tag = "mobile_app_availability"
    capability_description = (
        "Every place the course launches an external tool or asks a learner to obtain an app, "
        "gathered so a reviewer can check each one against the platforms learners have. Whether "
        "an app exists on Android and on iOS is not recorded anywhere in a course."
    )

    def examine(self, survey: Survey) -> Iterable[Finding]:
        """Records questions, not findings, and that is what Tier C means here.

        The course names the tool; the app stores answer whether it runs on both platforms, and no
        course artifact does. So every one of these is an
        :class:`~sightline.detect.base.Undetermined` carrying the context and the question, with no
        rating pre-selected (SPEC §02). It yields no findings at all, and that is not a detector
        that found nothing: the result still carries what it examined and what it could not read.
        """
        subjects = 0
        for module in survey.modules():
            for item in module_items(module):
                if item.item_type != "ExternalTool":
                    continue
                subjects += 1
                self._ask_about_tool(survey, item, item.title, item.external_url or "")
        for assignment in survey.assignments():
            if "external_tool" not in assignment.submission_types:
                continue
            subjects += 1
            self._ask_about_tool(survey, assignment, assignment.title, "")
        for body in survey.bodies():
            subjects += self._ask_about_prose(survey, body)
        survey.counted(subjects)
        return ()

    def _ask_about_tool(self, survey: Survey, artifact: Artifact, name: str, url: str) -> None:
        where = f" at {url}" if url else ""
        survey.undetermined(
            survey.location(artifact),
            reason=(
                f"the course launches {name!r}{where}; whether an app for it is published on both "
                "Android and iOS is not recorded in the course"
            ),
            remedy=(
                "Check the tool's own listing on both app stores, or confirm it runs in a mobile "
                "browser, and answer this row from that."
            ),
        )

    def _ask_about_prose(self, survey: Survey, body: Body) -> int:
        """Sentences asking a learner to get an app, read from the run's one parse of the body.

        ``Scan.text`` puts a separator at every rendered break and none inside a word, so a sentence
        is neither fused across a block seam nor split at an inline tag.
        """
        text = survey.parse(body).text
        asked = 0
        for match in _APP_REQUIREMENT.finditer(text):
            asked += 1
            quoted = verbatim(match.group(0).strip(), _QUOTE_LIMIT)
            survey.undetermined(
                survey.location(body.artifact, within=f"quote {asked}"),
                reason=(
                    f"the page asks a learner to obtain an app — {quoted!r} — and whether it is "
                    "published on both Android and iOS is not recorded in the course"
                ),
                remedy=(
                    "Check the app's listing on both stores and answer this row from that, or "
                    "note that the requirement is optional."
                ),
            )
        return asked


__all__ = [
    "APP_FORMATS",
    "ARCHIVE_FORMATS",
    "INDENTING_TAGS",
    "INDENT_LIMIT",
    "NARROW_VIEWPORT_PX",
    "PREFERRED_FORMAT",
    "SUPERSEDED_FORMATS",
    "AttachmentFormats",
    "EmbedFallbackLink",
    "FixedPixelWidths",
    "LegacyEmbeds",
    "NestedIndentLevels",
    "RequiredAppAvailability",
    "TextFloatedBesideImages",
]
