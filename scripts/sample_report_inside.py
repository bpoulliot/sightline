"""The half of ``scripts/make-sample-report.sh`` that runs inside the dev image.

Operator decision D1 (2026-09-25): the sample course goes through the NORMAL gated export into a
SCRATCH data folder — a run over ``examples/sample-course``, the review opened with
``python -m sightline.batch open-review``, the export through the gate — and a script outside
``src/sightline`` copies the result into ``examples/sample-report/``. This is that run. It uses the
same commands a person would, in the same order, and writes nothing but into ``--out``:

1. ``python -m sightline.batch run --bundle examples/sample-course`` — the bundle given
   repo-relative, because the review file records the reference exactly as typed;
2. ``python -m sightline.batch open-review COURSE``;
3. ``python -m sightline.report export COURSE``;
4. the review file, the PDF, the workbook and ``render.json`` (the opened-at moment the re-render
   needs, and the render environment) copied to ``--out``.

Not importable from the package and not run by the suite: the model's output varies between runs,
so the committed review file is the input and only this regenerates it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BUNDLE = "examples/sample-course"
PACK = "uccs-oscqr-3-1-3"


def run(*args: str) -> str:
    print("+", " ".join(args), flush=True)
    done = subprocess.run(args, check=True, capture_output=True, text=True)
    sys.stdout.write(done.stdout)
    sys.stderr.write(done.stderr)
    return done.stdout


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True, help="The model host root.")
    parser.add_argument("--model", required=True, help="An installed model's name.")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    data = os.environ.get("SIGHTLINE_DATA_DIR")
    if not data or not Path(data).is_dir():
        print("SIGHTLINE_DATA_DIR must name an existing scratch directory", file=sys.stderr)
        return 2

    python = sys.executable
    run(
        python, "-m", "sightline.batch", "run", "--bundle", BUNDLE, "--pack", PACK,
        "--endpoint", args.endpoint, "--model", args.model,
    )  # fmt: skip

    from sightline.batch.gate import ExportGate
    from sightline.config import get_settings
    from sightline.report.pdf import render_environment
    from sightline.store import RunStore

    settings = get_settings()
    paths = settings.paths
    store = RunStore.open(paths)
    try:
        runs = store.list_runs(limit=5)
        if len(runs) != 1 or len(runs[0].courses) != 1:
            print("expected exactly one run of one course in the scratch folder", file=sys.stderr)
            return 1
        course = runs[0].courses[0]
        course_id, run_id = course.id, course.run_id
    finally:
        store.dispose()

    run(python, "-m", "sightline.batch", "open-review", course_id)
    run(python, "-m", "sightline.report", "export", course_id)

    store = RunStore.open(paths)
    try:
        opened = ExportGate(store, paths).require_open(course_id)
    finally:
        store.dispose()

    args.out.mkdir(parents=True, exist_ok=True)
    root = paths.root
    shutil.copyfile(root / "runs" / run_id / "courses" / course_id / "review.json",
                    args.out / "review.json")  # fmt: skip
    for name in ("report.pdf", "review.xlsx"):
        shutil.copyfile(root / "reports" / course_id / name, args.out / name)
    record = {
        "opened_at": opened.isoformat(),
        "environment": render_environment(),
        "model": args.model,
    }
    (args.out / "render.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(f"sample written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
