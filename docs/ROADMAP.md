# Sightline — roadmap

Canonical for item status. `docs/SPEC.md` is canonical for design.

Labels: **READY** · **NEEDS DISCUSSION** · **NEEDS MEASUREMENT** · **BLOCKED**.
Nothing unready enters the queue. One item per conversation, one PR per item.

Queue lives at `~/docker/overnight-sightline/`. Next free item number: **24**.

---

## Status

| # | Item | Tier | Label | Status |
|---|---|---|---|---|
| 01 | Repo skeleton, config, run store, **container** | 0 | READY | **DONE** — PR #1 |
| 02 | Canvas connector and connection test | 0 | READY | **DONE** — PR #2 |
| 03 | Acquisition and normalization | 0 | READY | **DONE** — PR #3 |
| 04 | Rubric pack format + capability tags | 0 | READY | staged 05a |
| 05 | Ship the two rubric packs | 0 | blocked by 04 **in code only** | — |
| 06 | Detector framework + accessibility cluster | 0 | READY | queued 04 |
| 07 | Canvas structure detectors D01–D20 | 1 | READY | queued 06 |
| 08 | Mobile detectors + optional-section flag | 1 | READY | queued 07 |
| 09 | Ollama client, capability filter, model picker | 1 | READY | queued 08 |
| 10 | Extraction layer with verified quotes | 1 | READY | queued 09 |
| 11 | Three-run disagreement router | 1 | READY | queued 10 |
| 12 | Effort model calibration | 1 | **NEEDS MEASUREMENT** | — |
| 13 | XLSX writer | 2 | blocked by 04 **in code only** | — |
| 14 | PDF course report | 2 | READY | staged 14 |
| 15 | Narrative generator and voice contract | 2 | READY | staged 12 |
| 16 | Review UI | 2 | READY | staged 15 |
| 17 | Batch runner | 2 | READY | queued 11 |
| 18 | Escalation adapter | 3 | **BLOCKED** — ChatGPT Edu API access | — |
| 19 | Uploaded-rubric mapping screen | 3 | **SUPERSEDED by 22** | — |
| 20 | Multi-course comparison view | 3 | **NEEDS DISCUSSION** | — |
| 21 | Detector integrity guard | 0 | READY | queued 05 |
| 22 | Model-proposed bindings, human-confirmed | 3 | READY | blocked by 09/10 in code |
| 23 | Canvas-requested content export | 1 | READY | staged 03a |

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
03a-sightline-item23-content-export-request.md  # before 06 — D30/D36 need document bytes
04-sightline-item06-detector-framework.md
05-sightline-item21-detector-integrity-guard.md
05a-sightline-item04-rubric-pack-format.md       # clears item 08's stop; unblocks 05 and 13
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
not** — its prompt tells the session to stop rather than invent a pack format. Item 04 is queued at
**05a**, which sorts after the detector framework that defines the typed finding shape it projects,
and before the structure and mobile detectors. That ordering clears item 08's stop. Item 04 also
adds a test asserting every registered detector declares a capability tag, which is what forces
items 07 and 08 to comply without their prompts needing to know about tags.

**Batch 3 — output and UI. Composed in `staged/`, decided 2026-09-08.**

```
12-sightline-item15-voice-contract.md    # FIRST — 13, 14 and 16 all render text
13-  ...  RESERVED for item 13, the XLSX writer
14-sightline-item14-pdf-report.md
15-sightline-item16-review-ui.md
```

Item 15 leads the batch because the workbook, the PDF and the review screen all render sentences,
and a voice contract written after two of them have shipped is a rewrite rather than a contract.
**`13-` is deliberately left free** so the XLSX writer sorts between the voice contract it depends
on and the PDF that defers to it; item 05 should likewise take an `05b-` name so the packs land
immediately after the format. Item 16 is last: it consumes the voice layer and satisfies the export
gate item 14 builds.

---

## Not queued, and why

**05 — ship the two rubric packs · blocked by 04 in code only.** The format is decided; this needs
the schema and importer to exist before it can run. Queue it once 05a lands.

**13 — XLSX writer · blocked by 04 in code only.** Same: the decision no longer blocks it, the code
does.

**12 — effort model calibration · NEEDS MEASUREMENT.** Per-defect remediation costs and band
thresholds, calibrated against completed reviews. `CONFIG`'s `timeToFix` holds an effort table worth
checking first, cross-referenced against the `Instructional Designer` sheet. Measurement questions
are answered from data, never asked of the user.

**14 / 15 / 16 — output and UI · READY, decided 2026-09-08 as one cluster.** They were decided
together because they are three answers to one question — how much evidence to show, and in what
register — and deciding them apart reliably produces three inconsistent answers.

**15 — the narrative is assembled from computed facts; the model does not write prose.** No shipped
artifact contains a sentence a language model composed freely. §01 requires every machine-authored
line to be a computed count, a verbatim quote with a location, or an explicit statement that no
evidence was found, and a written paragraph is none of those — while the closing narrative is the
most quotable part of the report, the part that gets pasted into an email. §08 had already settled
phrase-bank rotation for everything else, so exempting the narrative would have given the document
two voices. This does **not** mean dropping quotes: extraction returns verbatim, string-matched
passages, which are model-*located*, never model-*written*. The item's real deliverable is the
**register lint** — hedge tokens rejected in Tier A, verdict tokens rejected in Tier C, grading
vocabulary and aggregate-score phrasing rejected everywhere — and it must be **proven to fire** on
planted bad phrases, the discipline item 21 applies to the detector guard.

**14 — the PDF is addressed to the designer and written so it can be forwarded to faculty
unchanged.** One document, one register; no separate faculty edition. That constraint is what
actually enforces the no-grading vocabulary in §11 — a document written for an internal audience
drifts toward evaluative shorthand, and then somebody forwards it anyway. Structure is
**action-plan led with evidence tiered**: summary, prioritized action plan, ACD and ENIN in full
with quotes, then one compact line per remaining criterion, then the narrative. The workbook stays
the exhaustive artifact and the PDF says so. §02's presentation constraint is explicit for the PDF:
Tier A and Tier C never share a visual channel. The first page carries a short *what this document
is* block — a review pass, ratings denominated in remediation effort rather than quality, Tier C
items are questions, no aggregate score anywhere — the cheapest guard available against the
adoption risk.

**16 — the review screen groups by rubric category, in the instrument's own order, with severity
as a sort and filter within it.** The screen's job is confirming a rubric and its output is
category-ordered, so the designer walks the instrument they already know; triage already has a home
in the action plan. Grouping by band and grouping by tier were both considered and rejected — the
tier option is the tempting one, since §02 does demand Tier A and Tier C be visually separate, but
that is satisfied by distinct treatment *within* a category rather than by tearing the rubric apart.

**One thread runs through all three, and it is the same failure §11 names.** Item 04 kept
`NOT_YET_IMPLEMENTED`, `NO_DETERMINISTIC_COVERAGE` and `SUFFICIENTLY_PRESENT` apart in the data
model. Each of these three items is a fresh chance to collapse them at the last moment before a
human reads the result — in a sentence, in a table cell, in a table row. All three prompts carry
that trap explicitly and all three acceptance lists test for it.

**18 — escalation adapter · BLOCKED.** Pending confirmation of ChatGPT Edu API access. The app
functions fully without it.

**19 · SUPERSEDED by 22.** Its premise — that an uploaded rubric needs a mapping screen before it
is usable — is dead. Under item 04 an unbound rubric runs immediately, extraction-only. What remains
worth building is item 22.

**22 — model-proposed bindings, human-confirmed · READY, blocked by 09/10 in code.** Give the model
the detector catalogue and the uploaded criterion text; it proposes tag bindings a human confirms
**once per rubric, never per course**. An accelerator, not a prerequisite. Needs the model layer
(09) and extraction (10) to exist before it can be queued.

**No confidence signal on a proposal — decided 2026-09-08, do not reintroduce.** Proposals are
shown as proposals and every one is freely changeable. A confidence number would have become a
de facto bulk-accept threshold, and §01 holds that verbalized self-confidence is worse than
useless. Without a confidence ranking there is nothing to rubber-stamp selectively: the proposal
set is accepted as a legible unit or edited.

**Bulk acceptance IS provided: checkboxes, check-all, accept-checked.** §09's no-bulk-accept rule
covers **Tier C ratings** and does not transfer here — the stakes differ by an order of magnitude.
A wrong rating is a claim about someone's work; a wrong binding only means a row gets evidence-only
treatment instead of deterministic, once per pack, visible in the coverage report and reversible.
Fifty individual confirmations buys no safety and is bad UX. **What matters is that nothing happens
by inertia**: boxes default to UNCHECKED so acceptance is an affirmative act, not that the act be
tedious. **The assurance belongs in the artifact, not the click** — after accepting, show the
coverage delta ("18 rows moved from evidence-only to deterministic, 25 remain evidence-only"),
which is a checkable artifact that is still true when someone revisits the pack later.

**The screen owes the user three things, and this is the acceptance bar:** *what* they are
selecting (the criterion text beside the tag and the plain-language description of what that
detector finds), *why* it was proposed, and *how it changes the review* — binding a row moves it
from evidence-only Tier B to deterministic Tier A arithmetic, and unbinding moves it back. Showing
the consequence of a control rather than just its label is the established pattern in this
codebase's sibling projects. Confirmation lives on the rubric import/selection path, shown once
per pack, never in the per-run flow.

**20 · NEEDS DISCUSSION.** Whether a batch produces anything beyond n independent reports.

**23 — Canvas-requested content export · READY, decided 2026-09-08.** Item 03 raised this and
deliberately declined to settle it, which was right: it is a product call, not an item's.

**Sightline requests the export.** It `POST`s to `/api/v1/courses/:id/content_exports`, polls until
the export finishes, and downloads it. That is the normal acquisition path, not an optional extra —
without it the detectors that need a document's own bytes (D02, D30, D36) are dead on arrival for
any course whose teacher has not exported it by hand, and asking a designer to do that for every
course they review is the kind of friction that decides whether a tool gets used.

**Framing worth stating precisely, because the repo currently states something stronger.** This is
not a change to course *content*. Nothing a learner or an instructor sees is altered; an export
artifact appears in the course's export list. The README's "nothing in Sightline writes to Canvas"
becomes "Sightline does not modify course content; it asks Canvas to package a copy of the course
it is reviewing" — a narrower claim, and one that stays true.

**A setting turns it off, and off is a supported configuration**, falling back to item 03's
behaviour: read an export the course already has, and report the steps when there is none. An
institution with a no-writes policy keeps a working tool.

**The scope list stays ONE tuple.** `test_the_scopes_are_all_read_only` inverts rather than
disappearing: it asserts there is **exactly one** non-`GET` scope and that it is the content-export
`POST`. A blanket ban that gets deleted teaches nothing; a count that must stay at one keeps the
next write honest.

**Always request a fresh export; never reuse an existing one.** Item 03 established that Canvas
does not bump a course's `updated_at` when a page inside it is edited, so there is no cheap way to
tell whether an existing export is stale — and a review of yesterday's course reported as today's
is exactly the silent wrongness this project is built to avoid.

**Built on the existing `httpx` client, not `canvasapi`.** Item 02's client carries knowledge the
package does not: `401` discriminated by `WWW-Authenticate`, `403`-as-rate-limit detected in the
body, `404`'s ambiguity between missing and invisible. It is also what base-URL redaction is
registered against and what the recorded-fixture transport intercepts. `canvasapi` uses `requests`,
so adopting it would bypass both. Adding a `POST` and a poll here is small; re-earning those
properties is not.

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

**2026-09-08 — item 04 resolved, then re-scoped after a challenge to its premise.** The item was
NEEDS DISCUSSION over how much uploaded-rubric mapping could be automatic. Reading both workbooks
reframed it, and a second round corrected the reframing.

Findings from the files:

- **Importing rows and binding detectors are different problems.** Criterion rows are mechanical to
  import. **Neither workbook has a column naming a detector**, so no import can produce bindings.
- **The 3.1.3 crosswalk is a UCCS↔OPEN SUNY mapping between OSCQR variants**, not a binding source.
  **The RSI workbook has no crosswalk columns at all** — seven columns, index and text.
- **The projection is many-to-many in both directions.** D01 fans out to GSOV 7–10 while GSOV 5
  receives from both D04 and D10.

The first-round answer — import to a skeleton, hand-author every binding — **was rejected as
encoding a single use case.** It made "upload Quality Matters" mean "write forty YAML bindings
first", which is a roadblock an open-source tool cannot justify, and the reasoning that rejected an
extraction fallback was wrong: Tier B already exists precisely to keep model-sourced evidence
distinct from Tier A arithmetic, so an extraction-only row breaks no rule in §01. The licensing
reality sharpens it — Sightline can never *ship* a QM pack, so third-party rubric support is the
only way those users exist.

**Decisions as settled.** **(1)** Bindings live in the pack and bind to **capability tags**, not
detector IDs — a detector declares a tag plus a description and never names a criterion, so a pack
is an artifact about course-quality concepts, portable across detector refactors and free of
Sightline's numbering. **(2)** **An unbound row is normal, not an error**: binding is an
optimization, and a rubric with zero bindings must load, run, and produce a usable review with its
rows routed to extraction. **(3)** A criterion fed by several detectors sums their effort and bands
the total — one global rule, so item 12 calibrates one model rather than 68. Band thresholds are
pack data; per-defect costs are global config.

**Sightline reviews the course; the rubric is only the lens.** No design step may require a rubric
to be an OSCQR derivative or be crosswalked to 3.1.3.

**Three states, and conflating them is the failure item 04 exists to prevent:**
`NOT_YET_IMPLEMENTED` (tag exists, no detector yet — transient, a development-phase artifact),
`NO_DETERMINISTIC_COVERAGE` (no binding — permanent, normal, routes to extraction; a fresh QM
import is entirely this and that is a success case), and `SUFFICIENTLY_PRESENT` (a detector ran and
found nothing — **the only one meaning clean**). Packs also report their own coverage, so a gap is
visible information rather than silent degradation.

**2026-09-08 — items 14, 15 and 16 resolved as one cluster.** Taken together rather than
separately: all three answer the same question — how much evidence to show and in what register —
and three separate sessions would have produced three inconsistent answers to it.

The decisions are recorded in *Not queued, and why* above. Two are worth restating as corrections
to how the spec reads on its own:

- **§08's "the closing narrative is honest and short" is not licence for generated prose.** The
  narrative is assembled from computed facts through the same phrase bank as every other sentence.
  §08 already prescribed a phrase bank with rotation for finding-level text; the narrative was never
  meant to be the exception, and reading it as one would have put the least checkable paragraph in
  the most quoted position.
- **§09's open question "category or severity" had a false symmetry.** The two groupings serve
  different tasks, and the product already has a home for the severity task — the prioritized
  action plan. The review screen only ever had one defensible answer.

**Ordering consequence for the queue.** Item 15 must precede items 13, 14 and 16, because all three
render text and a voice contract written after them is a rewrite. Batch 3 is numbered to leave
`13-` free for the XLSX writer.

**2026-09-08 — item 23, Canvas-requested content export.** Item 03 left this open as an explicit
product question and it is now decided: Sightline requests the export. The decision is written up
in *Not queued, and why* above.

It **reverses a claim the code currently makes in three places** — `scopes.py`'s module docstring,
the README's "every one is a `GET`" paragraph, and `test_the_scopes_are_all_read_only`. That those
three had to be found and changed together is the guard working as designed rather than an
oversight, and the test inverts rather than disappearing: exactly one non-`GET` scope, and it must
be the content-export `POST`.

The claim itself is narrowed rather than dropped. "Nothing in Sightline writes to Canvas" becomes
"Sightline does not modify course content; it asks Canvas to package a copy of the course it is
reviewing." Nothing a learner or instructor sees changes; an artifact appears in the export list.
