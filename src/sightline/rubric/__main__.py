"""``python -m sightline.rubric`` — import a rubric, and say what it can and cannot answer.

    python -m sightline.rubric packs
    python -m sightline.rubric coverage --pack rubrics/packs/uccs-oscqr-3-1-3.yaml
    python -m sightline.rubric import --workbook "rubrics/source/UCCS OSCQR 3.1.3.xlsx"
    python -m sightline.rubric tags

Four subcommands, each printing a checkable artifact rather than a conclusion.

``packs`` lists the rubric packs this build ships and what each of them can answer today. The two
are peers: neither is a default and neither is expressed in terms of the other.

``coverage`` is the one worth having at a terminal: *"4 of 10 criteria have deterministic
coverage, 3 are evidence-only."* It needs no course and no token, so the question "will Sightline
actually do anything useful with my rubric?" has an answer before anybody uploads a course. Rows
with no binding are reported as evidence-only, which is what they are — not as a fault.

``import`` reads a workbook and writes the pack to **stdout**, with a report of *what it had to
decide* on stderr: which column it read the criterion text from, which column the codes came from,
how many rows, and anything it could not find. An import is trusted by being checked, and that
report is what there is to check it against. It exits non-zero on an import that produced no rows,
because a zero-row pack is a failed import and not an empty rubric.

    python -m sightline.rubric import --workbook my-rubric.xlsx --id mine > packs/mine.yaml

``--map`` applies a mapping file — the capability tags, the determinacy tiers and the section flags
a rubric workbook has no column for. Without one an import produces a pack with empty bindings,
which is finished and usable. ``scripts/build-packs.sh`` regenerates the two shipped packs that
way, and re-running it is byte-identical because the mapping file fixes the import date rather than
reading the clock.

**There is no ``--out``, deliberately.** :mod:`sightline.paths` is the only module in this package
allowed to create a writable path, and ``tests/test_paths.py`` enforces that by scanning the source
tree — the repository is public, so a write that lands somewhere nobody is watching is the bug that
eventually commits a course export. Shell redirection does the same job and puts the choice of
where the file lands back where it belongs, with the person running the command.

``tags`` prints the capability vocabulary with, for each concept, the detectors that implement it
today. A concept with no detector yet is listed as such: that is the ``NOT_YET_IMPLEMENTED`` state
made visible, and it is ordinary.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..capabilities import vocabulary
from .coverage import pack_coverage
from .importer import RubricImportError, import_csv, import_workbook
from .mapping import MappingError
from .mapping import load as load_mapping
from .pack import RubricPackError, load
from .project import TagIndex, tag_descriptions
from .shipped import PacksNotFoundError, packs_dir, shipped_packs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sightline.rubric",
        description="Import a rubric pack and report what it can answer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("coverage", help="What a pack can and cannot answer. No course needed.")
    report.add_argument("--pack", type=Path, required=True, help="A rubric pack YAML file.")
    report.add_argument(
        "--without-section",
        action="append",
        default=[],
        metavar="CODE",
        help="Switch an optional section off. Repeatable.",
    )
    report.add_argument("--json", action="store_true", help="Print the report as JSON.")

    bring_in = sub.add_parser("import", help="A workbook to a pack with empty bindings.")
    source = bring_in.add_mutually_exclusive_group(required=True)
    source.add_argument("--workbook", type=Path, help="An .xlsx or .xlsm rubric workbook.")
    source.add_argument(
        "--csv", type=Path, help="A CSV export. A lossy fallback; prefer the workbook."
    )
    bring_in.add_argument("--sheet", help="Which sheet to read, when the workbook has several.")
    bring_in.add_argument(
        "--map",
        dest="mapping",
        type=Path,
        help="A mapping file: the tags, tiers and section flags the workbook cannot carry.",
    )
    bring_in.add_argument("--id", dest="pack_id", help="Pack id. Chosen once and never renumbered.")
    bring_in.add_argument("--name", help="What to call this instrument in the interface.")
    bring_in.add_argument("--version", help="The instrument's own version.")

    sub.add_parser("packs", help="The rubric packs this build ships, and what each can answer.")
    sub.add_parser("tags", help="The capability vocabulary, and what implements each concept.")
    return parser


def _coverage(args: argparse.Namespace) -> int:
    try:
        pack = load(args.pack)
    except RubricPackError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for code in args.without_section:
        try:
            pack = pack.with_section_enabled(code, False)
        except (KeyError, RubricPackError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
    report = pack_coverage(pack)
    if args.json:
        sys.stdout.write(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
    else:
        print(report.detail())
    # A report whose buckets do not account for every row is a bug here, not a fact about the pack.
    return 0 if report.ok else 1


def _import(args: argparse.Namespace) -> int:
    try:
        mapping = load_mapping(args.mapping) if args.mapping is not None else None
    except MappingError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        if args.csv is not None:
            result = import_csv(
                args.csv,
                pack_id=args.pack_id,
                name=args.name,
                version=args.version,
                mapping=mapping,
            )
        else:
            result = import_workbook(
                args.workbook,
                sheet=args.sheet,
                pack_id=args.pack_id,
                name=args.name,
                version=args.version,
                mapping=mapping,
            )
    except RubricImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(result.report.statement(), file=sys.stderr)
    if mapping is None:
        print(
            "  bindings: none. That is the finished state of an import — no workbook has a column "
            "naming a detector — and the rows below will run and produce a review as they are.",
            file=sys.stderr,
        )
    else:
        print(f"  {mapping.statement()}", file=sys.stderr)
    print(pack_coverage(result.pack).statement(), file=sys.stderr)

    sys.stdout.write(result.pack.to_yaml())
    return 0


def _packs(_args: argparse.Namespace) -> int:
    """What this build ships. Counts per pack, never a ranking: the packs are peers."""
    try:
        directory = packs_dir()
        packs = shipped_packs()
    except (PacksNotFoundError, RubricPackError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not packs:
        print(f"No rubric packs are shipped in this build ({directory}).", file=sys.stderr)
        return 1
    print(f"{len(packs)} rubric pack(s) in {directory}:")
    index = TagIndex.build()
    for pack in packs.values():
        print(f"\n{pack.id}")
        print(f"  {pack.statement()}")
        for line in pack_coverage(pack, tags=index).detail().splitlines():
            print(f"  {line}")
    return 0


def _tags(_args: argparse.Namespace) -> int:
    words = vocabulary()
    index = TagIndex.build()
    described = tag_descriptions()
    print(words.statement())
    print(index.statement())
    for group, entries in words.by_group().items():
        print(f"\n{group}: {words.groups[group]}")
        for capability in entries:
            ids = index.detectors_by_tag.get(capability.tag, ())
            behind = ", ".join(ids) if ids else "no detector yet"
            print(f"  {capability.tag}  [{behind}]")
            print(f"      {capability.summary}")
            for description in described.get(capability.tag, ()):
                print(f"      - {description}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return {"coverage": _coverage, "import": _import, "packs": _packs, "tags": _tags}[args.command](
        args
    )


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
