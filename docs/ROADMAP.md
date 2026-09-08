# Sightline — roadmap

Canonical for item status. `docs/SPEC.md` is canonical for design.

Labels: **READY** · **NEEDS DISCUSSION** · **NEEDS MEASUREMENT** · **BLOCKED**.
Nothing unready enters the queue. One item per conversation, one PR per item.

Queue lives at `~/docker/overnight-sightline/`. Next free item number: **22**.

---

## Status

| # | Item | Tier | Label | Status |
|---|---|---|---|---|
| 01 | Repo skeleton, config, run store, **container** | 0 | READY | **DONE** — PR #1 |
| 02 | Canvas connector and connection test | 0 | READY | **DONE** — PR #2 |
| 03 | Acquisition and normalization | 0 | READY | **DONE** — PR #3 |
| 04 | Rubric pack format | 0 | **NEEDS DISCUSSION** | — |
| 05 | Ship the two rubric packs | 0 | blocked by 04 | — |
| 06 | Detector framework + accessibility cluster | 0 | READY | queued 04 |
| 07 | Canvas structure detectors D01–D20 | 1 | READY | queued 06 |
| 08 | Mobile detectors + optional-section flag | 1 | READY | queued 07 |
| 09 | Ollama client, capability filter, model picker | 1 | READY | queued 08 |
| 10 | Extraction layer with verified quotes | 1 | READY | queued 09 |
| 11 | Three-run disagreement router | 1 | READY | queued 10 |
| 12 | Effort model calibration | 1 | **NEEDS MEASUREMENT** | — |
| 13 | XLSX writer | 2 | blocked by 04 | — |
| 14 | PDF course report | 2 | **NEEDS DISCUSSION** | — |
| 15 | Narrative generator and voice contract | 2 | **NEEDS DISCUSSION** | — |
| 16 | Review UI | 2 | **NEEDS DISCUSSION** | — |
| 17 | Batch runner | 2 | READY | queued 11 |
| 18 | Escalation adapter | 3 | **BLOCKED** — ChatGPT Edu API access | — |
| 19 | Uploaded-rubric mapping screen | 3 | **NEEDS DISCUSSION** | — |
| 20 | Multi-course comparison view | 3 | **NEEDS DISCUSSION** | — |
| 21 | Detector integrity guard | 0 | READY | queued 05 |

---

## Queued order

**Batch 1 — foundation. In `queue/` and ready to run.**

```
01-sightline-item01-skeleton.md            # DONE — PR #1
02-sightline-item02-canvas-connector.md    # DONE — PR #2
03-sightline-item03-acquisition.md         # DONE — PR #3
```

These three establish every convention the rest inherit — config, data-root containment, the
container, the document model, CI. They are reviewed by a human before batch 2 runs, because eight
unsupervised sessions building on a bad session 1 compounds.

**Batch 2 — held in `staged/`, pending that review and the item 04 decision.**

```
04-sightline-item06-detector-framework.md
05-sightline-item21-detector-integrity-guard.md
06-sightline-item07-canvas-structure-detectors.md
07-sightline-item08-mobile-detectors.md          # pack flag needs item 04
08-sightline-item09-ollama-client.md
09-sightline-item10-extraction-layer.md
10-sightline-item11-disagreement-router.md
11-sightline-item17-batch-runner.md
```

Item 21 runs immediately after 06 by design: 06 builds the under-detection invariant into the
detector interface, and 21 proves it holds by firing it deliberately. A guard that has never failed
is indistinguishable from a guard that cannot fail.

Item 08's mobile detectors are independent of item 04, but its **optional-section pack flag is
not** — its prompt tells the session to stop rather than invent a pack format. Settling 04 before
batch 2 removes that stop and lets 05 and 13 join the batch.

---

## Not queued, and why

**04 — rubric pack format · NEEDS DISCUSSION.** The schema binding criteria to detectors, and the
XLSX/CSV importer. The open question is how much of the uploaded-rubric mapping is automatic (via
the crosswalk columns already present in the workbook) versus a guided screen. This decision shapes
items 05, 13 and 19.

**05 — ship the two rubric packs · blocked by 04.** Cannot be built before the pack format exists.
Previously mislabelled READY; corrected.

**12 — effort model calibration · NEEDS MEASUREMENT.** Per-defect remediation costs and band
thresholds, calibrated against completed reviews. `CONFIG`'s `timeToFix` holds an effort table worth
checking first, cross-referenced against the `Instructional Designer` sheet. Measurement questions
are answered from data, never asked of the user.

**13 — XLSX writer · blocked by 04.** Writes rubric rows, so it needs the pack format. Previously
mislabelled READY; corrected.

**14 / 15 / 16 — output and UI · NEEDS DISCUSSION.** PDF layout and how much evidence belongs in the
PDF versus the workbook; the voice contract's actual wording (the register table in SPEC §08 is a
starting point, not final); and whether the review screen groups by rubric category or by severity.

**18 — escalation adapter · BLOCKED.** Pending confirmation of ChatGPT Edu API access. The app
functions fully without it.

**19 / 20 · NEEDS DISCUSSION.** Uploaded-rubric mapping shape depends on 04; whether a batch
produces anything beyond n independent reports is unresolved.

**Should Sightline ask Canvas to build a content export? · NEEDS DISCUSSION, unnumbered.** Raised
by item 03 and deliberately left open rather than settled by it. Creating a content export is a
`POST /api/v1/courses/:id/content_exports`, and it would be the only write Sightline makes to
Canvas — the README states that it makes none and `tests/test_canvas_scopes.py` fails the build
over a non-`GET` scope. Item 03 kept that true by *reading* an export the course already has and
reporting, with steps, when there is none.

What it costs: a course with no existing export is reviewed from the API alone, so the detectors
that need a document's own bytes (D02, D30, D36) have nothing to read until somebody exports the
course by hand. What the write would cost: an artifact appears in the course's export list, which
is visible to the teacher — small, but it is a change to someone's course made by a tool whose
entire product framing is that it reviews rather than acts. That is a person's call, not an
item's. Whoever takes it should also decide whether the scope list stays a single read-only tuple
or grows a second, separately granted one.

**Outcomes are not acquired.** D11 (outcomes defined at course and module level) needs
`/api/v1/courses/:id/outcome_group_links`, which item 03 did not normalize — the item's list of
things to normalize does not include outcomes, and inventing an endpoint and a model type for a
detector nobody has written yet would have been guesswork. Whichever item builds D11 adds the
endpoint, the scope row and the model field together. `examples/sample-course/defects.json` records
D11 as `not_covered` for exactly this reason, so it cannot be forgotten quietly.

---

## Deployment

**Containerized, one service.** A multi-stage Dockerfile and a single-service compose file, built by
item 01. No broker, no worker container — one FastAPI process in one container is the whole design.
`SIGHTLINE_DATA_DIR` is a volume so run state and downloads survive a rebuild. `.dockerignore`
mirrors `.gitignore`, because anything copied into a layer ships with the image. CI builds the
image on every PR; a Dockerfile that is never built in CI rots within a month.

There is no production deployment and no cutover — `docker compose up` on a developer machine is the
entire story until that changes.

**The compose network subnet is pinned**, and later items should not unpin it. Sightline has to
reach a model host on the LAN, and host firewalls commonly allow container egress by source range —
typically the whole of `172.16.0.0/12`. Docker's default pool hands out `172.17`–`172.31` as /16s
and then falls back to `192.168.0.0/16`; a container that lands in the fallback is dropped by such
a firewall, and the only symptom is a connection **timeout at the first model call**. This was
observed and fixed during item 01, not theorised. Override with `SIGHTLINE_SUBNET` where the
default `/24` collides.

## Repository and disclosure

**This repository is public on Gitea. There is no GitHub remote and none is to be added.**

- The **Canvas base URL is a secret** alongside the token — it identifies the institution. Docs,
  tests and `.env.example` use `canvas.example.edu`.
- **Course exports never enter the repo.** They carry instructor-authored content, page text naming
  real people, and unpublished drafts, even though a content export has no submissions or grades.
  Everything downloaded or generated lives under `SIGHTLINE_DATA_DIR` (default `./data`),
  gitignored in full. `examples/` ships a synthetic sample course and sample report instead.
- **The rubric workbooks at `rubrics/source/` are committed on purpose** — a blank template with no
  personal data, confirmed by a scan across every sheet. Publishing them and the derived packs is
  settled; a later move toward stock OSCQR 3.1 / 4.0 is a pack change, not a code change.

## Notes carried forward

**Item 03 — what later items inherit.**

- **`sightline.model` is the contract.** `CourseDocument` holds pages, files, assignments, quizzes,
  discussions, announcements, modules, module items, embeds and the syllabus. Detectors read that
  and never a Canvas payload. `SCHEMA_VERSION` is part of the cache key, so bumping it invalidates
  every cached document rather than deserializing an old one into a new class.
- **A deep link is a path, not a URL.** `artifact.canvas_path` is `/courses/4410/pages/x`;
  `document.deep_link(artifact)` joins it to the base URL. That is what keeps the institution
  hostname out of a cached document and out of `examples/sample-course/`. Item-level, never an
  editor. `link_exact=False` marks the weaker search link a file known only from an export gets.
- **Every collection carries an `Examination`.** `ok` / `empty` / `refused` / `absent` / `skipped`,
  with the endpoint, what came back and a remedy. A detector reporting "nothing found" over a
  collection that was never readable is the silent under-detection item 21 exists to catch, and
  this record is what makes that checkable rather than a matter of care.
- **`sightline.paths` gained the write primitives**, and they are called `save_bytes` / `save_text`
  / `staged_write` rather than `write_bytes` / `write_text`. The structural guard matches on the
  called name, so a method here spelled like `pathlib`'s would have blunted it at every call site.
  It caught exactly that during item 03; the rename is the fix and the guard stays unweakened.
- **`examples/sample-course/` is the fixture for everything downstream.** Load it with
  `sightline.acquire.load_bundle`, or run `python -m sightline.acquire --bundle
  examples/sample-course`. `defects.json` names, per detector, what is planted and where — and
  `tests/test_sample_course.py` fails the build when a body drifts away from a marker the registry
  claims is in it. Adding a detector to SPEC §05 without adding a case to the sample also fails.
- **Two recorded fixtures, and the second is the important one.** `course-acquisition.json` is a
  whole course; `course-partial.json` is the same course read by a token that may not see all of
  it. Reporting a refused collection as an empty one is the bug those exist to catch.
- **A Canvas quirk worth not rediscovering:** requesting modules with `include[]=items` returns a
  *capped* item list and gives no signal that it capped it. `CourseReader` compares against
  `items_count` and re-reads the module's own endpoint. Trusting the include silently loses the
  eleventh item of every long module, which is the module worth reviewing.
- **A Python quirk worth not rediscovering:** `html.parser.HTMLParser` treats `iframe` as a CDATA
  element, so an *unclosed* `<iframe>` swallows the rest of the page and every later embed on it
  disappears with no error. `_EmbedParser` overrides `CDATA_CONTENT_ELEMENTS` to `script` and
  `style` only.

**Item 02 — what later items inherit.**

- `sightline.canvas` is the connector: `client.py` (the API client and the connection test),
  `refs.py` (URL / bare id / CSV resolution), `errors.py` (every failure carries a `remedy`),
  `scopes.py` (the declared least-privilege scope set), `sanitize.py` (the fixture scrubber).
- **The scope list is a single source.** `REQUIRED_SCOPES` is what the README table is checked
  against by `tests/test_canvas_scopes.py`. Item 03 adds the acquisition endpoints to that tuple
  and to the README table together, or the build fails — which is the point.
- **Credentials are entered in the UI and encrypted at rest.** Ciphertext in the run store's
  `app_secrets` table, key in `<data root>/secrets/credentials.key` (0600) or in
  `SIGHTLINE_SECRET_KEY`. `sightline.paths.read_or_create_key` is the only writer, because
  `tests/test_paths.py` still forbids every other module from creating a file.
- **Fixtures, not a live instance.** `tests/fixtures/canvas/*.json` replay through
  `tests/canvas_recordings.py`, which refuses an unrecorded request rather than answering it.
  `tests/test_canvas_fixtures.py` is the mechanical proof the fixtures are synthetic.
- **The one deliberate exception to "no `Bearer` in a fixture"** is the literal
  `Bearer realm="canvas-lms"` in `invalid-token.json`. It is the `WWW-Authenticate` challenge
  Canvas sends with a rejected token, it is what the client keys off to tell a bad token from a
  missing permission, and it carries no credential. The check is narrowed to "Bearer followed by
  something credential-shaped" rather than dropped.

## Corrections log

**2026-09-08 — detector-to-criterion mappings revised against full criterion text.** The first pass
mapped against criterion text truncated at ~110 characters by a CSV display. Reading the full text
from the `Instructional Designer` sheet found:

- **D32 (OER signals) was mapped to OREP 7. Correct target is OREP 8.** OREP 7 is "HTML (Pages) is
  used as the primary delivery method for text content"; OREP 8 is the OER criterion. D34 correctly
  holds OREP 7.
- **Eight criteria had no detector at all:** COAL 7, OREP 1, OREP 2, ENIN 7, ENIN 8, ENIN 10,
  ASFE 1, ASFE 3. Six now have coverage (D46, D47, E11, E12, E13, E14); COAL 7 remains human-only
  alongside MOB 5.
- **The "51 detectors covering all 68 criteria" claim in Rev A was false.** Rev B is 47 `D` + 14 `E`
  = 61 detectors, 66 criteria covered, 2 human-only.
- **MOB criteria carry no Essential/Important flag.** Action-plan ordering must tolerate a null
  priority.
