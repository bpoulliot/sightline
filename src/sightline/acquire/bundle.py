"""The course bundle: a normalized course written out as files a person can read.

A cached document is one large JSON file, which is right for a machine and useless to a reviewer.
A bundle is the same document with the HTML pulled out into real files, which is what makes
``examples/sample-course/`` reviewable — course HTML is only legible as course HTML, and a page
body escaped into a JSON string is not.

    <bundle>/
        course.json      structure, with ``body_file`` references instead of inline bodies
        bodies/*.html    one file per artifact that has a body

Two things are derived rather than declared, so that the files and the model cannot disagree:
embeds are re-scanned from the bodies on load, and the examination record is synthesized from what
the bundle actually contains. A bundle may still declare ``examinations`` explicitly, which is how
the sample course shows what a *refused* collection looks like without needing a Canvas that
refuses one.

Loading is read-only and takes a plain directory, so a bundle can live in the repository. Nothing
here writes: the write gate in :mod:`sightline.paths` confines every write to the data root, which
is precisely why ``examples/sample-course/`` is hand-authored rather than generated in place.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, MutableMapping
from pathlib import Path
from typing import Any

from ..model import (
    SCHEMA_VERSION,
    CourseDocument,
    Examination,
    ExaminationState,
    Source,
)
from .embeds import embeds_for

MANIFEST_NAME = "course.json"
BODIES_DIR = "bodies"

#: Collections a bundle can carry, and the label their examination record uses.
BUNDLE_COLLECTIONS: tuple[str, ...] = (
    "announcements",
    "assignment_groups",
    "assignments",
    "discussions",
    "embeds",
    "files",
    "modules",
    "outcomes",
    "pages",
    "quizzes",
    "rubrics",
    "syllabus",
    "tabs",
)

#: ``course.json`` key -> document key, where the two differ.
_COLLECTION_FIELD = {"tabs": "navigation"}


class BundleError(ValueError):
    """A bundle could not be read. Always says which file and what to change."""


def load_bundle(directory: Path | str) -> CourseDocument:
    """Read a course bundle into a normalized document."""
    root = Path(directory)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BundleError(
            f"{root} is not a course bundle: it has no {MANIFEST_NAME}. "
            "A bundle is a directory holding course.json and a bodies/ directory."
        )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise BundleError(f"{manifest_path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise BundleError(f"{manifest_path} must hold a JSON object.")

    payload.setdefault("schema_version", SCHEMA_VERSION)
    payload = _with_bodies(payload, root)
    _apply_module_paths(payload)
    payload.setdefault("examinations", [])
    payload["provenance"] = {
        **dict(payload.get("provenance") or {}),
        "acquired_from": "bundle",
    }
    # Embeds are derived from the bodies, never declared: a bundle that could disagree with its own
    # HTML about what it embeds would be a fixture that tests the fixture.
    payload["embeds"] = []

    document = CourseDocument.from_dict(payload)
    embeds = embeds_for(
        [
            artifact
            for artifact in document.artifacts()
            if artifact.body_html and artifact.kind.value != "embed"
        ]
    )
    declared = tuple(Examination.from_dict(entry) for entry in payload.get("examinations") or ())
    examinations = _examinations(document, declared)
    return CourseDocument(
        **{
            **{
                field: getattr(document, field)
                for field in (
                    "schema_version",
                    "base_url",
                    "course",
                    "syllabus",
                    "modules",
                    "pages",
                    "assignments",
                    "quizzes",
                    "discussions",
                    "announcements",
                    "files",
                    "outcomes",
                    "navigation",
                    "assignment_groups",
                    "rubrics",
                    "provenance",
                )
            },
            "embeds": tuple(sorted(embeds, key=lambda item: item.id)),
            "examinations": examinations,
        }
    )


def _with_bodies(payload: MutableMapping[str, Any], root: Path) -> dict[str, Any]:
    """Replace every ``body_file`` reference with the file's text."""
    resolved = dict(payload)
    for node in _artifact_nodes(resolved):
        reference = node.pop("body_file", None)
        if reference is None:
            continue
        node["body_html"] = _read_body(root, str(reference))
        node.setdefault("body_source", Source.BUNDLE.value)
    return resolved


def _read_body(root: Path, reference: str) -> str:
    """Read one body file, refusing anything that points outside the bundle.

    The bundle is data, and data from outside this repository is untrusted even when it is only
    read: a manifest naming ``../../../etc/passwd`` should fail by name rather than quietly become
    a page body.
    """
    if reference.startswith("/") or ".." in Path(reference).parts:
        raise BundleError(f"body_file {reference!r} points outside the bundle.")
    target = root / reference
    if not target.is_file():
        raise BundleError(f"body_file {reference!r} does not exist in the bundle.")
    real_root = root.resolve()
    if not target.resolve().is_relative_to(real_root):
        raise BundleError(f"body_file {reference!r} resolves outside the bundle.")
    return target.read_text(encoding="utf-8")


def _apply_module_paths(payload: MutableMapping[str, Any]) -> None:
    """Give each artifact the module path its module item implies.

    Derived rather than declared, for the same reason embeds are: the modules already say where
    everything sits, and a bundle able to disagree with itself about that is a fixture with a bug
    in it. An artifact that declares its own ``module_path`` keeps it — a page can be in a module
    without a module item, and only the author knows that.
    """
    paths: dict[str, tuple[str, ...]] = {}
    for module in payload.get("modules") or ():
        if not isinstance(module, MutableMapping):
            continue
        title = str(module.get("title") or "")
        module.setdefault("module_path", [title] if title else [])
        heading: str | None = None
        for item in module.get("items") or ():
            if not isinstance(item, MutableMapping):
                continue
            if item.get("item_type") == "SubHeader":
                heading = str(item.get("title") or "") or None
            here = [title, heading] if heading else [title]
            item.setdefault("module_path", here)
            target = item.get("target_id")
            if target:
                paths.setdefault(str(target), tuple(here))

    for node in _artifact_nodes(payload):
        identifier = str(node.get("id") or "")
        if identifier in paths and not node.get("module_path"):
            node["module_path"] = list(paths[identifier])


def _artifact_nodes(payload: Mapping[str, Any]) -> Iterator[MutableMapping[str, Any]]:
    """Every node in a manifest that may carry a body, including nested module items."""
    syllabus = payload.get("syllabus")
    if isinstance(syllabus, MutableMapping):
        yield syllabus
    for key in ("pages", "assignments", "quizzes", "discussions", "announcements", "files"):
        for node in payload.get(key) or ():
            if isinstance(node, MutableMapping):
                yield node
    for module in payload.get("modules") or ():
        if not isinstance(module, MutableMapping):
            continue
        yield module
        for item in module.get("items") or ():
            if isinstance(item, MutableMapping):
                yield item


def _examinations(
    document: CourseDocument, declared: tuple[Examination, ...]
) -> tuple[Examination, ...]:
    """Synthesize an examination per collection, letting a declared one win.

    Synthesizing rather than requiring the author to write ten records keeps a bundle small, and
    a bundle that silently claimed nothing was examined would be the exact failure SPEC §11 calls
    silent under-detection — in the fixture, where it would be hardest to notice.
    """
    by_name = {entry.collection: entry for entry in declared}
    built: list[Examination] = []
    for name in BUNDLE_COLLECTIONS:
        if name in by_name:
            built.append(by_name[name])
            continue
        items = getattr(document, _COLLECTION_FIELD.get(name, name), ())
        # The syllabus is one artifact rather than a collection, so it is counted as present or
        # absent. It still needs a record: a detector reading it has to be able to tell a course
        # with no syllabus from a syllabus the bundle failed to load.
        count = (1 if items else 0) if name == "syllabus" else len(items)
        built.append(
            Examination(
                collection=name,
                state=ExaminationState.OK if count else ExaminationState.EMPTY,
                examined=count,
                source=Source.BUNDLE,
                endpoint=f"{MANIFEST_NAME}#{name}",
                detail=f"The bundle declares {count} item(s) in this collection.",
            )
        )
    for entry in declared:
        if entry.collection not in BUNDLE_COLLECTIONS:
            built.append(entry)
    return tuple(sorted(built, key=lambda entry: entry.collection))


__all__ = [
    "BODIES_DIR",
    "BUNDLE_COLLECTIONS",
    "MANIFEST_NAME",
    "BundleError",
    "load_bundle",
]
