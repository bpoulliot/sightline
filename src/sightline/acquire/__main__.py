"""``python -m sightline.acquire`` — normalize a course and describe what came back.

Two forms, and the first needs no Canvas token at all:

    python -m sightline.acquire --bundle examples/sample-course
    python -m sightline.acquire 4410

The second reads the configured Canvas instance. It prints counts, the examination record and
sample deep links; it never prints a body, because a course body is instructor-authored content
and a terminal is a place things get pasted from. ``--json`` writes the whole document to stdout
for anyone who does want it.

It also honours the fresh-export preference set on the settings screen, and says so while it
waits: an export takes minutes on a large course, and a command that prints nothing for that long
looks like one that has hung. ``--no-request-export`` overrides the preference for one run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from ..canvas.client import CanvasClient
from ..canvas.errors import CanvasError
from ..canvas.exports import ExportProgress
from ..config import get_settings
from ..model import CourseDocument
from ..preferences import PreferenceStore
from ..store import RunStore
from .bundle import BundleError, load_bundle
from .service import CourseAcquisition


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sightline.acquire",
        description="Normalize a Canvas course into Sightline's document model.",
    )
    parser.add_argument(
        "course",
        nargs="?",
        help="A course id or a Canvas course URL. Omitted when --bundle is given.",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        help="Read a course bundle directory instead of Canvas — needs no token.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print the whole normalized document to stdout."
    )
    parser.add_argument(
        "--no-cache", action="store_true", help="Acquire from Canvas even if a cache entry matches."
    )
    parser.add_argument(
        "--no-export",
        action="store_true",
        help="Use no content export at all: no request, and no existing one read.",
    )
    parser.add_argument(
        "--no-request-export",
        action="store_true",
        help=(
            "Do not ask Canvas to build a fresh export; read one the course already has. "
            "Overrides the setting for this run only."
        ),
    )
    return parser


def describe(document: CourseDocument) -> str:
    """Counts, examinations and a few links. Facts only — this is what tier A sounds like."""
    lines = [
        f"{document.course.name} ({document.course.id})",
        f"  base URL: {document.base_url}",
        "  artifacts: "
        + (", ".join(f"{name} {count}" for name, count in document.counts().items()) or "none"),
        "",
        "  examined:",
    ]
    for entry in document.examinations:
        lines.append(
            f"    {entry.collection:<22} {entry.state.value:<8} {entry.examined:>4}  "
            f"{entry.detail}"
        )
        if entry.remedy:
            lines.append(f"    {'':<22} remedy: {entry.remedy}")
    inexact = [artifact for artifact in document.artifacts() if not artifact.link_exact]
    lines += [
        "",
        f"  deep links: {sum(1 for _ in document.artifacts())} artifact(s), "
        f"{len(inexact)} located by search rather than by id",
    ]
    for artifact in list(document.artifacts())[:5]:
        lines.append(f"    {artifact.kind.value:<12} {document.deep_link(artifact)}")
    return "\n".join(lines)


def _announce(progress: ExportProgress) -> None:
    """Say that the wait is a wait.

    An export takes minutes on a large course, and a command that prints nothing for six minutes
    is indistinguishable from one that has hung. Goes to stderr so ``--json`` stays pipeable.
    """
    print(progress.message, file=sys.stderr, flush=True)


async def _from_canvas(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.canvas_configured:
        print(
            "No Canvas base URL and token are configured. Set CANVAS_BASE_URL and "
            "CANVAS_API_TOKEN, or run against a bundle with --bundle.",
            file=sys.stderr,
        )
        return 2
    token = settings.canvas_api_token.get_secret_value() if settings.canvas_api_token else None
    paths = settings.paths.ensure()

    # The preference is read from the run store, not from the environment alone: a deployment
    # that switched the fresh-export request off on the settings screen must stay off here too.
    # That is the whole point of a no-writes policy, and a CLI that ignored it would be a hole
    # in one.
    store = RunStore.open(paths)
    try:
        preference = PreferenceStore(
            store, env_request_content_export=settings.request_content_export_from_env
        ).request_content_export()
    finally:
        store.dispose()
    request_export = preference.enabled and not args.no_request_export

    async with CanvasClient(settings.canvas_base_url, token) as client:
        acquisition = CourseAcquisition(
            client,
            paths,
            request_export=request_export,
            on_export_progress=_announce,
        )
        try:
            result = await acquisition.acquire(
                args.course,
                use_cache=not args.no_cache,
                include_export=not args.no_export,
            )
        except CanvasError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    if args.json:
        sys.stdout.write(result.document.to_json())
    else:
        print(describe(result.document))
        print()
        print(f"  {result.summary()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.bundle is not None:
        try:
            document = load_bundle(args.bundle)
        except BundleError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        if args.json:
            sys.stdout.write(document.to_json())
        else:
            print(describe(document))
        return 0
    if not args.course:
        build_parser().print_usage(sys.stderr)
        print("a course id or --bundle is required", file=sys.stderr)
        return 2
    return asyncio.run(_from_canvas(args))


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
