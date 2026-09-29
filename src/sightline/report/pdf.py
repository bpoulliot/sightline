"""The report model as HTML, and the HTML as PDF bytes (roadmap item 14).

HTML, then WeasyPrint: one process in one container, no headless browser (SPEC §04). US Letter,
the light palette pinned, the vendored fonts and nothing the host happens to have installed.

**Bytes, never a path.** :meth:`weasyprint.HTML.write_pdf` given a target writes a file, and a
library call is a write the structural guard in ``tests/test_paths.py`` cannot see by its name
alone — so that guard now refuses ``write_pdf`` called with a target, and this module calls it with
none and hands the bytes to :meth:`sightline.batch.gate.ExportGate.save`, the only way to
``reports/``.

**The same input gives the same bytes.** Checked against WeasyPrint 70.0, there are two clocks:

1. *The document's own dates.* WeasyPrint writes a creation or modification date only from
   ``dcterms.created`` / ``dcterms.modified`` metadata in the document, and a file identifier only
   when asked for one; it does not read ``SOURCE_DATE_EPOCH`` itself. So both dates are pinned in
   the template to the moment a person opened the review, which the caller passes in, and no
   identifier is written.
2. *The embedded font subsets.* Where ``libharfbuzz-subset`` is not installed, WeasyPrint subsets
   fonts with fontTools, which stamps each subset's ``head.modified`` with the clock — unless
   ``SOURCE_DATE_EPOCH`` is set, which fontTools honours. (HarfBuzz's subsetter, used when the
   library is present, as in the image, copies the stamp from the font file.) So the render runs
   with ``SOURCE_DATE_EPOCH`` set to the same opened-at moment, under a lock, and the variable is
   put back afterwards. Either subsetter then gives the same bytes on every run.

What the bytes still depend on is the native text stack — pango, harfbuzz, fontconfig — and
``uv.lock``; see the README on regenerating the sample report.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, PackageLoader, StrictUndefined

from ..assets import resolve_fonts_dir, resolve_tokens_css
from .model import ReportModel

if TYPE_CHECKING:
    from ..batch.gate import ExportGate

#: The exported file's name inside the course's export directory.
REPORT_FILENAME = "report.pdf"

#: The faces the report is set in: family, file, weight, style. Static weights only; each file sits
#: beside its licence in ``assets/fonts/``.
FONT_FACES: tuple[tuple[str, str, int, str], ...] = (
    ("Instrument Serif", "InstrumentSerif-Regular.ttf", 400, "normal"),
    ("IBM Plex Sans", "IBMPlexSans-Regular.ttf", 400, "normal"),
    ("IBM Plex Sans", "IBMPlexSans-Italic.ttf", 400, "italic"),
    ("IBM Plex Sans", "IBMPlexSans-SemiBold.ttf", 600, "normal"),
    ("IBM Plex Mono", "IBMPlexMono-Regular.ttf", 400, "normal"),
    ("IBM Plex Mono", "IBMPlexMono-SemiBold.ttf", 600, "normal"),
)

_ENV = Environment(
    loader=PackageLoader("sightline.report", "templates"),
    autoescape=True,
    undefined=StrictUndefined,
    keep_trailing_newline=True,
)


def to_html(model: ReportModel) -> str:
    """The report as one HTML document. Deterministic: the same model gives the same string."""
    return _ENV.get_template("report.html").render(
        model=model, created=model.created.strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def font_faces(fonts: Path, faces: Sequence[tuple[str, str, int, str]] = FONT_FACES) -> str:
    """``@font-face`` rules for the vendored fonts, relative to ``fonts``. Raises if one is missing."""
    rules = []
    for family, filename, weight, style in faces:
        if not (fonts / filename).is_file():
            raise FileNotFoundError(f"the report font {filename} is not in {fonts}")
        rules.append(
            f'@font-face {{ font-family: "{family}"; src: url("{filename}"); '
            f"font-weight: {weight}; font-style: {style}; }}"
        )
    return "\n".join(rules)


def to_pdf(
    model: ReportModel,
    *,
    fonts: Path | None = None,
    tokens: Path | None = None,
) -> bytes:
    """The report as PDF bytes. Never a path: the export gate does the writing."""
    from weasyprint import CSS, HTML
    from weasyprint.text.fonts import FontConfiguration

    fonts_dir = fonts or resolve_fonts_dir()
    tokens_css = tokens or resolve_tokens_css()
    config = FontConfiguration()
    stylesheets = [
        CSS(
            string=font_faces(fonts_dir),
            base_url=fonts_dir.as_uri() + "/",
            font_config=config,
        ),
        CSS(string=tokens_css.read_text(encoding="utf-8"), font_config=config),
        CSS(
            string=resources.files("sightline.report").joinpath("print.css").read_text("utf-8"),
            font_config=config,
        ),
    ]
    document = HTML(string=to_html(model))
    with source_date(int(model.created.timestamp())):
        return document.write_pdf(stylesheets=stylesheets, font_config=config, pdf_tags=True)


#: Held while ``SOURCE_DATE_EPOCH`` is pinned, so two renders cannot interleave their settings.
_SOURCE_DATE = threading.Lock()


@contextmanager
def source_date(epoch: int) -> Iterator[None]:
    """Run with ``SOURCE_DATE_EPOCH`` set to ``epoch``, then restore whatever was there."""
    with _SOURCE_DATE:
        before = os.environ.get("SOURCE_DATE_EPOCH")
        os.environ["SOURCE_DATE_EPOCH"] = str(epoch)
        try:
            yield
        finally:
            if before is None:
                del os.environ["SOURCE_DATE_EPOCH"]
            else:
                os.environ["SOURCE_DATE_EPOCH"] = before


def export_report(gate: ExportGate, course_id: str, data: bytes) -> Path:
    """Write the PDF for one course through the export gate (SPEC §09).

    Refused, by the gate, for a course whose review nobody has opened.
    """
    return gate.save(course_id, REPORT_FILENAME, data)


__all__ = ["FONT_FACES", "REPORT_FILENAME", "export_report", "font_faces", "to_html", "to_pdf"]
