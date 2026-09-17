"""The one write Sightline makes, and the four ways it is allowed to end.

Requesting a content export is asynchronous, so most of what can go wrong happens *after* Canvas
has answered 200: the export finishes, or it fails, or it sits in a state nobody has heard of, or
it never finishes at all. Each of those is asserted here against a recorded fixture, because each
of them is a place where reporting "nothing found" instead of "nothing examined" would hand every
detector downstream a course that looks clean.

The ceiling is asserted without waiting for it. A test that actually waited fifteen minutes is a
test somebody deletes, and a deleted test is how a ceiling stops working — so the clock and the
sleep are injected through :class:`sightline.canvas.exports.PollPacing` and the wait is simulated.
"""

from __future__ import annotations

import asyncio
import io
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from canvas_recordings import EXAMPLE_BASE_URL, EXAMPLE_HOST, FAKE_TOKEN, load
from conftest import SimulatedClock
from sightline.canvas.client import CanvasClient
from sightline.canvas.errors import (
    CanvasExportFailedError,
    CanvasExportNotPermittedError,
    CanvasExportStateError,
    CanvasExportTimeoutError,
    CanvasPermissionError,
    CanvasRateLimitedError,
    CanvasResponseError,
)
from sightline.canvas.exports import (
    ContentExport,
    ExportProgress,
    PollPacing,
    build_fresh_export,
    request_export,
    wait_for_export,
)
from sightline.logging import configure_logging
from sightline.paths import DataPaths
from sightline.redaction import register_secret

EXPORT_PATH = "/api/v1/courses/4414/content_exports"


def client_for(transport: httpx.AsyncBaseTransport) -> CanvasClient:
    return CanvasClient(EXAMPLE_BASE_URL, FAKE_TOKEN, transport=transport)


def exports_only(states: list[dict[str, Any]], *, post_status: int = 200) -> httpx.MockTransport:
    """A Canvas that answers the ``POST`` and then hands back ``states`` in order."""
    remaining = list(states)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            if post_status != 200:
                return httpx.Response(
                    post_status,
                    json={"errors": [{"message": "user not authorized to perform that action"}]},
                    headers={"Content-Type": "application/json"},
                )
            return httpx.Response(200, json=remaining.pop(0))
        return httpx.Response(200, json=remaining.pop(0) if remaining else states[-1])

    return httpx.MockTransport(handle)


async def never_downloaded(export: ContentExport) -> Path:  # pragma: no cover - never reached
    raise AssertionError("the download must not run when the export did not finish")


# -- the happy path, from a recorded fixture ----------------------------------------------------


async def test_the_recorded_flow_posts_polls_and_downloads(
    tmp_path: Path, instant_pacing: PollPacing
) -> None:
    """One fixture drives the whole thing: the POST, a poll that sees 'exporting' before
    'exported', and the download of the archive that comes out."""
    recording = load("export-requested")
    seen: list[ExportProgress] = []
    landed = tmp_path / "export.imscc"

    async def download(export: ContentExport) -> Path:
        assert export.attachment_url is not None
        response = await client._client.get(export.attachment_url)  # noqa: SLF001 - test reach-in
        landed.write_bytes(response.content)
        return landed

    async with client_for(recording.transport()) as client:
        fresh = await build_fresh_export(
            client,
            "4414",
            download=download,
            on_progress=seen.append,
            pacing=instant_pacing,
        )

    assert fresh.export.workflow_state == "exported"
    assert fresh.archive == landed
    assert landed.read_bytes().startswith(b"PK")

    # The POST happened exactly once, and the poll ran until Canvas said it was done.
    assert recording.methods_called.count("POST") == 1
    assert recording.paths_called.count(f"{EXPORT_PATH}/8801") >= 1
    assert [progress.state for progress in seen] == ["created", "exporting", "exported"]
    assert fresh.attempts == 3
    assert "Canvas finished it" in fresh.detail()


async def test_the_request_asks_for_a_common_cartridge() -> None:
    """The fixture refuses a body it did not record, so this passing is the assertion."""
    recording = load("export-requested")
    async with client_for(recording.transport()) as client:
        export = await request_export(client, "4414")
    assert export.id == "8801"
    assert recording.calls[0].method == "POST"
    assert b"export_type=common_cartridge" in recording.calls[0].content


async def test_a_wait_reports_progress_a_person_can_read(instant_pacing: PollPacing) -> None:
    recording = load("export-requested")
    seen: list[ExportProgress] = []
    async with client_for(recording.transport()) as client:
        first = await request_export(client, "4414")
        await wait_for_export(client, "4414", first, on_progress=seen.append, pacing=instant_pacing)

    waiting = [progress for progress in seen if progress.waiting]
    assert waiting, "nothing reported the wait, so a six-minute export would look like a hang"
    assert "Checking again in" in waiting[0].message
    assert "8801" in waiting[0].message
    assert not seen[-1].waiting


# -- the ways it ends badly ---------------------------------------------------------------------


async def test_a_failed_export_is_a_failure_with_a_remedy(
    instant_pacing: PollPacing,
) -> None:
    recording = load("export-failed")
    async with client_for(recording.transport()) as client:
        requested = await request_export(client, "4415")
        with pytest.raises(CanvasExportFailedError) as caught:
            await wait_for_export(client, "4415", requested, pacing=instant_pacing)

    assert "failed" in caught.value.summary
    assert "not an empty one" in (caught.value.detail or "")
    assert "Export Course Content" in caught.value.remedy


async def test_an_unrecognised_state_terminates_the_poll_and_names_the_state(
    simulated_clock: SimulatedClock,
) -> None:
    """Not "keep waiting". A state nobody has heard of, polled forever, is a run that hangs."""
    recording = load("export-unknown-state")
    clock = simulated_clock
    async with client_for(recording.transport()) as client:
        requested = await request_export(client, "4416")
        with pytest.raises(CanvasExportStateError) as caught:
            await wait_for_export(
                client,
                "4416",
                requested,
                pacing=clock.pacing(),
            )

    assert "quiesced" in caught.value.summary
    # One sleep, not an unbounded number: it saw the state once and stopped.
    assert len(clock.slept) == 1


async def test_the_ceiling_is_enforced_without_waiting_for_it(
    simulated_clock: SimulatedClock,
) -> None:
    """The whole point of injecting the clock. Sixty simulated seconds, no real ones."""
    clock = simulated_clock
    transport = exports_only(
        [{"id": 8804, "workflow_state": "created"}]
        + [{"id": 8804, "workflow_state": "exporting"}] * 50
    )
    async with client_for(transport) as client:
        requested = await request_export(client, "4417")
        with pytest.raises(CanvasExportTimeoutError) as caught:
            await wait_for_export(
                client,
                "4417",
                requested,
                pacing=clock.pacing(ceiling_seconds=60.0),
            )

    assert clock.now <= 60.0, "the poll slept past its own ceiling"
    assert sum(clock.slept) > 0, "nothing was ever waited on, so the ceiling proved nothing"
    assert "60s" in (caught.value.detail or "")
    assert "still finish" in caught.value.remedy


async def test_the_poll_backs_off_rather_than_hammering_canvas(
    simulated_clock: SimulatedClock,
) -> None:
    clock = simulated_clock
    transport = exports_only(
        [{"id": 8805, "workflow_state": "created"}]
        + [{"id": 8805, "workflow_state": "exporting"}] * 50
    )
    async with client_for(transport) as client:
        requested = await request_export(client, "4418")
        with pytest.raises(CanvasExportTimeoutError):
            await wait_for_export(
                client,
                "4418",
                requested,
                pacing=clock.pacing(ceiling_seconds=300.0),
            )

    assert len(clock.slept) > 3
    assert clock.slept == sorted(clock.slept), "the delay must not shrink"
    assert clock.slept[-1] > clock.slept[0], "the delay never backed off at all"


async def test_a_refused_request_has_its_own_remedy_not_the_read_one() -> None:
    """A 403 on this call means something different from a 403 on a GET, so it says something
    different. Sending somebody to check their read permission would be a wrong answer."""
    async with client_for(exports_only([], post_status=403)) as client:
        with pytest.raises(CanvasExportNotPermittedError) as caught:
            await request_export(client, "4419")

    remedy = caught.value.remedy
    assert "Export Course Content" in remedy
    assert "switch" in remedy and "off" in remedy
    assert remedy != CanvasPermissionError("x").remedy


async def test_a_throttled_request_is_still_reported_as_throttling() -> None:
    """Canvas rate-limits with a 403 whose body says so. Re-labelling that as a permission
    problem would send somebody to change a role over a rate limit."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="403 Forbidden (Rate Limit Exceeded)",
            headers={"Content-Type": "text/plain"},
        )

    async with client_for(httpx.MockTransport(handle)) as client:
        with pytest.raises(CanvasRateLimitedError):
            await request_export(client, "4420")


async def test_a_finished_export_with_no_attachment_is_reported_not_downloaded(
    instant_pacing: PollPacing,
) -> None:
    transport = exports_only([{"id": 8806, "workflow_state": "exported"}])
    async with client_for(transport) as client:
        with pytest.raises(CanvasResponseError) as caught:
            await build_fresh_export(
                client, "4421", download=never_downloaded, pacing=instant_pacing
            )
    assert "attached no file" in caught.value.summary


async def test_an_export_object_with_no_state_is_refused() -> None:
    transport = exports_only([{"id": 8807}])
    async with client_for(transport) as client:
        with pytest.raises(CanvasResponseError) as caught:
            await request_export(client, "4422")
    assert "no workflow state" in caught.value.summary


# -- secrets ------------------------------------------------------------------------------------


def test_the_repr_of_an_export_does_not_carry_its_download_link() -> None:
    """A repr reaches tracebacks and pytest output, and the link is a credential in a URL."""
    export = ContentExport(
        id="8801",
        workflow_state="exported",
        attachment_url=f"{EXAMPLE_BASE_URL}/files/9101/download?verifier=synthetic",
    )
    assert "verifier" not in repr(export)
    assert "9101" not in repr(export)
    assert "attachment=present" in repr(export)


async def test_the_download_link_and_its_host_never_reach_the_log(paths: DataPaths) -> None:
    """Asserted against the bytes the handler wrote, not an intercepted record.

    Canvas hands out a link on its own host and then redirects to a storage back end. That back
    end is not the configured host, so nothing has registered it with redaction and nothing can
    scrub it — which is exactly why no log line may carry it.
    """
    from sightline.acquire.archive import download_archive

    storage_host = "content-storage.example"
    signed = f"https://{storage_host}/bundles/8801?X-Amz-Signature=synthetic"

    stream = io.StringIO()
    configure_logging(level="DEBUG", fmt="json", stream=stream)
    register_secret(EXAMPLE_BASE_URL)
    register_secret(EXAMPLE_HOST)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == EXAMPLE_HOST:
            return httpx.Response(302, headers={"Location": signed})
        return httpx.Response(200, content=b"PK\x03\x04 synthetic", request=request)

    try:
        async with client_for(httpx.MockTransport(handle)) as client:
            await download_archive(
                client,
                f"{EXAMPLE_BASE_URL}/files/9101/download?download_frd=1&verifier=synthetic",
                paths=paths,
                destination=("exports", "redaction-check", "8801.imscc"),
                canvas_base_url=EXAMPLE_BASE_URL,
            )
        written = stream.getvalue()
    finally:
        logging.getLogger().handlers.clear()

    assert "archive_downloaded" in written, "nothing was logged, so this proved nothing"
    assert storage_host not in written
    assert "X-Amz-Signature=synthetic" not in written
    assert "verifier=synthetic" not in written
    assert EXAMPLE_HOST not in written
    # ...and the fact that mattered is still there, as a fact.
    assert '"served_by_canvas_host": false' in written


async def test_no_log_line_from_the_export_flow_carries_a_url(
    instant_pacing: PollPacing,
) -> None:
    recording = load("export-requested")
    stream = io.StringIO()
    configure_logging(level="DEBUG", fmt="json", stream=stream)
    register_secret(EXAMPLE_BASE_URL)
    register_secret(EXAMPLE_HOST)

    async def download(export: ContentExport) -> Path:
        return Path("/dev/null")

    try:
        async with client_for(recording.transport()) as client:
            await build_fresh_export(client, "4414", download=download, pacing=instant_pacing)
        written = stream.getvalue()
    finally:
        logging.getLogger().handlers.clear()

    assert "content_export_requested" in written
    assert "content_export_ready" in written
    assert "verifier" not in written
    assert EXAMPLE_HOST not in written


# -- the fixture transport itself ---------------------------------------------------------------


async def test_an_unrecorded_post_is_refused_exactly_as_an_unrecorded_get_is() -> None:
    """A test must not be able to make a live request by accident, and a mock that answers
    anything is how that happens."""
    from canvas_recordings import UnrecordedRequest

    recording = load("export-requested")
    async with client_for(recording.transport()) as client:
        with pytest.raises(UnrecordedRequest):
            await client.post_json("/api/v1/courses/4414/pages", title="anything")
        with pytest.raises(UnrecordedRequest):
            await client.get_json("/api/v1/courses/4414/nothing_recorded")


def test_a_fixture_needs_a_token_or_it_skips() -> None:
    """There is no test here that needs a real token — everything runs against a recording. The
    rule is asserted rather than assumed: a suite that quietly needs a credential is a suite that
    does not run in CI."""
    import os

    assert os.environ.get("CANVAS_API_TOKEN") is None
    if os.environ.get("SIGHTLINE_LIVE_CANVAS_TOKEN") is None:
        pytest.skip("no live Canvas token: every path here is covered by a recorded fixture")


def test_the_pacing_defaults_are_a_ceiling_a_person_would_accept() -> None:
    """A ceiling is a product decision as much as a constant, so it is stated in a test."""
    from sightline.canvas.exports import CEILING_SECONDS, FIRST_DELAY_SECONDS, MAX_DELAY_SECONDS

    assert FIRST_DELAY_SECONDS < MAX_DELAY_SECONDS < CEILING_SECONDS
    assert CEILING_SECONDS >= 600, "a large course takes minutes; this would fail normal exports"
    assert PollPacing().sleep is asyncio.sleep
