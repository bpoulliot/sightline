"""Reading structure out of the three document formats a course actually attaches.

PDF, DOCX and PPTX, with the standard library and :mod:`defusedxml` and nothing else. Two reasons
the dependency list stays where it is. The obvious one is that a course attachment is a file
somebody else wrote, and every parser added here is another parser pointed at untrusted bytes.
The one that decides it is licensing: Sightline is MIT and public, and the PDF library this
project's sibling tools reach for first is AGPL. A dependency that would force a licence change
on the whole repository is not a convenience.

**What this cannot read, it says.** Every probe returns a report carrying ``readable`` and, when
false, the reason. An encrypted PDF, a legacy binary ``.doc``, a PDF whose streams use a filter
this module does not implement — each comes back as an explicit "not determined", which the
detector turns into an :class:`~sightline.detect.base.Undetermined` record. None of them comes back
as an accessible document, and that distinction is the entire reason this module reports rather
than returns booleans.

**The PDF probe is structural, not a renderer.** It answers two questions — is there a tag tree,
and is there a text layer — by decompressing the file's streams and looking for the objects that
would have to be there. That is enough for both, it is stated as what it is, and where a file
defeats it the report says so instead of guessing.
"""

from __future__ import annotations

import re
import zipfile
import zlib
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from defusedxml import ElementTree as DefusedElementTree

#: Total decompressed bytes examined in one PDF. A stream that expands past this is not read
#: further: a course attachment is not worth an unbounded allocation.
MAX_INFLATED_BYTES = 64 * 1024 * 1024

#: Same idea for an Office file, which is a zip archive and can therefore claim any size at all.
MAX_ENTRY_BYTES = 16 * 1024 * 1024

_PDF_HEADER = re.compile(rb"^%PDF-\d\.\d")
_STREAM = re.compile(rb"stream\r?\n?(.*?)endstream", re.DOTALL)
_STRUCT_TREE = re.compile(rb"/StructTreeRoot")
_MARKED = re.compile(rb"/Marked\s+true", re.IGNORECASE)
_PAGE_OBJECT = re.compile(rb"/Type\s*/Page(?![sR])")
_FONT = re.compile(rb"/Font\b")
_SHOW_TEXT = re.compile(rb"(?:\)\s*Tj|\]\s*TJ|BT\b)")
_IMAGE_XOBJECT = re.compile(rb"/Subtype\s*/Image")
_ENCRYPT = re.compile(rb"/Encrypt\b")
_LANG = re.compile(rb"/Lang\s*\(")


@dataclass(frozen=True, slots=True)
class PdfReport:
    """What could be read out of a PDF's structure."""

    readable: bool
    reason: str | None = None
    tagged: bool = False
    marked: bool = False
    has_text_layer: bool = False
    has_language: bool = False
    pages: int = 0
    images: int = 0
    encrypted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "readable": self.readable,
            "reason": self.reason,
            "tagged": self.tagged,
            "marked": self.marked,
            "has_text_layer": self.has_text_layer,
            "has_language": self.has_language,
            "pages": self.pages,
            "images": self.images,
            "encrypted": self.encrypted,
        }


def inspect_pdf(data: bytes) -> PdfReport:
    """Probe a PDF for a tag tree and a text layer."""
    if not data:
        return PdfReport(readable=False, reason="the file is empty")
    if not _PDF_HEADER.match(data[:16]):
        return PdfReport(
            readable=False,
            reason="the file does not begin with a PDF header, so it is not a PDF",
        )
    if _ENCRYPT.search(data):
        return PdfReport(
            readable=False,
            encrypted=True,
            reason=(
                "the PDF is encrypted, so its structure cannot be read without the password "
                "that protects it"
            ),
        )

    inflated = _inflate_streams(data)
    corpus = data + b"".join(inflated)
    pages = len(_PAGE_OBJECT.findall(corpus))
    has_font = bool(_FONT.search(corpus))
    shows_text = any(_SHOW_TEXT.search(chunk) for chunk in inflated) or bool(
        _SHOW_TEXT.search(data)
    )

    if pages == 0 and not inflated:
        return PdfReport(
            readable=False,
            reason=(
                "no page object was found and no stream in the file could be decompressed, so "
                "this PDF uses a filter Sightline does not read"
            ),
        )
    return PdfReport(
        readable=True,
        tagged=bool(_STRUCT_TREE.search(corpus)),
        marked=bool(_MARKED.search(corpus)),
        has_text_layer=has_font and shows_text,
        has_language=bool(_LANG.search(corpus)),
        pages=pages,
        images=len(_IMAGE_XOBJECT.findall(corpus)),
    )


def _inflate_streams(data: bytes) -> list[bytes]:
    """Every stream in the file that inflates, up to :data:`MAX_INFLATED_BYTES` in total.

    A stream that does not inflate is skipped rather than reported: a PDF mixes filters, and one
    DCT-encoded image says nothing about whether the document has a tag tree.
    """
    inflated: list[bytes] = []
    budget = MAX_INFLATED_BYTES
    for match in _STREAM.finditer(data):
        if budget <= 0:
            break
        raw = match.group(1)
        try:
            chunk = zlib.decompressobj().decompress(raw, budget)
        except zlib.error:
            continue
        if chunk:
            inflated.append(chunk)
            budget -= len(chunk)
    return inflated


@dataclass(frozen=True, slots=True)
class OfficeReport:
    """What could be read out of a Word or PowerPoint file."""

    readable: bool
    reason: str | None = None
    #: DOCX: whether any paragraph uses a built-in heading style.
    uses_heading_styles: bool = False
    heading_styles: tuple[str, ...] = ()
    #: DOCX: the default typeface and size the document's own styles declare.
    default_face: str | None = None
    default_points: float | None = None
    tables: int = 0
    tables_with_header_rows: int = 0
    images: int = 0
    images_without_alt: int = 0
    #: PPTX.
    slides: int = 0
    slide_titles: tuple[str, ...] = ()
    slides_without_title: tuple[int, ...] = ()
    duplicate_titles: tuple[str, ...] = ()
    slides_advancing_automatically: tuple[int, ...] = ()
    slides_off_layout: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "readable": self.readable,
            "reason": self.reason,
            "uses_heading_styles": self.uses_heading_styles,
            "heading_styles": list(self.heading_styles),
            "default_face": self.default_face,
            "default_points": self.default_points,
            "tables": self.tables,
            "tables_with_header_rows": self.tables_with_header_rows,
            "images": self.images,
            "images_without_alt": self.images_without_alt,
            "slides": self.slides,
            "slide_titles": list(self.slide_titles),
            "slides_without_title": list(self.slides_without_title),
            "duplicate_titles": list(self.duplicate_titles),
            "slides_advancing_automatically": list(self.slides_advancing_automatically),
            "slides_off_layout": list(self.slides_off_layout),
        }


@dataclass
class _Archive:
    """An Office file's zip container, with each entry read at most once and capped."""

    zf: zipfile.ZipFile
    cache: dict[str, bytes | None] = field(default_factory=dict)

    def read(self, name: str) -> bytes | None:
        if name not in self.cache:
            try:
                info = self.zf.getinfo(name)
            except KeyError:
                self.cache[name] = None
            else:
                self.cache[name] = None if info.file_size > MAX_ENTRY_BYTES else self.zf.read(name)
        return self.cache[name]

    def names(self, prefix: str, suffix: str) -> list[str]:
        return sorted(
            name for name in self.zf.namelist() if name.startswith(prefix) and name.endswith(suffix)
        )


def inspect_docx(data: bytes) -> OfficeReport:
    """Probe a Word document for heading styles, table headers, image alt text and default type."""
    archive = _open_office(data, marker="word/document.xml", kind="Word document")
    if isinstance(archive, OfficeReport):
        return archive

    body = archive.read("word/document.xml")
    if body is None:
        return OfficeReport(readable=False, reason="word/document.xml could not be read")
    try:
        root = _parse_xml(body)
    except ValueError as exc:
        return OfficeReport(readable=False, reason=str(exc))

    styles = [
        _attr(node, "val")
        for node in root.iter()
        if _local(node.tag) == "pStyle" and _attr(node, "val")
    ]
    headings = sorted({name for name in styles if name and name.lower().startswith("heading")})

    tables = [node for node in root.iter() if _local(node.tag) == "tbl"]
    with_header = sum(
        1 for table in tables if any(_local(node.tag) == "tblHeader" for node in table.iter())
    )

    pictures = [node for node in root.iter() if _local(node.tag) == "docPr"]
    without_alt = sum(1 for node in pictures if not (node.get("descr") or "").strip())

    face, points = _docx_defaults(archive)
    return OfficeReport(
        readable=True,
        uses_heading_styles=bool(headings),
        heading_styles=tuple(headings),
        default_face=face,
        default_points=points,
        tables=len(tables),
        tables_with_header_rows=with_header,
        images=len(pictures),
        images_without_alt=without_alt,
    )


def _docx_defaults(archive: _Archive) -> tuple[str | None, float | None]:
    raw = archive.read("word/styles.xml")
    if raw is None:
        return None, None
    try:
        root = _parse_xml(raw)
    except ValueError:
        return None, None
    # The document's *defaults* live under ``w:docDefaults``. Scanning the whole file would take
    # whichever named style happened to be serialized first, which is a different fact entirely.
    scope = next((node for node in root.iter() if _local(node.tag) == "docDefaults"), root)
    face: str | None = None
    points: float | None = None
    for node in scope.iter():
        name = _local(node.tag)
        if name == "rFonts" and face is None:
            face = node.get(_w("ascii")) or node.get("ascii")
        if name == "sz" and points is None:
            value = _attr(node, "val")
            if value and value.isdigit():
                # Word stores a size in half-points, which is the single most common way to get
                # this number wrong by a factor of two.
                points = int(value) / 2
    return face, points


def inspect_pptx(data: bytes) -> OfficeReport:
    """Probe a PowerPoint deck for slide titles, layout use and automatic transitions."""
    archive = _open_office(data, marker="ppt/presentation.xml", kind="PowerPoint file")
    if isinstance(archive, OfficeReport):
        return archive

    slide_names = [
        name for name in archive.names("ppt/slides/slide", ".xml") if not name.endswith(".rels")
    ]
    slide_names.sort(key=_slide_number)
    titles: list[str] = []
    missing: list[int] = []
    automatic: list[int] = []
    off_layout: list[int] = []
    images = 0
    images_without_alt = 0

    for position, name in enumerate(slide_names, start=1):
        raw = archive.read(name)
        if raw is None:
            continue
        try:
            root = _parse_xml(raw)
        except ValueError:
            continue
        title = _slide_title(root)
        if title:
            titles.append(title)
        else:
            missing.append(position)
        if _advances_automatically(root):
            automatic.append(position)
        placeholders = [node for node in root.iter() if _local(node.tag) == "ph"]
        if not placeholders:
            # Every shape drawn free-hand rather than into the layout's placeholders. That is what
            # makes a deck unreadable in order by a screen reader.
            off_layout.append(position)
        for node in root.iter():
            if _local(node.tag) == "cNvPr" and _has_picture_parent(root, node):
                images += 1
                if not (node.get("descr") or "").strip():
                    images_without_alt += 1

    seen: dict[str, int] = {}
    for title in titles:
        key = title.strip().lower()
        seen[key] = seen.get(key, 0) + 1
    duplicates = tuple(sorted(title for title, count in seen.items() if count > 1))

    return OfficeReport(
        readable=True,
        slides=len(slide_names),
        slide_titles=tuple(titles),
        slides_without_title=tuple(missing),
        duplicate_titles=duplicates,
        slides_advancing_automatically=tuple(automatic),
        slides_off_layout=tuple(off_layout),
        images=images,
        images_without_alt=images_without_alt,
    )


def _open_office(data: bytes, *, marker: str, kind: str) -> _Archive | OfficeReport:
    if not data:
        return OfficeReport(readable=False, reason="the file is empty")
    if data[:2] != b"PK":
        return OfficeReport(
            readable=False,
            reason=(
                f"the file is not a zip container, so it is not a modern {kind}. A pre-2007 "
                "binary format carries no accessibility structure Sightline can read"
            ),
        )
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        return OfficeReport(readable=False, reason=f"the file could not be opened: {exc}")
    if marker not in zf.namelist():
        return OfficeReport(
            readable=False, reason=f"the archive holds no {marker}, so it is not a {kind}"
        )
    return _Archive(zf=zf)


def _parse_xml(raw: bytes) -> Any:
    try:
        return DefusedElementTree.fromstring(raw)
    except Exception as exc:  # defusedxml raises several unrelated types
        raise ValueError(f"the XML inside the file could not be parsed: {exc}") from exc


def _local(tag: Any) -> str:
    text = str(tag)
    return text.rpartition("}")[2]


def _attr(node: Any, name: str) -> str | None:
    for key, value in node.attrib.items():
        if _local(key) == name:
            return str(value)
    return None


def _w(name: str) -> str:
    return f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}{name}"


def _slide_number(name: str) -> tuple[int, str]:
    digits = "".join(character for character in name.rpartition("/")[2] if character.isdigit())
    return (int(digits) if digits else 0, name)


def _slide_title(root: Any) -> str | None:
    for shape in root.iter():
        if _local(shape.tag) != "sp":
            continue
        placeholder = next((node for node in shape.iter() if _local(node.tag) == "ph"), None)
        if placeholder is None:
            continue
        if (placeholder.get("type") or "").lower() not in {"title", "ctrtitle"}:
            continue
        text = "".join(node.text or "" for node in shape.iter() if _local(node.tag) == "t").strip()
        if text:
            return text
    return None


def _advances_automatically(root: Any) -> bool:
    for node in root.iter():
        if _local(node.tag) != "transition":
            continue
        if _attr(node, "advTm"):
            return True
        advance = _attr(node, "advClick")
        if advance is not None and advance.lower() in {"0", "false"}:
            return True
    return False


def _has_picture_parent(root: Any, target: Any) -> bool:
    for node in root.iter():
        if _local(node.tag) != "pic":
            continue
        if any(child is target for child in node.iter()):
            return True
    return False


__all__ = [
    "MAX_ENTRY_BYTES",
    "MAX_INFLATED_BYTES",
    "OfficeReport",
    "PdfReport",
    "inspect_docx",
    "inspect_pdf",
    "inspect_pptx",
]
