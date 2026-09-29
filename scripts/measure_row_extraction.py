"""Roadmap item 33's instrument: what a routed row costs, and what each way of cutting it loses.

    .venv/bin/python scripts/measure_row_extraction.py selftest
    .venv/bin/python scripts/measure_row_extraction.py run --arm baseline --model M --out A.json
    .venv/bin/python scripts/measure_row_extraction.py run --arm multi --rows-per-call 4 ...
    .venv/bin/python scripts/measure_row_extraction.py embed --out E.json
    .venv/bin/python scripts/measure_row_extraction.py compare A.json B.json [C.json ...]
    .venv/bin/python scripts/measure_row_extraction.py narrow A.json [B.json] --embeddings E.json

Everything runs over the SYNTHETIC sample course (``examples/sample-course``) and a shipped pack.
Nothing here reaches Canvas, and nothing here writes anywhere but the ``--out`` path it is given.
The model host is ``OLLAMA_BASE_URL`` (or ``--endpoint``); the model is named on every run and its
digest is read from ``/api/tags`` and recorded beside the numbers.

**The arms.**

* ``baseline`` is :func:`sightline.extract.runner.answer_requests` exactly as shipped: one call per
  passage per routed row. It is the ground truth, and it is not deterministic at temperature 0
  (item 10 measured 18 and 16 quotes from two identical runs), so it is run twice and the second
  run is scored against the first the same way a candidate is. A candidate's misses are signal only
  where they exceed that.
* ``multi`` is candidate (a): one call per passage carrying ``--rows-per-call`` criteria, each
  finding keyed by criterion code. Every returned quote goes through the same
  :meth:`~sightline.extract.base.Extractor.accept` the baseline uses.
* Candidates (b) and (c) — narrowing a row's corpus by a lexical pre-filter or by embedding
  similarity — are **simulated from the baseline runs, not re-run**. Each baseline call sees exactly
  one passage and one row, so skipping a passage removes that call and changes no other: the
  narrowed arm's evidence *is* the baseline's evidence on the passages it kept. ``narrow`` reports,
  per setting, the calls kept and the baseline quotes that fell in a skipped passage.

**What "recalled" means.** A baseline quote is a (row, passage, source span). The candidate recalls
it when it returned, for the same row, a verified quote in the same passage whose source span
overlaps it. ``exact`` is the stricter count — the identical span. Spans are the harness's own:
every quote is re-located by :func:`~sightline.extract.verify.verify` against the passage text, and
the model's stated location is never read.

**Self-test, both directions** (``selftest``, and before every ``compare``/``narrow``): a run scored
against itself misses nothing; a run scored against an empty run misses everything; a run with one
quote moved to another row misses exactly that quote; a narrowing that keeps every passage keeps
every call and loses nothing, and one that keeps none keeps no call and loses everything. Any
failure refuses the report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
import time
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sightline.acquire.bundle import load_bundle
from sightline.batch.pipeline import estimated_calls
from sightline.detect.runner import run_detectors
from sightline.extract.base import Corpus
from sightline.extract.passages import Passage
from sightline.extract.runner import RowExtractor, answer_requests, thinking_for
from sightline.extract.schema import MAX_LOCATION_CHARS, MAX_QUOTE_CHARS, RawFinding
from sightline.extract.verify import verify
from sightline.llm.client import OllamaClient
from sightline.rubric.project import project
from sightline.rubric.routing import ExtractionRequest, manifest
from sightline.rubric.shipped import shipped_pack

REPO = Path(__file__).resolve().parent.parent
BUNDLE = REPO / "examples" / "sample-course"


# ---------------------------------------------------------------------------------------------
# The course, the rows, the passages — identical for every arm
# ---------------------------------------------------------------------------------------------


def setup(pack_id: str, rows: Sequence[str] | None):
    document = load_bundle(BUNDLE)
    projection = project(shipped_pack(pack_id), run_detectors(document))
    requests = list(manifest(projection.rows).requests)
    if rows:
        wanted = set(rows)
        requests = [request for request in requests if request.criterion in wanted]
        if {request.criterion for request in requests} != wanted:
            raise SystemExit(f"--rows names a criterion the pack does not route: {sorted(wanted)}")
    passages = list(Corpus(document, RowExtractor(requests[0])).passages())
    return document, requests, passages


def passage_key(passage: Passage) -> str:
    return f"{passage.artifact.id}#{passage.part}"


def locate(quote: str, artifact_id: str, passages: Sequence[Passage]) -> dict[str, Any] | None:
    """Re-find a verified quote in the passage it came from. The harness's span, not the model's."""
    for passage in passages:
        if passage.artifact.id != artifact_id:
            continue
        match = verify(quote, passage.text, normalized_source=passage.normalized)
        if match is not None:
            return {"passage": passage_key(passage), "start": match.start, "end": match.end}
    return None


def digest_of(endpoint: str, model: str) -> str | None:
    with urllib.request.urlopen(f"{endpoint.rstrip('/')}/api/tags", timeout=15) as response:
        for entry in json.load(response).get("models", []):
            if entry.get("name") == model or entry.get("model") == model:
                return entry.get("digest")
    return None


def endpoint_of(args: argparse.Namespace) -> str:
    endpoint = args.endpoint or os.environ.get("OLLAMA_BASE_URL")
    if not endpoint:
        raise SystemExit("Name the model host with --endpoint or OLLAMA_BASE_URL.")
    return endpoint


# ---------------------------------------------------------------------------------------------
# run — the baseline, or candidate (a)
# ---------------------------------------------------------------------------------------------


async def run_baseline(client, card, document, requests, passages) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    calls: list[dict[str, Any]] = []
    answered = await answer_requests(requests, document, client, card)
    for row in answered:
        evidence = []
        for item in row.result.evidence:
            span = locate(item.quote, item.location.artifact_id, passages)
            if span is None:
                raise SystemExit(f"A verified quote could not be re-located: {item.quote[:60]!r}")
            evidence.append({"quote": item.quote, **span})
        rows[row.criterion] = {
            "evidence": evidence,
            "discarded": len(row.result.discarded),
            "failures": len(row.result.failures),
            "passages_examined": row.result.passages,
        }
        for call in row.result.calls:
            calls.append(
                {
                    "rows": [row.criterion],
                    "passage": call.passage,
                    "ok": call.ok,
                    "latency_ms": call.latency_ms,
                    "prompt_tokens": call.prompt_tokens,
                    "completion_tokens": call.completion_tokens,
                }
            )
    return {"rows": rows, "calls": calls}


def multi_schema(codes: Sequence[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "criterion": {"type": "string", "enum": list(codes)},
                        "quote": {"type": "string"},
                        "location": {"type": "string"},
                    },
                    "required": ["criterion", "quote", "location"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["findings"],
        "additionalProperties": False,
    }


def multi_prompt(passage: Passage, chunk: Sequence[ExtractionRequest]) -> list[dict[str, str]]:
    """Candidate (a)'s messages: :func:`~sightline.extract.base.prompt_for`, with N criteria."""
    kind = passage.artifact.kind.value.replace("_", " ")
    header = f"{passage.label} — a course {kind}"
    if passage.artifact.module_path:
        header += f", in {' › '.join(passage.artifact.module_path)}"
    listed = "\n".join(f"    [{request.criterion}] “{request.text}”" for request in chunk)
    system = (
        "You locate passages inside one document. You never summarise, never rate, never "
        "evaluate, never explain, and never write prose. You answer with JSON and nothing else."
    )
    user = (
        f"Document: {header}\n\n"
        "---\n"
        f"{passage.text}\n"
        "---\n\n"
        "The criteria this course is being read against are, each with its code:\n"
        f"{listed}\n\n"
        "For each criterion, find every passage in the document above that bears on it — a "
        "passage a reviewer would point at when deciding it. Quote the passage itself. A passage "
        "that bears on two criteria is listed once under each code.\n\n"
        "Rules:\n"
        '- Give each quote the "criterion" code it bears on, exactly as written in the list.\n'
        "- Quote exactly as written above, character for character. Do not paraphrase, do not "
        "shorten, do not join two separate sentences together, do not correct anything.\n"
        "- Only quote text that appears in the document above.\n"
        "- Include a passage only if the passage itself says what is asked for. A passage that "
        "mentions the subject without saying it is not one of these.\n"
        '- Give each quote a short "location" naming where in this document you found it.\n'
        "- Most documents bear on few of these criteria. If this one bears on none, return "
        '{"findings": []}. An empty list is a complete and expected answer.\n'
        "- Do not rate, score or judge anything. There is no field for it."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_multi(content: str, codes: Sequence[str]) -> list[tuple[str, RawFinding]] | str:
    """The keyed findings, or the reason the response was refused. Refused, never coerced."""
    try:
        payload = json.loads(content.strip())
    except json.JSONDecodeError as exc:
        return f"not JSON ({exc.msg})"
    if not isinstance(payload, dict) or sorted(payload) != ["findings"]:
        return "not an object with exactly 'findings'"
    if not isinstance(payload["findings"], list):
        return "'findings' is not a list"
    found = []
    for entry in payload["findings"]:
        if not isinstance(entry, dict) or sorted(entry) != ["criterion", "location", "quote"]:
            return "a finding without exactly criterion/quote/location"
        if entry["criterion"] not in codes:
            return f"a finding keyed to {entry['criterion']!r}, not a code asked about"
        quote, location = entry["quote"], entry["location"]
        if not isinstance(quote, str) or not isinstance(location, str) or not quote.strip():
            return "a finding with an empty or non-string quote"
        if len(quote) > MAX_QUOTE_CHARS or len(location) > MAX_LOCATION_CHARS:
            return "a finding over the length limits"
        found.append((entry["criterion"], RawFinding(quote=quote, location=location.strip())))
    return found


async def run_multi(client, card, requests, passages, per_call: int) -> dict[str, Any]:
    thinking = thinking_for(card)
    chunks = [requests[i : i + per_call] for i in range(0, len(requests), per_call)]
    rows: dict[str, Any] = {
        request.criterion: {
            "evidence": [],
            "discarded": 0,
            "failures": 0,
            "passages_examined": len(passages),
        }
        for request in requests
    }
    calls: list[dict[str, Any]] = []
    for passage in passages:
        for chunk in chunks:
            codes = [request.criterion for request in chunk]
            started = time.perf_counter()
            completion = await client.chat(
                card,
                multi_prompt(passage, chunk),
                thinking=thinking,
                operation="extract.item33-multi",
                response_format=multi_schema(codes),
            )
            latency = round((time.perf_counter() - started) * 1000, 2)
            parsed = parse_multi(completion.content, codes)
            calls.append(
                {
                    "rows": codes,
                    "passage": passage.label,
                    "ok": not isinstance(parsed, str),
                    "error": parsed if isinstance(parsed, str) else None,
                    "latency_ms": latency,
                    "prompt_tokens": completion.prompt_tokens,
                    "completion_tokens": completion.completion_tokens,
                }
            )
            if isinstance(parsed, str):
                for code in codes:
                    rows[code]["failures"] += 1
                continue
            by_code = {request.criterion: request for request in chunk}
            for code, finding in parsed:
                match = RowExtractor(by_code[code]).accept(finding, passage)
                if match is None:
                    rows[code]["discarded"] += 1
                    continue
                rows[code]["evidence"].append(
                    {
                        "quote": match.text,
                        "passage": passage_key(passage),
                        "start": match.start,
                        "end": match.end,
                    }
                )
    return {"rows": rows, "calls": calls}


async def command_run(args: argparse.Namespace) -> None:
    endpoint = endpoint_of(args)
    document, requests, passages = setup(args.pack, args.rows)
    client = OllamaClient(endpoint)
    card = await client.card(args.model)
    started = time.time()
    clock = time.perf_counter()
    if args.arm == "baseline":
        result = await run_baseline(client, card, document, requests, passages)
    else:
        result = await run_multi(client, card, requests, passages, args.rows_per_call)
    wall = time.perf_counter() - clock
    estimate = estimated_calls(document, requests)
    if args.arm == "baseline" and len(result["calls"]) != estimate:
        # The acceptance item 33 carries: the price printed at the project stage is the price paid.
        raise SystemExit(
            f"The project stage estimated {estimate} call(s) and the run made "
            f"{len(result['calls'])}. The estimate no longer matches the spend."
        )
    payload = {
        "estimated_calls": estimate,
        "arm": args.arm if args.arm == "baseline" else f"multi-{args.rows_per_call}",
        "pack": args.pack,
        "model": card.name,
        "digest": digest_of(endpoint, args.model),
        "thinking_pinned": thinking_for(card).value if card.supports_thinking else None,
        "rows_asked": [request.criterion for request in requests],
        "passages": [passage_key(passage) for passage in passages],
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
        "wall_s": round(wall, 1),
        **result,
    }
    Path(args.out).write_text(json.dumps(payload, indent=1))
    quotes = sum(len(row["evidence"]) for row in result["rows"].values())
    print(
        f"{payload['arm']}: {len(result['calls'])} call(s), {wall:.0f} s, {quotes} verified "
        f"quote(s), {sum(1 for call in result['calls'] if not call['ok'])} failed call(s) "
        f"-> {args.out}"
    )


# ---------------------------------------------------------------------------------------------
# compare — recall of one run against another
# ---------------------------------------------------------------------------------------------


def truth_quotes(run: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(code, quote) for code, row in run["rows"].items() for quote in row["evidence"]]


def recalled(quote: dict[str, Any], candidates: Sequence[dict[str, Any]], *, exact: bool) -> bool:
    for other in candidates:
        if other["passage"] != quote["passage"]:
            continue
        if exact and (other["start"], other["end"]) == (quote["start"], quote["end"]):
            return True
        if not exact and other["start"] < quote["end"] and quote["start"] < other["end"]:
            return True
    return False


def score(truth: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """Per row: baseline quotes, missed (overlap), missed (exact span), and the candidate's extras."""
    rows: dict[str, dict[str, int]] = {}
    for code, row in truth["rows"].items():
        mine = candidate["rows"].get(code, {"evidence": []})["evidence"]
        theirs = row["evidence"]
        rows[code] = {
            "truth": len(theirs),
            "missed": sum(1 for quote in theirs if not recalled(quote, mine, exact=False)),
            "missed_exact": sum(1 for quote in theirs if not recalled(quote, mine, exact=True)),
            "extra": sum(1 for quote in mine if not recalled(quote, theirs, exact=False)),
        }
    totals = {key: sum(row[key] for row in rows.values()) for key in next(iter(rows.values()))}
    rows_losing = sum(1 for row in rows.values() if row["missed"])
    rows_emptied = sum(
        1
        for code, row in rows.items()
        if row["truth"] and not candidate["rows"].get(code, {"evidence": []})["evidence"]
    )
    return {"rows": rows, "totals": totals, "rows_losing": rows_losing, "rows_emptied": rows_emptied}


def self_test_compare() -> None:
    run = {
        "rows": {
            "1": {"evidence": [{"passage": "p#1", "start": 0, "end": 10}]},
            "2": {
                "evidence": [
                    {"passage": "p#1", "start": 20, "end": 30},
                    {"passage": "q#1", "start": 5, "end": 9},
                ]
            },
        }
    }
    empty = {"rows": {"1": {"evidence": []}, "2": {"evidence": []}}}
    moved = {
        "rows": {
            "1": {"evidence": [{"passage": "p#1", "start": 0, "end": 10}]},
            "2": {"evidence": [{"passage": "p#1", "start": 20, "end": 30}]},
            "3": {"evidence": [{"passage": "q#1", "start": 5, "end": 9}]},
        }
    }
    wider = {"rows": {"1": {"evidence": [{"passage": "p#1", "start": 0, "end": 40}]}}}
    checks = [
        (score(run, run)["totals"]["missed"], 0, "a run against itself"),
        (score(run, empty)["totals"]["missed"], 3, "a run against an empty run"),
        (score(run, empty)["rows_emptied"], 2, "rows emptied by an empty run"),
        (score(run, moved)["totals"]["missed"], 1, "one quote moved to another row"),
        (score(run, wider)["totals"]["missed"], 2, "a wider span overlaps, row 2 absent"),
        (score(run, wider)["totals"]["missed_exact"], 3, "a wider span is not the exact span"),
    ]
    failed = [f"{label}: got {got}, expected {want}" for got, want, label in checks if got != want]
    if failed:
        raise SystemExit("comparator self-test FAILED, refusing to report:\n  " + "\n  ".join(failed))


def command_compare(args: argparse.Namespace) -> None:
    self_test_compare()
    runs = [json.loads(Path(path).read_text()) for path in args.runs]
    truth = runs[0]
    models = {(run["model"], run["digest"]) for run in runs}
    if len(models) != 1:
        raise SystemExit(f"Runs are from different models, not comparable: {models}")
    print(f"truth: {args.runs[0]} ({truth['arm']}, {truth['model']} {truth['digest'][:12]})")
    print(
        f"{'run':<34} {'calls':>5} {'wall s':>7} {'s/call':>6} {'quotes':>6} {'missed':>6} "
        f"{'exact':>6} {'extra':>5} {'rows lost':>9} {'emptied':>7} {'failed':>6}"
    )
    for path, run in zip(args.runs, runs, strict=True):
        result = score(truth, run)
        calls = len(run["calls"])
        quotes = sum(len(row["evidence"]) for row in run["rows"].values())
        failed = sum(1 for call in run["calls"] if not call["ok"])
        print(
            f"{Path(path).name:<34} {calls:>5} {run['wall_s']:>7.0f} "
            f"{run['wall_s'] / max(calls, 1):>6.1f} {quotes:>6} {result['totals']['missed']:>6} "
            f"{result['totals']['missed_exact']:>6} {result['totals']['extra']:>5} "
            f"{result['rows_losing']:>9} {result['rows_emptied']:>7} {failed:>6}"
        )
    if args.per_row:
        header = "row  " + " ".join(f"{Path(p).stem[-14:]:>15}" for p in args.runs)
        print("\nper row, quotes/missed (overlap) against the truth run:")
        print(header)
        for code in truth["rows"]:
            cells = []
            for run in runs:
                row = score(truth, run)["rows"][code]
                quotes = len(run["rows"].get(code, {"evidence": []})["evidence"])
                cells.append(f"{quotes:>3}q {row['missed']:>2}m/{row['truth']:<3}")
            print(f"{code:<4} " + " ".join(f"{cell:>15}" for cell in cells))


# ---------------------------------------------------------------------------------------------
# embed + narrow — candidates (b) and (c), simulated from the baseline
# ---------------------------------------------------------------------------------------------

_WORD = re.compile(r"[a-z][a-z'-]+")
#: Function words and the rubric's own boilerplate, which every row and every passage shares.
_STOP = set(
    """
    a about above after all also an and any are as at be been being both but by can course
    courses each for from has have how i if in include includes into is it its learner learners
    may more most not of on or other our provide provided should so some such than that the their
    them there these they this those through to use used via was we were what when where which
    who will with within would you your e.g i.e etc
    """.split()
)


def stem(word: str) -> str:
    for suffix in ("ations", "ation", "ings", "ing", "ies", "es", "ed", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def terms(text: str) -> set[str]:
    return {stem(word) for word in _WORD.findall(text.lower()) if word not in _STOP}


def lexical_keep(requests, passages, threshold: int) -> dict[str, set[str]]:
    kept = {}
    for request in requests:
        wanted = terms(request.text)
        kept[request.criterion] = {
            passage_key(passage)
            for passage in passages
            if len(wanted & terms(passage.text)) >= threshold
        }
    return kept


def embedding_keep(embeddings: dict[str, Any], top: int) -> dict[str, set[str]]:
    def cosine(a: Sequence[float], b: Sequence[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))

    kept = {}
    for code, vector in embeddings["rows"].items():
        ranked = sorted(
            embeddings["passages"], key=lambda key: -cosine(vector, embeddings["passages"][key])
        )
        kept[code] = set(ranked[:top])
    return kept


def narrowed(run: dict[str, Any], kept: dict[str, set[str]]) -> dict[str, Any]:
    """The baseline run as it would have come out had each row read only its kept passages."""
    return {
        **run,
        "rows": {
            code: {
                **row,
                "evidence": [q for q in row["evidence"] if q["passage"] in kept.get(code, set())],
            }
            for code, row in run["rows"].items()
        },
    }


def self_test_narrow(truth: dict[str, Any]) -> None:
    every = {code: set(truth["passages"]) for code in truth["rows"]}
    none: dict[str, set[str]] = {code: set() for code in truth["rows"]}
    total = sum(len(row["evidence"]) for row in truth["rows"].values())
    problems = []
    if score(truth, narrowed(truth, every))["totals"]["missed"] != 0:
        problems.append("keeping every passage lost a quote")
    if sum(len(v) for v in every.values()) != len(truth["calls"]):
        problems.append("keeping every passage does not keep every call")
    if score(truth, narrowed(truth, none))["totals"]["missed"] != total:
        problems.append("keeping no passage did not lose every quote")
    if problems:
        raise SystemExit("narrowing self-test FAILED, refusing to report:\n  " + "\n  ".join(problems))


async def command_embed(args: argparse.Namespace) -> None:
    endpoint = endpoint_of(args)
    _, requests, passages = setup(args.pack, args.rows)
    client = OllamaClient(endpoint)
    # nomic-embed-text is trained with task prefixes; the query side is the criterion.
    texts = [f"search_query: {request.text}" for request in requests] + [
        f"search_document: {passage.label}\n{passage.text}" for passage in passages
    ]
    started = time.perf_counter()
    body = await client._request(  # noqa: SLF001 — a measurement, not a feature
        "POST", "/api/embed", payload={"model": args.model, "input": texts}, timeout=120
    )
    wall = time.perf_counter() - started
    vectors = body["embeddings"]
    if len(vectors) != len(texts) or len({len(v) for v in vectors}) != 1:
        raise SystemExit("The host returned the wrong number or shape of embeddings.")
    payload = {
        "model": args.model,
        "digest": digest_of(endpoint, args.model),
        "wall_s": round(wall, 2),
        "rows": {r.criterion: vectors[i] for i, r in enumerate(requests)},
        "passages": {
            passage_key(p): vectors[len(requests) + i] for i, p in enumerate(passages)
        },
    }
    Path(args.out).write_text(json.dumps(payload))
    print(f"{len(texts)} embeddings of dimension {len(vectors[0])} in {wall:.2f} s -> {args.out}")


def command_narrow(args: argparse.Namespace) -> None:
    self_test_compare()
    runs = [json.loads(Path(path).read_text()) for path in args.runs]
    base = runs[0]
    self_test_narrow(base)
    _, requests, passages = setup(base["pack"], None)
    requests = [r for r in requests if r.criterion in base["rows"]]
    arms: list[tuple[str, dict[str, set[str]]]] = [
        (f"lexical >= {t} shared term(s)", lexical_keep(requests, passages, t)) for t in (1, 2, 3)
    ]
    if args.embeddings:
        embeddings = json.loads(Path(args.embeddings).read_text())
        arms += [
            (f"embedding top {k} of {len(passages)}", embedding_keep(embeddings, k))
            for k in (3, 5, 8, 10)
        ]
    full = len(base["calls"])
    print(f"baseline {full} call(s) over {len(passages)} passage(s) x {len(base['rows'])} row(s)")
    for index, run in enumerate(runs, start=1):
        total = sum(len(row["evidence"]) for row in run["rows"].values())
        print(f"\nagainst baseline run {index} ({total} quotes, {Path(args.runs[index - 1]).name}):")
        print(f"{'arm':<32} {'calls':>5} {'share':>6} {'missed':>6} {'rows lost':>9} {'emptied':>7}")
        for label, kept in arms:
            calls = sum(len(passages_) for passages_ in kept.values())
            result = score(run, narrowed(run, kept))
            print(
                f"{label:<32} {calls:>5} {calls / full:>6.0%} {result['totals']['missed']:>6} "
                f"{result['rows_losing']:>9} {result['rows_emptied']:>7}"
            )


# ---------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--arm", choices=("baseline", "multi"), required=True)
    run.add_argument("--rows-per-call", type=int, default=4)
    run.add_argument("--model", required=True)
    run.add_argument("--pack", default="uccs-oscqr-rsi")
    run.add_argument("--rows", nargs="*", help="Criterion codes to restrict to (default: all).")
    run.add_argument("--endpoint")
    run.add_argument("--out", required=True)
    embed = sub.add_parser("embed")
    embed.add_argument("--model", default="nomic-embed-text:latest")
    embed.add_argument("--pack", default="uccs-oscqr-rsi")
    embed.add_argument("--rows", nargs="*")
    embed.add_argument("--endpoint")
    embed.add_argument("--out", required=True)
    compare = sub.add_parser("compare")
    compare.add_argument("runs", nargs="+", help="The first run is the truth.")
    compare.add_argument("--per-row", action="store_true")
    narrow = sub.add_parser("narrow")
    narrow.add_argument("runs", nargs="+", help="Baseline runs; the first defines the arms.")
    narrow.add_argument("--embeddings")
    sub.add_parser("selftest")
    args = parser.parse_args(argv)
    if args.command == "run":
        asyncio.run(command_run(args))
    elif args.command == "embed":
        asyncio.run(command_embed(args))
    elif args.command == "compare":
        command_compare(args)
    elif args.command == "narrow":
        command_narrow(args)
    else:
        self_test_compare()
        print("comparator self-test passed (6 checks, both directions)")


if __name__ == "__main__":
    sys.exit(main())
