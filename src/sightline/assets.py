"""Locating the design tokens, and the fonts the printed report is set in.

``docs/tokens.css`` is the approved palette and the **single source of colour** for every
template. It is not copied into the package: a copy is a second source, and a second source drifts.
The file is located at runtime and served verbatim, and ``tests/test_templates.py`` asserts that
no component stylesheet or template declares a colour literal of its own.

Search order, first hit wins:

1. ``SIGHTLINE_TOKENS_CSS``, for a deployment that puts it somewhere else entirely.
2. ``<cwd>/docs/tokens.css`` — the container layout, where ``WORKDIR`` is ``/app``.
3. ``<repo>/docs/tokens.css`` — a source checkout, resolved relative to this file.

Nothing is found silently: :func:`resolve_tokens_css` raises rather than returning ``None``, so a
missing palette fails at startup instead of rendering an unstyled page nobody notices.
"""

from __future__ import annotations

from pathlib import Path

TOKENS_RELATIVE = Path("docs") / "tokens.css"


class TokensNotFoundError(RuntimeError):
    """The design tokens could not be located."""


def candidate_paths(explicit: Path | str | None = None) -> list[Path]:
    """Where to look. An explicit setting is authoritative: it is the only candidate.

    Falling back from a path someone configured to one that happens to exist would turn a typo
    into a page that renders correctly on the developer's machine and wrongly everywhere else.
    """
    if explicit:
        return [Path(explicit).expanduser()]
    candidates: list[Path] = []
    candidates.append(Path.cwd() / TOKENS_RELATIVE)
    # src/sightline/assets.py -> src/sightline -> src -> repository root
    candidates.append(Path(__file__).resolve().parents[2] / TOKENS_RELATIVE)
    return candidates


def resolve_tokens_css(explicit: Path | str | None = None) -> Path:
    """Return the path to ``tokens.css`` or raise :class:`TokensNotFoundError`."""
    tried = candidate_paths(explicit)
    for candidate in tried:
        if candidate.is_file():
            return candidate.resolve()
    locations = ", ".join(str(path) for path in tried)
    raise TokensNotFoundError(
        f"design tokens not found. Looked in: {locations}. "
        "Set SIGHTLINE_TOKENS_CSS to the path of docs/tokens.css."
    )


# ---------------------------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------------------------

#: Where the vendored fonts live, relative to the repository root and to the container's WORKDIR.
#: Each font file sits beside its licence file; see ``assets/fonts/README.md``.
FONTS_RELATIVE = Path("assets") / "fonts"


class FontsNotFoundError(RuntimeError):
    """The vendored fonts could not be located."""


def resolve_fonts_dir() -> Path:
    """The directory of vendored fonts, or raise :class:`FontsNotFoundError`.

    The same two places as the tokens: the working directory (the container, ``WORKDIR /app``)
    and the source checkout. A PDF set in whatever fonts the host happens to have would be a
    different document on every machine, so a missing directory is an error, not a fallback.
    """
    tried = [Path.cwd() / FONTS_RELATIVE, Path(__file__).resolve().parents[2] / FONTS_RELATIVE]
    for candidate in tried:
        if candidate.is_dir() and any(candidate.glob("*.ttf")):
            return candidate.resolve()
    locations = ", ".join(str(path) for path in tried)
    raise FontsNotFoundError(f"the report fonts were not found. Looked in: {locations}.")
