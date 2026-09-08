"""Turning whatever a designer pasted into a course id.

Three input forms, one result. A designer copies a URL out of the address bar, types a bare id
from a spreadsheet, or uploads a CSV that a registrar exported. All three resolve to the same
course id, and ``tests/test_canvas_refs.py`` asserts exactly that.

Nothing here talks to Canvas. Parsing is separated from resolution on purpose: a malformed
reference should be reported the moment it is pasted, without a round trip, and the parser is then
testable without a token.

**Nothing is dropped silently.** SPEC §11 names silent under-detection as the failure mode that
kills a tool like this, and a CSV importer that skips the rows it did not understand is the same
disease in miniature. Every batch result reports how many rows were examined, how many were
accepted, and — one by one, with a reason and a remedy — which were not.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import unquote, urlsplit

from .errors import CanvasError

#: Forms a reference can arrive in. Recorded on the result because a later screen shows the user
#: what it understood, and "it read this as a URL" is a checkable statement.
RefForm = Literal["numeric-id", "course-url", "sis-id"]

#: ``/courses/<segment>`` anywhere in a path. Canvas hangs everything off this prefix, so a link
#: to a page, an assignment, a module item or a discussion all carry the course id in the same
#: place — which is why a trailing ``/pages/foo`` needs no special case.
_COURSE_SEGMENT = re.compile(r"(?:^|/)courses/(?P<segment>[^/?#]+)", re.IGNORECASE)

_NUMERIC = re.compile(r"^[0-9]+$")

#: Canvas accepts an SIS id in place of a numeric one, spelled ``sis_course_id:ABC-123``, and its
#: own UI produces URLs in that form. Supported because a registrar's CSV is full of them.
_SIS = re.compile(r"^sis_course_id:(?P<sis>[^\s/?#]+)$", re.IGNORECASE)

#: Wrapping a pasted value picks up quotes, angle brackets and Markdown link syntax.
_TRIM = " \t\r\n\"'<>()[]"

#: Header names that mean "this is the column". Lower-cased and stripped of non-letters before
#: comparison, so ``Course URL``, ``course_url`` and ``Course-URL`` all match.
_HEADER_NAMES: tuple[str, ...] = (
    "courseid",
    "canvascourseid",
    "canvasid",
    "id",
    "course",
    "courseurl",
    "canvasurl",
    "url",
    "link",
    "courselink",
    "sisid",
    "siscourseid",
)


class CourseRefError(CanvasError):
    """A reference could not be parsed into a course id."""

    default_remedy = (
        "Paste a Canvas course URL (https://canvas.example.edu/courses/4410), a bare numeric "
        "course id (4410), or upload a CSV with a column of either."
    )


@dataclass(frozen=True)
class CourseRef:
    """One successfully parsed reference."""

    raw: str
    course_id: str
    form: RefForm
    #: Host from the pasted URL, when there was one. ``None`` for a bare id.
    host: str | None = None
    #: 1-based line or row the reference came from, when it came from a batch.
    line: int | None = None

    @property
    def api_segment(self) -> str:
        """The path segment to put after ``/api/v1/courses/``.

        A numeric id is itself; an SIS id keeps its ``sis_course_id:`` prefix, which Canvas
        requires and which must not be percent-encoded — Canvas matches on the literal colon.
        """
        return self.course_id


@dataclass(frozen=True)
class RefRejection:
    """One reference that could not be used, and what to do about it."""

    raw: str
    reason: str
    remedy: str
    line: int | None = None


@dataclass(frozen=True)
class RefBatch:
    """The result of parsing many references at once. Every count is reported, including zero."""

    accepted: list[CourseRef] = field(default_factory=list)
    rejected: list[RefRejection] = field(default_factory=list)
    #: References that repeat one already accepted. Reported rather than discarded quietly.
    duplicates: list[RefRejection] = field(default_factory=list)
    #: Non-empty lines or rows the parser looked at.
    examined: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.accepted) and not self.rejected

    @property
    def course_ids(self) -> list[str]:
        return [ref.course_id for ref in self.accepted]

    def summary(self) -> str:
        """A sentence of counts. Declarative, per the tier A register in SPEC §08."""
        parts = [f"{self.examined} examined", f"{len(self.accepted)} resolved"]
        if self.duplicates:
            parts.append(f"{len(self.duplicates)} repeated")
        if self.rejected:
            parts.append(f"{len(self.rejected)} not understood")
        return ", ".join(parts) + "."


@dataclass(frozen=True)
class CsvBatch(RefBatch):
    """A :class:`RefBatch` from a CSV, plus how the column was chosen."""

    header: list[str] | None = None
    column_index: int | None = None
    column_name: str | None = None
    #: Why that column and not another. Shown to the user, because a CSV importer that guesses
    #: without saying so is one that silently reads the wrong column.
    column_choice: str = ""

    def summary(self) -> str:
        base = super().summary()
        if self.column_choice:
            return f"{base} {self.column_choice}"
        return base


# ---------------------------------------------------------------------------------------------
# Base URL
# ---------------------------------------------------------------------------------------------


def normalize_base_url(text: str) -> str:
    """Return the scheme-and-host root of a Canvas instance, or raise.

    Pasting ``canvas.example.edu/courses/4410`` as the base URL is the single most common
    configuration mistake, and it produces a 404 on every call with nothing pointing at the cause.
    So the path is stripped rather than rejected, and the caller reports what was stored.
    """
    candidate = text.strip().strip(_TRIM)
    if not candidate:
        raise CourseRefError(
            "The Canvas base URL is empty.",
            remedy="Enter the root of your Canvas instance, for example https://canvas.example.edu",
        )
    if "//" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlsplit(candidate)
    if parsed.scheme not in {"http", "https"}:
        raise CourseRefError(
            f"'{parsed.scheme}' is not a URL scheme Sightline can use.",
            remedy="Use https:// — or http:// for a local instance that has no certificate.",
        )
    if not parsed.hostname:
        raise CourseRefError(
            "That base URL has no hostname.",
            remedy="Enter the root of your Canvas instance, for example https://canvas.example.edu",
        )
    return f"{parsed.scheme}://{parsed.netloc}"


def host_of(url: str | None) -> str | None:
    """Hostname of a URL, tolerating a missing scheme. ``None`` in, ``None`` out."""
    if not url:
        return None
    return urlsplit(url if "//" in url else f"//{url}").hostname


# ---------------------------------------------------------------------------------------------
# One reference
# ---------------------------------------------------------------------------------------------


def parse_course_ref(
    text: str,
    *,
    expected_host: str | None = None,
    line: int | None = None,
) -> CourseRef:
    """Parse one reference, or raise :class:`CourseRefError` saying why it could not be.

    ``expected_host`` is the configured Canvas host. When a pasted URL names a different one the
    reference is rejected rather than resolved against the wrong instance: two Canvas instances
    both have a course 4410, and reviewing the wrong one is a failure that looks like success.
    """
    raw = text.strip().strip(_TRIM)
    if not raw:
        raise CourseRefError("Empty reference.", detail=_at(line))

    if _NUMERIC.match(raw):
        return CourseRef(raw=raw, course_id=raw, form="numeric-id", line=line)

    sis = _SIS.match(raw)
    if sis:
        return CourseRef(raw=raw, course_id=f"sis_course_id:{sis['sis']}", form="sis-id", line=line)

    candidate = raw
    if "//" not in candidate and not candidate.startswith("/"):
        # ``canvas.example.edu/courses/4410`` — a host with no scheme. Only treat it as one when
        # it actually looks like a host, so that a stray word is rejected as a word.
        head = candidate.split("/", 1)[0]
        if "." in head and " " not in head:
            candidate = f"//{candidate}"

    parsed = urlsplit(candidate)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        raise CourseRefError(
            f"'{raw}' is not a Canvas course URL.",
            detail=_at(line),
            remedy=f"'{parsed.scheme}:' is not a scheme Sightline follows. Paste an http(s) URL.",
        )

    path = unquote(parsed.path or candidate)
    match = _COURSE_SEGMENT.search(path)
    if not match:
        raise CourseRefError(
            f"'{_clip(raw)}' is neither a course id nor a Canvas course URL.",
            detail=_at(line),
        )

    segment = match["segment"].strip()
    host = parsed.hostname

    if expected_host and host and host.lower() != expected_host.lower():
        raise CourseRefError(
            f"That URL points at {host}, but Sightline is configured for {expected_host}.",
            detail=_at(line),
            remedy=(
                f"Paste a URL from {expected_host}, or change the Canvas base URL on the Settings "
                "screen if you meant to review the other instance."
            ),
        )

    if _NUMERIC.match(segment):
        return CourseRef(raw=raw, course_id=segment, form="course-url", host=host, line=line)

    sis = _SIS.match(segment)
    if sis:
        return CourseRef(
            raw=raw,
            course_id=f"sis_course_id:{sis['sis']}",
            form="sis-id",
            host=host,
            line=line,
        )

    raise CourseRefError(
        f"'{_clip(segment)}' is not a course id.",
        detail=_at(line),
        remedy=(
            "A Canvas course URL has a number after /courses/, as in /courses/4410. A URL for a "
            "course's front page or a section list does not carry one."
        ),
    )


def _at(line: int | None) -> str | None:
    return None if line is None else f"Line {line}."


def _clip(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


# ---------------------------------------------------------------------------------------------
# Many references
# ---------------------------------------------------------------------------------------------


def parse_course_refs(text: str, *, expected_host: str | None = None) -> RefBatch:
    """Parse a pasted block: one reference per line, or comma-separated, or both."""
    accepted: list[CourseRef] = []
    rejected: list[RefRejection] = []
    duplicates: list[RefRejection] = []
    seen: dict[str, int] = {}
    examined = 0

    for lineno, line in enumerate(text.splitlines(), start=1):
        for token in _split_line(line):
            examined += 1
            _absorb(token, lineno, expected_host, accepted, rejected, duplicates, seen)

    return RefBatch(accepted=accepted, rejected=rejected, duplicates=duplicates, examined=examined)


def _split_line(line: str) -> list[str]:
    """Split one pasted line into candidate references.

    Commas, semicolons and tabs always separate: a URL never contains one unencoded.

    A **space** separates only when every piece that results is itself a reference. Splitting on
    spaces unconditionally turns one unusable line — ``not a course`` — into three rejections that
    quote words the user never meant as references, which is a report that misdescribes its own
    input. Requiring every piece to parse keeps ``4410 4417 4424`` working and leaves prose whole.
    """
    pieces: list[str] = []
    for chunk in re.split(r"[,;\t]+", line.strip()):
        chunk = chunk.strip()
        if not chunk:
            continue
        words = chunk.split()
        if len(words) > 1 and all(_parses(word, None) for word in words):
            pieces.extend(words)
        else:
            pieces.append(chunk)
    return pieces


def _absorb(
    token: str,
    line: int,
    expected_host: str | None,
    accepted: list[CourseRef],
    rejected: list[RefRejection],
    duplicates: list[RefRejection],
    seen: dict[str, int],
) -> None:
    try:
        ref = parse_course_ref(token, expected_host=expected_host, line=line)
    except CourseRefError as exc:
        rejected.append(RefRejection(raw=token, reason=exc.summary, remedy=exc.remedy, line=line))
        return
    first = seen.get(ref.course_id)
    if first is not None:
        duplicates.append(
            RefRejection(
                raw=token,
                reason=f"Course {ref.course_id} was already listed on line {first}.",
                remedy="Nothing to do — it is reviewed once.",
                line=line,
            )
        )
        return
    seen[ref.course_id] = line
    accepted.append(ref)


# ---------------------------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------------------------

#: A registrar's export can be long, and a runaway file should fail as a stated limit rather than
#: as a memory error. Sixteen courses is the batch cap (SPEC §09); the parser reads far more than
#: that so the user can see everything they uploaded and choose.
MAX_CSV_ROWS = 5000


def parse_course_ref_csv(
    data: bytes | str,
    *,
    expected_host: str | None = None,
    filename: str | None = None,
) -> CsvBatch:
    """Parse an uploaded CSV, choosing the column of course references and saying why.

    Column choice is a named header first — ``course_url``, ``course_id``, ``id`` and their
    spellings — and otherwise the column that parsed the most references. Both outcomes are
    reported in ``column_choice``, because an importer that guesses without saying so is one that
    silently reads the wrong column.
    """
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    if not text.strip():
        return CsvBatch(
            column_choice=f"{_name(filename)} is empty.",
            rejected=[
                RefRejection(
                    raw=_name(filename),
                    reason="The uploaded file has no rows.",
                    remedy="Upload a CSV with a column of course URLs or course ids.",
                )
            ],
        )

    dialect: type[csv.Dialect] | csv.Dialect
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    # A wholly blank row is formatting — a trailing newline, a spacer between blocks — and
    # dropping it is not under-detection. A row with content in other columns and nothing in the
    # reference column is a different thing entirely, and is reported below, one row at a time.
    rows = [
        row for row in csv.reader(io.StringIO(text), dialect) if any(cell.strip() for cell in row)
    ]
    rows = rows[:MAX_CSV_ROWS]

    if not rows:
        return CsvBatch(
            column_choice=f"{_name(filename)} has no non-empty rows.",
            rejected=[
                RefRejection(
                    raw=_name(filename),
                    reason="Every row in the file was blank.",
                    remedy="Upload a CSV with a column of course URLs or course ids.",
                )
            ],
        )

    index, header, choice = _choose_column(rows, expected_host)
    if index is None:
        seen_header = ", ".join(cell.strip() for cell in rows[0][:8]) or "(no header row)"
        return CsvBatch(
            header=rows[0],
            column_choice=choice,
            examined=len(rows),
            rejected=[
                RefRejection(
                    raw=_name(filename),
                    reason="No column in the file held course URLs or course ids.",
                    remedy=(
                        "Name the column course_url or course_id, or make sure one column holds "
                        f"values like 4410 or https://canvas.example.edu/courses/4410. "
                        f"Columns seen: {seen_header}."
                    ),
                )
            ],
        )

    body = rows[1:] if header is not None else rows
    offset = 2 if header is not None else 1

    accepted: list[CourseRef] = []
    rejected: list[RefRejection] = []
    duplicates: list[RefRejection] = []
    seen: dict[str, int] = {}
    examined = 0

    for position, row in enumerate(body):
        rowno = position + offset
        cell = row[index].strip() if index < len(row) else ""
        if not cell:
            rejected.append(
                RefRejection(
                    raw="",
                    reason="The reference column is empty on this row.",
                    remedy="Fill the cell in, or delete the row.",
                    line=rowno,
                )
            )
            examined += 1
            continue
        examined += 1
        _absorb(cell, rowno, expected_host, accepted, rejected, duplicates, seen)

    return CsvBatch(
        accepted=accepted,
        rejected=rejected,
        duplicates=duplicates,
        examined=examined,
        header=header,
        column_index=index,
        column_name=(header[index].strip() if header and index < len(header) else None),
        column_choice=choice,
    )


def _name(filename: str | None) -> str:
    return filename or "The uploaded file"


def _normalized_header(cell: str) -> str:
    return re.sub(r"[^a-z]", "", cell.lower())


def _choose_column(
    rows: list[list[str]], expected_host: str | None
) -> tuple[int | None, list[str] | None, str]:
    """Return ``(column index, header row or None, explanation)``."""
    first = rows[0]

    # A header row is one whose cells are names rather than references. Test it by parsing: if a
    # cell parses as a course reference it is data, not a header.
    first_row_is_data = any(_parses(cell, expected_host) for cell in first)

    if not first_row_is_data:
        wanted = {_normalized_header(cell): position for position, cell in enumerate(first)}
        for name in _HEADER_NAMES:
            if name in wanted:
                position = wanted[name]
                label = first[position].strip()
                return position, first, f"Read the column headed '{label}'."

    # No usable header name: score every column by how many of its cells parse.
    body = rows if first_row_is_data else rows[1:]
    width = max((len(row) for row in body), default=0)
    scores = [
        sum(1 for row in body if index < len(row) and _parses(row[index], expected_host))
        for index in range(width)
    ]
    if not scores or max(scores) == 0:
        return None, (None if first_row_is_data else first), "No column held course references."

    best = scores.index(max(scores))
    header = None if first_row_is_data else first
    label = header[best].strip() if header and best < len(header) else f"column {best + 1}"
    return (
        best,
        header,
        f"No named column, so read '{label}': it parsed {scores[best]} of {len(body)} rows, "
        f"more than any other column.",
    )


def _parses(cell: str, expected_host: str | None) -> bool:
    try:
        parse_course_ref(cell, expected_host=expected_host)
    except CourseRefError:
        return False
    return True
