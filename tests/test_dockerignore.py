"""``.dockerignore`` keeps host bytecode out of the image at every depth (roadmap item 37).

Docker matches a bare ``.dockerignore`` pattern against the build context's root only, so
``__pycache__/`` excluded nothing under ``src/`` or ``tests/``: a locally built image carried the
developer's bytecode, and every ``.pyc`` in it carried the absolute path it was compiled under — a
username and a home directory. ``.gitignore`` means "at any depth" by the same line; this one has
to say so with ``**/``.

The file is part of the build context, not of the image, so the dev image does not carry it. The
test skips there and runs everywhere the context is — CI's checkout included. It skips only when the
Dockerfile is missing too, so a ``.dockerignore`` that disappears from a checkout fails rather than
skipping.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERIGNORE = REPO_ROOT / ".dockerignore"

#: Each pattern the file must carry, exactly as Docker reads it: anchored at no particular depth.
REQUIRED_PATTERNS = ("**/__pycache__", "**/*.py[cod]")


def _patterns() -> set[str]:
    if not DOCKERIGNORE.is_file():
        if not (REPO_ROOT / "Dockerfile").is_file():
            pytest.skip("not a build context (the dev image carries no .dockerignore)")
        pytest.fail(".dockerignore is missing beside the Dockerfile")
    lines = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


@pytest.mark.parametrize("pattern", REQUIRED_PATTERNS)
def test_bytecode_is_excluded_at_every_depth(pattern: str) -> None:
    assert pattern in _patterns()
