"""``python -m sightline.extract`` — run the extractors over a course, or show what they would send.

    python -m sightline.extract --bundle examples/sample-course --dry-run
    python -m sightline.extract --bundle examples/sample-course --model gemma4:e4b

``--dry-run`` needs no model host and makes no network call. It prints, per extractor, the passages
that *would* be sent and the prompt for the first of them — which is the only way to read what a
model is actually shown before spending an hour of a local GPU finding out.

A real run needs ``OLLAMA_BASE_URL`` (or ``--endpoint``) and ``--model``. It prints every verified
quote with its computed location, every quote discarded for not being in the source, and every
passage that could not be read. Those three are kept apart on the screen exactly as they are kept
apart in the data, because collapsing them is how a failed run reads as a clean course.

It prints quotes and counts, never a rating. A Tier C extractor prints the question it drafted and
no answer to it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ..acquire.bundle import BundleError, load_bundle
from ..config import get_settings
from ..detect.base import ResultState, Tier
from ..llm.client import OllamaClient
from ..llm.errors import ModelHostError
from ..logging import configure_logging
from ..model import CourseDocument
from .base import extractors as registered_extractors
from .base import prompt_for, registry
from .passages import WORD_BUDGET
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
        "--json", action="store_true", help="Print the whole report to stdout as JSON."
    )
    return parser


def describe(report: ExtractionReport, document: CourseDocument) -> str:
    lines = [report.statement(), ""]
    for result in report.results:
        lines.append(f"  {result.extractor}  {result.title}")
        lines.append(f"      {result.statement()}")
        for item in result.evidence:
            where = item.location.within or item.location.title
            lines.append(f"      quote   “{item.quote}”")
            lines.append(f"              {item.location.title} — {where}")
            lines.append(f"              {item.location.deep_link(document.base_url)}")
            if item.context:
                readable = ", ".join(
                    f"{key}={value}" for key, value in sorted(item.context.items())
                )
                lines.append(f"              {readable}")
        for dropped in result.discarded:
            lines.append(f"      dropped {dropped.reason}: “{dropped.excerpt}”")
        for failure in result.failures:
            lines.append(f"      unread  {failure.passage}: {failure.reason}")
        if result.summary:
            lines.append(f"      counts  {json.dumps(result.summary, sort_keys=True)}")
        if result.question:
            lines.append(f"      asks    {result.question}")
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

    try:
        report = asyncio.run(
            _run(
                document,
                endpoint=endpoint,
                model=args.model,
                only=list(args.extractor) or None,
                budget=args.budget,
            )
        )
    except ModelHostError as exc:
        print(f"{exc.summary} {exc.detail or ''}\n{exc.remedy}", file=sys.stderr)
        return 1

    if args.json:
        sys.stdout.write(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    else:
        print(describe(report, document))
    # A gap in the review is not a clean course. An extractor that could not run, or a passage
    # nobody managed to read, exits non-zero so that a script cannot treat it as one.
    gaps = [result for result in report.results if result.state is ResultState.NOT_EXAMINED]
    return 1 if gaps or report.failures else 0


async def _run(
    document: CourseDocument,
    *,
    endpoint: str,
    model: str,
    only: list[str] | None,
    budget: int,
) -> ExtractionReport:
    settings = get_settings()
    async with OllamaClient(
        endpoint,
        concurrency=settings.ollama_concurrency,
        context_tokens=settings.ollama_context_tokens,
    ) as client:
        return await run_extractors(document, client, model, only=only, budget=budget)


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
