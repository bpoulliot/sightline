"""A reader for the PDFs this project writes: the text on each page, and the links on it.

Nothing in the dependency set parses a PDF, and item 14 adds only the writer, so the suite reads
the report back with this: enough of the format to recover what WeasyPrint writes — indirect
objects, compressed object streams, Flate streams, Type 0 fonts with a ``ToUnicode`` map, text
shown with ``Tj`` and ``TJ``, and ``/URI`` link annotations. It is not a general PDF parser and
does not pretend to be.

A reader that silently returns nothing would let every test built on it pass. So it refuses: a
page with no text, a font with no ``ToUnicode`` map, a glyph the map does not cover — each raises
rather than being skipped — and ``tests/test_report.py`` checks it against a document whose text
is known, in both directions, before trusting it on the report.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass, field

_OBJECT = re.compile(rb"(\d+) 0 obj\b")
_REF = re.compile(rb"(\d+) 0 R")


class PdfReadError(ValueError):
    """The file holds something this reader does not understand. Never silently skipped."""


@dataclass
class Page:
    text: str
    links: list[str] = field(default_factory=list)


def _literal(data: bytes, start: int) -> tuple[bytes, int]:
    """A ``( … )`` string starting at ``start``, unescaped, and the index after it."""
    out = bytearray()
    depth = 0
    index = start
    while index < len(data):
        char = data[index]
        if char == 0x5C:  # backslash
            nxt = data[index + 1]
            mapped = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
            if nxt in mapped:
                out.append(mapped[nxt])
                index += 2
                continue
            if 0x30 <= nxt <= 0x37:
                digits = re.match(rb"[0-7]{1,3}", data[index + 1 : index + 4])
                assert digits is not None
                out.append(int(digits.group(), 8))
                index += 1 + len(digits.group())
                continue
            out.append(nxt)
            index += 2
            continue
        if char == 0x28:  # (
            depth += 1
            if depth > 1:
                out.append(char)
        elif char == 0x29:  # )
            depth -= 1
            if depth == 0:
                return bytes(out), index + 1
            out.append(char)
        else:
            out.append(char)
        index += 1
    raise PdfReadError("an unterminated string")


class PdfDocument:
    def __init__(self, data: bytes) -> None:
        if not data.startswith(b"%PDF-"):
            raise PdfReadError("not a PDF")
        self.data = data
        #: object number -> (dictionary bytes, stream bytes or None)
        self.objects: dict[int, tuple[bytes, bytes | None]] = {}
        self._read_objects()

    # -- objects ----------------------------------------------------------------------------------

    def _read_objects(self) -> None:
        data = self.data
        position = 0
        while True:
            match = _OBJECT.search(data, position)
            if match is None:
                break
            number = int(match.group(1))
            body_start = match.end()
            stream_at = data.find(b"stream", body_start)
            end_at = data.find(b"endobj", body_start)
            if end_at < 0:
                raise PdfReadError(f"object {number} has no endobj")
            if 0 <= stream_at < end_at:
                head = data[body_start:stream_at]
                length = re.search(rb"/Length (\d+)(?! \d+ R)", head)
                if length is None:
                    raise PdfReadError(f"object {number}: a stream with no direct /Length")
                begin = stream_at + len(b"stream")
                begin += 2 if data[begin : begin + 2] == b"\r\n" else 1
                raw = data[begin : begin + int(length.group(1))]
                if b"/FlateDecode" in head:
                    raw = zlib.decompress(raw)
                self.objects[number] = (head, raw)
                end_at = data.find(b"endobj", begin + int(length.group(1)))
            else:
                self.objects[number] = (data[body_start:end_at], None)
            position = end_at + len(b"endobj")
        # Objects packed into object streams (PDF 1.5+), which is how a compressed file carries
        # every dictionary that is not itself a stream.
        for head, raw in list(self.objects.values()):
            if raw is None or b"/ObjStm" not in head:
                continue
            count = int(re.search(rb"/N (\d+)", head).group(1))  # type: ignore[union-attr]
            first = int(re.search(rb"/First (\d+)", head).group(1))  # type: ignore[union-attr]
            pairs = [int(value) for value in raw[:first].split()]
            offsets = list(zip(pairs[0::2], pairs[1::2], strict=True))
            if len(offsets) != count:
                raise PdfReadError("an object stream whose header does not match its /N")
            for index, (number, offset) in enumerate(offsets):
                end = offsets[index + 1][1] if index + 1 < len(offsets) else len(raw) - first
                self.objects[number] = (raw[first + offset : first + end], None)

    def get(self, number: int) -> tuple[bytes, bytes | None]:
        try:
            return self.objects[number]
        except KeyError:
            raise PdfReadError(f"no object {number}") from None

    def _ref(self, head: bytes, key: bytes) -> int | None:
        match = re.search(re.escape(key) + rb"\s*(\d+) 0 R", head)
        return int(match.group(1)) if match else None

    # -- pages ------------------------------------------------------------------------------------

    def page_numbers(self) -> list[int]:
        catalog = next(
            (head for head, raw in self.objects.values() if raw is None and b"/Catalog" in head),
            None,
        )
        if catalog is None:
            raise PdfReadError("no catalog")
        root = self._ref(catalog, b"/Pages")
        if root is None:
            raise PdfReadError("the catalog names no page tree")
        return self._kids(root)

    def _kids(self, number: int) -> list[int]:
        head, _ = self.get(number)
        if re.search(rb"/Type\s*/Page\b(?!s)", head):
            return [number]
        kids = re.search(rb"/Kids\s*\[([^\]]*)\]", head)
        if kids is None:
            raise PdfReadError(f"page tree node {number} has no /Kids")
        out: list[int] = []
        for ref in _REF.finditer(kids.group(1)):
            out.extend(self._kids(int(ref.group(1))))
        return out

    def pages(self) -> list[Page]:
        return [self._page(number) for number in self.page_numbers()]

    def _page(self, number: int) -> Page:
        head, _ = self.get(number)
        fonts = self._fonts(head)
        contents = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+ 0 R)", head)
        if contents is None:
            raise PdfReadError(f"page object {number} has no /Contents")
        stream = b"".join(
            self.get(int(ref.group(1)))[1] or b"" for ref in _REF.finditer(contents.group(1))
        )
        text = _show(stream, fonts)
        links: list[str] = []
        annots = re.search(rb"/Annots\s*\[([^\]]*)\]", head)
        if annots:
            for ref in _REF.finditer(annots.group(1)):
                annot, _ = self.get(int(ref.group(1)))
                at = annot.find(b"/URI (")
                if at >= 0:
                    links.append(_literal(annot, at + len(b"/URI "))[0].decode("latin-1"))
        return Page(text=text, links=links)

    def _fonts(self, page_head: bytes) -> dict[bytes, dict[int, str]]:
        resources = self._ref(page_head, b"/Resources")
        head = self.get(resources)[0] if resources is not None else page_head
        font_ref = self._ref(head, b"/Font")
        if font_ref is not None:
            fonts_head = self.get(font_ref)[0]
        else:
            inline = re.search(rb"/Font\s*<<(.*?)>>", head, re.S)
            fonts_head = inline.group(1) if inline else b""
        maps: dict[bytes, dict[int, str]] = {}
        for match in re.finditer(rb"/([^\s/<>\[\]()]+)\s+(\d+) 0 R", fonts_head):
            font_head, _ = self.get(int(match.group(2)))
            unicode_ref = self._ref(font_head, b"/ToUnicode")
            if unicode_ref is None:
                raise PdfReadError(f"font /{match.group(1).decode()} has no ToUnicode map")
            maps[match.group(1)] = _cmap(self.get(unicode_ref)[1] or b"")
        return maps


def _cmap(stream: bytes) -> dict[int, str]:
    out: dict[int, str] = {}
    for block in re.finditer(rb"beginbfchar(.*?)endbfchar", stream, re.S):
        for code, dest in re.findall(rb"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block.group(1)):
            out[int(code, 16)] = bytes.fromhex(dest.decode()).decode("utf-16-be")
    for block in re.finditer(rb"beginbfrange(.*?)endbfrange", stream, re.S):
        for low, high, dest in re.findall(
            rb"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block.group(1)
        ):
            base = int(dest, 16)
            for offset, code in enumerate(range(int(low, 16), int(high, 16) + 1)):
                out[code] = chr(base + offset)
    return out


_TOKEN = re.compile(rb"\[|\]|<[0-9a-fA-F\s]*>|\(|/[^\s/<>\[\]()]+|[-+]?\d*\.?\d+|[A-Za-z'\"*]+")


@dataclass(frozen=True)
class _Shown:
    """A string operand: glyph ids, two bytes each, in a Type 0 font."""

    data: bytes


def _show(stream: bytes, fonts: dict[bytes, dict[int, str]]) -> str:
    """The text a content stream shows, one line per text object (``BT`` … ``ET``)."""
    lines: list[str] = []
    current: list[str] = []
    font: dict[int, str] | None = None
    operands: list[object] = []
    index = 0
    while True:
        match = _TOKEN.search(stream, index)
        if match is None:
            break
        token = match.group()
        index = match.end()
        if token == b"(":
            literal, index = _literal(stream, match.start())
            operands.append(_Shown(literal))
            continue
        if token.startswith(b"<"):
            operands.append(_Shown(bytes.fromhex(re.sub(rb"\s", b"", token[1:-1]).decode())))
            continue
        if token in (b"[", b"]"):
            operands.append(token)
            continue
        if token.startswith(b"/") or re.fullmatch(rb"[-+]?\d*\.?\d+", token):
            operands.append(token)
            continue
        # an operator
        if token == b"Tf":
            name = operands[-2]
            assert isinstance(name, bytes)
            key = name[1:]
            if key not in fonts:
                raise PdfReadError(f"text set in /{key.decode()}, a font the page does not list")
            font = fonts[key]
        elif token in (b"Tj", b"TJ"):
            if font is None:
                raise PdfReadError("text shown before any font was set")
            for operand in operands:
                if isinstance(operand, _Shown):
                    for at in range(0, len(operand.data), 2):
                        glyph = int.from_bytes(operand.data[at : at + 2], "big")
                        if glyph not in font:
                            raise PdfReadError(f"glyph {glyph} is not in the font's ToUnicode map")
                        current.append(font[glyph])
        elif token == b"ET":
            lines.append("".join(current))
            current = []
        operands = []
    if current:
        lines.append("".join(current))
    return "\n".join(lines)


def read_pdf(data: bytes) -> list[Page]:
    """Every page's text and links, in page order. Raises rather than returning nothing."""
    pages = PdfDocument(data).pages()
    if not pages:
        raise PdfReadError("the document has no pages")
    return pages


__all__ = ["Page", "PdfDocument", "PdfReadError", "read_pdf"]
