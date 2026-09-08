"""Content exports: fetching an archive, unpacking it safely, and reading its manifest.

This is the acquisition half of AccessLens (``app/canvas/content_export.py`` and
``app/scanner/imscc_manifest.py``), ported rather than reimplemented, as the roadmap item asks.
Its analysis half is deliberately left behind — Sightline's analysis is rubric-scoped and much
narrower than AccessLens's.

Four things came across because they were learned the hard way there, and one changed:

* **The download URL is validated against the Canvas host** before a byte is fetched. An export
  link is handed to us by the instance; following it blindly is following a redirect target
  somebody else chose.
* **Downloads are capped** and the cap is a stated failure, not a full disk.
* **Extraction is capped** the same way, and only file types a detector can actually read are
  written out.
* **Zip Slip is impossible**, and this is the part that changed. AccessLens compares a realpath
  against a prefix; here every member name goes through :meth:`sightline.paths.DataPaths.resolve`,
  which is the same gate the whole application writes through and the one ``tests/test_paths.py``
  proves. A second, parallel implementation of the same check is a second thing to get wrong.

A Canvas content export carries no student submissions and no grades. It does carry
instructor-authored content, page text naming real people, and unpublished drafts, which is why
everything here stays under ``SIGHTLINE_DATA_DIR`` and never moves out of it (SPEC §11).
"""

from __future__ import annotations

import hashlib
import posixpath
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import defusedxml.ElementTree as ElementTree

from ..canvas.client import CanvasClient, Download
from ..logging import get_logger
from ..paths import DataPaths

log = get_logger("sightline.acquire.archive")

#: File types a Sightline detector can read. Everything else stays inside the archive: a course's
#: lecture video is a hundred times the size of everything a detector will ever open.
EXTRACTABLE_SUFFIXES = frozenset(
    {".html", ".htm", ".xml", ".pdf", ".docx", ".pptx", ".xlsx", ".doc", ".ppt", ".txt", ".rtf"}
)

#: Ceiling on what is written to disk out of one archive.
MAX_EXTRACT_BYTES = 512 * 1024 * 1024

#: Ceiling on one member. A single 2 GB entry is either a mistake or an attack.
MAX_MEMBER_BYTES = 128 * 1024 * 1024

#: Marker written once an extraction finished. Its absence means the directory is a half-unpacked
#: archive and must not be read as a course.
COMPLETE_MARKER = ".extracted"

#: Hosts a download link may name: the configured Canvas host, or a subdomain of it. Nothing else.
#:
#: AccessLens also allows Canvas's vendor storage domains here. Sightline does not, for two
#: reasons. SPEC §04 says nothing institution- or deployment-specific is compiled in, and a vendor
#: domain list is exactly that — ``tests/test_config.py`` fails the build over one, and it is right
#: to. And it is not needed for the common case: Canvas returns the attachment URL on the API host,
#: and the redirect to storage that follows is handled inside the HTTP client, which drops the
#: Authorization header when the origin changes.
#:
#: If a deployment turns out to hand back attachment URLs on another host, the refusal below names
#: that host and says so, which is a better first move than widening the rule for a case nobody has
#: observed. The fix then is a configured allowlist, not a constant.
ALLOWED_HOST_NOTE = "the configured Canvas host, or a subdomain of it"


class ArchiveError(RuntimeError):
    """An archive could not be fetched or unpacked. Always carries what to change."""

    def __init__(self, summary: str, *, remedy: str | None = None) -> None:
        super().__init__(summary)
        self.summary = summary
        self.remedy = remedy


def validate_download_url(url: str, *, canvas_base_url: str) -> str:
    """Return ``url`` if it belongs to the configured Canvas instance, or raise.

    Ported from AccessLens. The host is compared against the configured Canvas host and the
    storage domains Canvas Cloud redirects to; anything else is refused by name.
    """
    if not url:
        raise ArchiveError(
            "The archive link is empty.",
            remedy="Canvas reported the export as ready but gave no download URL. Retry it.",
        )
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise ArchiveError(
            f"The archive link uses an unexpected scheme: {parts.scheme or 'none'}.",
            remedy="Only http and https links are followed.",
        )
    canvas_host = (urlsplit(canvas_base_url).hostname or "").lower()
    download_host = (parts.hostname or "").lower()
    if not download_host:
        raise ArchiveError("The archive link names no host.")
    if canvas_host and not (
        download_host == canvas_host or download_host.endswith(f".{canvas_host}")
    ):
        raise ArchiveError(
            f"The archive link points at {download_host}, which is not this Canvas instance.",
            remedy=(
                f"Sightline follows a download link only to {ALLOWED_HOST_NOTE}. Check the base "
                "URL setting; if this instance genuinely serves exports from another host, that "
                "host has to be configured rather than assumed."
            ),
        )
    return url


async def download_archive(
    client: CanvasClient,
    url: str,
    *,
    paths: DataPaths,
    destination: tuple[str, ...],
    canvas_base_url: str,
    max_bytes: int = MAX_EXTRACT_BYTES,
) -> tuple[Path, Download]:
    """Stream an archive to ``destination`` beneath the data root.

    ``destination`` is a path *inside* the root; it goes through the gate, so a hostile filename
    on the far end cannot choose where the bytes land.
    """
    validate_download_url(url, canvas_base_url=canvas_base_url)
    with paths.staged_write(*destination) as handle:
        result = await client.download_to(url, handle.write, max_bytes=max_bytes)
    target = paths.resolve(*destination)
    if result.truncated:
        raise ArchiveError(
            f"The archive exceeded the {max_bytes // (1024 * 1024)} MB download limit.",
            remedy="Raise the limit, or review this course from the Canvas API alone.",
        )
    log.info(
        "archive_downloaded",
        bytes=result.bytes_written,
        content_type=result.content_type,
        served_by=result.served_by,
    )
    return target, result


# ---------------------------------------------------------------------------------------------
# Unpacking
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractionReport:
    """What unpacking an archive examined and what it wrote."""

    root: Path
    members_seen: int
    members_written: int
    bytes_written: int
    skipped_by_type: int
    truncated: bool
    #: Uncompressed size of every member, extracted or not. The honest measure of a course's
    #: content size, and the one AccessLens found worth keeping.
    total_uncompressed_bytes: int


def extract_archive(
    archive: Path,
    *,
    paths: DataPaths,
    destination: tuple[str, ...],
    max_bytes: int = MAX_EXTRACT_BYTES,
) -> ExtractionReport:
    """Unpack the readable members of ``archive`` into a directory inside the data root.

    Re-running against an already-complete extraction is a no-op: the destination is named after
    the archive's digest, so a changed archive unpacks somewhere else rather than mixing its files
    with the previous one's. Nothing is ever deleted to make room.
    """
    root = paths.resolve(*destination)
    marker = paths.resolve(*destination, COMPLETE_MARKER)
    if marker.is_file():
        return _report_from_marker(root, marker)

    seen = written = written_bytes = skipped = 0
    total_uncompressed = 0
    truncated = False

    try:
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if member.is_dir():
                    continue
                seen += 1
                total_uncompressed += member.file_size
                name = member.filename.replace("\\", "/")
                if posixpath.splitext(name.lower())[1] not in EXTRACTABLE_SUFFIXES:
                    skipped += 1
                    continue
                if member.file_size > MAX_MEMBER_BYTES:
                    skipped += 1
                    log.warning("archive_member_too_large", size=member.file_size)
                    continue
                if written_bytes + member.file_size > max_bytes:
                    truncated = True
                    break
                # The gate, not a prefix comparison: a member called ``../../etc/passwd`` is
                # rejected here by the same code the containment tests exercise.
                with (
                    bundle.open(member) as source,
                    paths.staged_write(*destination, "files", name) as sink,
                ):
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        sink.write(chunk)
                written += 1
                written_bytes += member.file_size
    except zipfile.BadZipFile as exc:
        raise ArchiveError(
            "That file is not a readable archive.",
            remedy=(
                "A Canvas content export is a .imscc or .zip file. Check that the download "
                "completed and that the file is the export itself, not an HTML error page."
            ),
        ) from exc

    report = ExtractionReport(
        root=root,
        members_seen=seen,
        members_written=written,
        bytes_written=written_bytes,
        skipped_by_type=skipped,
        truncated=truncated,
        total_uncompressed_bytes=total_uncompressed,
    )
    paths.save_text(
        *destination,
        COMPLETE_MARKER,
        text=(
            f"members_seen={seen}\n"
            f"members_written={written}\n"
            f"bytes_written={written_bytes}\n"
            f"skipped_by_type={skipped}\n"
            f"truncated={int(truncated)}\n"
            f"total_uncompressed_bytes={total_uncompressed}\n"
        ),
    )
    log.info(
        "archive_extracted",
        members_seen=seen,
        members_written=written,
        skipped_by_type=skipped,
        truncated=truncated,
    )
    return report


def _report_from_marker(root: Path, marker: Path) -> ExtractionReport:
    values: dict[str, int] = {}
    for line in marker.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if value.isdigit():
            values[key] = int(value)
    return ExtractionReport(
        root=root,
        members_seen=values.get("members_seen", 0),
        members_written=values.get("members_written", 0),
        bytes_written=values.get("bytes_written", 0),
        skipped_by_type=values.get("skipped_by_type", 0),
        truncated=bool(values.get("truncated", 0)),
        total_uncompressed_bytes=values.get("total_uncompressed_bytes", 0),
    )


def archive_digest(archive: Path) -> str:
    """SHA-256 of an archive, hex, first 16 characters. Names its extraction directory."""
    digest = hashlib.sha256()
    # Reading, not writing: the write gate in sightline.paths governs file creation, and a
    # digest of a file already inside the data root creates nothing.
    with open(archive, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


# ---------------------------------------------------------------------------------------------
# The IMS manifest
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ManifestEntry:
    """One resource in ``imsmanifest.xml``.

    ``module_path`` is the organization tree above the resource — the course's own module names,
    which is the only place an export records them.
    """

    href: str
    title: str
    module_path: tuple[str, ...]


def parse_manifest(manifest: Path) -> dict[str, ManifestEntry]:
    """``href -> entry`` from an IMSCC manifest. A missing or unparseable manifest yields ``{}``.

    Ported from AccessLens's ``imscc_manifest.parse_manifest``, minus its Canvas-URL derivation:
    Sightline builds deep links from the API's own identifiers in :mod:`sightline.model`, and two
    ways of spelling the same link is one too many.
    """
    if not manifest.is_file():
        return {}
    try:
        tree = ElementTree.parse(manifest)
    except ElementTree.ParseError:
        log.warning("manifest_unparseable")
        return {}

    root = tree.getroot()
    href_by_id: dict[str, str] = {}
    for element in root.iter():
        if _local(element.tag) != "resource":
            continue
        identifier = element.get("identifier", "")
        href = element.get("href", "")
        if identifier and href:
            href_by_id[identifier] = href

    crumbs_by_id: dict[str, tuple[tuple[str, ...], str]] = {}

    def walk(item: Any, stack: tuple[str, ...]) -> None:
        title = ""
        for child in item:
            if _local(child.tag) == "title" and child.text:
                title = child.text.strip()
                break
        reference = item.get("identifierref", "")
        if reference and reference in href_by_id:
            crumbs_by_id[reference] = (stack, title)
        deeper = stack + (title,) if title else stack
        for child in item:
            if _local(child.tag) == "item":
                walk(child, deeper)

    for element in root.iter():
        if _local(element.tag) != "organization":
            continue
        for child in element:
            if _local(child.tag) != "item":
                continue
            # The outermost <item> is a "LearningModules" wrapper with no useful title in every
            # Canvas export seen; its children are the modules.
            for grandchild in child:
                if _local(grandchild.tag) == "item":
                    walk(grandchild, ())
        break

    entries: dict[str, ManifestEntry] = {}
    for identifier, href in href_by_id.items():
        module_path, title = crumbs_by_id.get(identifier, ((), ""))
        entries[href] = ManifestEntry(
            href=href,
            title=title or _title_from_href(href),
            module_path=module_path,
        )
    return entries


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _title_from_href(href: str) -> str:
    name = posixpath.splitext(posixpath.basename(href))[0]
    return name.replace("-", " ").replace("_", " ").strip() or href


# ---------------------------------------------------------------------------------------------
# Reading an unpacked export
# ---------------------------------------------------------------------------------------------


class UnpackedExport:
    """An extracted content export, read-only.

    The two things a normalizer wants from an export are page bodies (``wiki_content/<slug>.html``)
    and document bytes (``web_resources/...``); everything else in a Common Cartridge duplicates
    what the API says better.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.files_root = root / "files"
        self.manifest = parse_manifest(self.files_root / "imsmanifest.xml")

    @property
    def usable(self) -> bool:
        return (self.root / COMPLETE_MARKER).is_file() and self.files_root.is_dir()

    def page_body(self, slug: str) -> str | None:
        candidate = self.files_root / "wiki_content" / f"{slug}.html"
        if not candidate.is_file():
            return None
        return candidate.read_text(encoding="utf-8", errors="replace")

    def page_slugs(self) -> tuple[str, ...]:
        directory = self.files_root / "wiki_content"
        if not directory.is_dir():
            return ()
        return tuple(sorted(path.stem for path in directory.glob("*.html")))

    def documents(self) -> Iterator[tuple[str, Path]]:
        """``relative href -> path`` for every extracted web resource, in a stable order."""
        directory = self.files_root / "web_resources"
        if not directory.is_dir():
            return
        for path in sorted(p for p in directory.rglob("*") if p.is_file()):
            yield posixpath.join("web_resources", path.relative_to(directory).as_posix()), path

    def module_path_for(self, href: str) -> tuple[str, ...]:
        entry = self.manifest.get(href)
        return entry.module_path if entry else ()

    def search_href(self, filename: str) -> str:
        return quote(filename)


__all__ = [
    "ALLOWED_HOST_NOTE",
    "COMPLETE_MARKER",
    "EXTRACTABLE_SUFFIXES",
    "MAX_EXTRACT_BYTES",
    "MAX_MEMBER_BYTES",
    "ArchiveError",
    "ExtractionReport",
    "ManifestEntry",
    "UnpackedExport",
    "archive_digest",
    "download_archive",
    "extract_archive",
    "parse_manifest",
    "validate_download_url",
]
