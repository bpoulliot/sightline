"""``python -m sightline.extract`` — run the extractors over a course, or show what they would send.

    python -m sightline.extract --bundle examples/sample-course --dry-run
    python -m sightline.extract --bundle examples/sample-course --model gemma4:e4b
    python -m sightline.extract --bundle examples/sample-course --model gemma4:e4b --runs 3

``--dry-run`` needs no model host and makes no network call. It prints, per extractor, the passages
that *would* be sent and the prompt for the first of them — which is the only way to read what a
model is actually shown before spending an hour of a local GPU finding out.

A real run needs ``OLLAMA_BASE_URL`` (or ``--endpoint``) and ``--model``. It prints every verified
quote with its computed location, every quote discarded for not being in the source, and every
passage that could not be read. Those three are kept apart on the screen exactly as they are kept
apart in the data, because collapsing them is how a failed run reads as a clean course.

``--runs`` engages the disagreement router (:mod:`sightline.extract.router`): the same extraction
is run that many times and the runs are compared on the spans they returned. It costs exactly what
it says — concurrency is 1, so three runs take three times as long — which is why the default is
one run and the router is asked for. A subject whose runs differ is printed under *needs
attention*; a subject whose runs agree prints exactly as a single run does, with no marker of any
kind, because agreement is not evidence of anything (SPEC §01).

It prints quotes and counts, never a rating. A Tier C extractor prints the question it drafted and
no answer to it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..acquire.bundle import BundleError, load_bundle
from ..config import get_settings
from ..detect.base import ResultState, Tier
from ..llm.client import OllamaClient
from ..llm.errors import ModelHostError
from ..logging import configure_logging
from ..model import CourseDocument
from ..paths import DataPathError
from .base import Evidence, prompt_for, registry
from .base import extractors as registered_extractors
from .passages import WORD_BUDGET
from .router import (
    ATTENTION_NOTE,
    DISAGREEMENTS_FILENAME,
    RUNS,
    RouterReport,
    route_extractors,
)
from .runner import ExtractionReport, run_extractors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sightline.extract",
        description="Run Sightline's model-backed extractors over a normalized course.",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        required=True,
        help="A course bundle directory, e.g. examples/sample-course — needs no token.",
    )
    parser.add_argument(
        "--extractor",
        action="append",
        default=[],
        metavar="ID",
        help="Run only this extractor. Repeatable.",
    )
    parser.add_argument("--model", help="The model to run, e.g. gemma4:e4b.")
    parser.add_argument(
        "--endpoint", help="Model host root. Defaults to OLLAMA_BASE_URL from the environment."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the passages and print the prompt. Calls nothing.",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=WORD_BUDGET,
        help=f"Words of course text per call (default {WORD_BUDGET}).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        metavar="N",
        help=(
            f"Run each extraction N times and compare the spans (the router; {RUNS} is the "
            "design figure). Costs N times one pass, because concurrency is 1. Default 1."
        ),
    )
    parser.add_argument(
        "--save-disagreements",
        action="store_true",
        help=(
            "Write the flagged subset under the data root, for a later escalation pass to re-run. "
            "Needs --runs."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the whole report to stdout as JSON."
    )
    return parser


def describe(report: ExtractionReport, document: CourseDocument) -> str:
    lines = [report.statement(), ""]
    for result in report.results:
        lines.append(f"  {result.extractor}  {result.title}")
        lines.append(f"      {result.statement()}")
        lines.extend(_quotes(result.evidence, document))
        for dropped in result.discarded:
            lines.append(f"      dropped {dropped.reason}: “{dropped.excerpt}”")
        for failure in result.failures:
            lines.append(f"      unread  {failure.passage}: {failure.reason}")
        lines.extend(_counts(result.summary, result.question))
        lines.append("")
    return "\n".join(lines)


def _quotes(evidence: Sequence[Evidence], document: CourseDocument) -> list[str]:
    """One verified quote, where it is, and how to open it. Shared so the two screens cannot drift.

    They are the same screen with a different header, and the router's job is to show what a
    single pass shows. Two copies of this loop is how the router came to print no counts and no
    Tier C question at all.
    """
    lines: list[str] = []
    for item in evidence:
        where = item.location.within or item.location.title
        lines.append(f"      quote   “{item.quote}”")
        lines.append(f"              {item.location.title} — {where}")
        lines.append(f"              {item.location.deep_link(document.base_url)}")
        if item.context:
            readable = ", ".join(f"{key}={value}" for key, value in sorted(item.context.items()))
            lines.append(f"              {readable}")
    return lines


def _counts(summary: Mapping[str, Any], question: str | None) -> list[str]:
    """The arithmetic, and the Tier C question it assembles. Never an answer to that question."""
    lines: list[str] = []
    if summary:
        lines.append(f"      counts  {json.dumps(dict(summary), sort_keys=True)}")
    if question:
        lines.append(f"      asks    {question}")
    return lines


def describe_router(report: RouterReport, document: CourseDocument) -> str:
    """The router's screen. Flagged subjects are named as such; the rest print as a single run.

    *Print as a single run* is meant literally, and it is why the quote block below is
    :func:`_quotes` shared with :func:`describe` rather than a second copy of it. A subject whose
    runs agreed has to carry everything one pass would have shown — the counts, and a Tier C
    extractor's drafted question, which for three of the ten extractors is the entire deliverable.
    A router that quietly dropped it would be charging three times the calls for less output.

    Nothing is printed *beside* an agreeing subject — no marker, no adjective, no count of how many
    runs returned each quote. That absence is the design (SPEC §01) and not an omission.
    """
    lines = [report.statement(), ""]
    for comparison in report.comparisons:
        lines.append(f"  {comparison.subject}  {comparison.title}")
        lines.append(f"      {comparison.statement()}")
        lines.extend(_quotes(comparison.evidence, document))
        for run, result in enumerate(comparison.runs, start=1):
            for dropped in result.discarded:
                lines.append(f"      dropped run {run}, {dropped.reason}: “{dropped.excerpt}”")
            for failure in result.failures:
                lines.append(f"      unread  run {run}, {failure.passage}: {failure.reason}")
        lines.extend(_counts(comparison.summary, comparison.question))
        attention = comparison.attention
        if attention is not None:
            lines.append("")
            lines.append("      NEEDS ATTENTION")
            for reason in attention.reasons():
                lines.append(f"        {reason}")
            for span in attention.contested:
                runs = ", ".join(str(run) for run in span.returned_by)
                lines.append(f"        run(s) {runs} only: “{span.evidence.quote}”")
            for unread in attention.unread:
                runs = ", ".join(str(run) for run in unread.unread_by)
                lines.append(f"        run(s) {runs} could not read {unread.passage}")
            lines.append(f"        {ATTENTION_NOTE}")
        lines.append("")
    return "\n".join(lines)


def describe_dry_run(document: CourseDocument, chosen: list[str], budget: int) -> str:
    from .base import Corpus

    lines = ["Nothing was sent. This is what would be.", ""]
    for extractor in registered_extractors():
        if chosen and extractor.id not in chosen:
            continue
        corpus = Corpus(document, extractor, budget=budget)
        passages = list(corpus.passages())
        tier = "C — a question is drafted, never answered" if extractor.tier is Tier.C else "B"
        lines.append(f"  {extractor.id}  {extractor.title}")
        lines.append(
            f"      tier {tier}; {corpus.examined} artifact(s) opened, "
            f"{len(passages)} call(s) would be made"
        )
        for passage in passages:
            rebuilt = " (rebuilt from the whole body)" if passage.rebuilt else ""
            lines.append(f"        {passage.label} — {passage.words} words{rebuilt}")
        if passages:
            lines.append("")
            lines.append("      the first prompt, in full:")
            for message in prompt_for(extractor, passages[0]):
                for line in message["content"].splitlines():
                    lines.append(f"        {message['role'][:6]:<6} {line}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        document = load_bundle(args.bundle)
    except BundleError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    known = registry()
    missing = [name for name in args.extractor if name not in known]
    if missing:
        print(
            f"no such extractor: {', '.join(missing)}. Registered: {', '.join(known)}",
            file=sys.stderr,
        )
        return 2

    if args.runs < 1:
        print("--runs must be at least 1.", file=sys.stderr)
        return 2
    if args.save_disagreements and args.runs < 2:
        print(
            "--save-disagreements needs --runs 2 or more: one run has nothing to disagree with.",
            file=sys.stderr,
        )
        return 2

    if args.dry_run:
        print(describe_dry_run(document, list(args.extractor), args.budget))
        return 0

    if not args.model:
        print("--model is required for a real run; --dry-run needs no model.", file=sys.stderr)
        return 2

    settings = get_settings()
    # The report goes to stdout so that `--json` can be piped; the log goes to stderr so that
    # piping it still works. A model call logs a line per passage, and a run of them interleaved
    # with the JSON is a JSON document nothing can parse.
    configure_logging(level=settings.log_level, fmt=settings.log_format, stream=sys.stderr)

    endpoint = args.endpoint or settings.ollama_base_url
    if not endpoint:
        print(
            "No model host. Pass --endpoint, or set OLLAMA_BASE_URL in the environment.",
            file=sys.stderr,
        )
        return 2

    if args.save_disagreements:
        # The write itself happens after every model call has been paid for. Proving the
        # destination now is what keeps a mistyped data root from costing a whole run: the
        # measured three-run pass over the sample course is 381 calls and 48 minutes, and it
        # ended in a traceback about `mkdir` until this check existed.
        try:
            settings.paths.ensure_writable()
        except DataPathError as exc:
            print(
                f"--save-disagreements has nowhere to write: {exc}\n"
                "Point SIGHTLINE_DATA_DIR at a writable directory, or drop the flag.",
                file=sys.stderr,
            )
            return 2

    try:
        report = asyncio.run(
            _run(
                document,
                endpoint=endpoint,
                model=args.model,
                only=list(args.extractor) or None,
                budget=args.budget,
                runs=args.runs,
            )
        )
    except ModelHostError as exc:
        print(f"{exc.summary} {exc.detail or ''}\n{exc.remedy}", file=sys.stderr)
        return 1

    if args.json:
        sys.stdout.write(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    elif isinstance(report, RouterReport):
        print(describe_router(report, document))
    else:
        print(describe(report, document))

    if args.save_disagreements and isinstance(report, RouterReport):
        # Checked before the run, and checked again here by doing it: a root that was writable an
        # hour ago can be full, unmounted or read-only now. The report is already on stdout, so
        # what is lost is the saved subset and not the runs — the message says which.
        try:
            written = _save_disagreements(report)
        except (DataPathError, OSError) as exc:
            print(
                f"the runs finished and are above; the disagreement set could not be "
                f"written: {exc}",
                file=sys.stderr,
            )
            return 1
        print(f"disagreement set written to {written}", file=sys.stderr)

    # A gap in the review is not a clean course. An extractor that could not run, or a passage
    # nobody managed to read, exits non-zero so that a script cannot treat it as one. A subject
    # needing attention is NOT one of those: it is an ordinary outcome of a router and the exit
    # code says nothing about it.
    results = (
        [run for comparison in report.comparisons for run in comparison.runs]
        if isinstance(report, RouterReport)
        else list(report.results)
    )
    gaps = [result for result in results if result.state is ResultState.NOT_EXAMINED]
    failures = [failure for result in results for failure in result.failures]
    return 1 if gaps or failures else 0


def _save_disagreements(report: RouterReport) -> Path:
    """Write the flagged subset under the data root, through the one gate that may write.

    Nothing here builds a path by hand: ``DataPaths.save_text`` resolves inside the configured
    root and refuses anything that escapes it, which is what keeps a course's own identifiers from
    steering a write somewhere else.

    A second pass over the same course with the same model replaces the file rather than adding
    one. That is intended: the record describes the runs that just happened, and a stale one
    beside a fresh one is worse than either.
    """
    paths = get_settings().paths.ensure()
    run_id = f"router-{report.course_id or 'course'}-{report.model.replace(':', '-')}"
    payload = json.dumps(report.disagreements().to_dict(), indent=2, sort_keys=True) + "\n"
    return paths.save_text("runs", run_id, DISAGREEMENTS_FILENAME, text=payload)


async def _run(
    document: CourseDocument,
    *,
    endpoint: str,
    model: str,
    only: list[str] | None,
    budget: int,
    runs: int = 1,
) -> ExtractionReport | RouterReport:
    settings = get_settings()
    async with OllamaClient(
        endpoint,
        concurrency=settings.ollama_concurrency,
        context_tokens=settings.ollama_context_tokens,
    ) as client:
        if runs > 1:
            return await route_extractors(
                document, client, model, only=only, budget=budget, runs=runs
            )
        return await run_extractors(document, client, model, only=only, budget=budget)


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
