"""No token, and no institution hostname, in anything the suite can log.

Two of this item's acceptance criteria live here. The token must not appear in any log line during
the suite, and the base URL is a secret alongside it because it identifies the institution
(SPEC §11).

The assertions are made against the **bytes the handler actually wrote**, not against an
intercepted record. A redaction processor that runs after the renderer, or a logger configured
around the handler, both pass a test that inspects the record and fail the one that reads the
stream.
"""

from __future__ import annotations

import io
import logging

import httpx
import pytest

from canvas_recordings import EXAMPLE_BASE_URL, EXAMPLE_HOST, FAKE_TOKEN, load
from sightline.canvas import CanvasClient, CanvasError
from sightline.logging import configure_logging
from sightline.redaction import register_secret, scrub

#: A stand-in for the real institution host, registered the way Settings registers the real one.
INSTITUTION_HOST = "lms.institution-under-test.example"
INSTITUTION_BASE = f"https://{INSTITUTION_HOST}"


@pytest.fixture
def log_stream() -> io.StringIO:
    stream = io.StringIO()
    configure_logging(level="DEBUG", fmt="json", stream=stream)
    yield stream
    # Leave the root logger without a handler pointed at a closed StringIO.
    logging.getLogger().handlers.clear()


async def test_a_whole_connection_test_leaves_no_token_in_the_log(log_stream: io.StringIO) -> None:
    register_secret(FAKE_TOKEN)
    register_secret(EXAMPLE_BASE_URL)
    register_secret(EXAMPLE_HOST)

    recording = load("happy")
    async with CanvasClient(
        EXAMPLE_BASE_URL, FAKE_TOKEN, transport=recording.transport()
    ) as client:
        report = await client.test_connection(course_reference="4410")

    written = log_stream.getvalue()
    assert written, "the connection test logged nothing at all"
    assert "canvas_connection_test" in written
    assert FAKE_TOKEN not in written
    assert EXAMPLE_HOST not in written
    # ...and the report the user sees does name the host, which is the whole point of the test.
    assert report.host == EXAMPLE_HOST


async def test_the_institution_host_is_scrubbed_even_out_of_an_http_client_log_line(
    log_stream: io.StringIO,
) -> None:
    """httpx logs every request URL at INFO. That is a leak unless redaction reaches it."""
    register_secret(INSTITUTION_BASE)
    register_secret(INSTITUTION_HOST)
    register_secret(FAKE_TOKEN)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 4021, "name": "Reviewer A"}, request=request)

    async with CanvasClient(
        INSTITUTION_BASE, FAKE_TOKEN, transport=httpx.MockTransport(handler)
    ) as client:
        await client.current_user()

    written = log_stream.getvalue()
    assert "HTTP Request" in written, "httpx did not log, so this test proved nothing"
    assert INSTITUTION_HOST not in written
    assert FAKE_TOKEN not in written


async def test_an_error_message_names_the_host_and_a_log_of_it_does_not() -> None:
    """The two requirements pull in opposite directions and both have to hold."""
    register_secret(INSTITUTION_BASE)
    register_secret(INSTITUTION_HOST)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    async with CanvasClient(
        INSTITUTION_BASE, FAKE_TOKEN, transport=httpx.MockTransport(refuse)
    ) as client:
        with pytest.raises(CanvasError) as caught:
            await client.current_user()

    assert INSTITUTION_HOST in str(caught.value)
    assert INSTITUTION_HOST not in scrub(str(caught.value))


def test_the_guard_would_catch_a_leak() -> None:
    """A guard nobody has fired is indistinguishable from a guard that cannot fire."""
    register_secret(FAKE_TOKEN)
    assert FAKE_TOKEN not in scrub(f"Authorization: Bearer {FAKE_TOKEN}")
    # ...and the token shape is caught even when nothing registered that particular value.
    unregistered = "9001" + "~" + "SomeOtherTokenValueEntirely" + ("1" * 8)
    assert unregistered not in scrub(f"token={unregistered}")
