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
from sightline.extract.router import DISAGREEMENTS_FILENAME, DisagreementSet
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


def test_the_parser_says_what_a_second_run_costs() -> None:
    """Three runs cost three times one pass and the help says so, because nothing amortizes it."""
    help_text = build_parser().format_help()
    assert "--runs" in help_text
    assert "concurrency is 1" in help_text


# -- the router, through the CLI -----------------------------------------------------------------


def _routed(
    host: recordings.FakeHost,
    sample: str,
    monkeypatch: pytest.MonkeyPatch,
    *extra: str,
) -> int:
    monkeypatch.setattr(
        "sightline.extract.__main__.OllamaClient",
        lambda endpoint, **kwargs: OllamaClient(endpoint, transport=host.transport(), **kwargs),
    )
    return main(
        [
            "--bundle",
            sample,
            "--extractor",
            "E09",
            "--model",
            MODEL,
            "--endpoint",
            recordings.EXAMPLE_ENDPOINT,
            "--runs",
            "3",
            *extra,
        ]
    )


def _quote(text: str) -> str:
    return json.dumps({"findings": [{"quote": text, "location": "Grading"}]})


LATE = "Late work is accepted at the instructor's discretion."
NOTHING = json.dumps({"findings": []})

#: E09 opens ten artifacts of the sample course, so one run is ten calls and the recorded host
#: answers them in order. A run's ten answers are built together rather than relying on the
#: host's clamp, or "run 2" would silently mean "every call after the second".
CALLS_PER_RUN = 10


def _run_answers(*bodies: str) -> list[dict[str, object]]:
    return [recordings.chat_response(MODEL, body) for body in bodies for _ in range(CALLS_PER_RUN)]


def test_runs_that_differ_are_printed_under_needs_attention(
    sample: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Run 2 returned nothing anywhere. Runs 1 and 3 found a sentence that is really there."""
    host = recordings.only([MODEL])
    host.chat_responses = _run_answers(_quote(LATE), NOTHING, _quote(LATE))
    assert _routed(host, sample, monkeypatch) == 0
    out = capsys.readouterr().out
    assert "NEEDS ATTENTION" in out
    assert "span(s) were returned by some runs and not by others" in out
    assert "run(s) 1, 3 only:" in out
    assert "not a statement about which run is right" in out


def test_runs_that_agree_print_no_marker_at_all(
    sample: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The quote prints. Nothing prints beside it — no marker, no adjective, no run count.

    The pass's own header still counts the flagged subjects, and *zero* is the honest thing for it
    to say: that is arithmetic about the run, on the line that also reports what it cost. The rule
    is about what gets attached to a subject, and the subject's own block is checked here.
    """
    host = recordings.only([MODEL])
    host.chat_responses = _run_answers(_quote(LATE), _quote(LATE), _quote(LATE))
    assert _routed(host, sample, monkeypatch) == 0
    out = capsys.readouterr().out
    header, blocks = out.split("\n\n", 1)
    assert "0 subject(s) need attention." in header
    assert LATE in blocks
    for marker in ("attention", "agree", "confiden", "only:", "could not read"):
        assert marker not in blocks.lower()


def test_saving_the_disagreement_set_needs_more_than_one_run(
    sample: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--bundle", sample, "--model", MODEL, "--save-disagreements"])
    assert code == 2
    assert "one run has nothing to disagree with" in capsys.readouterr().err


def test_a_data_root_it_cannot_write_to_is_refused_before_a_single_call(
    sample: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The saved subset is written after every model call, so the check belongs before them.

    This cost a real run: a three-run pass over the sample course spent 381 calls and 48 minutes
    and then died with a ``PermissionError`` about ``mkdir``, because the data root it had been
    given exists inside the container and not on the host. The assertion that matters is
    ``host.calls == []`` — the exit code alone would pass just as well if the refusal arrived an
    hour late.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("a file where a directory was expected", encoding="utf-8")
    monkeypatch.setenv("SIGHTLINE_DATA_DIR", str(blocker / "data"))

    host = recordings.only([MODEL])
    host.chat_responses = _run_answers(_quote(LATE), NOTHING, _quote(LATE))

    assert _routed(host, sample, monkeypatch, "--save-disagreements") == 2
    assert host.calls == []

    err = capsys.readouterr().err
    assert "--save-disagreements has nowhere to write" in err
    assert str(blocker / "data") in err
    assert "SIGHTLINE_DATA_DIR" in err


def test_a_write_that_fails_after_the_runs_keeps_the_runs_and_says_what_was_lost(
    sample: str,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A root writable at the start can be full, unmounted or read-only by the end.

    The pre-flight cannot promise otherwise, so the late failure is handled rather than raised: the
    quotes stay on stdout where the reviewer can read them, and the exit code stops a script
    treating the subset as saved.
    """
    monkeypatch.setenv("SIGHTLINE_DATA_DIR", str(data_root))

    def full_disk(report: object) -> Path:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("sightline.extract.__main__._save_disagreements", full_disk)
    host = recordings.only([MODEL])
    host.chat_responses = _run_answers(_quote(LATE), NOTHING, _quote(LATE))

    assert _routed(host, sample, monkeypatch, "--save-disagreements") == 1

    captured = capsys.readouterr()
    assert LATE in captured.out
    assert "the disagreement set could not be written" in captured.err
    assert "No space left on device" in captured.err


def test_the_disagreement_set_is_written_inside_the_data_root(
    sample: str,
    data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The subset item 18 re-runs, on disk, readable back into requests."""
    monkeypatch.setenv("SIGHTLINE_DATA_DIR", str(data_root))
    host = recordings.only([MODEL])
    host.chat_responses = _run_answers(_quote(LATE), NOTHING, _quote(LATE))
    assert _routed(host, sample, monkeypatch, "--save-disagreements") == 0

    written = sorted(data_root.rglob(DISAGREEMENTS_FILENAME))
    assert len(written) == 1
    assert data_root in written[0].parents
    recorded = DisagreementSet.from_dict(json.loads(written[0].read_text(encoding="utf-8")))
    assert [entry.subject for entry in recorded.entries] == ["E09"]
    assert recorded.runs == 3


def test_the_router_screen_prints_everything_one_pass_prints(
    sample: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Three runs must not buy a reviewer *less* on the screen than one run does.

    E05 is Tier C, so its drafted question is the deliverable and the quotes are its context. The
    check is against a single pass's own screen rather than a hand-written string: anything one
    run puts under a subject, the router puts under it too.
    """
    monkeypatch.setattr(
        "sightline.extract.__main__.OllamaClient",
        lambda endpoint, **kwargs: OllamaClient(endpoint, transport=_host().transport(), **kwargs),
    )
    base = [
        "--bundle",
        sample,
        "--extractor",
        "E05",
        "--model",
        MODEL,
        "--endpoint",
        recordings.EXAMPLE_ENDPOINT,
    ]
    assert main(base) == 0
    once = capsys.readouterr().out
    assert main([*base, "--runs", "3"]) == 0
    routed = capsys.readouterr().out

    assert E05_QUOTE in once, "the fixture quote is not in the sample course any more"
    asked = [line for line in once.splitlines() if line.strip().startswith("asks")]
    assert asked, "the single-run screen stopped printing the Tier C question"
    assert any(line.strip().startswith("quote") for line in once.splitlines())
    assert any(line.strip().startswith("counts") for line in once.splitlines())
    for line in asked:
        assert line in routed.splitlines()
    for line in once.splitlines():
        if line.strip().startswith(("quote", "counts")):
            assert line in routed.splitlines()
    assert "NEEDS ATTENTION" not in routed


#: A sentence that really is in the sample course's first assignment, so the quote verifies and
#: the screens have a quote, a count and a question on them to compare.
E05_QUOTE = "Then reply to two classmates."


def _host() -> recordings.FakeHost:
    """Answers every call the same way, so one pass and three runs see identical evidence."""
    host = recordings.only([MODEL])
    body = json.dumps({"findings": [{"quote": E05_QUOTE, "location": "the task"}]})
    host.chat_responses = [recordings.chat_response(MODEL, body) for _ in range(120)]
    return host
