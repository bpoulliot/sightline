"""The cache key, and what must and must not move it.

The acceptance condition here is that **a changed course invalidates the cache**. The dangerous
alternative is subtle: a key built on the course's own ``updated_at`` looks right and is wrong,
because Canvas does not bump a course's timestamp when a page inside it is edited. The symptom
would be a review of yesterday's course reported as today's, with nothing to see.

So the key is a digest of everything the cheap index phase listed, and the tests below fire it in
both directions — a real edit must move it, and a value that rotates per request must not.
"""

from __future__ import annotations

from typing import Any

import pytest

from canvas_recordings import EXAMPLE_BASE_URL, FAKE_TOKEN, load
from sightline.acquire import CourseReader, DocumentCache, course_key, normalize, signature_of
from sightline.acquire.canvas_api import Collection, CourseIndex
from sightline.canvas.client import CanvasClient
from sightline.model import Examination, ExaminationState, Source
from sightline.paths import DataPaths


async def read_index(course_id: str = "4411") -> CourseIndex:
    recording = load("course-acquisition")
    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=recording.transport()
    ) as client:
        return await CourseReader(client).read_index(course_id)


def with_collection(index: CourseIndex, name: str, items: tuple[Any, ...]) -> CourseIndex:
    """A copy of ``index`` with one collection replaced. Used to simulate an edit."""
    original = index.collections[name]
    replaced = Collection(
        name=name,
        items=items,
        examination=Examination(
            collection=name,
            state=ExaminationState.OK if items else ExaminationState.EMPTY,
            examined=len(items),
            source=Source.API,
            endpoint=original.examination.endpoint,
            detail="replaced for the test",
        ),
    )
    return CourseIndex(course=index.course, collections={**index.collections, name: replaced})


# -- the key ----------------------------------------------------------------------------------


def test_the_course_key_does_not_disclose_the_institution() -> None:
    """Listing the cache directory should not name the host. The documents inside must, to build
    links; the shape of the tree need not."""
    key = course_key(EXAMPLE_BASE_URL, "4410")
    assert "example" not in key
    assert "4410" not in key
    assert len(key) == 16


def test_the_course_key_separates_two_instances_with_the_same_course_id() -> None:
    """Two Canvas instances both have a course 4410, and reviewing the wrong one looks like
    success."""
    assert course_key("https://canvas.example.edu", "4410") != course_key(
        "https://other.example.edu", "4410"
    )


async def test_the_signature_is_stable_across_two_reads() -> None:
    assert signature_of(await read_index()) == signature_of(await read_index())


async def test_an_edited_page_moves_the_signature() -> None:
    """This is the failure a course-level timestamp would miss."""
    index = await read_index()
    edited = [dict(page) for page in index.items("pages")]
    edited[0]["updated_at"] = "2026-06-01T09:00:00Z"
    assert signature_of(with_collection(index, "pages", tuple(edited))) != signature_of(index)


async def test_an_added_assignment_moves_the_signature() -> None:
    index = await read_index()
    added = (*index.items("assignments"), {"id": 9999, "name": "Synthetic new"})
    assert signature_of(with_collection(index, "assignments", added)) != signature_of(index)


async def test_a_removed_module_moves_the_signature() -> None:
    index = await read_index()
    fewer = index.items("modules")[:1]
    assert signature_of(with_collection(index, "modules", fewer)) != signature_of(index)


async def test_a_collection_becoming_unreadable_moves_the_signature() -> None:
    """Losing access to the quizzes changes what the review can say, so it must not hit a cache
    entry built when they were readable."""
    index = await read_index()
    assert signature_of(with_collection(index, "quizzes", ())) != signature_of(index)


async def test_a_rotating_file_verifier_does_not_move_the_signature() -> None:
    """A Canvas file URL carries a verifier that differs on every fetch. Including it would mean
    the cache never hit, which is the same as not having one."""
    index = await read_index()
    rotated = [
        {**file, "url": f"{EXAMPLE_BASE_URL}/files/5101/download?verifier=rotated"}
        for file in index.items("files")
    ]
    assert signature_of(with_collection(index, "files", tuple(rotated))) == signature_of(index)


async def test_a_new_student_reply_does_not_move_the_signature() -> None:
    """Sightline reviews the design of a course, not the activity in it. Re-fetching every page
    body because somebody posted would be a cache that costs more than it saves."""
    index = await read_index()
    busier = [
        {**topic, "discussion_subentry_count": (topic.get("discussion_subentry_count") or 0) + 5}
        for topic in index.items("discussions")
    ]
    assert signature_of(with_collection(index, "discussions", tuple(busier))) == signature_of(index)


# -- storage ----------------------------------------------------------------------------------


async def test_a_stored_document_round_trips_through_the_cache(paths: DataPaths) -> None:
    index = await read_index()
    document = normalize(index, base_url=EXAMPLE_BASE_URL)
    cache = DocumentCache(paths)
    entry = cache.entry(EXAMPLE_BASE_URL, "4411", signature_of(index))

    assert cache.load(entry) is None
    stored = cache.store(entry, document)
    assert paths.contains(stored)
    assert cache.load(entry).to_json() == document.to_json()


async def test_a_different_signature_is_a_miss_not_a_stale_hit(paths: DataPaths) -> None:
    index = await read_index()
    cache = DocumentCache(paths)
    hit = cache.entry(EXAMPLE_BASE_URL, "4411", signature_of(index))
    cache.store(hit, normalize(index, base_url=EXAMPLE_BASE_URL))
    miss = cache.entry(EXAMPLE_BASE_URL, "4411", "0" * 32)
    assert cache.load(miss) is None
    assert cache.load(hit) is not None


async def test_a_cache_entry_from_another_schema_version_is_a_miss(paths: DataPaths) -> None:
    """Refusing to start over a stale cache file would make an upgrade look like a failure."""
    index = await read_index()
    cache = DocumentCache(paths)
    entry = cache.entry(EXAMPLE_BASE_URL, "4411", signature_of(index))
    target = cache.store(entry, normalize(index, base_url=EXAMPLE_BASE_URL))
    rewritten = target.read_text(encoding="utf-8").replace(
        '"schema_version": 1', '"schema_version": 99'
    )
    paths.save_text(*entry.parts, text=rewritten)
    assert cache.load(entry) is None


async def test_nothing_is_deleted_when_a_new_signature_is_stored(paths: DataPaths) -> None:
    """Clearing the cache is a human's decision about a directory, not a program's about a file."""
    index = await read_index()
    cache = DocumentCache(paths)
    document = normalize(index, base_url=EXAMPLE_BASE_URL)
    cache.store(cache.entry(EXAMPLE_BASE_URL, "4411", "a" * 32), document)
    cache.store(cache.entry(EXAMPLE_BASE_URL, "4411", "b" * 32), document)
    assert cache.entries(EXAMPLE_BASE_URL, "4411") == ("a" * 32, "b" * 32)


def test_the_cache_cannot_write_outside_the_data_root(paths: DataPaths) -> None:
    """Item 01's containment guarantee still holds for everything this item added."""
    from sightline.paths import DataPathEscape

    cache = DocumentCache(paths)
    entry = cache.entry(EXAMPLE_BASE_URL, "../../escape", "x" * 32)
    with pytest.raises(DataPathEscape):
        paths.resolve("cache", "courses", "../../escape", "x.json")
    assert paths.contains(paths.resolve(*entry.parts))
