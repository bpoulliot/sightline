"""Roadmap item 22's instrument: what the proposer proposes over the shipped packs, unbound.

    .venv/bin/python scripts/measure_binding_proposals.py selftest
    .venv/bin/python scripts/measure_binding_proposals.py run \
        --pack rubrics/packs/uccs-oscqr-rsi.yaml --model qwen3-coder:30b --out rsi.json
    .venv/bin/python scripts/measure_binding_proposals.py compare rsi.json [oscqr.json ...]

``run`` takes a shipped pack, strips every binding (tiers, text and codes are kept), and runs
:func:`sightline.rubric.proposals.propose` over it exactly as ``python -m sightline.rubric propose``
does. It writes the proposal set, the shipped bindings, the model's digest from ``/api/tags`` and
the host version to ``--out``, and nowhere else. No course is involved and nothing reaches Canvas.

``compare`` scores each run against the pack's shipped bindings as sets of (row code, tag) pairs:
proposed-and-shipped, proposed-not-shipped, shipped-not-proposed, with counts, per pack. **These
are a fact about one model on these packs on one day. They are not a product claim**, the tool
prints nothing like them, and the shipped bindings are one author's reading rather than ground
truth: a proposed-not-shipped pair is a disagreement with that reading, not necessarily an error.

**Self-test, both directions** (``selftest``, and before every ``compare``): the shipped bindings
scored against themselves give no extra and no missing pair; an empty proposal set misses every
shipped pair and proposes none; one pair moved to another row is exactly one extra and one missing;
stripping really leaves zero bindings and changes the pack's fingerprint. Any failure refuses the
report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from sightline.llm.client import OllamaClient
from sightline.rubric.pack import RubricPack, load
from sightline.rubric.proposals import fingerprint, propose

Pair = tuple[str, str]


def strip(pack: RubricPack) -> RubricPack:
    return replace(
        pack, criteria=tuple(replace(criterion, bindings=()) for criterion in pack.criteria)
    )


def shipped_pairs(pack: RubricPack) -> set[Pair]:
    return {(c.code, tag) for c in pack.criteria for tag in c.bindings}


def score(proposed: Iterable[Pair], shipped: Iterable[Pair]) -> dict[str, Any]:
    got, want = set(proposed), set(shipped)
    both, extra, missing = got & want, got - want, want - got
    return {
        "proposed": len(got),
        "shipped": len(want),
        "proposed_and_shipped": len(both),
        "proposed_not_shipped": len(extra),
        "shipped_not_proposed": len(missing),
        "pairs": {
            "proposed_and_shipped": sorted(both),
            "proposed_not_shipped": sorted(extra),
            "shipped_not_proposed": sorted(missing),
        },
    }


def selftest() -> list[str]:
    failures: list[str] = []
    pack = load("rubrics/packs/uccs-oscqr-rsi.yaml")
    want = shipped_pairs(pack)
    if not want:
        failures.append("the shipped pack has no bindings to score against")
        return failures
    same = score(want, want)
    if same["proposed_not_shipped"] or same["shipped_not_proposed"]:
        failures.append("the shipped set scored against itself is not a perfect match")
    if same["proposed_and_shipped"] != len(want):
        failures.append("the shipped set scored against itself lost pairs")
    empty = score(set(), want)
    if empty["shipped_not_proposed"] != len(want) or empty["proposed"]:
        failures.append("an empty proposal set did not miss every shipped pair")
    moved = set(want)
    code, tag = sorted(moved)[0]
    moved.discard((code, tag))
    other = next(c.code for c in pack.criteria if c.code != code and (c.code, tag) not in want)
    moved.add((other, tag))
    off = score(moved, want)
    if (off["proposed_not_shipped"], off["shipped_not_proposed"]) != (1, 1):
        failures.append("one pair moved to another row did not score as one extra, one missing")
    bare = strip(pack)
    if any(c.bindings for c in bare.criteria):
        failures.append("stripping left a binding behind")
    if fingerprint(bare) == fingerprint(pack):
        failures.append("stripping did not change the fingerprint")
    return failures


def model_digest(endpoint: str, model: str) -> str | None:
    with urllib.request.urlopen(f"{endpoint.rstrip('/')}/api/tags", timeout=15) as response:
        for entry in json.load(response).get("models", []):
            if entry.get("name") == model:
                return entry.get("digest")
    return None


async def _run(pack: RubricPack, endpoint: str, model: str) -> tuple[Any, float]:
    started = time.perf_counter()
    async with OllamaClient(endpoint, concurrency=1) as client:
        proposals = await propose(pack, client, model)
    return proposals, round(time.perf_counter() - started, 1)


def run(args: argparse.Namespace) -> int:
    endpoint = args.endpoint or os.environ.get("OLLAMA_BASE_URL")
    if not endpoint:
        print("pass --endpoint or set OLLAMA_BASE_URL", file=sys.stderr)
        return 2
    pack = load(args.pack)
    bare = strip(pack)
    proposals, seconds = asyncio.run(_run(bare, endpoint, args.model))
    record = {
        "pack": pack.id,
        "pack_version": pack.version,
        "rows": len(pack.criteria),
        "model": args.model,
        "model_digest": model_digest(endpoint, args.model),
        "seconds": seconds,
        "shipped": sorted(shipped_pairs(pack)),
        "proposals": proposals.to_dict(),
    }
    Path(args.out).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"{pack.id}: {proposals.statement()} {seconds} s. Written to {args.out}")
    return 0


def summarise(record: Mapping[str, Any]) -> dict[str, Any]:
    body = record["proposals"]
    proposed = {(p["code"], p["tag"]) for p in body["proposals"]}
    result = score(proposed, {tuple(pair) for pair in record["shipped"]})
    spans: dict[str, int] = {}
    for item in body["proposals"]:
        spans[item["span_state"]] = spans.get(item["span_state"], 0) + 1
    result.update(
        pack=record["pack"],
        rows=record["rows"],
        model=record["model"],
        model_digest=record["model_digest"],
        host_version=body["made_by"]["host_version"],
        seconds=record["seconds"],
        rows_with_none=sum(
            1
            for row in body["rows"]
            if row["failure"] is None and row.get("not_asked") is None and not row["proposals"]
        ),
        rows_not_asked=sum(1 for row in body["rows"] if row.get("not_asked") is not None),
        rows_failed=sum(1 for row in body["rows"] if row["failure"] is not None),
        unknown_dropped=sum(len(row["unknown"]) for row in body["rows"]),
        spans=spans,
    )
    return result


def compare(args: argparse.Namespace) -> int:
    failures = selftest()
    if failures:
        print("SELF-TEST FAILED; no report:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1
    for path in args.files:
        summary = summarise(json.loads(Path(path).read_text()))
        print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="measure_binding_proposals")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("selftest")
    go = sub.add_parser("run")
    go.add_argument("--pack", required=True)
    go.add_argument("--model", required=True)
    go.add_argument("--endpoint")
    go.add_argument("--out", required=True)
    cmp = sub.add_parser("compare")
    cmp.add_argument("files", nargs="+")
    args = parser.parse_args(argv)
    if args.command == "selftest":
        failures = selftest()
        print("self-test: " + ("PASS" if not failures else "FAIL\n  " + "\n  ".join(failures)))
        return 1 if failures else 0
    return {"run": run, "compare": compare}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
