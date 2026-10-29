"""``python -m sightline.batch`` — start a batch, resume one, read its progress, open a review.

    python -m sightline.batch run --bundle examples/sample-course --model gemma4:e4b
    python -m sightline.batch run --course 12345 --course 12346 --pack uccs-oscqr-rsi
    python -m sightline.batch resume 7f3c…
    python -m sightline.batch status 7f3c…
    python -m sightline.batch open-review 91ab…

``--bundle`` reviews a course bundle on disk and needs no Canvas token; ``--course`` takes course
ids or Canvas URLs and needs credentials. They cannot be mixed in one run, because a run records
what it reviewed and half a batch from a different source is not one batch.

Progress prints per course and per stage as it happens, because a sixteen-course batch against a
local model host is an hour of silence otherwise. What prints is what the stage measured — the
count it examined and its own statement — never a verdict about the course.

**This command does not export anything and cannot be made to.** ``open-review`` records that a
person opened a course's review, which is the gate the workbook and the PDF report are written
through — by ``python -m sightline.report export``, a separate command a person runs. Sightline
still generates no report on its own (SPEC §09).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any

from ..config import Settings, get_settings
from ..credentials import CredentialStore
from ..llm.client import OllamaClient
from ..logging import configure_logging
from ..paths import DataPaths
from ..preferences import PreferenceStore
from ..rubric.effort import load_effort_model
from ..rubric.shipped import shipped_pack, shipped_packs
from ..store import RunStore
from .gate import ExportGate
from .pipeline import (
    BundleSource,
    CoursePipeline,
    CourseWork,
    ReviewPipeline,
    is_bundle_reference,
)
from .progress import progress_of
from .runner import BatchRunner, BatchSummary, BatchTooLargeError, create_batch_run
from .service import live_pipeline_factory
from .stages import Stage, StageOutcome

DEFAULT_PACK = "uccs-oscqr-3-1-3"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sightline.batch",
        description="Review a batch of courses, one after another, and read the run back.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("run", help="Start a new batch.")
    start.add_argument(
        "--course",
        action="append",
        default=[],
        metavar="REF",
        help="A Canvas course id or URL. Repeatable. Needs credentials.",
    )
    start.add_argument(
        "--bundle",
        action="append",
        default=[],
        metavar="DIR",
        help="A course bundle directory, e.g. examples/sample-course. Repeatable, no token.",
    )
    start.add_argument(
        "--pack", default=DEFAULT_PACK, help=f"Rubric pack id (default {DEFAULT_PACK})."
    )
    start.add_argument("--model", help="The model to run, e.g. gemma4:e4b.")
    start.add_argument("--endpoint", help="Model host root. Defaults to the stored preference.")
    start.add_argument("--note", help="A note recorded on the run.")

    resume = commands.add_parser("resume", help="Continue a run that was interrupted.")
    resume.add_argument("run_id")
    resume.add_argument("--model", help="The model to run, e.g. gemma4:e4b.")
    resume.add_argument("--endpoint", help="Model host root. Defaults to the stored preference.")

    status = commands.add_parser("status", help="Print a run's progress, or list recent runs.")
    status.add_argument("run_id", nargs="?")

    opened = commands.add_parser(
        "open-review", help="Record that a person opened a course's review."
    )
    opened.add_argument("course_id")

    return parser


# ---------------------------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------------------------


class Narrating:
    """Wraps a pipeline and prints what each stage examined as it finishes.

    A decorator rather than a hook on the runner: the runner already writes every one of these to
    the run store, which is what a screen and a restart read. This is a terminal watching one run.
    """

    def __init__(self, inner: CoursePipeline) -> None:
        self.inner = inner

    async def run(self, stage: Stage, work: CourseWork) -> StageOutcome:
        print(f"  course {work.ordinal + 1}  {stage.value:<8} …", flush=True)
        outcome = await self.inner.run(stage, work)
        print(f"  course {work.ordinal + 1}  {stage.value:<8} {outcome.statement}", flush=True)
        return outcome

    def review(self, work: CourseWork) -> Mapping[str, Any]:
        return self.inner.review(work)


def describe(summary: BatchSummary) -> str:
    return "\n".join(summary.lines())


# ---------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------


def _open(settings: Settings) -> tuple[DataPaths, RunStore]:
    paths = settings.paths.ensure()
    return paths, RunStore.open(paths)


def _bundle_factory(
    settings: Settings,
    pack_id: str,
    *,
    endpoint: str | None,
    model: str | None,
):
    pack = shipped_pack(pack_id)

    @asynccontextmanager
    async def build(_run: Any):
        client = (
            OllamaClient(
                endpoint,
                concurrency=settings.ollama_concurrency,
                context_tokens=settings.ollama_context_tokens,
            )
            if endpoint
            else None
        )
        try:
            yield Narrating(
                ReviewPipeline(
                    source=BundleSource(),
                    pack=pack,
                    client=client,
                    model=model,
                    effort=load_effort_model(settings.effort_costs),
                )
            )
        finally:
            if client is not None:
                await client.aclose()

    return build


def _narrating_factory(inner_factory):
    @asynccontextmanager
    async def build(run: Any):
        async with inner_factory(run) as pipeline:
            yield Narrating(pipeline)

    return build


def _factory(
    settings: Settings,
    paths: DataPaths,
    store: RunStore,
    *,
    bundles: bool,
    pack_id: str,
    endpoint: str | None,
    model: str | None,
):
    """Build the pipeline factory this invocation needs.

    A ``--model`` or ``--endpoint`` typed here overrides the stored preference for this run and
    does not change it: a flag is an answer about one run.
    """
    preferences = PreferenceStore(
        store,
        env_request_content_export=settings.request_content_export_from_env,
        env_model_host_endpoint=settings.ollama_base_url,
    )
    if bundles:
        return _bundle_factory(
            settings,
            pack_id,
            endpoint=endpoint or preferences.model_host().endpoint,
            model=model or preferences.review_model().name,
        )
    credentials = CredentialStore(
        store,
        paths,
        env_base_url=settings.canvas_base_url,
        env_token=(
            settings.canvas_api_token.get_secret_value() if settings.canvas_api_token else None
        ),
        key=(settings.secret_key.get_secret_value() if settings.secret_key else None),
    )
    return _narrating_factory(
        live_pipeline_factory(
            settings=settings,
            paths=paths,
            credentials=credentials,
            preferences=preferences,
            model_endpoint=endpoint,
            model_name=model,
        )
    )


async def _start(args: argparse.Namespace, settings: Settings) -> int:
    if args.course and args.bundle:
        print(
            "Give --course or --bundle, not both. A run records what it reviewed, and half a "
            "batch from a different source is not one batch.",
            file=sys.stderr,
        )
        return 2
    references: Sequence[str] = args.course or args.bundle
    if not references:
        print("Nothing to review. Give at least one --course or --bundle.", file=sys.stderr)
        return 2
    if args.pack not in shipped_packs():
        available = ", ".join(sorted(shipped_packs()))
        print(f"No rubric pack {args.pack!r}. Available: {available}.", file=sys.stderr)
        return 2

    paths, store = _open(settings)
    try:
        try:
            run_id = create_batch_run(
                store,
                references,
                rubric_pack=args.pack,
                cap=settings.batch_cap,
                note=args.note,
            )
        except BatchTooLargeError as exc:
            print(f"{exc.summary} {exc.remedy}", file=sys.stderr)
            return 2
        print(f"Run {run_id}: {len(references)} course(s) against {args.pack}.")
        factory = _factory(
            settings,
            paths,
            store,
            bundles=bool(args.bundle),
            pack_id=args.pack,
            endpoint=args.endpoint,
            model=args.model,
        )
        summary = await BatchRunner(store, paths, factory).execute(run_id)
        print(describe(summary))
        return 0 if not (summary.failed or summary.aborted or summary.not_run) else 1
    finally:
        store.dispose()


async def _resume(args: argparse.Namespace, settings: Settings) -> int:
    paths, store = _open(settings)
    try:
        run = store.get_run(args.run_id)
        if run is None:
            print(f"No run {args.run_id!r} in the run store.", file=sys.stderr)
            return 2
        store.recover_interrupted()
        bundles = any(is_bundle_reference(course.course_ref) for course in run.courses)
        factory = _factory(
            settings,
            paths,
            store,
            bundles=bundles,
            pack_id=run.rubric_pack,
            endpoint=args.endpoint,
            model=args.model,
        )
        summary = await BatchRunner(store, paths, factory).execute(args.run_id)
        print(describe(summary))
        return 0 if not (summary.failed or summary.aborted or summary.not_run) else 1
    finally:
        store.dispose()


def _status(args: argparse.Namespace, settings: Settings) -> int:
    _paths, store = _open(settings)
    try:
        if not args.run_id:
            runs = store.list_runs(limit=20)
            if not runs:
                print("No runs recorded yet.")
                return 0
            for run in runs:
                print(
                    f"{run.id}  {run.status_label:<24} {len(run.courses)} course(s)  "
                    f"{run.rubric_pack}"
                )
            return 0
        progress = progress_of(store, args.run_id)
        if progress is None:
            print(f"No run {args.run_id!r} in the run store.", file=sys.stderr)
            return 2
        print(f"Run {progress.run_id} — {progress.statement()}")
        for course in progress.courses:
            print(f"  {course.line()}")
            print(f"    id {course.course_id}")
            for stage in course.stages:
                print(f"    {stage.line()}")
            if course.reviewable:
                opened = (
                    f"review opened {course.review_opened_at:%Y-%m-%d %H:%M}"
                    if course.review_opened
                    else "no review opened yet — nothing may be exported for this course"
                )
                print(f"    {opened}")
                print(f"    review at /review/{course.course_id}")
        return 0
    finally:
        store.dispose()


def _open_review(args: argparse.Namespace, settings: Settings) -> int:
    paths, store = _open(settings)
    try:
        gate = ExportGate(store, paths)
        try:
            opened = gate.open_review(args.course_id)
        except KeyError:
            print(f"No course {args.course_id!r} in the run store.", file=sys.stderr)
            return 2
        print(
            f"Review of course {args.course_id} opened at {opened:%Y-%m-%d %H:%M:%S}. "
            "A report may now be written for it — Sightline still writes none by itself."
        )
        return 0
    finally:
        store.dispose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)
    match args.command:
        case "run":
            return asyncio.run(_start(args, settings))
        case "resume":
            return asyncio.run(_resume(args, settings))
        case "status":
            return _status(args, settings)
        case "open-review":
            return _open_review(args, settings)
    return 2  # pragma: no cover - argparse rejects anything else


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
