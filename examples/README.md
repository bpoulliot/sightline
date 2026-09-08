# Examples

Committed, synthetic, and safe to publish. Nothing here came from a live Canvas instance.

- `sample-course/` — a hand-built course in Sightline's normalized document model, small enough to
  read, with deliberate defects so every detector has something to find. It is the test suite's
  fixture and it is what a new user runs without a Canvas token. See `sample-course/README.md` for
  what is planted in it and why four detectors are marked as not yet covered.
- `sample-report/` — what Sightline produces from `sample-course/`: the XLSX workbook and the PDF
  course report. Arrives with the items that build those writers.

```sh
python -m sightline.acquire --bundle examples/sample-course
```

Real course exports never live here. They go under `SIGHTLINE_DATA_DIR` (default `./data`), which
is gitignored in full, and the write gate in `sightline.paths` is what makes that structural rather
than a convention — no module but `sightline/paths.py` may open a file for writing, so there is no
code path that could put one here.
