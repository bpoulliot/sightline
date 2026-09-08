"""Embed extraction: what an embed is, once, so four detectors agree about it.

D27 wants caption tracks, D39 wants an accompanying hyperlink, D42 wants fixed pixel widths and
D43 wants legacy players. Extracting embeds in the acquisition layer is what stops those four
disagreeing about which elements count.

Nothing here judges. The assertions are on recorded facts — the tag, the source, the dimensions as
written, whether a track element is present — never on whether any of that is a defect.
"""

from __future__ import annotations

import pytest

from sightline.acquire.embeds import accompanies, provider_of, scan_embeds
from sightline.model import ArtifactKind, Page, Source


def page(body: str) -> Page:
    return Page(
        id="page:sample",
        title="Sample",
        canvas_path="/courses/4410/pages/sample",
        slug="sample",
        body_html=body,
        body_source=Source.API,
        module_path=("Module 1",),
    )


def test_a_page_with_no_body_yields_no_embeds() -> None:
    assert scan_embeds(Page(id="p", title="p", canvas_path="/c", slug="p")) == ()


def test_every_embed_tag_is_found() -> None:
    found = scan_embeds(
        page(
            '<iframe src="https://media.example.edu/a"></iframe>'
            '<video><source src="/files/1.mp4"></video>'
            '<audio src="/files/2.mp3"></audio>'
            '<object data="/files/3.swf"></object>'
            '<embed src="/files/4.swf">'
        )
    )
    assert [item.tag for item in found] == ["iframe", "video", "audio", "object", "embed"]
    assert all(item.kind is ArtifactKind.EMBED for item in found)


def test_an_embed_inherits_its_container_link_and_names_it() -> None:
    """An embed has no Canvas id, so the reviewer is sent to the page that holds it — which is
    where the fix is made."""
    container = page('<iframe src="https://media.example.edu/a"></iframe>')
    embed = scan_embeds(container)[0]
    assert embed.canvas_path == container.canvas_path
    assert embed.container_id == container.id
    assert embed.module_path == container.module_path
    assert embed.id == "embed:page:sample:000"


def test_ordinals_are_document_order() -> None:
    found = scan_embeds(
        page('<iframe src="/a"></iframe><iframe src="/b"></iframe><iframe src="/c"></iframe>')
    )
    assert [item.id[-3:] for item in found] == ["000", "001", "002"]
    assert [item.src for item in found] == ["/a", "/b", "/c"]


def test_a_source_child_supplies_the_parent_source() -> None:
    """``<video><source src=…></video>`` is the ordinary form, and an embed with no source at all
    would be a fact a detector could not act on."""
    embed = scan_embeds(page('<video controls><source src="/files/9.mp4"></video>'))[0]
    assert embed.src == "/files/9.mp4"


def test_a_caption_track_is_recorded_and_its_absence_is_too() -> None:
    with_track = scan_embeds(
        page('<video><source src="/f.mp4"><track kind="captions" src="/c.vtt"></video>')
    )[0]
    without = scan_embeds(page('<video><source src="/f.mp4"></video>'))[0]
    assert with_track.has_caption_track is True
    assert without.has_caption_track is False


def test_dimensions_are_recorded_as_written() -> None:
    """As written, not as interpreted: D42 asks whether a width is a pixel count, and normalizing
    ``640`` and ``100%`` to a number would destroy the thing it looks for."""
    found = scan_embeds(
        page('<iframe src="/a" width="640" height="360"></iframe><video width="100%"></video>')
    )
    assert (found[0].width, found[0].height) == ("640", "360")
    assert found[1].width == "100%"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://www.youtube.com/embed/abc123", "youtube"),
        ("https://youtu.be/abc123", "youtube"),
        ("https://player.vimeo.com/video/778899", "vimeo"),
        ("https://example.panopto.com/Panopto/Pages/Embed.aspx?id=1", "panopto"),
        ("/courses/4410/files/1/download", None),
        ("", None),
    ],
)
def test_known_providers_are_named(source: str, expected: str | None) -> None:
    assert provider_of(source or None) == expected


def test_an_accompanying_link_is_found_across_canvas_url_rewriting() -> None:
    """Canvas turns a ``watch?v=`` link into an ``/embed/`` player URL, so comparing whole URLs
    finds nothing. Host plus resource-identifying tokens is what actually coincides."""
    assert accompanies(
        "https://www.youtube.com/embed/abc123xyz",
        ["https://www.youtube.com/watch?v=abc123xyz"],
    )


def test_a_link_to_a_different_resource_does_not_count_as_accompanying() -> None:
    assert not accompanies(
        "https://www.youtube.com/embed/abc123xyz",
        ["https://www.youtube.com/watch?v=zzz999yyy", "https://vimeo.com/abc123xyz"],
    )


def test_a_relative_file_link_beside_its_own_embed_counts() -> None:
    found = scan_embeds(
        page(
            '<video><source src="/courses/4410/files/900021/download"></video>'
            '<a href="/courses/4410/files/900021/download">download it</a>'
        )
    )
    assert found[0].accompanied_by_link is True


def test_malformed_html_still_produces_embeds() -> None:
    """Course HTML is malformed in every interesting way. An acquisition that died on one page
    would lose the whole course, so the parser is lenient by construction."""
    found = scan_embeds(page('<div><iframe src="/a"><p>unclosed everything<iframe src="/b">'))
    assert {item.src for item in found} == {"/a", "/b"}
