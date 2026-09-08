"""Finding embedded media inside an artifact's body.

An embed is one of the eight normalized kinds (SPEC §04), so it is extracted here rather than
rediscovered by each detector that cares — D27 wants caption tracks, D39 wants an accompanying
hyperlink, D42 wants fixed pixel widths, D43 wants legacy players. Extracting once means those
four agree about what an embed *is*.

Nothing here judges. It records the tag, the source, the dimensions as written, whether a caption
track element is present, and whether a link to the same source appears in the same body. Whether
any of that is a defect is a detector's business.

The parser is :mod:`html.parser` from the standard library. Course HTML is malformed in every
interesting way and ``HTMLParser`` is lenient by construction — it never raises on a course body,
which is the property that matters when the alternative is an acquisition that dies on one page.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

from ..model import Artifact, ArtifactKind, Embed, embed_id

#: Elements that embed something rather than describe it.
EMBED_TAGS = frozenset({"iframe", "embed", "object", "video", "audio"})

#: Where an element keeps its source, most specific first.
SOURCE_ATTRIBUTES = ("src", "data", "href")

#: Hostname fragment -> provider name. Recognition, not endorsement: the name is recorded so a
#: finding can say "a YouTube embed on this page" rather than quoting an opaque URL at a reviewer.
PROVIDERS: tuple[tuple[str, str], ...] = (
    ("youtube.com", "youtube"),
    ("youtu.be", "youtube"),
    ("vimeo.com", "vimeo"),
    ("kaltura.com", "kaltura"),
    ("mediaspace", "kaltura"),
    ("panopto.com", "panopto"),
    ("echo360", "echo360"),
    ("h5p", "h5p"),
    ("soundcloud.com", "soundcloud"),
    ("spotify.com", "spotify"),
    ("docs.google.com", "google-docs"),
    ("drive.google.com", "google-drive"),
    ("instructuremedia.com", "canvas-studio"),
)


@dataclass
class _Open:
    """An embed element the parser is currently inside."""

    tag: str
    attrs: dict[str, str]
    has_caption_track: bool = False
    depth: int = 0


@dataclass
class _Scan:
    embeds: list[tuple[str, dict[str, str], bool]] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


class _EmbedParser(HTMLParser):
    #: ``HTMLParser`` treats ``iframe``, ``noembed``, ``noframes`` and ``xmp`` as CDATA elements,
    #: which means an **unclosed** ``<iframe>`` swallows the rest of the document: every later
    #: embed on that page silently disappears. That is precisely the silent under-detection SPEC
    #: §11 is about, and a course body is exactly the kind of HTML that has an unclosed tag in it.
    #:
    #: An iframe's content is fallback text for browsers that cannot render one, so parsing it as
    #: ordinary markup loses nothing and costs nothing. ``script`` and ``style`` stay CDATA — their
    #: content really does contain ``<`` that is not markup.
    CDATA_CONTENT_ELEMENTS = ("script", "style")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.result = _Scan()
        self._stack: list[_Open] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = {name.lower(): (value or "") for name, value in attrs}
        lowered = tag.lower()
        if lowered == "a":
            href = mapping.get("href", "").strip()
            if href:
                self.result.links.append(href)
            return
        if lowered == "source":
            # ``<video><source src=...></video>`` is the ordinary form, so the child supplies the
            # parent's source when the parent carries none of its own.
            value = mapping.get("src", "").strip()
            if value and self._stack and not _source_of(self._stack[-1].attrs):
                self._stack[-1].attrs["src"] = value
            return
        if lowered == "track":
            if self._stack and mapping.get("kind", "").lower() in {"captions", "subtitles"}:
                self._stack[-1].has_caption_track = True
            return
        if lowered in EMBED_TAGS:
            self._stack.append(_Open(tag=lowered, attrs=mapping))
            # ``iframe`` and ``embed`` are void in practice inside course HTML even though the
            # spec says otherwise, so an unclosed one still has to produce an embed.
            if lowered in {"embed"}:
                self._close(lowered)
        elif self._stack:
            self._stack[-1].depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() in EMBED_TAGS:
            self._close(tag.lower())

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in EMBED_TAGS:
            self._close(lowered)
        elif self._stack and self._stack[-1].depth > 0:
            self._stack[-1].depth -= 1

    def _close(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index].tag == tag:
                element = self._stack.pop(index)
                self.result.embeds.append((element.tag, element.attrs, element.has_caption_track))
                return

    def close(self) -> None:
        super().close()
        while self._stack:
            element = self._stack.pop()
            self.result.embeds.append((element.tag, element.attrs, element.has_caption_track))


def scan_embeds(container: Artifact) -> tuple[Embed, ...]:
    """Every embed in ``container``'s body, in document order.

    Ordinals are assigned in document order because an embed has no identity of its own; the id
    always names its container as well, so a finding still says which page to open.
    """
    if not container.body_html:
        return ()
    parser = _EmbedParser()
    parser.feed(container.body_html)
    parser.close()
    scan = parser.result

    found: list[Embed] = []
    for ordinal, (tag, attrs, has_track) in enumerate(scan.embeds):
        source = _source_of(attrs)
        title = attrs.get("title", "").strip() or f"{tag} embed on {container.title}"
        found.append(
            Embed(
                id=embed_id(container.id, ordinal),
                title=title,
                canvas_path=container.canvas_path,
                link_exact=container.link_exact,
                published=container.published,
                module_path=container.module_path,
                container_id=container.id,
                tag=tag,
                src=source or None,
                provider=provider_of(source),
                width=attrs.get("width") or None,
                height=attrs.get("height") or None,
                has_caption_track=has_track,
                accompanied_by_link=accompanies(source, scan.links),
            )
        )
    return tuple(found)


def provider_of(source: str | None) -> str | None:
    """A recognised provider name, or ``None`` when the source names none."""
    if not source:
        return None
    host = (urlsplit(source).hostname or source).lower()
    for fragment, name in PROVIDERS:
        if fragment in host or fragment in source.lower():
            return name
    return None


def _source_of(attrs: dict[str, str]) -> str:
    for name in SOURCE_ATTRIBUTES:
        value = attrs.get(name, "").strip()
        if value:
            return value
    return ""


#: Path and query words that identify a route rather than a resource. Without these excluded,
#: every YouTube embed on a page "matches" every YouTube link on it.
_STRUCTURAL_TOKENS = frozenset(
    {"embed", "watch", "video", "videos", "media", "player", "files", "download", "content"}
)

#: Below this length a token is an index or a flag, not a resource identifier.
_MIN_TOKEN = 6


def accompanies(source: str, links: Sequence[str]) -> bool:
    """Whether any of ``links`` points at the same resource as ``source``.

    Comparing whole URLs finds nothing, because Canvas rewrites a ``watch?v=`` link into an
    ``/embed/`` URL when it builds the player: same video, no common path. So the comparison is
    host plus the *resource-identifying tokens* — the path segments and query values that are not
    route words — and a match needs both. That is a heuristic and it is stated as one: D39 reports
    what it examined, and a false negative here reads as "no accompanying link found", which is
    checkable in seconds against the page.
    """
    if not source:
        return False
    source_host, source_tokens = _media_identity(source)
    for link in links:
        link_host, link_tokens = _media_identity(link)
        if source_host != link_host:
            continue
        if source_tokens and link_tokens:
            if source_tokens & link_tokens:
                return True
        elif _bare_path(source) == _bare_path(link):
            return True
    return False


def _media_identity(url: str) -> tuple[str, frozenset[str]]:
    parts = urlsplit(url.strip())
    words: list[str] = [segment for segment in parts.path.split("/") if segment]
    for pair in parts.query.split("&"):
        _, _, value = pair.partition("=")
        if value:
            words.append(value)
    tokens = {
        word.lower()
        for word in words
        if len(word) >= _MIN_TOKEN and word.lower() not in _STRUCTURAL_TOKENS
    }
    return (parts.hostname or "").lower(), frozenset(tokens)


def _bare_path(url: str) -> str:
    return urlsplit(url.strip()).path.rstrip("/").lower()


def embeds_for(artifacts: list[Artifact]) -> tuple[Embed, ...]:
    """Scan a list of body-carrying artifacts, skipping embeds themselves."""
    found: list[Embed] = []
    for artifact in artifacts:
        if artifact.kind is ArtifactKind.EMBED:
            continue
        found.extend(scan_embeds(artifact))
    return tuple(found)


__all__ = [
    "EMBED_TAGS",
    "PROVIDERS",
    "accompanies",
    "embeds_for",
    "provider_of",
    "scan_embeds",
]
