"""Colour, contrast and type, read out of the styles a course body actually declares.

Two detectors share this: D25 asks whether declared text colour reaches the contrast the rubric
asks for, and D28 asks what typeface and size a body declares. Both read the same three places a
Canvas body can carry a style — a ``style`` attribute, a ``<style>`` block, and the legacy
``<font>`` element — so both are parsed here once.

**What is assumed is stated, every time.** A course body almost never declares a background colour;
it inherits one from the Canvas theme, which is not in the export. So a ratio computed against an
assumed background is computed against a *named* assumption, carried in the finding, and a
reviewer who knows the theme differs can dismiss it in seconds. A contrast number presented without
the background it was measured against is not checkable, and an uncheckable number is worse than no
number.

**Unparsed is counted, not skipped.** A selector this module does not understand is tallied rather
than dropped, so under-detection shows up as a number in the result instead of as silence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

#: The background a ratio is measured against when the body declares none. Canvas's own content
#: area is white; a theme can change it, which is why the assumption travels with the finding.
ASSUMED_BACKGROUND = "#ffffff"

#: WCAG 2.1 AA. 4.5:1 for body text, 3:1 for large text.
NORMAL_TEXT_RATIO = 4.5
LARGE_TEXT_RATIO = 3.0

#: "Large" in WCAG terms: 18pt, or 14pt when bold.
LARGE_POINTS = 18.0
LARGE_BOLD_POINTS = 14.0

#: The rubric asks for at least 12pt body type.
MINIMUM_POINTS = 12.0

#: A browser's default: 16px, which is 12pt. Relative units resolve against it.
BASE_POINTS = 12.0

_DECLARATION = re.compile(r"\s*([A-Za-z-]+)\s*:\s*([^;]+?)\s*(?:;|$)")
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.DOTALL)
_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_HEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FUNCTIONAL = re.compile(r"^rgba?\(([^)]*)\)$", re.IGNORECASE)
_LENGTH = re.compile(r"^(-?\d+(?:\.\d+)?)\s*(pt|px|em|rem|%|in|cm|mm|pc)?$", re.IGNORECASE)

#: The named colours that actually turn up in course HTML. Deliberately not the full CSS list: an
#: unrecognised name is reported as unparsed, which is visible, rather than guessed at.
NAMED_COLOURS: Mapping[str, str] = {
    "aqua": "#00ffff",
    "black": "#000000",
    "blue": "#0000ff",
    "brown": "#a52a2a",
    "cyan": "#00ffff",
    "darkblue": "#00008b",
    "darkgray": "#a9a9a9",
    "darkgreen": "#006400",
    "darkgrey": "#a9a9a9",
    "darkred": "#8b0000",
    "fuchsia": "#ff00ff",
    "gold": "#ffd700",
    "gray": "#808080",
    "green": "#008000",
    "grey": "#808080",
    "indigo": "#4b0082",
    "lightblue": "#add8e6",
    "lightgray": "#d3d3d3",
    "lightgreen": "#90ee90",
    "lightgrey": "#d3d3d3",
    "lime": "#00ff00",
    "magenta": "#ff00ff",
    "maroon": "#800000",
    "navy": "#000080",
    "olive": "#808000",
    "orange": "#ffa500",
    "pink": "#ffc0cb",
    "purple": "#800080",
    "red": "#ff0000",
    "silver": "#c0c0c0",
    "teal": "#008080",
    "violet": "#ee82ee",
    "white": "#ffffff",
    "yellow": "#ffff00",
}

#: Generic families and the common faces in each. A course body names a face, not a family, so the
#: mapping has to be by name. An unlisted face is reported as unrecognised rather than as serif.
SERIF_FACES = frozenset(
    {
        "book antiqua",
        "bookman old style",
        "cambria",
        "constantia",
        "garamond",
        "georgia",
        "palatino",
        "palatino linotype",
        "serif",
        "times",
        "times new roman",
    }
)

SANS_FACES = frozenset(
    {
        "arial",
        "calibri",
        "helvetica",
        "helvetica neue",
        "lato",
        "lato extended",
        "open sans",
        "sans-serif",
        "segoe ui",
        "system-ui",
        "tahoma",
        "trebuchet ms",
        "verdana",
    }
)

#: Faces that are neither serif nor sans and are hard to read at body sizes.
DECORATIVE_FACES = frozenset(
    {
        "brush script mt",
        "comic sans ms",
        "copperplate",
        "cursive",
        "fantasy",
        "impact",
        "lucida handwriting",
        "monospace",
        "papyrus",
        "script",
    }
)


@dataclass(frozen=True, slots=True)
class Colour:
    """An opaque sRGB colour. Alpha is recorded but never composited."""

    red: int
    green: int
    blue: int
    alpha: float = 1.0
    #: The value exactly as it was written, so a finding quotes the course rather than a rounding.
    written: str = ""

    @property
    def hex(self) -> str:
        return f"#{self.red:02x}{self.green:02x}{self.blue:02x}"

    @property
    def luminance(self) -> float:
        """WCAG relative luminance."""

        def channel(value: int) -> float:
            fraction = value / 255
            if fraction <= 0.04045:
                return fraction / 12.92
            return ((fraction + 0.055) / 1.055) ** 2.4

        return (
            0.2126 * channel(self.red) + 0.7152 * channel(self.green) + 0.0722 * channel(self.blue)
        )


def parse_colour(value: str | None) -> Colour | None:
    """A CSS colour value, or ``None`` when this module does not recognise the form."""
    if not value:
        return None
    written = value.strip()
    text = written.lower()
    if text in NAMED_COLOURS:
        return _from_hex(NAMED_COLOURS[text], written)
    if text in {"transparent", "inherit", "initial", "unset", "currentcolor"}:
        return None
    if _HEX.match(text):
        return _from_hex(text, written)
    functional = _FUNCTIONAL.match(text)
    if functional:
        parts = [part.strip() for part in re.split(r"[,\s/]+", functional.group(1)) if part.strip()]
        if len(parts) < 3:
            return None
        try:
            channels = [_channel(part) for part in parts[:3]]
        except ValueError:
            return None
        alpha = 1.0
        if len(parts) >= 4:
            try:
                alpha = float(parts[3].rstrip("%")) / (100 if parts[3].endswith("%") else 1)
            except ValueError:
                alpha = 1.0
        return Colour(*channels, alpha=alpha, written=written)
    return None


def _channel(part: str) -> int:
    if part.endswith("%"):
        return max(0, min(255, round(float(part[:-1]) * 255 / 100)))
    return max(0, min(255, round(float(part))))


def _from_hex(text: str, written: str) -> Colour:
    digits = text.lstrip("#")
    if len(digits) in {3, 4}:
        digits = "".join(digit * 2 for digit in digits)
    red, green, blue = (int(digits[index : index + 2], 16) for index in (0, 2, 4))
    alpha = int(digits[6:8], 16) / 255 if len(digits) == 8 else 1.0
    return Colour(red, green, blue, alpha=alpha, written=written or text)


def contrast_ratio(foreground: Colour, background: Colour) -> float:
    """WCAG contrast ratio, between 1.0 and 21.0."""
    lighter, darker = sorted((foreground.luminance, background.luminance), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def required_ratio(points: float | None, *, bold: bool) -> float:
    """The ratio this text has to reach, which depends on how large it is."""
    if points is None:
        return NORMAL_TEXT_RATIO
    if points >= LARGE_POINTS or (bold and points >= LARGE_BOLD_POINTS):
        return LARGE_TEXT_RATIO
    return NORMAL_TEXT_RATIO


def declarations(style: str | None) -> dict[str, str]:
    """``"color: red; font-size: 9pt"`` -> ``{"color": "red", "font-size": "9pt"}``."""
    if not style:
        return {}
    return {
        name.lower(): value.strip()
        for name, value in _DECLARATION.findall(_COMMENT.sub(" ", style))
    }


@dataclass(frozen=True, slots=True)
class Rule:
    selector: str
    declarations: Mapping[str, str]


@dataclass
class Stylesheet:
    """The ``<style>`` blocks of one body, matched by tag, class and id only.

    That is a deliberate floor rather than a CSS engine. Descendant combinators, pseudo-classes and
    media queries are counted in :attr:`unsupported` and not applied, so what this cannot see is a
    number in the result rather than a quiet gap. Course bodies overwhelmingly carry flat rules;
    when they do not, the count says so.
    """

    rules: tuple[Rule, ...] = ()
    unsupported: tuple[str, ...] = ()

    @classmethod
    def parse(cls, blocks: Iterable[str]) -> Stylesheet:
        rules: list[Rule] = []
        unsupported: list[str] = []
        for block in blocks:
            text = _COMMENT.sub(" ", block)
            for raw_selector, body in _RULE.findall(text):
                parsed = declarations(body)
                if not parsed:
                    continue
                for selector in (part.strip() for part in raw_selector.split(",")):
                    if not selector:
                        continue
                    if _simple_selector(selector) is None:
                        unsupported.append(selector)
                        continue
                    rules.append(Rule(selector=selector, declarations=parsed))
        return cls(rules=tuple(rules), unsupported=tuple(unsupported))

    def matching(self, tag: str, attrs: Mapping[str, str]) -> dict[str, str]:
        """Declarations from every rule that matches, later rules winning, as CSS does."""
        classes = {value.lower() for value in (attrs.get("class") or "").split() if value}
        element_id = (attrs.get("id") or "").lower()
        merged: dict[str, str] = {}
        for rule in self.rules:
            kind, name = _simple_selector(rule.selector) or ("", "")
            if (
                (kind == "tag" and name == tag)
                or (kind == "class" and name in classes)
                or (kind == "id" and name == element_id)
            ):
                merged.update(rule.declarations)
        return merged


def _simple_selector(selector: str) -> tuple[str, str] | None:
    """``.warning`` -> ``("class", "warning")``. ``None`` for anything compound."""
    text = selector.strip().lower()
    if not text or any(character in text for character in " >+~:[()"):
        return None
    if text.startswith("."):
        return ("class", text[1:]) if text[1:] else None
    if text.startswith("#"):
        return ("id", text[1:]) if text[1:] else None
    if text.isalpha():
        return ("tag", text)
    return None


def font_points(value: str | None) -> float | None:
    """A CSS length in points, or ``None`` when the unit is one this module cannot resolve.

    Relative units resolve against a 16px root, which is what a browser uses when nothing overrides
    it. That is an assumption and it is why a size finding quotes the value as written alongside
    the number it resolved to.
    """
    if not value:
        return None
    text = value.strip().lower()
    keywords = {
        "xx-small": 6.75,
        "x-small": 7.5,
        "small": 9.75,
        "medium": 12.0,
        "large": 13.5,
        "x-large": 18.0,
        "xx-large": 27.0,
    }
    if text in keywords:
        return keywords[text]
    match = _LENGTH.match(text)
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "px").lower()
    factors = {
        "pt": 1.0,
        "px": 0.75,
        "em": BASE_POINTS,
        "rem": BASE_POINTS,
        "%": BASE_POINTS / 100,
        "in": 72.0,
        "cm": 72.0 / 2.54,
        "mm": 7.2 / 2.54,
        "pc": 12.0,
    }
    return amount * factors[unit]


@dataclass(frozen=True, slots=True)
class Typeface:
    """What a ``font-family`` declaration names, and which of the three kinds it is."""

    written: str
    first: str
    kind: str  # "sans" | "serif" | "decorative" | "unrecognised"


def typeface(value: str | None) -> Typeface | None:
    """Classify a ``font-family`` list by its first usable face."""
    if not value:
        return None
    faces = [face.strip().strip("'\"").lower() for face in value.split(",")]
    faces = [face for face in faces if face]
    if not faces:
        return None
    for face in faces:
        if face in SANS_FACES:
            return Typeface(written=value.strip(), first=face, kind="sans")
        if face in SERIF_FACES:
            return Typeface(written=value.strip(), first=face, kind="serif")
        if face in DECORATIVE_FACES:
            return Typeface(written=value.strip(), first=face, kind="decorative")
    return Typeface(written=value.strip(), first=faces[0], kind="unrecognised")


@dataclass
class StyleContext:
    """Declarations in force on one element, merged through its ancestors.

    Inheritance is applied for the properties that inherit in CSS and the ones these detectors
    read — colour, background, family, size, weight. It is not a rendering engine and is not
    presented as one.
    """

    colour: Colour | None = None
    background: Colour | None = None
    background_written: str | None = None
    points: float | None = None
    points_written: str | None = None
    face: Typeface | None = None
    bold: bool = False
    #: Declarations this context could not resolve, kept so the gap is countable.
    unresolved: tuple[str, ...] = field(default_factory=tuple)

    def merged_with(self, declared: Mapping[str, str], *, bold_tag: bool = False) -> StyleContext:
        unresolved = list(self.unresolved)
        colour = self.colour
        if "color" in declared:
            parsed = parse_colour(declared["color"])
            if parsed is None:
                unresolved.append(f"color: {declared['color']}")
            else:
                colour = parsed
        background = self.background
        background_written = self.background_written
        for name in ("background-color", "background"):
            if name in declared:
                parsed = parse_colour(declared[name].split()[0] if declared[name] else "")
                if parsed is not None:
                    background = parsed
                    background_written = declared[name]
                break
        points = self.points
        points_written = self.points_written
        if "font-size" in declared:
            resolved = font_points(declared["font-size"])
            if resolved is None:
                unresolved.append(f"font-size: {declared['font-size']}")
            else:
                points = resolved
                points_written = declared["font-size"]
        face = self.face
        if "font-family" in declared:
            face = typeface(declared["font-family"]) or face
        weight = declared.get("font-weight", "").strip().lower()
        bold = self.bold or bold_tag
        if weight:
            bold = weight in {"bold", "bolder"} or (weight.isdigit() and int(weight) >= 600)
        return StyleContext(
            colour=colour,
            background=background,
            background_written=background_written,
            points=points,
            points_written=points_written,
            face=face,
            bold=bold,
            unresolved=tuple(unresolved),
        )


def legacy_font_declarations(attrs: Mapping[str, str]) -> dict[str, str]:
    """``<font face="Papyrus" size="2" color="red">`` as the declarations it stands for.

    The element has been obsolete for two decades and is still all over courses imported from
    older systems. Translating it here means every style-reading detector sees it without each one
    knowing the HTML 3.2 size scale.
    """
    scale = {"1": "7.5pt", "2": "10pt", "3": "12pt", "4": "13.5pt", "5": "18pt", "6": "24pt"}
    declared: dict[str, str] = {}
    if attrs.get("face"):
        declared["font-family"] = attrs["face"]
    if attrs.get("color"):
        declared["color"] = attrs["color"]
    size = (attrs.get("size") or "").strip()
    if size in scale:
        declared["font-size"] = scale[size]
    return declared


def summarise_ratio(ratio: float) -> str:
    return f"{ratio:.2f}:1"


__all__ = [
    "ASSUMED_BACKGROUND",
    "BASE_POINTS",
    "DECORATIVE_FACES",
    "LARGE_BOLD_POINTS",
    "LARGE_POINTS",
    "LARGE_TEXT_RATIO",
    "MINIMUM_POINTS",
    "NAMED_COLOURS",
    "NORMAL_TEXT_RATIO",
    "SANS_FACES",
    "SERIF_FACES",
    "Colour",
    "Rule",
    "StyleContext",
    "Stylesheet",
    "Typeface",
    "contrast_ratio",
    "declarations",
    "font_points",
    "legacy_font_declarations",
    "parse_colour",
    "required_ratio",
    "summarise_ratio",
    "typeface",
]
