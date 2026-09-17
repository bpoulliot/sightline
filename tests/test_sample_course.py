"""``examples/sample-course/`` — that it is synthetic, and that it still tests what it claims to.

The sample course is committed to a public repository and does three jobs: it is what a new user
runs without a Canvas token, it is the suite's fixture for everything downstream of acquisition,
and it is what every detector is developed against. Each job has a way of failing quietly, and
this file is the guard for all three.

**Synthetic.** A course that traced back to a real one would be irreversible once pushed, and git
history keeps it after a later deletion. So the checks are mechanical: every hostname under
``example.edu``, no address at any other domain, no token shape, no ``Bearer``, no institution.

**Still testing what it claims.** ``defects.json`` names a detector, an artifact and usually a
literal marker. A body edited without updating the registry breaks the build here — which is the
point. A fixture that quietly stops testing what it says it tests is the same failure as a test
suite that collects nothing and exits green.

**Loadable and deterministic**, with a resolvable deep link on every object. Those are this
roadmap item's acceptance conditions and they are asserted over the whole course, not a sample.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from sightline.acquire import load_bundle
from sightline.model import Artifact, ArtifactKind, CourseDocument

#: The one domain the sample may name. RFC 2606 reserves ``example.edu``, so it can never belong
#: to an institution. Everything in the course — the LMS, the library, the OER collection, the
#: media host — is a subdomain of it.
ALLOWED_DOMAIN = "example.edu"

EXPECTATIONS = {"defect", "clean", "not_covered"}

_URL_HOST = re.compile(r"https?://([^/\s\"'<>)]+)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+")
_TOKEN_SHAPE = re.compile(r"\b\d{3,6}~[A-Za-z0-9]{20,}\b")
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9\-._~+/]{16,}")
#: SPEC §05 lists every detector as a table row beginning with its id.
_SPEC_DETECTOR = re.compile(r"^\|\s*(D\d{2}|E\d{2})\s*\|", re.MULTILINE)


@pytest.fixture
def sample_dir(repo_root: Path) -> Path:
    return repo_root / "examples" / "sample-course"


@pytest.fixture
def document(sample_dir: Path) -> CourseDocument:
    return load_bundle(sample_dir)


@pytest.fixture
def planted(sample_dir: Path) -> list[dict[str, str]]:
    return json.loads((sample_dir / "defects.json").read_text(encoding="utf-8"))["planted"]


def sample_files(sample_dir: Path) -> list[Path]:
    return sorted(path for path in sample_dir.rglob("*") if path.is_file())


def searchable(artifact: Artifact) -> str:
    """Body plus every other field, as plain text.

    Not the JSON form: JSON escapes the quotes inside an HTML attribute, so a marker like
    ``alt="IMG_2043.jpg"`` would never match and the check would silently pass on nothing.
    """
    values = [artifact.body_html or ""]
    values += [str(value) for key, value in artifact.to_dict().items() if key != "body_html"]
    return "\n".join(values)


# -- synthetic ----------------------------------------------------------------------------------


def test_there_are_files_to_examine(sample_dir: Path) -> None:
    """A guard that examined nothing is not a guard."""
    assert len(sample_files(sample_dir)) >= 15


def test_no_hostname_outside_the_reserved_domain(sample_dir: Path) -> None:
    offenders: list[str] = []
    for path in sample_files(sample_dir):
        for match in _URL_HOST.finditer(path.read_text(encoding="utf-8")):
            host = match.group(1).rstrip(".<>,").lower()
            if host != ALLOWED_DOMAIN and not host.endswith(f".{ALLOWED_DOMAIN}"):
                offenders.append(f"{path.name}: {host}")
    assert not offenders, offenders


def test_no_address_at_any_other_domain(sample_dir: Path) -> None:
    for path in sample_files(sample_dir):
        for match in _EMAIL.finditer(path.read_text(encoding="utf-8")):
            assert match.group(0).endswith(f"@{ALLOWED_DOMAIN}"), f"{path.name}: {match.group(0)}"


def test_no_credential_shape_anywhere(sample_dir: Path) -> None:
    for path in sample_files(sample_dir):
        text = path.read_text(encoding="utf-8")
        assert not _TOKEN_SHAPE.search(text), path.name
        assert not _BEARER.search(text), path.name
        assert "instructure.com" not in text.lower(), path.name


def test_the_course_identifies_no_institution(document: CourseDocument) -> None:
    assert document.base_url == f"https://canvas.{ALLOWED_DOMAIN}"
    assert document.course.name == "Introduction to Synthetic Studies"
    assert document.course.course_code == "SYN-1010"


# -- the acceptance conditions --------------------------------------------------------------------


def test_the_sample_course_loads(document: CourseDocument) -> None:
    counts = document.counts()
    for kind in (
        ArtifactKind.PAGE,
        ArtifactKind.ASSIGNMENT,
        ArtifactKind.QUIZ,
        ArtifactKind.DISCUSSION,
        ArtifactKind.ANNOUNCEMENT,
        ArtifactKind.MODULE,
        ArtifactKind.MODULE_ITEM,
        ArtifactKind.FILE,
        ArtifactKind.EMBED,
        ArtifactKind.SYLLABUS,
        ArtifactKind.OUTCOME,
    ):
        assert counts.get(kind.value), f"the sample course holds no {kind.value}"


def test_loading_it_twice_gives_identical_bytes(sample_dir: Path) -> None:
    assert load_bundle(sample_dir).to_json() == load_bundle(sample_dir).to_json()


def test_every_object_carries_a_resolvable_deep_link(document: CourseDocument) -> None:
    for artifact in document.artifacts():
        assert artifact.canvas_path.startswith("/courses/4410"), artifact.id
        assert "/edit" not in artifact.canvas_path, artifact.id
        assert document.deep_link(artifact).startswith(
            f"https://canvas.{ALLOWED_DOMAIN}/courses/4410"
        )


def test_canvas_paths_match_what_the_model_would_build(document: CourseDocument) -> None:
    """Hand-written paths in ``course.json`` and the path helpers in ``sightline.model`` cannot
    drift apart without this failing."""
    from sightline.model import (
        announcement_path,
        assignment_path,
        discussion_path,
        file_path,
        module_item_path,
        module_path,
        outcome_path,
        page_path,
        quiz_path,
        syllabus_path,
    )

    course = document.course.id
    assert document.syllabus.canvas_path == syllabus_path(course)
    for page in document.pages:
        assert page.canvas_path == page_path(course, page.slug)
    for item in document.assignments:
        assert item.canvas_path == assignment_path(course, item.canvas_id)
    for quiz in document.quizzes:
        assert quiz.canvas_path == quiz_path(course, quiz.canvas_id)
    for topic in document.discussions:
        assert topic.canvas_path == discussion_path(course, topic.canvas_id)
    for topic in document.announcements:
        assert topic.canvas_path == announcement_path(course, topic.canvas_id)
    for module in document.modules:
        assert module.canvas_path == module_path(course, module.canvas_id)
        for item in module.items:
            assert item.canvas_path == module_item_path(course, item.canvas_id)
    for outcome in document.outcomes:
        assert outcome.canvas_path == outcome_path(course, outcome.canvas_id)
    for item in document.files:
        if item.link_exact:
            assert item.canvas_path == file_path(course, item.canvas_id)
        else:
            assert item.canvas_path.startswith(f"/courses/{course}/files?search_term=")


def test_only_a_file_with_no_canvas_id_is_linked_by_search(document: CourseDocument) -> None:
    inexact = [artifact for artifact in document.artifacts() if not artifact.link_exact]
    assert [artifact.filename for artifact in inexact] == ["orphaned-rubric.docx"]


# -- the planted defects ---------------------------------------------------------------------------


def test_the_registry_is_not_empty(planted: list[dict[str, str]]) -> None:
    assert len(planted) >= 40


def test_every_row_names_a_detector_the_spec_lists(
    planted: list[dict[str, str]], repo_root: Path
) -> None:
    """A row naming D99 would look like coverage and be nothing."""
    spec = (repo_root / "docs" / "SPEC.md").read_text(encoding="utf-8")
    known = set(_SPEC_DETECTOR.findall(spec))
    assert len(known) > 40, "the spec scan found almost no detectors; the table format changed"
    unknown = sorted({row["detector"] for row in planted} - known)
    assert not unknown, unknown


def test_every_row_names_an_artifact_or_collection_that_exists(
    planted: list[dict[str, str]], document: CourseDocument
) -> None:
    # The course artifact is deliberately not in `artifacts()` — it is the container, not one of
    # the contents — but it is what a finding about the course as a whole is located on, so a row
    # is allowed to name it. `by_id` resolves it, which is what the detectors depend on.
    ids = {artifact.id for artifact in document.artifacts()} | {document.course_artifact.id}
    collections = {"navigation", "assignment_groups", "pages", "quizzes", "modules", "course"}
    for row in planted:
        target = row.get("artifact")
        if target is not None:
            assert target in ids, f"{row['detector']} names a missing artifact: {target}"
        else:
            assert row.get("collection") in collections, row


def test_every_marker_is_still_present(
    planted: list[dict[str, str]], document: CourseDocument
) -> None:
    """The check that makes the registry an artifact rather than a comment."""
    missing = []
    checked = 0
    for row in planted:
        marker = row.get("marker")
        target = row.get("artifact")
        if not marker or not target:
            continue
        checked += 1
        if marker not in searchable(document.by_id(target)):
            missing.append(f"{row['detector']} {target}: {marker!r}")
    assert checked >= 20, "almost no markers were checked; the registry lost its markers"
    assert not missing, missing


def test_every_expectation_is_one_of_the_three(planted: list[dict[str, str]]) -> None:
    assert {row["expectation"] for row in planted} <= EXPECTATIONS


def test_the_registry_carries_clean_cases_as_well_as_defects(
    planted: list[dict[str, str]],
) -> None:
    """A detector with only defects to look at passes its tests by returning 'found it'
    unconditionally."""
    by_expectation = {row["expectation"] for row in planted}
    assert by_expectation >= EXPECTATIONS
    assert sum(1 for row in planted if row["expectation"] == "clean") >= 8


def test_every_row_says_what_was_planted_and_why(planted: list[dict[str, str]]) -> None:
    for row in planted:
        assert len(row.get("note", "")) > 15, row


def test_a_not_covered_row_says_why_it_is_not_covered(planted: list[dict[str, str]]) -> None:
    """An uncovered detector is information. An uncovered detector nobody mentioned is a gap."""
    uncovered = [row for row in planted if row["expectation"] == "not_covered"]
    assert uncovered
    for row in uncovered:
        assert "needs" in row["note"] or "not" in row["note"], row


def test_the_deterministic_structure_detectors_are_nearly_all_exercised(
    planted: list[dict[str, str]], repo_root: Path
) -> None:
    """Every ``D`` detector in the spec has a row here, covered or explicitly not.

    Stated as coverage of the registry rather than of the code, because no detector exists yet.
    When one arrives it finds a case waiting, and when a new detector is added to the spec this
    fails until the sample course has something for it to look at.
    """
    spec = (repo_root / "docs" / "SPEC.md").read_text(encoding="utf-8")
    deterministic = {name for name in _SPEC_DETECTOR.findall(spec) if name.startswith("D")}
    named = {row["detector"] for row in planted}
    assert not sorted(deterministic - named), sorted(deterministic - named)


def test_the_readme_documents_the_uncovered_detectors(
    planted: list[dict[str, str]], sample_dir: Path
) -> None:
    readme = (sample_dir / "README.md").read_text(encoding="utf-8")
    for row in planted:
        if row["expectation"] == "not_covered":
            assert row["detector"] in readme, row["detector"]
