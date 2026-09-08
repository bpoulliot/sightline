"""Model calls are never silent.

The failure that killed an earlier local-AI pipeline on this network was a 404 and a timeout that
nobody saw for weeks. These tests assert that a call logs its latency and its token counts on both
paths, and that a missing token count is stated rather than omitted.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Iterator

import pytest

from sightline.config import Settings
from sightline.logging import configure_logging, endpoint_host, get_logger, model_call


@pytest.fixture
def log_stream() -> Iterator[io.StringIO]:
    """Capture the bytes the formatter actually renders, not an intercepted record."""
    stream = io.StringIO()
    configure_logging(level="DEBUG", fmt="json", stream=stream)
    yield stream
    configure_logging(level="INFO", fmt="json")


FAKE_ENDPOINT = "http://models.example.lan:11434"


def _events(captured: str) -> list[dict]:
    events = []
    for line in captured.splitlines():
        line = line.strip()
        if line.startswith("{"):
            events.append(json.loads(line))
    return events


def test_model_call_logs_latency_and_tokens(log_stream: io.StringIO) -> None:
    with model_call("extract", model="gemma-fake:1b", endpoint=FAKE_ENDPOINT) as call:
        call.tokens(prompt=120, completion=48)

    events = _events(log_stream.getvalue())
    assert len(events) == 1
    event = events[0]
    assert event["event"] == "model_call"
    assert event["status"] == "ok"
    assert event["operation"] == "extract"
    assert event["model"] == "gemma-fake:1b"
    assert event["endpoint_host"] == "models.example.lan"
    assert event["prompt_tokens"] == 120
    assert event["completion_tokens"] == 48
    assert event["total_tokens"] == 168
    assert event["tokens_reported"] is True
    assert isinstance(event["latency_ms"], (int, float))


def test_missing_token_counts_are_stated_not_omitted(
    log_stream: io.StringIO,
) -> None:
    with model_call("probe", model="gemma-fake:1b"):
        pass
    event = _events(log_stream.getvalue())[0]
    assert event["tokens_reported"] is False
    assert event["prompt_tokens"] is None
    assert event["total_tokens"] is None


def test_a_failing_model_call_is_logged_and_reraised(
    log_stream: io.StringIO,
) -> None:
    with (
        pytest.raises(TimeoutError),
        model_call("extract", model="gemma-fake:1b", endpoint=FAKE_ENDPOINT),
    ):
        raise TimeoutError("read timed out after 120s")

    event = _events(log_stream.getvalue())[0]
    assert event["status"] == "error"
    assert event["error_type"] == "TimeoutError"
    assert "read timed out" in event["error"]
    assert "latency_ms" in event


def test_secrets_are_scrubbed_from_log_events(log_stream: io.StringIO) -> None:
    Settings(_env_file=None, canvas_base_url="https://canvas.example.edu")
    get_logger("test").info("fetch_failed", url="https://canvas.example.edu/api/v1/courses/1")
    captured = log_stream.getvalue()
    assert "canvas.example.edu" not in captured
    assert "[redacted]" in captured


def test_stdlib_logging_shares_the_same_stream(log_stream: io.StringIO) -> None:
    logging.getLogger("uvicorn.error").warning("something from a third-party logger")
    events = _events(log_stream.getvalue())
    assert any("third-party logger" in event.get("event", "") for event in events)


def test_extra_fields_ride_along(log_stream: io.StringIO) -> None:
    with model_call("extract", model="m", course_id="c-1", criterion="ACD 5"):
        pass
    event = _events(log_stream.getvalue())[0]
    assert event["course_id"] == "c-1"
    assert event["criterion"] == "ACD 5"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://models.example.lan:11434", "models.example.lan"),
        ("https://canvas.example.edu/api", "canvas.example.edu"),
        ("models.example.lan:11434", "models.example.lan"),
        (None, None),
        ("", None),
    ],
)
def test_endpoint_host(url: str | None, expected: str | None) -> None:
    assert endpoint_host(url) == expected
