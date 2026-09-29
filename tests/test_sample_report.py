"""The committed sample report re-renders from committed files alone (operator decision D1).

``examples/sample-report/`` is what ``scripts/make-sample-report.sh`` produced: a normal batch run
over ``examples/sample-course`` into a scratch data folder, the review opened, the export through
the gate. The model's output varies between runs, so the committed review file is the input, and
this re-renders from it — no model host, no network, no clock — and compares:

- the PDF **byte for byte**, in the environment it was made in. The bytes depend on the native text
  stack (pango, harfbuzz, fontconfig, freetype) as well as on ``uv.lock``, so elsewhere — the
  runner's own image, a developer's machine — the comparison is skipped, naming what differs. The
  dev image sets ``SIGHTLINE_SAMPLE_REPORT_REQUIRED``, and there it may not skip: CI's image job
  runs this file in that image, so the bar holds in at least one job on every change;
- the workbook **cell by cell**, everywhere (item 13's bar: a zip's bytes follow its writer);
- and that nothing committed carries a real host, an absolute path, an address or a token.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from pdf_reader import read_pdf
from sightline.report import build_report, parse_review, to_pdf
from sightline.report.pdf import render_environment
from sightline.workbook import build_workbook

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample-report"
FILES = ("review.json", "render.json", "report.pdf", "review.xlsx")
REQUIRED = os.environ.get("SIGHTLINE_SAMPLE_REPORT_REQUIRED") == "1"
REMEDY = (
    "Re-run ./scripts/make-sample-report.sh and commit examples/sample-report/ with the change "
    "that moved it (README, 'The sample report')."
)


@pytest.fixture(scope="module")
def committed() -> dict[str, bytes]:
    missing = [name for name in FILES if not (SAMPLE / name).is_file()]
    assert not missing, f"examples/sample-report/ is missing {missing}. {REMEDY}"
    return {name: (SAMPLE / name).read_bytes() for name in FILES}


@pytest.fixture(scope="module")
def render(committed) -> dict[str, Any]:
    return json.loads(committed["render.json"])


@pytest.fixture(scope="module")
def loaded(committed):
    return parse_review(committed["review.json"].decode("utf-8"))


@pytest.fixture(scope="module")
def opened(render) -> datetime:
    moment = datetime.fromisoformat(render["opened_at"])
    assert moment.tzinfo is not None
    return moment


@pytest.fixture(scope="module")
def rerendered(loaded, opened) -> bytes:
    return to_pdf(build_report(loaded, created=opened))


def _difference(then: dict[str, str], now: dict[str, str]) -> str:
    names = sorted(set(then) | set(now))
    moved = [f"{n} {then.get(n)} -> {now.get(n)}" for n in names if then.get(n) != now.get(n)]
    return "; ".join(moved) or "none"


def test_the_committed_pdf_is_byte_identical_to_a_rerender(render, committed, rerendered) -> None:
    then = render["environment"]
    now = render_environment()
    if now != then and not REQUIRED:
        pytest.skip(
            "the sample was rendered in the dev image and this is another environment "
            f"({_difference(then, now)}); CI's image job runs this comparison there"
        )
    assert rerendered == committed["report.pdf"], (
        f"the committed sample PDF does not match a re-render. Environment then -> now: "
        f"{_difference(then, now)}. {REMEDY}"
    )


def test_the_environment_check_itself_notices_a_moved_library(render) -> None:
    """The skip above rests on this comparison, so it is fired in both directions."""
    now = render_environment()
    assert {"pango", "harfbuzz", "weasyprint", "fonttools"} <= set(now)
    moved = dict(now, pango="0.0.0")
    assert moved != now and _difference(now, moved) == f"pango {now['pango']} -> 0.0.0"
    assert _difference(now, dict(now)) == "none"
    assert set(render["environment"]) == set(now)


def test_a_rerender_is_byte_identical_to_a_second_rerender(loaded, opened, rerendered) -> None:
    assert to_pdf(build_report(loaded, created=opened)) == rerendered


def test_the_committed_workbook_matches_a_rerender_cell_by_cell(committed, loaded, opened) -> None:
    model = build_workbook(
        loaded.projection,
        loaded.pack,
        base_url=loaded.base_url or "",
        created=opened,
        report=loaded.report,
        extractions=loaded.extractions,
        course_id=loaded.course_id,
    )
    book = openpyxl.load_workbook(BytesIO(committed["review.xlsx"]))
    read = [
        (sheet.title, [[cell.value for cell in row] for row in sheet.iter_rows()])
        for sheet in book.worksheets
    ]
    assert read == model.values()


def test_the_committed_sample_is_the_sample_course_through_the_gate(loaded, render, committed):
    assert loaded.base_url == "https://canvas.example.edu"
    assert loaded.projection.pack_id == "uccs-oscqr-3-1-3"
    review = json.loads(committed["review.json"])
    assert review["course"]["reference"] == "examples/sample-course"
    # A real run: the extraction stage gathered located evidence with a model.
    assert loaded.extractions, "the committed review carries no located evidence"
    assert render["model"]
    pages = read_pdf(committed["report.pdf"])
    assert len(pages) > 5


# -- nothing committed identifies anybody or anything ---------------------------------------------

_HOST = re.compile(r"\b(?:https?|ftp)://([^/\s\"'<>)\\]+)", re.I)
_ABSOLUTE = re.compile(r"(?<![\w.:/])/(?:home|Users|root|app|tmp|scratch|var|mnt|srv|opt)/", re.I)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_TOKEN = re.compile(r"\b(?:Bearer\s+\S+|[A-Za-z0-9_\-]{40,})")
_ADDRESS = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")


def _readable(name: str, data: bytes) -> str:
    """Everything in a committed file a person or a scanner could read."""
    if name.endswith(".pdf"):
        pages = read_pdf(data)
        text = "\n".join(page.text + "\n" + "\n".join(page.links) for page in pages)
        return text + "\n" + data.decode("latin-1")
    if name.endswith(".xlsx"):
        with zipfile.ZipFile(BytesIO(data)) as archive:
            return "\n".join(archive.read(n).decode("utf-8") for n in archive.namelist())
    return data.decode("utf-8")


def test_nothing_committed_carries_a_real_host_path_address_or_token(committed) -> None:
    problems: list[str] = []
    for name, data in committed.items():
        text = _readable(name, data)
        for host in _HOST.findall(text):
            bare = host.split("@")[-1].split(":")[0].lower()
            allowed = bare.endswith(".example.edu") or bare in {
                # Namespaces written by the file formats themselves, not hosts anybody runs.
                "schemas.openxmlformats.org",
                "schemas.microsoft.com",
                "purl.org",
                "www.w3.org",
                "ns.adobe.com",
                "www.aiim.org",
            }
            if not allowed:
                problems.append(f"{name}: host {host}")
        problems.extend(f"{name}: path {m}" for m in _ABSOLUTE.findall(text))
        problems.extend(f"{name}: address {m}" for m in _EMAIL.findall(text))
        problems.extend(f"{name}: ip {m}" for m in _ADDRESS.findall(text) if m != "0.0.0.0")
        if not name.endswith((".pdf", ".xlsx")):
            problems.extend(f"{name}: token-shaped {m[:12]}…" for m in _TOKEN.findall(text))
    assert problems == [], problems[:20]


def test_the_scan_fires_on_what_it_looks_for() -> None:
    """Both directions: the patterns catch a planted leak and pass the sample's own host."""
    planted = (
        "see https://canvas.realcollege.edu/courses/1 and /home/someone/data, write to "
        "a.person@realcollege.edu, host 10.1.2.3, Bearer abcdef"
    )
    assert _HOST.findall(planted) == ["canvas.realcollege.edu"]
    assert _ABSOLUTE.findall(planted) == ["/home/"]
    assert _EMAIL.findall(planted) == ["a.person@realcollege.edu"]
    assert _ADDRESS.findall(planted) == ["10.1.2.3"]
    assert _TOKEN.findall(planted) == ["Bearer abcdef"]
    assert not _ABSOLUTE.findall("https://canvas.example.edu/courses/4410/pages/start-here")
