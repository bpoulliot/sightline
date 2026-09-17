"""Finding the rubric packs Sightline ships with.

A pack file nobody can find is not shipped. The two packs generated from the committed instruments
live at ``rubrics/packs/`` — beside the workbooks they came from rather than inside the Python
package, because a non-programmer has to be able to read and diff them and ``src/sightline/`` is
not where anyone looks for a data file. ``Dockerfile`` copies ``rubrics/`` into both the dev stage
and the runtime image, so the same two files are there in a checkout and in a container.

Search order mirrors :mod:`sightline.assets`, which locates the design tokens the same way and for
the same reason: first an explicit setting, then the container layout where ``WORKDIR`` is
``/app``, then a source checkout resolved relative to this file.

**Nothing here decides which pack a review uses.** That is a choice made per run, and the packs are
peers: the reference instrument is not a default that the RSI one is a variant of. This module
lists what is on disk and loads one by id, and no more than that.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from .pack import RubricPack, load

PACKS_RELATIVE = Path("rubrics") / "packs"

#: An override for a deployment that keeps its packs somewhere else entirely.
PACKS_DIR_ENV = "SIGHTLINE_RUBRIC_PACKS_DIR"


class PacksNotFoundError(RuntimeError):
    """The shipped packs directory could not be located."""


def candidate_paths(explicit: Path | str | None = None) -> list[Path]:
    """Where to look. An explicit setting is authoritative and is the only candidate.

    Falling back from a configured directory to one that happens to exist would turn a typo into a
    deployment that quietly reviews against a different set of rubrics than it was told to.
    """
    chosen = explicit if explicit is not None else os.environ.get(PACKS_DIR_ENV)
    if chosen:
        return [Path(chosen).expanduser()]
    # src/sightline/rubric/shipped.py -> src/sightline/rubric -> src/sightline -> src -> repo root
    return [Path.cwd() / PACKS_RELATIVE, Path(__file__).resolve().parents[3] / PACKS_RELATIVE]


def packs_dir(explicit: Path | str | None = None) -> Path:
    """The directory holding the shipped packs, or raise naming everywhere that was tried."""
    tried = candidate_paths(explicit)
    for candidate in tried:
        if candidate.is_dir():
            return candidate.resolve()
    locations = ", ".join(str(path) for path in tried)
    raise PacksNotFoundError(
        f"the shipped rubric packs were not found. Looked in: {locations}. "
        f"Set {PACKS_DIR_ENV} to the directory holding them."
    )


def pack_paths(explicit: Path | str | None = None) -> tuple[Path, ...]:
    """Every pack file on disk, in filename order. Deterministic, so a listing does not shuffle."""
    return tuple(sorted(packs_dir(explicit).glob("*.yaml")))


def shipped_packs(explicit: Path | str | None = None) -> Mapping[str, RubricPack]:
    """Load every shipped pack, keyed by its own id.

    Keyed by the id *inside* the file rather than by its filename: the id is what a run records and
    what a re-import diffs against, and a file somebody renamed must not become a different rubric.
    """
    loaded: dict[str, RubricPack] = {}
    for path in pack_paths(explicit):
        pack = load(path)
        if pack.id in loaded:
            raise PacksNotFoundError(
                f"two shipped packs both claim the id {pack.id!r}: {path.name} and another. An id "
                "is chosen once and never reused, because a run records it."
            )
        loaded[pack.id] = pack
    return loaded


def shipped_pack(pack_id: str, explicit: Path | str | None = None) -> RubricPack:
    """One shipped pack by id. Raises naming what is available rather than returning ``None``."""
    packs = shipped_packs(explicit)
    try:
        return packs[pack_id]
    except KeyError:
        known = ", ".join(sorted(packs)) or "none"
        raise KeyError(f"no shipped rubric pack with id {pack_id!r}. Available: {known}.") from None


__all__ = [
    "PACKS_DIR_ENV",
    "PACKS_RELATIVE",
    "PacksNotFoundError",
    "candidate_paths",
    "pack_paths",
    "packs_dir",
    "shipped_pack",
    "shipped_packs",
]
