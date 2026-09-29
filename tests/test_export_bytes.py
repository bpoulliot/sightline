"""Document bytes, from a downloaded export all the way to a finding (roadmap item 26).

Item 06 proved the document detectors work when they are handed bytes. What nothing proved was
that anything ever handed them any: the unpacked export was a local inside
``CourseAcquisition.acquire``, so on a real course D30 reported "the bytes were not acquired" for
every attachment — an honest-looking answer on a course whose export had downloaded perfectly well.

So every test here starts from an **archive**, runs the real acquisition over it, and asserts what
the detectors report. None of them hands a detector a :class:`FileSource` built by hand around a
fixture document; the source is always the one the acquired document itself names.

Each positive assertion has its negative beside it — the same course through the old wiring, or a
file the export does not carry — because a test that could only see findings would pass over a
source that invented them.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from types import SimpleNamespace
from typing import Any

import httpx

import detector_fixtures as fx
from canvas_recordings import EXAMPLE_BASE_URL, FAKE_TOKEN
from sightline.acquire import CourseAcquisition
from sightline.acquire.cache import DocumentCache
from sightline.batch import CourseWork, ReviewPipeline, Stage
from sightline.batch.pipeline import CanvasSource
from sightline.batch.service import live_pipeline_factory
from sightline.canvas.client import CanvasClient
from sightline.config import Settings
from sightline.credentials import CredentialStore
from sightline.detect.runner import DetectionReport, run_detectors
from sightline.detect.sources import ExportFileSource, NoFileSource, export_file_source
from sightline.model import EXPORT_FILES_KEY, CourseDocument
from sightline.paths import DataPaths
from sightline.preferences import PreferenceStore
from sightline.rubric.shipped import shipped_pack
from sightline.store import RunStore

COURSE = "4415"
EXPORT_PATH = "/files/9101/download"
EXPORT_URL = f"{EXAMPLE_BASE_URL}{EXPORT_PATH}?download_frd=1&verifier=synthetic"

#: Documents the export carries, by the href the archive stores them under. Each is built to
#: trip a specific detector: an untagged PDF (D30), a Word file with a 9pt serif default and no
#: heading styles (D28 and D30), and a syllabus PDF with no tag tree and no outline (D02).
READING_PDF = fx.pdf(tagged=False, text_layer=True)
READING_DOCX = fx.docx(heading_styles=False, face="Times New Roman", half_points=18)
SYLLABUS_PDF = fx.pdf(tagged=False, text_layer=True)
CARRIED = {
    "web_resources/week2-reading.pdf": READING_PDF,
    "web_resources/week2-reading.docx": READING_DOCX,
    "web_resources/syllabus.pdf": SYLLABUS_PDF,
}

#: Listed by the API and absent from the export. It must still come back "not acquired".
NOT_CARRIED = "lecture-notes.pdf"

MANIFEST = (
    '<?xml version="1.0" encoding="UTF-8"?><manifest><organizations><organization>'
    "<item><item><title>Synthetic week two</title>"
    '<item identifierref="r1"><title>Week two reading</title></item></item></item>'
    "</organization></organizations><resources>"
    '<resource identifier="r1" href="web_resources/week2-reading.pdf"/>'
    "</resources></manifest>"
)


def build_archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("imsmanifest.xml", MANIFEST)
        for href, data in CARRIED.items():
            bundle.writestr(href, data)
    return buffer.getvalue()


def listed_file(canvas_id: int, name: str) -> dict[str, Any]:
    return {
        "id": canvas_id,
        "display_name": name,
        "filename": name,
        "content-type": "application/octet-stream",
        "size": 1024,
        "updated_at": "2026-02-01T09:00:00Z",
        "folder_path": "course files",
    }


class Instance:
    """One synthetic course with one finished export, counting how often the archive is fetched."""

    def __init__(self) -> None:
        self.archive = build_archive()
        self.downloads = 0
        self.bodies: dict[str, Any] = {
            f"/api/v1/courses/{COURSE}": {
                "id": int(COURSE),
                "name": "Synthetic Studies 4415",
                "workflow_state": "available",
                "syllabus_body": "<h2>Policies</h2><p>Synthetic syllabus.</p>",
            },
            f"/api/v1/courses/{COURSE}/tabs": [],
            f"/api/v1/courses/{COURSE}/assignment_groups": [],
            f"/api/v1/courses/{COURSE}/rubrics": [],
            f"/api/v1/courses/{COURSE}/modules": [],
            f"/api/v1/courses/{COURSE}/pages": [],
            f"/api/v1/courses/{COURSE}/assignments": [],
            f"/api/v1/courses/{COURSE}/quizzes": [],
            f"/api/v1/courses/{COURSE}/discussion_topics": [],
            f"/api/v1/courses/{COURSE}/files": [
                listed_file(5101, "week2-reading.pdf"),
                listed_file(5102, "week2-reading.docx"),
                listed_file(5103, "syllabus.pdf"),
                listed_file(5104, NOT_CARRIED),
            ],
            f"/api/v1/courses/{COURSE}/outcome_group_links": [],
            f"/api/v1/courses/{COURSE}/content_exports": [
                {
                    "id": 81,
                    "workflow_state": "exported",
                    "export_type": "common_cartridge",
                    "created_at": "2026-02-01T09:00:00Z",
                    "attachment": {"url": EXPORT_URL},
                }
            ],
        }

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == EXPORT_PATH:
                self.downloads += 1
                return httpx.Response(200, content=self.archive)
            if path in self.bodies:
                return httpx.Response(
                    200,
                    content=json.dumps(self.bodies[path]).encode("utf-8"),
                    headers={"Content-Type": "application/json; charset=utf-8"},
                )
            raise AssertionError(f"unexpected request: {request.method} {path}")

        return httpx.MockTransport(handle)


async def acquire(paths: DataPaths, instance: Instance, **options: Any):  # noqa: ANN201
    """The real acquisition, on the path that reads an export the course already has."""
    async with CanvasClient(EXAMPLE_BASE_URL, FAKE_TOKEN, transport=instance.transport()) as client:
        return await CourseAcquisition(client, paths, request_export=False).acquire(
            COURSE, **options
        )


def detect(document: CourseDocument, paths: DataPaths) -> DetectionReport:
    return run_detectors(document, files=export_file_source(document, paths))


def by_detector(report: DetectionReport, detector: str) -> tuple[list[Any], list[Any]]:
    findings = [item for item in report.findings if item.detector == detector]
    undetermined = [item for item in report.undetermined if item.detector == detector]
    return findings, undetermined


def file_id(document: CourseDocument, filename: str) -> str:
    return next(item.id for item in document.files if item.filename == filename)


# -- the contract: what local_path is relative to -----------------------------------------------


async def test_every_local_path_resolves_under_the_export_the_document_names(
    paths: DataPaths,
) -> None:
    """``local_path`` is relative to the export's ``files/`` directory, and the document names
    that directory relative to the data root. This is the docstring on ``CourseFile``, asserted."""
    result = await acquire(paths, Instance())
    document = result.document

    assert document.export_files is not None
    assert not document.export_files.startswith("/")
    assert document.provenance[EXPORT_FILES_KEY] == document.export_files
    files_root = paths.resolve(document.export_files)

    carried = {item.local_path: item for item in document.files if item.local_path}
    assert set(carried) == set(CARRIED)
    for href, data in CARRIED.items():
        assert (files_root / href).read_bytes() == data
        # And *not* relative to the data root, which is what the docstring used to say.
        assert not paths.resolve(href).exists()
    missing = next(item for item in document.files if item.filename == NOT_CARRIED)
    assert missing.local_path is None


# -- the detectors fire -------------------------------------------------------------------------


async def test_d30_reports_a_located_finding_on_a_course_acquired_from_an_export(
    paths: DataPaths,
) -> None:
    result = await acquire(paths, Instance())
    document = result.document
    report = detect(document, paths)

    findings, undetermined = by_detector(report, "D30")
    untagged = [item for item in findings if item.code == "pdf_without_a_tag_tree"]
    located = {item.location.artifact_id for item in untagged}
    assert file_id(document, "week2-reading.pdf") in located
    assert file_id(document, "syllabus.pdf") in located
    reading_id = file_id(document, "week2-reading.pdf")
    reading = next(item for item in untagged if item.location.artifact_id == reading_id)
    assert reading.location.canvas_path == f"/courses/{COURSE}/files/5101"
    assert any(
        item.code == "document_without_heading_styles"
        and item.location.artifact_id == file_id(document, "week2-reading.docx")
        for item in findings
    )
    # The one file the export does not carry is still reported as not read — the source did not
    # invent bytes for it, and nothing else is left undetermined.
    assert [item.location.artifact_id for item in undetermined] == [file_id(document, NOT_CARRIED)]
    assert "not acquired" in undetermined[0].reason


async def test_d28_and_d02_read_the_documents_too(paths: DataPaths) -> None:
    result = await acquire(paths, Instance())
    report = detect(result.document, paths)

    d28, d28_undetermined = by_detector(report, "D28")
    assert {item.code for item in d28} >= {
        "document_type_smaller_than_the_minimum",
        "document_typeface_is_not_sans_serif",
    }
    assert d28_undetermined == []

    d02, d02_undetermined = by_detector(report, "D02")
    assert [item.code for item in d02] == ["syllabus_attachment_without_sections"]
    assert d02_undetermined == []


async def test_the_same_course_without_the_wiring_reads_nothing(paths: DataPaths) -> None:
    """The negative control, and the before/after count for the PR: the wiring main had
    (``files=None``) over the same acquired course reports every document as not read."""
    result = await acquire(paths, Instance())
    document = result.document

    before = run_detectors(document, files=None)
    after = detect(document, paths)

    def byte_undetermined(report: DetectionReport) -> int:
        return len(
            [
                item
                for item in report.undetermined
                if item.detector in {"D02", "D28", "D30"} and "not acquired" in item.reason
            ]
        )

    assert by_detector(before, "D30")[0] == []
    assert byte_undetermined(before) == 6  # D30 x4, D28 x1 (the .docx), D02 x1
    assert byte_undetermined(after) == 1  # D30: the file the export does not carry
    assert len(after.findings) > len(before.findings)


# -- through the batch pipeline, which is where files=None used to live ------------------------


async def test_the_batch_pipeline_hands_each_course_its_own_bytes(paths: DataPaths) -> None:
    instance = Instance()
    async with CanvasClient(EXAMPLE_BASE_URL, FAKE_TOKEN, transport=instance.transport()) as client:
        acquisition = CourseAcquisition(client, paths, request_export=False)
        wired = ReviewPipeline(
            source=CanvasSource(acquisition),
            pack=shipped_pack("uccs-oscqr-rsi"),
            data_paths=paths,
        )
        work = CourseWork(reference=COURSE, course_id=COURSE, ordinal=0)
        await wired.run(Stage.ACQUIRE, work)
        await wired.run(Stage.DETECT, work)

    assert work.detection is not None and work.document is not None
    findings, undetermined = by_detector(work.detection, "D30")
    assert file_id(work.document, "week2-reading.pdf") in {
        item.location.artifact_id for item in findings if item.code == "pdf_without_a_tag_tree"
    }
    assert len(undetermined) == 1

    unwired = ReviewPipeline(source=CanvasSource(acquisition), pack=shipped_pack("uccs-oscqr-rsi"))
    assert unwired._files_for(work.document) is None


async def test_the_live_factory_hands_the_pipeline_the_data_root(
    paths: DataPaths, settings: Settings
) -> None:
    """``sightline.batch.service`` is where ``files=None`` used to be written, so it is asserted
    directly: the application's own pipeline reads each course's bytes from its data root."""
    store = RunStore.open(paths)
    try:
        credentials = CredentialStore(store, paths)
        credentials.save_canvas(base_url=EXAMPLE_BASE_URL, token=FAKE_TOKEN)
        factory = live_pipeline_factory(
            settings=settings,
            paths=paths,
            credentials=credentials,
            preferences=PreferenceStore(store),
        )
        async with factory(SimpleNamespace(rubric_pack="uccs-oscqr-rsi")) as pipeline:
            assert isinstance(pipeline, ReviewPipeline)
            assert pipeline.data_paths == paths
            assert pipeline.files is None
    finally:
        store.dispose()


async def test_the_pipeline_reads_nothing_for_a_course_that_recorded_no_export(
    paths: DataPaths,
) -> None:
    document = fx.course(files=(fx.course_file("5001", "syllabus.pdf"),))
    assert document.export_files is None
    assert isinstance(export_file_source(document, paths), NoFileSource)


# -- the cache ----------------------------------------------------------------------------------


async def test_a_cache_hit_reaches_the_same_bytes_as_the_cold_run(paths: DataPaths) -> None:
    instance = Instance()
    cold = await acquire(paths, instance)
    warm = await acquire(paths, instance)

    assert cold.from_cache is False
    assert warm.from_cache is True
    assert instance.downloads == 1
    assert warm.document.export_files == cold.document.export_files

    cold_source = export_file_source(cold.document, paths)
    warm_source = export_file_source(warm.document, paths)
    for item in warm.document.files:
        assert warm_source.read(item) == cold_source.read(item)
    assert detect(warm.document, paths).to_dict() == detect(cold.document, paths).to_dict()


async def test_a_cache_hit_whose_export_was_swept_is_acquired_again(paths: DataPaths) -> None:
    """The directory is gone, so the cached document would read as "not acquired" on every
    document. It is treated as a miss instead, and the second run finds what the first did."""
    instance = Instance()
    cold = await acquire(paths, instance)
    assert cold.document.export_files is not None
    unpacked = paths.resolve(cold.document.export_files).parent
    shutil.rmtree(unpacked)

    again = await acquire(paths, instance)

    assert again.from_cache is False
    assert instance.downloads == 2
    assert paths.resolve(again.document.export_files or "").is_dir()
    findings, _ = by_detector(detect(again.document, paths), "D30")
    assert any(item.code == "pdf_without_a_tag_tree" for item in findings)


async def test_a_cached_document_that_never_recorded_its_export_is_a_miss(
    paths: DataPaths,
) -> None:
    """A document cached before item 26 carries file paths and no directory to resolve them
    against. Serving it would be the silent state this item exists to end."""
    instance = Instance()
    cold = await acquire(paths, instance)
    provenance = {
        key: value for key, value in cold.document.provenance.items() if key != EXPORT_FILES_KEY
    }
    legacy = CourseDocument.from_dict({**cold.document.to_dict(), "provenance": provenance})
    DocumentCache(paths).store(cold.entry, legacy)

    again = await acquire(paths, instance)

    assert again.from_cache is False
    assert again.document.export_files == cold.document.export_files


async def test_a_course_without_file_paths_is_served_from_the_cache_as_before(
    paths: DataPaths,
) -> None:
    """No paths, no bytes to lose: the reachability check must not turn every export-less course
    into a permanent cache miss."""
    instance = Instance()
    del instance.bodies[f"/api/v1/courses/{COURSE}/content_exports"][0]
    first = await acquire(paths, instance)
    second = await acquire(paths, instance)
    assert first.document.export_files is None
    assert second.from_cache is True


# -- the source refuses what the archive has no business naming ---------------------------------


async def test_a_local_path_climbing_out_of_the_export_is_refused(paths: DataPaths) -> None:
    result = await acquire(paths, Instance())
    document = result.document
    paths.save_bytes("secrets", "planted.pdf", data=READING_PDF)
    source = export_file_source(document, paths)
    assert isinstance(source, ExportFileSource)

    climbing = fx.course_file("5999", "planted.pdf", local_path="../../../../secrets/planted.pdf")
    assert source.read(climbing) is None
    inside = next(item for item in document.files if item.filename == "week2-reading.pdf")
    assert source.read(inside) == READING_PDF
