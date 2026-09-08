"""Caching a normalized course, keyed so that a changed course cannot hit a stale entry.

Acquisition has a cheap half and an expensive half. Listing a course's structure is a dozen
requests; fetching every page body is one request per page, and unpacking an export is a download.
So the cheap half always runs, its result is reduced to a **signature**, and the expensive half is
skipped when that signature matches something already cached.

That ordering is what makes the key trustworthy. A cache keyed on the course's own ``updated_at``
would be wrong in the quiet way — Canvas does not bump a course's timestamp when a page inside it
is edited — and the symptom would be a review of yesterday's course reported as today's. Keying on
a digest of every listed artifact means an edit anywhere changes the key, because the edit changes
the ``updated_at`` the listing carries.

Two things are deliberately excluded from the digest:

* **Values that rotate per request.** A Canvas file URL carries a ``verifier``, which is different
  on every fetch. Including it would mean the cache never hit, which is the same as not having one.
* **Reply counts.** Sightline reviews the design of a course, not the activity in it. A student
  posting to a discussion is not a change to what is being reviewed, and treating it as one would
  re-fetch every page body in the course each time somebody replied.

Nothing here deletes. A superseded entry stays on disk under its old signature, so clearing the
cache is a human's decision about a directory rather than a program's about a file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..logging import get_logger
from ..model import SCHEMA_VERSION, CourseDocument, SchemaVersionMismatch
from ..paths import DataPaths
from .canvas_api import CourseIndex

log = get_logger("sightline.acquire.cache")

CACHE_SUBDIR = "courses"

#: Keys whose value differs between two identical fetches, or which describe activity rather than
#: design. Removed recursively before the digest.
VOLATILE_KEYS = frozenset(
    {
        "url",
        "preview_url",
        "thumbnail_url",
        "modules_url",
        "progress_url",
        "items_url",
        "uuid",
        "verifier",
        "unread_count",
        "read_state",
        "subscribed",
        "last_reply_at",
        "discussion_subentry_count",
        "user_can_see_posts",
        "locked_for_user",
        "lock_info",
        "lock_explanation",
    }
)


def course_key(base_url: str, course_id: str) -> str:
    """Directory name for one course's cache.

    A digest rather than the host and id, so that listing the cache directory does not disclose
    the institution. The documents inside still name the base URL — they must, to build links —
    but the shape of the tree is not itself a disclosure.
    """
    material = f"{base_url.rstrip('/')}\0{course_id}".encode()
    return hashlib.sha256(material).hexdigest()[:16]


def signature_of(index: CourseIndex) -> str:
    """Digest of everything the cheap phase saw. Any content change moves it."""
    material = {
        "schema": SCHEMA_VERSION,
        "course": _stable(index.course),
        "collections": {
            name: {
                "state": collection.examination.state.value,
                "items": [_stable(item) for item in collection.items],
            }
            for name, collection in sorted(index.collections.items())
        },
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]


def _stable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _stable(item) for key, item in sorted(value.items()) if key not in VOLATILE_KEYS
        }
    if isinstance(value, list | tuple):
        return [_stable(item) for item in value]
    return value


@dataclass(frozen=True)
class CacheEntry:
    """Where a cached document lives and what it was keyed on."""

    course_key: str
    signature: str

    @property
    def parts(self) -> tuple[str, ...]:
        return ("cache", CACHE_SUBDIR, self.course_key, f"{self.signature}.json")


class DocumentCache:
    """Reads and writes normalized documents beneath the data root."""

    def __init__(self, paths: DataPaths) -> None:
        self._paths = paths

    def entry(self, base_url: str, course_id: str, signature: str) -> CacheEntry:
        return CacheEntry(course_key=course_key(base_url, course_id), signature=signature)

    def load(self, entry: CacheEntry) -> CourseDocument | None:
        """The cached document, or ``None`` when there is none this build can read.

        A document written by a different schema version is a miss, not an error: the version is
        in the signature already, and refusing to start over a stale cache file would make an
        upgrade look like a failure.
        """
        target = self._paths.resolve(*entry.parts)
        if not target.is_file():
            return None
        try:
            document = CourseDocument.from_json(target.read_text(encoding="utf-8"))
        except (SchemaVersionMismatch, ValueError, KeyError) as exc:
            log.warning("cache_entry_unreadable", reason=type(exc).__name__)
            return None
        log.info("cache_hit", signature=entry.signature[:8])
        return document

    def store(self, entry: CacheEntry, document: CourseDocument) -> Path:
        """Write a document under its signature and return the path."""
        target = self._paths.save_text(*entry.parts, text=document.to_json())
        log.info("cache_stored", signature=entry.signature[:8], bytes=target.stat().st_size)
        return target

    def entries(self, base_url: str, course_id: str) -> tuple[str, ...]:
        """Signatures currently cached for a course, newest last by name.

        Exposed because nothing here prunes: a person deciding whether to clear the cache should
        be able to see what is in it.
        """
        directory = self._paths.resolve("cache", CACHE_SUBDIR, course_key(base_url, course_id))
        if not directory.is_dir():
            return ()
        return tuple(sorted(path.stem for path in directory.glob("*.json")))


__all__ = [
    "CACHE_SUBDIR",
    "VOLATILE_KEYS",
    "CacheEntry",
    "DocumentCache",
    "course_key",
    "signature_of",
]
