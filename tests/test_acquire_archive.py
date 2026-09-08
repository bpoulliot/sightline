"""The fetch and unzip layer, ported from AccessLens.

Four properties came across from ``app/canvas/content_export.py`` because they were learned the
hard way there — a validated download host, a capped download, a capped extraction, and a Zip Slip
that cannot happen — and one of them was rebuilt rather than copied. AccessLens compares a realpath
against a prefix; here every archive member name goes through the same write gate the rest of the
application uses, which is the one ``tests/test_paths.py`` already proves. A second implementation
of a security check is a second thing to get wrong.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest

from canvas_recordings import EXAMPLE_BASE_URL, FAKE_TOKEN
from sightline.acquire.archive import (
    COMPLETE_MARKER,
    MAX_MEMBER_BYTES,
    ArchiveError,
    UnpackedExport,
    archive_digest,
    download_archive,
    extract_archive,
    parse_manifest,
    validate_download_url,
)
from sightline.canvas.client import CanvasClient
from sightline.canvas.errors import CanvasUnreachableError
from sightline.paths import DataPaths

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<manifest xmlns="http://www.imsglobal.org/xsd/imsccv1p1/imscp_v1p1">
  <organizations>
    <organization>
      <item identifier="root">
        <item identifier="m1">
          <title>Synthetic module one</title>
          <item identifier="i1" identifierref="r1"><title>Synthetic overview</title></item>
        </item>
      </item>
    </organization>
  </organizations>
  <resources>
    <resource identifier="r1" type="webcontent" href="wiki_content/synthetic-overview.html"/>
    <resource identifier="r2" type="webcontent" href="web_resources/synthetic-handout.pdf"/>
  </resources>
</manifest>
"""


def build_archive(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, payload in members.items():
            bundle.writestr(name, payload)
    return buffer.getvalue()


SAMPLE_MEMBERS = {
    "imsmanifest.xml": MANIFEST.encode("utf-8"),
    "wiki_content/synthetic-overview.html": b"<h2>Synthetic overview</h2>",
    "web_resources/synthetic-handout.pdf": b"%PDF-1.4 synthetic",
    "web_resources/synthetic-video.mp4": b"\x00" * 64,
}


@pytest.fixture
def archive(paths: DataPaths) -> Path:
    return paths.save_bytes("exports", "sample.imscc", data=build_archive(SAMPLE_MEMBERS))


# -- the download URL -----------------------------------------------------------------------


def test_a_link_on_the_configured_host_is_accepted() -> None:
    url = f"{EXAMPLE_BASE_URL}/files/1/download?verifier=synthetic"
    assert validate_download_url(url, canvas_base_url=EXAMPLE_BASE_URL) == url


def test_a_link_on_a_subdomain_of_the_configured_host_is_accepted() -> None:
    url = "https://files.canvas.example.edu/1/download"
    assert validate_download_url(url, canvas_base_url=EXAMPLE_BASE_URL)


def test_a_link_on_another_host_is_refused_by_name() -> None:
    """The instance hands us this link. Following it blindly is following a redirect target
    somebody else chose."""
    with pytest.raises(ArchiveError) as caught:
        validate_download_url("https://elsewhere.example.org/x", canvas_base_url=EXAMPLE_BASE_URL)
    assert "elsewhere.example.org" in caught.value.summary
    assert caught.value.remedy and "base URL" in caught.value.remedy


@pytest.mark.parametrize("url", ["", "ftp://canvas.example.edu/x", "not a url at all"])
def test_an_unusable_link_is_refused_with_a_remedy(url: str) -> None:
    with pytest.raises(ArchiveError) as caught:
        validate_download_url(url, canvas_base_url=EXAMPLE_BASE_URL)
    assert caught.value.summary


# -- downloading ----------------------------------------------------------------------------


async def test_a_download_lands_inside_the_data_root(paths: DataPaths) -> None:
    payload = build_archive(SAMPLE_MEMBERS)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    async with CanvasClient(EXAMPLE_BASE_URL, FAKE_TOKEN, transport=transport) as client:
        target, result = await download_archive(
            client,
            f"{EXAMPLE_BASE_URL}/files/1/download?verifier=synthetic",
            paths=paths,
            destination=("exports", "abc", "1.imscc"),
            canvas_base_url=EXAMPLE_BASE_URL,
        )
    assert paths.contains(target)
    assert target.read_bytes() == payload
    assert result.bytes_written == len(payload)


async def test_a_download_over_the_cap_fails_and_leaves_no_file(paths: DataPaths) -> None:
    """A stated limit, not a full disk — and nothing plausible left behind."""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"x" * 4096))
    async with CanvasClient(EXAMPLE_BASE_URL, FAKE_TOKEN, transport=transport) as client:
        with pytest.raises(ArchiveError) as caught:
            await download_archive(
                client,
                f"{EXAMPLE_BASE_URL}/files/1/download",
                paths=paths,
                destination=("exports", "abc", "big.imscc"),
                canvas_base_url=EXAMPLE_BASE_URL,
                max_bytes=1024,
            )
    assert "download limit" in caught.value.summary
    assert not list((paths.root / "exports" / "abc").glob("*.partial"))


async def test_a_failed_download_leaves_no_partial_file(paths: DataPaths) -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    transport = httpx.MockTransport(explode)
    async with CanvasClient(EXAMPLE_BASE_URL, FAKE_TOKEN, transport=transport) as client:
        with pytest.raises(CanvasUnreachableError):
            await download_archive(
                client,
                f"{EXAMPLE_BASE_URL}/files/1/download",
                paths=paths,
                destination=("exports", "abc", "gone.imscc"),
                canvas_base_url=EXAMPLE_BASE_URL,
            )
    assert not (paths.root / "exports" / "abc" / "gone.imscc").exists()
    assert not list((paths.root / "exports" / "abc").glob("*.partial"))


# -- unpacking ------------------------------------------------------------------------------


def test_extraction_writes_only_readable_types(paths: DataPaths, archive: Path) -> None:
    """A course's lecture video is a hundred times the size of everything a detector opens."""
    report = extract_archive(archive, paths=paths, destination=("exports", "c", "unpacked"))
    assert report.members_seen == 4
    assert report.members_written == 3
    assert report.skipped_by_type == 1
    assert report.total_uncompressed_bytes > report.bytes_written
    written = {p.name for p in (report.root / "files").rglob("*") if p.is_file()}
    assert "synthetic-video.mp4" not in written
    assert "synthetic-handout.pdf" in written


def test_extraction_is_idempotent_and_never_deletes(paths: DataPaths, archive: Path) -> None:
    destination = ("exports", "c", "unpacked")
    first = extract_archive(archive, paths=paths, destination=destination)
    second = extract_archive(archive, paths=paths, destination=destination)
    assert (first.members_written, first.members_seen) == (
        second.members_written,
        second.members_seen,
    )
    assert (first.root / COMPLETE_MARKER).is_file()


def test_a_zip_slip_member_cannot_escape_the_data_root(paths: DataPaths) -> None:
    """Rejected by the same gate the whole application writes through, not by a second copy of
    the check."""
    hostile = paths.save_bytes(
        "exports",
        "hostile.imscc",
        data=build_archive({"../../../../etc/passwd.html": b"<p>no</p>"}),
    )
    from sightline.paths import DataPathEscape

    with pytest.raises(DataPathEscape):
        extract_archive(hostile, paths=paths, destination=("exports", "h", "unpacked"))


def test_a_member_over_the_per_member_cap_is_skipped(paths: DataPaths) -> None:
    big = paths.save_bytes(
        "exports",
        "big.imscc",
        data=build_archive({"web_resources/huge.txt": b"x" * 64, "imsmanifest.xml": b"<m/>"}),
    )
    report = extract_archive(
        big, paths=paths, destination=("exports", "b", "unpacked"), max_bytes=1
    )
    assert report.truncated or report.members_written == 0
    assert MAX_MEMBER_BYTES > 0


def test_a_file_that_is_not_an_archive_says_so(paths: DataPaths) -> None:
    junk = paths.save_bytes("exports", "junk.imscc", data=b"<html>an error page</html>")
    with pytest.raises(ArchiveError) as caught:
        extract_archive(junk, paths=paths, destination=("exports", "j", "unpacked"))
    assert caught.value.remedy and "not an HTML error page" in caught.value.remedy


def test_the_digest_names_the_extraction_directory(paths: DataPaths, archive: Path) -> None:
    """A changed archive unpacks somewhere else rather than mixing with the previous one."""
    other = paths.save_bytes(
        "exports", "other.imscc", data=build_archive({"imsmanifest.xml": b"<m/>"})
    )
    assert archive_digest(archive) != archive_digest(other)
    assert len(archive_digest(archive)) == 16


# -- the manifest ---------------------------------------------------------------------------


def test_the_manifest_gives_a_module_path(paths: DataPaths, archive: Path) -> None:
    report = extract_archive(archive, paths=paths, destination=("exports", "c", "unpacked"))
    entries = parse_manifest(report.root / "files" / "imsmanifest.xml")
    assert entries["wiki_content/synthetic-overview.html"].module_path == ("Synthetic module one",)
    assert entries["wiki_content/synthetic-overview.html"].title == "Synthetic overview"


def test_a_missing_or_broken_manifest_is_empty_rather_than_fatal(tmp_path: Path) -> None:
    assert parse_manifest(tmp_path / "nope.xml") == {}
    broken = tmp_path / "imsmanifest.xml"
    broken.write_text("<manifest><unclosed>", encoding="utf-8")
    assert parse_manifest(broken) == {}


# -- reading an unpacked export ---------------------------------------------------------------


def test_an_unpacked_export_answers_for_bodies_and_documents(
    paths: DataPaths, archive: Path
) -> None:
    report = extract_archive(archive, paths=paths, destination=("exports", "c", "unpacked"))
    export = UnpackedExport(report.root)
    assert export.usable
    assert export.page_slugs() == ("synthetic-overview",)
    assert export.page_body("synthetic-overview") == "<h2>Synthetic overview</h2>"
    assert export.page_body("nothing-here") is None
    assert [href for href, _ in export.documents()] == ["web_resources/synthetic-handout.pdf"]


def test_an_unextracted_directory_is_not_usable(paths: DataPaths) -> None:
    """The completion marker is what separates a finished extraction from a half-unpacked one."""
    export = UnpackedExport(paths.resolve_dir("exports", "never-unpacked"))
    assert not export.usable
