# Examples

Committed, synthetic, and safe to publish. Nothing here came from a live Canvas instance.

- `sample-course/` — a hand-built course in Sightline's normalized document model, small enough to
  read, with deliberate defects so every detector has something to find. Used by the test suite and
  by anyone who wants to try the tool without a Canvas token.
- `sample-report/` — what Sightline produces from `sample-course/`: the XLSX workbook and the PDF
  course report.

Real course exports never live here. They go under `SIGHTLINE_DATA_DIR` (default `./data`), which
is gitignored in full.
