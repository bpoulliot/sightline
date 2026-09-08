"""docs/tokens.css is the single source of colour.

No component stylesheet and no template may declare a colour literal. A second source of colour
drifts from the first, and the tier treatments in particular carry meaning — filled is computed,
washed is evidenced, dashed is judgment, dotted is unreachable — so a stray hex value is a
semantic bug, not a cosmetic one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

#: Colour literals in every spelling CSS accepts.
COLOUR_LITERAL = re.compile(
    r"(#[0-9a-fA-F]{3,8}\b)"
    r"|(\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color)\s*\()"
    r"|(\b(?:red|blue|green|black|white|grey|gray|yellow|orange|purple|pink|brown|cyan|"
    r"magenta|silver|gold|navy|teal|olive|maroon|lime|aqua|fuchsia)\b\s*[;}])",
)


def _package_files(repo_root: Path, suffix: str) -> list[Path]:
    return sorted((repo_root / "src" / "sightline").rglob(f"*{suffix}"))


def test_component_css_declares_no_colour_literal(repo_root: Path) -> None:
    stylesheets = _package_files(repo_root, ".css")
    assert stylesheets, "no component stylesheets were examined"
    offenders: list[str] = []
    for sheet in stylesheets:
        for lineno, line in enumerate(sheet.read_text(encoding="utf-8").splitlines(), start=1):
            code = line.split("/*", 1)[0]
            if COLOUR_LITERAL.search(code):
                offenders.append(f"{sheet.relative_to(repo_root)}:{lineno}: {line.strip()}")
    assert not offenders, f"colour literals outside docs/tokens.css: {offenders}"


def test_templates_declare_no_colour_literal(repo_root: Path) -> None:
    templates = _package_files(repo_root, ".html")
    assert templates, "no templates were examined"
    offenders: list[str] = []
    for template in templates:
        for lineno, line in enumerate(template.read_text(encoding="utf-8").splitlines(), start=1):
            if COLOUR_LITERAL.search(line) or "style=" in line:
                offenders.append(f"{template.relative_to(repo_root)}:{lineno}: {line.strip()}")
    assert not offenders, f"colour literals or inline styles in templates: {offenders}"


def test_the_guard_would_catch_a_literal() -> None:
    """A guard nobody has fired is indistinguishable from a guard that cannot fire."""
    for sample in ("color: #ff0000;", "background: rgb(1,2,3);", "border-color: red;"):
        assert COLOUR_LITERAL.search(sample), sample
    for sample in ("color: var(--ink);", "box-shadow: var(--shadow);", "grid-column: 1 / -1;"):
        assert not COLOUR_LITERAL.search(sample), sample


def test_every_colour_used_by_components_is_defined_in_tokens(repo_root: Path) -> None:
    """A var() that no token defines renders as nothing, silently."""
    tokens_text = (repo_root / "docs" / "tokens.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", tokens_text))
    assert defined, "no tokens were parsed out of docs/tokens.css"
    used: set[str] = set()
    for sheet in _package_files(repo_root, ".css"):
        used.update(re.findall(r"var\(\s*(--[a-z0-9-]+)", sheet.read_text(encoding="utf-8")))
    assert used, "the component stylesheet referenced no tokens at all"
    assert used <= defined, f"undefined tokens referenced: {sorted(used - defined)}"


@pytest.mark.parametrize("tier_class", ["tier-a", "tier-b", "tier-c", "tier-d"])
def test_all_four_tier_treatments_exist(repo_root: Path, tier_class: str) -> None:
    tokens_text = (repo_root / "docs" / "tokens.css").read_text(encoding="utf-8")
    assert f".{tier_class}" in tokens_text
