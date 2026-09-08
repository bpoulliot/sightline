"""The sanitizer, tested by giving it something dirty and checking what comes out.

The item's rule is that fixtures are synthetic or "sanitized past the point of tracing back", and
that the scrubbing is done by a script so the next item can reuse it and so it is auditable. A
sanitizer nobody has fed a real-looking capture is a sanitizer nobody knows the behaviour of, so
this file builds one — with a plausible hostname, a plausible token, real-looking names and
instructor prose — and asserts that none of it survives.

Nothing here is a real capture. The "dirty" document is invented for the test, and
``example.net`` / ``institution-under-test.example`` are reserved names.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout

import pytest

from sightline.canvas.sanitize import (
    EXAMPLE_HOST,
    Sanitizer,
    main,
    verify,
)

DIRTY = {
    "interactions": [
        {
            "method": "GET",
            "path": "/api/v1/courses/918273",
            "request": {
                "headers": {
                    "Authorization": "Bearer 4021" + "~" + "PretendThisWasCapturedLive" + "9" * 10,
                    "Cookie": "_normandy_session=abc123",
                    "Accept": "application/json",
                }
            },
            "response": {
                "status": 200,
                "headers": {
                    "Content-Type": "application/json; charset=utf-8",
                    "Set-Cookie": "log_session_id=deadbeef; path=/",
                    "X-Session-Id": "9f2a",
                    "Link": (
                        "<https://lms.institution-under-test.example/api/v1/courses/918273>;"
                        ' rel="next"'
                    ),
                },
                "json": {
                    "id": 918273,
                    "name": "BIOL 2100: Human Anatomy",
                    "course_code": "BIOL-2100-001",
                    "account_id": 55210,
                    "enrollment_term_id": 771,
                    "html_url": "https://lms.institution-under-test.example/courses/918273",
                    "syllabus_body": "<p>Office hours with Dr Marisol Okonkwo, Thursdays.</p>",
                    "teachers": [
                        {
                            "id": 44119,
                            "display_name": "Marisol Okonkwo",
                            "sortable_name": "Okonkwo, Marisol",
                            "primary_email": "m.okonkwo@institution-under-test.example",
                            "avatar_url": "https://lms.institution-under-test.example/images/44119",
                        }
                    ],
                },
            },
        }
    ]
}


@pytest.fixture
def cleaned() -> dict:
    return Sanitizer().sanitize(DIRTY)


def test_the_dirty_document_is_dirty(cleaned: dict) -> None:
    """A guard nobody has fired is indistinguishable from a guard that cannot fire."""
    residues = verify(DIRTY)
    assert residues, "the fixture this file relies on is already clean, so it tests nothing"
    assert any("host" in item for item in residues)
    assert any("token" in item for item in residues)


def test_nothing_survives_the_verify_pass(cleaned: dict) -> None:
    assert verify(cleaned) == []


def test_every_hostname_becomes_the_example_one(cleaned: dict) -> None:
    text = json.dumps(cleaned)
    assert "institution-under-test.example" not in text
    assert EXAMPLE_HOST in text


def test_headers_are_kept_by_allowlist_not_dropped_by_denylist(cleaned: dict) -> None:
    """The header nobody thought of is the one that carries the session id."""
    response = cleaned["interactions"][0]["response"]
    assert set(response["headers"]) == {"Content-Type", "Link"}
    assert cleaned["interactions"][0]["request"]["headers"] == {}


def test_ids_are_renumbered_consistently_so_the_capture_still_hangs_together(cleaned: dict) -> None:
    course = cleaned["interactions"][0]["response"]["json"]
    new_id = course["id"]
    assert new_id != 918273
    assert f"/courses/{new_id}" in course["html_url"]
    assert f"/courses/{new_id}" in cleaned["interactions"][0]["path"]


def test_instructor_prose_is_replaced_wholesale(cleaned: dict) -> None:
    body = cleaned["interactions"][0]["response"]["json"]["syllabus_body"]
    assert "Okonkwo" not in body
    assert "Synthetic" in body


def test_people_are_replaced_deterministically(cleaned: dict) -> None:
    teacher = cleaned["interactions"][0]["response"]["json"]["teachers"][0]
    assert teacher["display_name"].startswith("Reviewer ")
    assert teacher["sortable_name"].startswith("Reviewer ")
    assert teacher["primary_email"].endswith("@example.edu")
    assert "Okonkwo" not in json.dumps(cleaned)
    assert "Marisol" not in json.dumps(cleaned)


def test_a_course_name_does_not_become_a_persons_name(cleaned: dict) -> None:
    """Over-redaction that makes every fixture unreadable is its own failure: nobody checks one."""
    course = cleaned["interactions"][0]["response"]["json"]
    assert course["name"].startswith("Sample Course ")
    assert "BIOL" not in course["name"]


def test_the_report_masks_what_it_found(cleaned: dict) -> None:
    """The report is printed in CI, and CI output is as public as the repository."""
    sanitizer = Sanitizer()
    sanitizer.sanitize(DIRTY)
    rendered = sanitizer.report.render()
    assert "institution-under-test.example" not in rendered
    assert "Okonkwo" not in rendered
    assert "hosts" in rendered


def test_a_literal_replacement_covers_what_the_key_rules_cannot_see() -> None:
    document = {"note": "Ask Marisol Okonkwo about the BIOL 2100 rebuild."}
    cleaned = Sanitizer(literals={"Marisol Okonkwo": "Reviewer A"}).sanitize(document)
    assert cleaned["note"] == "Ask Reviewer A about the BIOL 2100 rebuild."


def test_a_caller_supplied_forbidden_pattern_is_a_residue() -> None:
    residues = verify({"note": "see the BIOL 2100 rebuild"}, forbid=[r"BIOL\s?2100"])
    assert residues == ["a caller-supplied forbidden pattern matched"]
    # ...and the residue does not reprint what matched.
    assert "BIOL" not in residues[0]


# -- the CLI ------------------------------------------------------------------------------------


def _run(args: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


def test_the_cli_emits_a_clean_capture_on_stdout(tmp_path) -> None:
    source = tmp_path / "capture.json"
    source.write_text(json.dumps(DIRTY), encoding="utf-8")
    code, out, err = _run([str(source)])
    assert code == 0
    assert verify(json.loads(out)) == []
    assert "residue       none" in err


def test_the_cli_refuses_rather_than_emitting_a_mostly_clean_capture(tmp_path) -> None:
    """'Mostly clean' is not a state this tool has, and a refusal writes nothing at all."""
    source = tmp_path / "capture.json"
    source.write_text(json.dumps({"note": "the BIOL 2100 rebuild"}), encoding="utf-8")
    code, out, err = _run([str(source), "--forbid", r"BIOL\s?2100"])
    assert code == 1
    assert out == ""
    assert "refusing to write" in err


def test_the_cli_check_mode_reports_a_dirty_capture(tmp_path) -> None:
    source = tmp_path / "capture.json"
    source.write_text(json.dumps(DIRTY), encoding="utf-8")
    code, _out, err = _run([str(source), "--check"])
    assert code == 1
    assert "RESIDUE" in err


def test_the_cli_check_mode_passes_a_committed_fixture() -> None:
    from canvas_recordings import FIXTURE_DIR

    code, _out, err = _run([str(FIXTURE_DIR / "happy.json"), "--check"])
    assert code == 0, err


def test_a_malformed_replace_rule_is_refused(tmp_path) -> None:
    source = tmp_path / "capture.json"
    source.write_text("{}", encoding="utf-8")
    code, _out, err = _run([str(source), "--replace", "nonsense"])
    assert code == 2
    assert "OLD=NEW" in err
