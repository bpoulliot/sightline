"""The course bundle format: a normalized course written as files a person can read.

A bundle exists because a cached document is one large JSON file, which is right for a machine and
useless to a reviewer. The properties tested here are the ones that keep a bundle honest: bodies
live in real files, embeds and module paths are **derived** rather than declared so the manifest
cannot disagree with its own HTML, and a body reference cannot point outside the bundle.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sightline.acquire import load_bundle
from sightline.acquire.bundle import BundleError
from sightline.model import ArtifactKind, ExaminationState, Source

MINIMAL = {
    "schema_version": 1,
    "base_url": "https://canvas.example.edu",
    "course": {"id": "4410", "name": "Sample Course 4410"},
    "pages": [
        {
            "kind": "page",
            "id": "page:one",
            "slug": "one",
            "title": "One",
            "canvas_path": "/courses/4410/pages/one",
            "body_file": "bodies/one.html",
        }
    ],
    "modules": [
        {
            "kind": "module",
            "id": "module:1",
            "canvas_id": "1",
            "title": "Module 1",
            "canvas_path": "/courses/4410/modules#module_1",
            "items": [
                {
                    "kind": "module_item",
                    "id": "module_item:11",
                    "canvas_id": "11",
                    "module_id": "module:1",
                    "title": "One",
                    "canvas_path": "/courses/4410/modules/items/11",
                    "item_type": "Page",
                    "target_id": "page:one",
                }
            ],
        }
    ],
}

BODY = '<p>Text <iframe src="https://media.example.edu/a"></iframe></p>'


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    (root / "bodies").mkdir(parents=True)
    (root / "bodies" / "one.html").write_text(BODY, encoding="utf-8")
    (root / "course.json").write_text(json.dumps(MINIMAL), encoding="utf-8")
    return root


def test_a_bundle_loads_into_the_document_model(bundle: Path) -> None:
    document = load_bundle(bundle)
    assert document.course.id == "4410"
    assert document.pages[0].body_html == BODY
    assert document.pages[0].body_source is Source.BUNDLE
    assert document.provenance["acquired_from"] == "bundle"


def test_embeds_are_derived_from_the_bodies(bundle: Path) -> None:
    """A manifest able to disagree with its own HTML about what it embeds would be a fixture that
    tests the fixture."""
    document = load_bundle(bundle)
    assert [embed.tag for embed in document.embeds] == ["iframe"]
    assert document.embeds[0].container_id == "page:one"
    assert document.embeds[0].kind is ArtifactKind.EMBED


def test_a_declared_embed_list_is_ignored(bundle: Path) -> None:
    payload = {**MINIMAL, "embeds": [{"kind": "embed", "id": "embed:fake", "title": "fake"}]}
    (bundle / "course.json").write_text(json.dumps(payload), encoding="utf-8")
    document = load_bundle(bundle)
    assert [embed.id for embed in document.embeds] == ["embed:page:one:000"]


def test_module_paths_are_derived_from_the_modules(bundle: Path) -> None:
    assert load_bundle(bundle).pages[0].module_path == ("Module 1",)


def test_a_declared_module_path_is_kept(bundle: Path) -> None:
    """A page can sit in a module without a module item, and only the author knows that."""
    payload = json.loads(json.dumps(MINIMAL))
    payload["pages"][0]["module_path"] = ["Somewhere else"]
    (bundle / "course.json").write_text(json.dumps(payload), encoding="utf-8")
    assert load_bundle(bundle).pages[0].module_path == ("Somewhere else",)


def test_loading_twice_gives_identical_bytes(bundle: Path) -> None:
    assert load_bundle(bundle).to_json() == load_bundle(bundle).to_json()


# -- examinations ------------------------------------------------------------------------------


def test_examinations_are_synthesized_for_every_collection(bundle: Path) -> None:
    """A bundle that silently claimed nothing was examined would be the exact failure SPEC §11
    calls silent under-detection, in the fixture, where it is hardest to notice."""
    document = load_bundle(bundle)
    names = {entry.collection for entry in document.examinations}
    assert {"pages", "assignments", "quizzes", "modules", "files", "embeds"} <= names
    assert document.examination("pages").state is ExaminationState.OK
    assert document.examination("quizzes").state is ExaminationState.EMPTY


def test_a_declared_examination_wins(bundle: Path) -> None:
    """How the sample course shows what a refused collection looks like without needing a Canvas
    that refuses one."""
    payload = {
        **MINIMAL,
        "examinations": [
            {
                "collection": "quizzes",
                "state": "refused",
                "examined": 0,
                "source": "api",
                "endpoint": "GET /q",
                "detail": "declared",
                "remedy": "grant the permission",
            }
        ],
    }
    (bundle / "course.json").write_text(json.dumps(payload), encoding="utf-8")
    quizzes = load_bundle(bundle).examination("quizzes")
    assert quizzes.state is ExaminationState.REFUSED
    assert quizzes.remedy == "grant the permission"


# -- refusals ----------------------------------------------------------------------------------


def test_a_directory_that_is_not_a_bundle_says_so(tmp_path: Path) -> None:
    with pytest.raises(BundleError) as caught:
        load_bundle(tmp_path)
    assert "course.json" in str(caught.value)


def test_invalid_json_names_the_file(bundle: Path) -> None:
    (bundle / "course.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(BundleError) as caught:
        load_bundle(bundle)
    assert "course.json" in str(caught.value)


@pytest.mark.parametrize(
    "reference", ["../../../etc/passwd", "/etc/passwd", "bodies/../../outside.html"]
)
def test_a_body_reference_cannot_point_outside_the_bundle(bundle: Path, reference: str) -> None:
    """Data from outside this repository is untrusted even when it is only read: a manifest
    naming ``../../../etc/passwd`` should fail by name, not quietly become a page body."""
    payload = json.loads(json.dumps(MINIMAL))
    payload["pages"][0]["body_file"] = reference
    (bundle / "course.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BundleError) as caught:
        load_bundle(bundle)
    assert "outside the bundle" in str(caught.value)


def test_a_missing_body_file_is_named(bundle: Path) -> None:
    payload = json.loads(json.dumps(MINIMAL))
    payload["pages"][0]["body_file"] = "bodies/absent.html"
    (bundle / "course.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(BundleError) as caught:
        load_bundle(bundle)
    assert "bodies/absent.html" in str(caught.value)
