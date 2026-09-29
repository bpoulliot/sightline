"""Reading a finished course back from its review file (roadmap item 14).

After a run, a course exists only as ``runs/<run>/courses/<course>/review.json`` — what
:meth:`sightline.batch.pipeline.ReviewPipeline.review` produced and the batch runner saved. The
voice layer (:func:`sightline.voice.render_review`) takes live objects, so this module turns the
one into the other, through the ``from_dict`` inverse each persisted type carries.

**Nothing is re-run.** Not detection, not projection, not extraction: the file *is* what the run
proposed, and an export that re-derived any of it would be a second run somebody forwarded under
the first one's name. Two facts the file does not carry — the instrument's priority order and its
rating words — come from the shipped pack the file names, and only at the version it names. A key
the renderer needs and the file lacks is refused with a stated reason; it is never filled in.

The PDF report reads through here, and so will the review screen (roadmap item 16): one loader,
so the screen a designer reviews and the document they export cannot disagree about a course.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..batch.runner import REVIEW_FILENAME
from ..detect.runner import DetectionReport
from ..extract.runner import RowExtraction
from ..paths import DataPaths
from ..rubric.pack import RubricPack
from ..rubric.project import Projection
from ..rubric.shipped import shipped_pack
from ..store import RunStore
from ..voice.render import RenderedReview, Voice, render_review


class ReviewFileError(ValueError):
    """A review file cannot be read back into what the renderer takes. Says why."""


@dataclass(frozen=True, kw_only=True, slots=True)
class LoadedReview:
    """One course's review, as the live objects :func:`render_review` takes."""

    #: The Canvas course id the review file records. The narrative's rotation keys on it, so the
    #: same course renders the same sentences in every export.
    course_id: str | None
    course_name: str | None
    #: The configured Canvas URL at the time of the run; every link is built from it.
    base_url: str | None
    pack: RubricPack
    projection: Projection
    report: DetectionReport
    extractions: Mapping[str, RowExtraction]

    def render(self, voice: Voice | None = None) -> RenderedReview:
        return render_review(
            self.projection,
            report=self.report,
            extractions=self.extractions,
            base_url=self.base_url,
            course_id=self.course_id,
            voice=voice,
        )


def _require(payload: Mapping[str, Any], key: str, where: str) -> Any:
    value = payload.get(key) if isinstance(payload, Mapping) else None
    if value is None:
        raise ReviewFileError(
            f"the review file has no {where}{key!r}, so there is nothing to render it from. A "
            "course that did not finish its run has no review to export; nothing is re-derived "
            "to fill the gap."
        )
    return value


def load_review(payload: Mapping[str, Any], *, packs_dir: Path | str | None = None) -> LoadedReview:
    """Turn a parsed review file into a :class:`LoadedReview`, or raise :class:`ReviewFileError`."""
    course = _require(payload, "course", "")
    named = _require(payload, "pack", "")
    detection = _require(payload, "detection", "")
    projected = _require(payload, "projection", "")
    extraction = _require(payload, "extraction", "")
    rows = _require(extraction, "rows", "extraction ")

    pack_id = str(_require(named, "id", "pack "))
    version = str(_require(named, "version", "pack "))
    try:
        pack = shipped_pack(pack_id, packs_dir)
    except KeyError as exc:
        raise ReviewFileError(
            f"the review was made against rubric pack {pack_id!r}, which this build does not ship"
        ) from exc
    if pack.version != version:
        raise ReviewFileError(
            f"the review was made against {pack_id!r} version {version!r} and this build ships "
            f"version {pack.version!r}. The instrument's priority order and rating words come "
            "from the pack, so reading the review against a different version would order and "
            "name it by an instrument it was not made with."
        )

    try:
        projection = Projection.from_dict(projected, pack=pack)
        report = DetectionReport.from_dict(detection)
        extractions = {
            entry.criterion: entry for entry in (RowExtraction.from_dict(row) for row in rows)
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ReviewFileError(
            f"the review file does not read back as a review ({type(exc).__name__}: {exc}). "
            "It may come from an older build; re-run the course rather than editing the file."
        ) from exc

    return LoadedReview(
        course_id=_optional(course.get("id")),
        course_name=_optional(course.get("name")),
        base_url=_optional(course.get("base_url")),
        pack=pack,
        projection=projection,
        report=report,
        extractions=extractions,
    )


def _optional(value: Any) -> str | None:
    return None if value is None else str(value)


def parse_review(text: str, *, packs_dir: Path | str | None = None) -> LoadedReview:
    """:func:`load_review` over the file's text."""
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReviewFileError(f"the review file is not JSON: {exc}") from exc
    return load_review(payload, packs_dir=packs_dir)


def review_path(paths: DataPaths, store: RunStore, course_id: str) -> Path:
    """Where the batch runner saved a run-store course's review file. Raises if it is absent."""
    course = store.get_course(course_id)
    if course is None:
        raise KeyError(f"no course {course_id!r} in the run store")
    path = paths.resolve("runs", course.run_id, "courses", course.id, REVIEW_FILENAME)
    if not path.is_file():
        raise ReviewFileError(
            f"course {course.ordinal + 1} of its run has no review file. Only a course that "
            "finished its run can be exported; its run's status says what happened to it."
        )
    return path


def read_review(
    paths: DataPaths, store: RunStore, course_id: str, *, packs_dir: Path | str | None = None
) -> LoadedReview:
    """Read one run-store course's review file back. Reads; never writes."""
    return parse_review(
        review_path(paths, store, course_id).read_text(encoding="utf-8"), packs_dir=packs_dir
    )


__all__ = [
    "LoadedReview",
    "ReviewFileError",
    "load_review",
    "parse_review",
    "read_review",
    "review_path",
]
