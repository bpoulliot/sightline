"""The PDF course report, and reading a finished course back from its review file (item 14).

- :mod:`sightline.report.loader` turns a review file into the live objects the voice layer takes.
- :mod:`sightline.report.model` arranges the rendered review into the report's sections.
- :mod:`sightline.report.pdf` prints the model to PDF bytes.
- ``python -m sightline.report export COURSE`` writes the PDF and the workbook through the
  export gate, and only through it.
"""

from __future__ import annotations

from .loader import LoadedReview, ReviewFileError, load_review, parse_review, read_review
from .model import ReportError, ReportModel, build_report
from .pdf import REPORT_FILENAME, export_report, to_html, to_pdf

__all__ = [
    "REPORT_FILENAME",
    "LoadedReview",
    "ReportError",
    "ReportModel",
    "ReviewFileError",
    "build_report",
    "export_report",
    "load_review",
    "parse_review",
    "read_review",
    "to_html",
    "to_pdf",
]
