"""``python -m sightline.extract`` — what it shows before it spends an hour of a local GPU.

``--dry-run`` is the part worth testing hardest. It is the only way to read what a model is
actually sent, it must make no network call at all, and it must not need a host to be configured —
somebody reading their prompts should not first have to own a GPU.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import ollama_recordings as recordings
from sightline.extract.__main__ import build_parser, main
from sightline.llm.client import OllamaClient

MODEL = "llama3.1:8b"

# This module used to pin ``get_settings`` to a dotenv-free Settings of its own, because the
# "no model host is configured" test below otherwise found the endpoint in the repository's real
# ``.env`` and made fifteen calls to it. Roadmap item 32 moved that isolation into
# ``conftest.py``, where it covers every module rather than the one that noticed.


@pytest.fixture
def sample(repo_root: Path) -> str:
    return str(repo_root / "examples" / "sample-course")


def test_a_dry_run_shows_the_prompt_and_calls_nothing(
    sample: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--bundle", sample, "--dry-run", "--extractor", "E01"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Nothing was sent. This is what would be." in out
    assert "E01  Sentences stating what a learner will be able to do" in out
    assert "call(s) would be made" in out
    # the prompt itself, including the abstention instruction
    assert "An empty list is a complete and expected answer." in out


def test_a_dry_run_needs_no_model_and_no_endpoint(
    sample: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--bundle", sample, "--dry-run"]) == 0
    assert "E10" in capsys.readouterr().out


def test_a_tier_c_extractor_says_so_in_the_dry_run(
    sample: str, capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--bundle", sample, "--dry-run", "--extractor", "E05"])
    assert "a question is drafted, never answered" in capsys.readouterr().out


def test_an_unknown_extractor_is_refused(sample: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--bundle", sample, "--extractor", "E42", "--dry-run"]) == 2
    assert "no such extractor: E42" in capsys.readouterr().err


def test_a_real_run_without_a_model_is_refused(
    sample: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--bundle", sample]) == 2
    assert "--model is required" in capsys.readouterr().err


def test_a_real_run_without_a_host_says_which_variable(
    sample: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--bundle", sample, "--model", MODEL]) == 2
    assert "OLLAMA_BASE_URL" in capsys.readouterr().err


def test_a_missing_bundle_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--bundle", str(tmp_path / "nothing"), "--dry-run"]) == 1
    assert capsys.readouterr().err


def test_a_real_run_prints_quotes_locations_and_discards(
    sample: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    host = recordings.only([MODEL])
    host.chat_responses = [
        recordings.chat_response(
            MODEL,
            json.dumps(
                {
                    "findings": [
                        {
                            "quote": "Late work is accepted at the instructor's discretion.",
                            "location": "Grading",
                        },
                        {"quote": "No late work is ever accepted.", "location": "Grading"},
                    ]
                }
            ),
        )
    ]
    monkeypatch.setattr(
        "sightline.extract.__main__.OllamaClient",
        lambda endpoint, **kwargs: OllamaClient(endpoint, transport=host.transport(), **kwargs),
    )
    code = main(
        [
            "--bundle",
            sample,
            "--extractor",
            "E09",
            "--model",
            MODEL,
            "--endpoint",
            recordings.EXAMPLE_ENDPOINT,
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "Late work is accepted at the instructor's discretion." in out
    assert "https://canvas.example.edu/courses/4410/assignments/syllabus" in out
    assert "dropped not found in the source document" in out
    assert "No late work is ever accepted." in out  # as a discard, not as evidence
    assert out.index("dropped") > out.index("quote")


def test_a_run_with_an_unreadable_passage_exits_non_zero(
    sample: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A gap in the review is not a clean course, and a script must not read it as one."""
    host = recordings.only([MODEL])
    host.chat_responses = [recordings.chat_response(MODEL, "not json at all")]
    monkeypatch.setattr(
        "sightline.extract.__main__.OllamaClient",
        lambda endpoint, **kwargs: OllamaClient(endpoint, transport=host.transport(), **kwargs),
    )
    code = main(
        [
            "--bundle",
            sample,
            "--extractor",
            "E09",
            "--model",
            MODEL,
            "--endpoint",
            recordings.EXAMPLE_ENDPOINT,
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    # stdout is the report and nothing else; the per-call log went to stderr, or this would not
    # parse at all.
    payload = json.loads(captured.out)
    assert payload["results"][0]["failures"]
    assert "no evidence found" not in payload["results"][0]["statement"]
    assert "extraction_call" in captured.err


def test_the_parser_documents_the_budget() -> None:
    parser = build_parser()
    assert "--budget" in parser.format_help()
    assert "Words of course text per call" in parser.format_help()
