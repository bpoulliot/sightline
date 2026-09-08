"""Scrubbing a recorded Canvas capture until it is safe to commit.

**A development tool, not part of the pipeline.** It ships inside the package rather than beside
it so that it is importable, testable and reusable by later roadmap items, which is the point:
scrubbing done by hand is scrubbing nobody can audit, and the next person recording a fixture
should not have to re-derive the rules.

This repository is public, so a fixture recorded against a live instance is a leak unless three
things are true of it: no hostname but ``canvas.example.edu``, no credential in any header, and
no real person, course, email or instructor-authored prose anywhere in the body. A Canvas content
export carries no student submissions and no grades — worth knowing and not worth overstating —
but it does carry page text naming real people and unpublished drafts.

Two passes, and the second is the one that matters:

``sanitize``
    Rewrites. Headers are kept by **allowlist**, not dropped by denylist, because the header a
    denylist has never heard of is the one that carries the session id. Integers that look like
    ids are renumbered through a single shared table so ``"id": 918273`` and
    ``/courses/918273`` stay pointing at each other. Values under person-ish and prose-ish keys
    become synthetic placeholders, deterministically, so a diff of two captures still lines up.

``verify``
    Refuses. It re-reads the result and reports every residue it can still find: a non-example
    host, an authorization header, a token shape, an email at a real domain, or anything matching
    a caller-supplied pattern. A non-empty residue list is a non-zero exit. "Mostly clean" is not
    a state this tool has.

Command line::

    python -m sightline.canvas.sanitize capture.json \\
        --replace "Real Name=Alex Reviewer" --forbid 'real-host\\.edu' \\
        > tests/fixtures/canvas/thing.json
    python -m sightline.canvas.sanitize --check tests/fixtures/canvas/thing.json

The sanitized capture goes to stdout and the report to stderr, so a refusal writes nothing at all
rather than half a fixture. This module never opens a file for writing; :mod:`sightline.paths` is
the only module in the package allowed to, and ``tests/test_paths.py`` enforces that structurally.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Literal

#: The only host a committed fixture may name.
EXAMPLE_HOST = "canvas.example.edu"
EXAMPLE_ORIGIN = f"https://{EXAMPLE_HOST}"
EXAMPLE_EMAIL_DOMAIN = "example.edu"

#: Response headers a fixture may keep. Everything else is dropped. An allowlist because the
#: header nobody thought of is exactly the one that carries a session id.
HEADER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "content-type",
        "link",
        "www-authenticate",
        "x-rate-limit-remaining",
        "x-request-cost",
        "retry-after",
    }
)

#: Keys whose value names a person. Replaced with a deterministic synthetic person.
PERSON_KEYS: frozenset[str] = frozenset(
    {
        "name",
        "short_name",
        "sortable_name",
        "display_name",
        "user_name",
        "author_name",
        "created_by",
        "login_id",
        "sis_user_id",
        "integration_id",
    }
)

#: Keys whose value is an address. Replaced with an address at ``example.edu``.
EMAIL_KEYS: frozenset[str] = frozenset({"email", "primary_email", "login_email", "contact_email"})

#: Keys carrying instructor-authored prose. Replaced wholesale — this is the content a public
#: repository must never carry, and there is no partially safe version of it.
PROSE_KEYS: frozenset[str] = frozenset(
    {"body", "description", "message", "syllabus_body", "public_description", "bio", "title"}
)

#: Keys carrying a URL that identifies a person or a file. Replaced with an example URL.
URL_KEYS: frozenset[str] = frozenset({"avatar_url", "url", "html_url", "full_url", "calendar"})

#: Keys whose value is an id to renumber. Anything ending in ``_id`` is included as well.
ID_KEYS: frozenset[str] = frozenset({"id"})

#: Keys whose value is a course-facing code, not a person.
CODE_KEYS: frozenset[str] = frozenset({"course_code", "sis_course_id", "sis_section_id"})

#: Ids below this are left alone: they are almost always enum-ish (a page number, a count) and
#: renumbering them makes a fixture harder to read for no gain.
MIN_RENUMBERED_ID = 100

#: Where renumbered ids start. Deliberately small and obviously synthetic.
FIRST_SYNTHETIC_ID = 4000

#: What a JSON object appears to be, decided from the keys it carries. Only used to keep a course
#: name from being replaced with a person's name; when in doubt the answer is ``unknown`` and the
#: more aggressive rule wins.
ObjectKind = Literal["course", "person", "unknown"]

#: Keys that only a course object carries.
_COURSE_MARKERS: frozenset[str] = frozenset(
    {"course_code", "enrollment_term_id", "is_public", "start_at", "grading_standard_id"}
)

#: Keys that only a person object carries.
_PERSON_MARKERS: frozenset[str] = frozenset(
    {"sortable_name", "login_id", "primary_email", "avatar_url"}
)


def _object_kind(node: dict[str, Any]) -> ObjectKind:
    keys = {str(key) for key in node}
    if keys & _PERSON_MARKERS:
        return "person"
    if keys & _COURSE_MARKERS:
        return "course"
    return "unknown"


_ID_IN_PATH = re.compile(r"(?<=/)(\d{3,})(?=/|$|\?)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_HOST_IN_URL = re.compile(r"(?P<scheme>https?://)(?P<host>[^/\s\"']+)")
_TOKEN_SHAPE = re.compile(
    r"\b\d{3,6}~[A-Za-z0-9]{20,}\b|(?i:bearer)\s+[A-Za-z0-9\-._~+/]{16,}|\bsk-[A-Za-z0-9\-_]{16,}\b"
)


@dataclass
class SanitizerReport:
    """What the sanitizer did, and what it could still see afterwards."""

    values_examined: int = 0
    ids_renumbered: dict[str, str] = field(default_factory=dict)
    people_replaced: dict[str, str] = field(default_factory=dict)
    prose_replaced: list[str] = field(default_factory=list)
    hosts_rewritten: set[str] = field(default_factory=set)
    headers_dropped: list[str] = field(default_factory=list)
    literal_replacements: dict[str, str] = field(default_factory=dict)
    residues: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.residues

    def render(self) -> str:
        lines = [
            f"examined      {self.values_examined} values",
            f"ids           {len(self.ids_renumbered)} renumbered",
            f"people        {len(self.people_replaced)} replaced",
            f"prose         {len(self.prose_replaced)} field(s) replaced",
            f"hosts         {len(self.hosts_rewritten)} rewritten to {EXAMPLE_HOST}",
            f"headers       {len(self.headers_dropped)} dropped",
            f"literals      {len(self.literal_replacements)} caller-supplied replacement(s)",
        ]
        if self.residues:
            lines.append(f"RESIDUE       {len(self.residues)} finding(s):")
            lines.extend(f"  - {item}" for item in self.residues)
        else:
            lines.append("residue       none")
        return "\n".join(lines)


class Sanitizer:
    """Stateful because the mappings have to be consistent across a whole capture."""

    def __init__(self, *, literals: dict[str, str] | None = None) -> None:
        self.report = SanitizerReport()
        self._literals = dict(literals or {})
        self._next_id = FIRST_SYNTHETIC_ID
        self._next_person = 0
        self._id_map: dict[int, int] = {}
        self._person_map: dict[str, str] = {}

    # -- mappings ----------------------------------------------------------------------------

    def map_id(self, value: int) -> int:
        if value < MIN_RENUMBERED_ID:
            return value
        if value not in self._id_map:
            self._id_map[value] = self._next_id
            self._next_id += 7  # not consecutive, so a fixture cannot imply an ordering
            self.report.ids_renumbered[str(value)] = str(self._id_map[value])
        return self._id_map[value]

    def map_person(self, value: str) -> str:
        if value not in self._person_map:
            self._next_person += 1
            self._person_map[value] = f"Reviewer {_letters(self._next_person)}"
            self.report.people_replaced[_mask(value)] = self._person_map[value]
        return self._person_map[value]

    # -- the rewrite -------------------------------------------------------------------------

    def sanitize(self, document: Any) -> Any:
        return self._walk(document, key=None, kind="unknown")

    def _walk(self, node: Any, *, key: str | None, kind: ObjectKind) -> Any:
        if isinstance(node, dict):
            here = _object_kind(node)
            return {
                name: (
                    self._headers(value)
                    if name.lower() == "headers"
                    else self._walk(value, key=name, kind=here)
                )
                for name, value in node.items()
            }
        if isinstance(node, list):
            return [self._walk(item, key=key, kind=kind) for item in node]
        self.report.values_examined += 1
        if isinstance(node, bool):
            return node
        if isinstance(node, int):
            return self._integer(node, key)
        if isinstance(node, str):
            return self._string(node, key, kind)
        return node

    def _headers(self, headers: Any) -> Any:
        if not isinstance(headers, dict):
            return self._walk(headers, key="headers", kind="unknown")
        kept: dict[str, Any] = {}
        for name, value in headers.items():
            if name.lower() in HEADER_ALLOWLIST:
                kept[name] = self._string(str(value), "link" if name.lower() == "link" else None)
            else:
                self.report.headers_dropped.append(name)
        return kept

    def _integer(self, value: int, key: str | None) -> int:
        if key and (key in ID_KEYS or key.endswith("_id")):
            return self.map_id(value)
        return value

    def _string(self, value: str, key: str | None, kind: ObjectKind = "unknown") -> str:
        text = value
        for original, replacement in self._literals.items():
            if original and original in text:
                text = text.replace(original, replacement)
                self.report.literal_replacements[_mask(original)] = replacement

        if key in PROSE_KEYS:
            self.report.prose_replaced.append(key)
            return f"Synthetic {key} for a sample course. No real course text is committed here."
        if key in EMAIL_KEYS:
            return _synthetic_email(self.map_person(text))
        if key == "name" and kind == "course":
            # A course's ``name`` is not a person's. Without this, over-redaction turns every
            # fixture course into "Reviewer A", which is safe but unreadable — and a fixture
            # nobody can read is a fixture nobody checks.
            return f"Sample Course {self.map_id(abs(hash(text)) % 90000 + MIN_RENUMBERED_ID)}"
        if key in PERSON_KEYS:
            return self.map_person(text)
        if key in CODE_KEYS:
            return f"SAMPLE-{self.map_id(abs(hash(text)) % 900000 + MIN_RENUMBERED_ID)}"
        if key and (key in ID_KEYS or key.endswith("_id")) and text.isdigit():
            return str(self.map_id(int(text)))

        text = self._rewrite_hosts(text)
        text = _ID_IN_PATH.sub(lambda match: str(self.map_id(int(match[1]))), text)
        text = _EMAIL.sub(lambda match: _synthetic_email(self.map_person(match[0])), text)
        if key in URL_KEYS and "://" not in text:
            return f"{EXAMPLE_ORIGIN}/{text.lstrip('/')}"
        return text

    def _rewrite_hosts(self, text: str) -> str:
        def swap(match: re.Match[str]) -> str:
            host = match["host"]
            if host == EXAMPLE_HOST:
                return match[0]
            self.report.hosts_rewritten.add(_mask(host))
            return f"{match['scheme']}{EXAMPLE_HOST}"

        return _HOST_IN_URL.sub(swap, text)


# ---------------------------------------------------------------------------------------------
# Verification — the pass that actually gates a commit
# ---------------------------------------------------------------------------------------------


def verify(document: Any, *, forbid: list[str] | None = None) -> list[str]:
    """Return every residue found. An empty list is the only acceptable result."""
    blob = json.dumps(document, ensure_ascii=False)
    residues: list[str] = []

    for match in _HOST_IN_URL.finditer(blob):
        if match["host"] != EXAMPLE_HOST:
            residues.append(f"host {_mask(match['host'])} is not {EXAMPLE_HOST}")

    for match in _EMAIL.finditer(blob):
        if not match[0].endswith(f"@{EXAMPLE_EMAIL_DOMAIN}"):
            residues.append(f"email at a domain other than {EXAMPLE_EMAIL_DOMAIN}")

    if _TOKEN_SHAPE.search(blob):
        residues.append("a token-shaped string is present")

    for header in _all_header_names(document):
        if header.lower() not in HEADER_ALLOWLIST:
            residues.append(f"header '{header}' is outside the allowlist")

    for pattern in forbid or []:
        if re.search(pattern, blob, re.IGNORECASE):
            # The pattern itself is the caller's, but what it matched is not printed: this tool
            # runs in CI, and CI output is as public as the repository.
            residues.append("a caller-supplied forbidden pattern matched")

    # De-duplicate while keeping order, so a capture with forty copies of one host reports once.
    seen: set[str] = set()
    return [item for item in residues if not (item in seen or seen.add(item))]


def _all_header_names(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for name, value in node.items():
            if name.lower() == "headers" and isinstance(value, dict):
                found.extend(value.keys())
            else:
                found.extend(_all_header_names(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_all_header_names(item))
    return found


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def _letters(index: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA. Enough names for any capture worth committing."""
    name = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def _synthetic_email(person: str) -> str:
    return f"{person.lower().replace(' ', '.')}@{EXAMPLE_EMAIL_DOMAIN}"


def _mask(value: str) -> str:
    """Report that something was found without reprinting it.

    The sanitizer's own report is printed in CI, so it must not become the leak.
    """
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[0]}{'*' * (len(value) - 2)}{value[-1]}"


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sightline.canvas.sanitize",
        description=(
            "Scrub a recorded Canvas capture until it is safe to commit to a public repository, "
            "then verify that nothing traceable survived."
        ),
    )
    parser.add_argument("source", help="JSON capture to read")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify only: do not rewrite, just report residues and exit non-zero if any",
    )
    parser.add_argument(
        "--replace",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="literal substitution, repeatable, for anything the key rules cannot see",
    )
    parser.add_argument(
        "--forbid",
        action="append",
        default=[],
        metavar="REGEX",
        help="pattern that must not survive, repeatable (e.g. the real hostname)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    with open(args.source, encoding="utf-8") as handle:
        document = json.load(handle)

    if args.check:
        residues = verify(document, forbid=args.forbid)
        report = SanitizerReport(residues=residues)
        print(report.render(), file=sys.stderr)
        return 0 if report.clean else 1

    literals: dict[str, str] = {}
    for pair in args.replace:
        original, _, replacement = pair.partition("=")
        if not original or not replacement:
            print(f"--replace needs OLD=NEW, got {pair!r}", file=sys.stderr)
            return 2
        literals[original] = replacement

    sanitizer = Sanitizer(literals=literals)
    cleaned = sanitizer.sanitize(document)
    sanitizer.report.residues = verify(cleaned, forbid=args.forbid)
    print(sanitizer.report.render(), file=sys.stderr)

    if not sanitizer.report.clean:
        print(
            "refusing to write: the capture is not clean. Add --replace rules for what is left.",
            file=sys.stderr,
        )
        return 1

    # The result goes to stdout and the report to stderr, so redirection puts the capture where
    # the operator wants it. This tool never opens a file for writing: sightline.paths is the only
    # module in the package allowed to, and tests/test_paths.py enforces that structurally. It
    # also means a refusal writes nothing at all rather than half a fixture.
    sys.stdout.write(json.dumps(cleaned, indent=2, ensure_ascii=False, sort_keys=False) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main() in the suite
    raise SystemExit(main())
