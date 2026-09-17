# Sightline — roadmap

Canonical for item status. `docs/SPEC.md` is canonical for design.

Labels: **READY** · **NEEDS DISCUSSION** · **NEEDS MEASUREMENT** · **BLOCKED**.
Nothing unready enters the queue. One item per conversation, one PR per item.

Queue lives at `~/docker/overnight-sightline/`. Next free item number: **27**.

---

## Status

| # | Item | Tier | Label | Status |
|---|---|---|---|---|
| 01 | Repo skeleton, config, run store, **container** | 0 | READY | **DONE** — PR #1 |
| 02 | Canvas connector and connection test | 0 | READY | **DONE** — PR #2 |
| 03 | Acquisition and normalization | 0 | READY | **DONE** — PR #3 |
| 04 | Rubric pack format + capability tags | 0 | READY | queued 05a |
| 05 | Ship the two rubric packs | 0 | READY | queued 05b |
| 06 | Detector framework + accessibility cluster | 0 | READY | **DONE** — PR #7 |
| 07 | Canvas structure detectors D01–D20 | 1 | READY | queued 06 |
| 08 | Mobile detectors + optional-section flag | 1 | READY | queued 07 |
| 09 | Ollama client, capability filter, model picker | 1 | READY | queued 08 |
| 10 | Extraction layer with verified quotes | 1 | READY | queued 09 |
| 11 | Three-run disagreement router | 1 | READY | queued 10 |
| 12a | Effort model — ship it | 1 | READY | queued 11a |
| 12b | Effort model — fit the costs | 1 | **BLOCKED** — items 06/07/08 | — |
| 13 | XLSX writer | 2 | READY | staged 13 |
| 14 | PDF course report | 2 | READY | staged 14 |
| 15 | Narrative generator and voice contract | 2 | READY | staged 12 |
| 16 | Review UI | 2 | READY | staged 15 |
| 17 | Batch runner | 2 | READY | queued 11 |
| 18 | Escalation adapter | 3 | **BLOCKED** — ChatGPT Edu API access | — |
| 19 | Uploaded-rubric mapping screen | 3 | **SUPERSEDED by 22** | — |
| 20 | Multi-course comparison view | 3 | **DECIDED — not building it** | see 25 |
| 21 | Detector integrity guard | 0 | READY | queued 05 |
| 22 | Model-proposed bindings, human-confirmed | 3 | READY | blocked by 09/10 in code |
| 23 | Canvas-requested content export | 1 | READY | **DONE** — PR #6 |
| 24 | Agreement and near-boundary transparency | 2 | READY | staged 16 |
| 25 | Returned-review ingestion | 3 | **BLOCKED** — needs reviews delivered and returned | — |
| 26 | Wire document bytes from the export to the detectors | 1 | READY | queued 99 |

---

## Queued order

**Batch 1 — foundation. DONE, reviewed, and merged.**

```
01-sightline-item01-skeleton.md            # DONE — PR #1
02-sightline-item02-canvas-connector.md    # DONE — PR #2
03-sightline-item03-acquisition.md         # DONE — PR #3
```

These three establish every convention the rest inherit — config, data-root containment, the
container, the document model, CI. They were reviewed by a human before batch 2 ran, because eight
unsupervised sessions building on a bad session 1 compounds. **The review passed on 2026-09-08:**
data-root containment holds (39 tests, behavioural *and* AST-structural), the secrets CI job was
fired deliberately on a drill branch and was the only job that went red, and every documented
command runs verbatim (490 tests, clean lint and format, container healthy).

**Batch 2 — in `queue/`, released 2026-09-08.**

```
03a-sightline-item23-content-export-request.md  # DONE — PR #6
04-sightline-item06-detector-framework.md       # DONE — PR #7
05-sightline-item21-detector-integrity-guard.md
05a-sightline-item04-rubric-pack-format.md       # clears item 08's stop; unblocks 05 and 13
05b-sightline-item05-rubric-packs.md             # the two packs, straight after the format
06-sightline-item07-canvas-structure-detectors.md
07-sightline-item08-mobile-detectors.md          # pack flag needs item 04
08-sightline-item09-ollama-client.md
09-sightline-item10-extraction-layer.md
10-sightline-item11-disagreement-router.md
11-sightline-item17-batch-runner.md
11a-sightline-item12a-effort-model.md            # LAST in batch 2, before anything renders a band
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
13-sightline-item13-xlsx-writer.md       # the exhaustive artifact the PDF defers to
14-sightline-item14-pdf-report.md
15-sightline-item16-review-ui.md
16-sightline-item24-agreement-and-uncertainty.md   # LAST — it annotates the other three
```

Item 15 leads the batch because the workbook, the PDF and the review screen all render sentences,
and a voice contract written after two of them have shipped is a rewrite rather than a contract.
The XLSX writer sits at `13-`, between the voice contract it depends on and the PDF that defers to
it. Item 16 consumes the voice layer and satisfies the export gate item 14 builds. **Item 24 is
last on purpose**: it annotates the PDF and the review screen that items 14 and 16 build, and it
surfaces the near-boundary marker item 12a computes but deliberately leaves without a UI.

**Item 12a closes batch 2 rather than opening batch 3**, because every artifact in batch 3 renders
a band: the workbook, the PDF and the review screen all read the effort model, and shipping any of
them against a model that does not exist yet would mean three sessions each inventing one.

---

## Not queued, and why

**05 and 13 are now queued** — see *Queued order* above. Both were held as "blocked by 04 in code
only", which was never a reason to leave them uncomposed: the queue's own ordering satisfies a
code dependency. `05b-` follows the format it consumes; `13-` follows the voice contract it renders
through.

**12 · MEASURED 2026-09-08, AND SPLIT.** The item asked for per-defect costs and band thresholds
"calibrated against reviews already completed by hand". Reading the source workbooks answered half
of it and disproved the premise of the other half.

**The bands were never a guess and need no calibration.** They are printed on the instrument as
column headers — `Instructional Designer` row 3 carries the rating names and row 4 the ranges
directly beneath them: `T4` "1/2 hour or less", `U4` "1/2-2 hours", `V4` "2+ hours", under Minor,
Moderate and Major. Sufficiently Present has no band at all, because it is "no qualifying defects
found" rather than zero hours. The RSI instrument prints the same three ranges on its rows 6–7, a
second independent corroboration, and `timeToFix`'s point estimates fall inside them exactly. §07's
table is the instrument's own published scale.

**The spec is wrong about `timeToFix` and the correction is in the log below.** It is
`CONFIG!$H$15:$K$19` and it holds RATING → HOURS, the inverse of what Sightline computes. Nothing
in either workbook prices a defect.

- **12a — ship the effort model · READY, queued `11a-`.** Bands from the instrument, per-defect
  costs as an explicit, labelled, trivially-overridable placeholder exactly as §07 anticipates,
  one global sum-then-band roll-up rule, and the override capture that makes 12b possible. It also
  computes a **near-boundary marker** — with a wider margin at the Sufficiently Present / Minor
  boundary, because that is where humans actually disagree — and deliberately ships no UI for it.
  Item 24 surfaces it.
- **12b — fit the costs · BLOCKED on items 06/07/08.** A calibration corpus of real completed
  reviews exists outside the repository. It records outcomes, not inputs: no file states how many
  defects a rating was based on, and none records time actually spent. So the costs cannot be read
  off it. What it supplies is a *target* — fit the table so computed bands reproduce the human
  ratings on the same courses — which needs the detectors to exist and those courses to still be
  acquirable. It is not blocked on months of usage, and it is not a measurement question.

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

**20 — multi-course comparison view · DECIDED 2026-09-08. NOT BEING BUILT.** The question was
whether a batch produces anything beyond *n* independent reports. Asked plainly — what would a
designer *do* with the comparison — it has no answer: a cross-course view offers no verification
and builds no knowledge the individual reports do not already carry, and a feature with no use is
not made useful by being cheap to add. Ranking courses against each other is also the closest this
product can get to the aggregate quality score §11 forbids, which is a second reason to decline it.

The condition under which it comes back is specific and worth recording: **if the app can learn
something from comparing courses.** That is a calibration argument, not a UI one, and it is item
12b's territory rather than a screen's. Do not re-open this as a view.

**The real missing signal is what happens to a review AFTER it is handed over** — which is item 25.

**24 — agreement and near-boundary transparency · NEW, READY, staged `16-`.** Ship, in the product,
the measured fact that qualified human reviewers rating the same course independently agree between
72% and 91% of the time, that every disagreement observed was exactly one band, and that 29 of 34
were Sufficiently Present versus Minor Revision. Stated with its sample size and its limits, in the
PDF's *what this document is* block, on the review screen, and in the docs — not in the workbook,
which has no honest place for prose.

**The framing is the whole item.** These numbers describe **humans agreeing with each other**, and
therefore the difficulty of the task. They say nothing about how often Sightline is right, and any
sentence implying otherwise is a fabrication rather than a wording problem. §01 holds that
verbalized self-confidence is worse than useless; a measured statement about the task is the
opposite of that, and it is the most disarming true thing the product can say. The item's guard is
a test that plants the forbidden constructions — "Sightline agrees with reviewers 91% of the time",
"accuracy", "confidence", "reliability score" — and proves the check fires on them.

Its second half gives item 12a's near-boundary marker a home in the PDF and the review screen. It
is a computed distance from a threshold, never a percentage, a star, a colour ramp or the word
"unsure".

**25 — returned-review ingestion · NEW, BLOCKED on reviews being delivered and coming back.**
Item 16 captures the overrides a designer makes *inside* Sightline, and §09 already calls that
record the calibration data. It is not the whole signal. **What a human changes after the report
leaves the app** — the edits made to a workbook or an action plan on its way to faculty — is the
correction Sightline never sees, and it is a better calibration target than an in-app override
because it reflects what the reviewer was willing to put their name to.

Blocked on nothing technical: it needs real reviews to have been produced and returned. When that
starts, the returned files land outside the repository under `private/` and this item builds the
diff — original machine proposal against the delivered artifact against what came back — with the
same discipline the calibration corpus already carries: **aggregate figures only ever leave that
directory**, and no course, person or institution is ever named in anything committed. It feeds
12b and it does not block anything queued.

**23 — Canvas-requested content export · DONE 2026-09-17, PR #6.** Decided 2026-09-08; the
decisions below stood and were built as written. Item 03 raised this and deliberately declined to
settle it, which was right: it is a product call, not an item's.

Three things the build found that the decision did not anticipate, all recorded here because they
are the kind of thing the next item inherits:

*The export listing had to leave the cache signature.* Sightline's own export appears in the
course's ``content_exports`` listing, that listing feeds the cache signature, and a signature that
moves because Sightline ran is a cache that can never hit — which would mean a **new export in the
course on every single run, for ever**. The fresh path therefore does not list the course's exports
at all, which it can afford because it never reads one it did not ask for. With the request off the
listing is read exactly as item 03 read it, and a test asserts both halves.

*Redaction could not reach the host that serves the bytes.* Canvas hands out a download link on its
own host and redirects it to a storage back end that differs per deployment, which nothing
configures and therefore nothing registers as a secret — and `httpx` logs every request URL by
name. Registration cannot fix that, so `sightline.redaction` now scrubs the **origin of any
absolute URL** as a pattern, keeping the path. The download `verifier` is scrubbed as a
query-string credential alongside it.

*The Canvas permission behind "Export Course Content" is not named anywhere in this repository,*
deliberately. The remedy for a refused export points at the button a person can see in Canvas
rather than asserting a permission constant that could not be verified without an instance to check
it against. Worth confirming against a real Canvas and then naming.

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

**26 · FILED 2026-09-17 by item 06's session.** `ExportFileSource` exists and the detectors that
read document bytes take one, but **nothing can build one**: `CourseAcquisition.acquire` returns
`AcquisitionResult(document, signature, from_cache, entry)` and the `UnpackedExport` is a local
inside the method, so the unpacked export's directory is unreachable from outside. Two consequences
and the second is the quiet one. D30 and D28's document half can never fire on a real course — they
report "the bytes were not acquired" for every attachment even when an export was downloaded
successfully. And `CourseFile.local_path`'s docstring says it is *relative to the data root*, while
`acquire/normalize.py` sets it to the export-relative href (`web_resources/…`); the two disagree
and whoever wires this first will resolve against the wrong root. Filed sorting **after** every
pending item rather than jumping ahead: nothing calls stage 3 in a pipeline yet, so nothing is
producing wrong output today, and item 21 is sequenced immediately after item 06 on purpose.

**Item 06 — what later items inherit.**

- **`sightline.detect` is the detector contract.** A detector declares `id`, a plain-language
  `title`, a `tier` and the acquisition `collections` it reads, and implements one method:
  `examine(survey)`. `Detector.run` is **final** — a subclass that overrides it raises `TypeError`
  at class-definition time, because the examined count is taken there. Defining a subclass
  registers it; `registry()` is the list and there is no second list to forget.
- **A detector never touches the document.** Everything it can reach comes from the `Survey` it is
  handed, and the survey counts every artifact it gives out. That is what makes the under-detection
  invariant structural rather than a rule each new detector has to remember.
- **Zero examined resolves three ways and none of them is a pass.** A collection acquisition could
  not read is reported with its remedy; every declared collection recorded `empty` is a course that
  genuinely has none; a collection recorded `ok` paired with a detector that opened none of it
  explains nothing and raises `NothingExamined`. `DetectorResult.clean` is true only when a
  detector ran, found nothing, and had nothing left it could not read. **Item 21 fires this
  deliberately** — `registry_snapshot()` exists so a test can define a sabotaged detector without
  leaving it in the registry.
- **`Undetermined` is a first-class record beside the findings.** A PDF whose bytes were never
  downloaded is not an accessible PDF and is not an inaccessible one. Reporting it as either is a
  fabrication and reporting it as silence is the failure in SPEC §11.
- **Detectors do not name criteria, per the item 04 decision.** The item 06 prompt listed a
  criterion mapping as a finding field; the later decision recorded in this file's corrections log
  supersedes it, and SPEC §04 agrees — rubric packs project findings onto rows. `Finding` carries
  `detector`, `code`, `severity`, `tier`, `location`, `observed`, `evidence`, `examined` and a
  free-form `context`, and `tests/test_detect_base.py` fails the build if a detector's title ever
  names a category code. **Item 04 backfills `capability_tag`**, which was deliberately left to it
  rather than invented here.
- **`evidence` is verbatim and `observed` is not.** `html_scan.verbatim()` truncates only;
  `excerpt()` collapses whitespace and is for measured values. A quote that was silently tidied up
  cannot be found by searching the page, and the whole design rests on a finding being falsifiable
  in seconds.
- **PDF, DOCX and PPTX are read with the standard library** — `zipfile` plus `defusedxml`, and a
  structural probe over a PDF's decompressed streams. No new dependency. The reason that decided it
  is licensing: this repository is MIT and the PDF library these tools reach for first is AGPL.
  The probe answers two questions (is there a tag tree, is there a text layer) and reports
  *unreadable* for an encrypted file or a filter it does not implement, never *untagged*.
- **D26 and D29 are tier B and are computed by a parser.** The tier governs how a finding is worded
  and whether a rating may be pre-selected, not who computed it; a parser that quotes cannot invent
  the quote, which is a stricter form of the same contract. Item 10 may add model corroboration
  without changing the tier.
- **`examples/sample-course/defects.json` is now a live contract**, not only a marker check.
  `tests/test_detect_sample_course.py` asserts every `defect` row fires, every `clean` row stays
  quiet, and every `not_covered` row comes back with an `Undetermined` record rather than clean.
- **`python -m sightline.detect --bundle examples/sample-course` exits 1** when any detector could
  not run. A gap in the review is not a clean course, and a script reading the exit code has to be
  told so.

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
the total — one global rule, so item 12b calibrates one model rather than 68. Band thresholds are
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

---

**2026-09-08 — the spec is wrong about `timeToFix`, and item 12 splits.** §07 said `CONFIG`'s
`timeToFix` "holds an effort table worth checking first", meaning per-defect remediation costs. It
was checked. `timeToFix` is `CONFIG!$H$15:$K$19` and it holds **rating → hours** — Major 2.0/3.0,
Moderate 1.25/2.0, Minor 0.25/0.5, Sufficiently Present and N/A 0.0 — which is the **inverse** of
what Sightline computes. Nothing in either workbook prices a defect. Same class of error as the
Rev A → Rev B correction: the spec asserted something about the files that reading the files
disproves.

**The band thresholds, by contrast, were never a guess.** They are printed on the instrument.
`Instructional Designer` row 3 holds the rating names and row 4 the ranges beneath them — `T4`
"1/2 hour or less", `U4` "1/2-2 hours", `V4` "2+ hours" — and the RSI instrument prints the same
three on its rows 6–7. §07's table stands as published.

**A trap inside the same range.** `timeToFix`'s `VALUE` column encodes Major 1, Moderate 2, Minor 3,
Sufficiently Present 4 — an inverted numeric scale that exists to be averaged, which is precisely
the aggregate quality score §11 forbids. It is not an effort figure and must not be imported as one.

**2026-09-08 — importer findings, measured against the source workbooks.** These belong to items 04,
05 and 13, and every one has a plausible wrong answer:

- **Criterion text is column R and there are exactly 68 rows of it**, at rows 7–85. `max_row` is
  531; the ~460 rows below are template filler carrying a counter in column Q and nothing in R. An
  importer keying on "column Q has a value" imports 468 rows.
- **The composite code is assembled from two columns** — P is the category, Q the within-category
  index — and **column Q mixes float and int inside the same sheet** (62 float, 6 int on
  `Instructional Designer` alone), so `COAL 1.0` and `COAL 1` both occur. **Column F is the stable
  join key** (`1a`, `2b`, … `M-A`). Column G is the OPEN SUNY number and holds a different scheme
  again.
- **Category counts sum to 68**: COAL 7, GSOV 12, OREP 11, ACD 11, ENIN 12, ASFE 6, MOB 9.
- **The MOB block is numbered differently and carries no priority flag.** Rows 77–85 run `M-A`
  through `M-I` with `M` as the Number and `INT` rather than `UCCS` as the origin flag in column O,
  and column H is empty for all nine. §07's "ordering must tolerate a null" is about exactly these
  rows; an importer requiring a priority drops the whole mobile section.
- **Columns AB/AC default every row to `NR` / `No Response`** — abstention is native to the
  instrument, not something Sightline imposes.

**2026-09-08 — the RSI workbook does not open the obvious way.**
`openpyxl.load_workbook("rubrics/source/UCCS OSCQR RSI Review.xlsm", data_only=True)` raises
`TypeError: expected <class 'int'>` from `openpyxl/worksheet/merge.py`. The file is not corrupt: it
carries 19 merged-cell records, five of them **whole-row references with no column letters** —
`ref="8:8"`, `"13:13"`, `"18:18"`, `"22:22"`, `"31:31"`. Excel accepts those; openpyxl's `CellRange`
demands integer columns. **`read_only=True` loads it cleanly**, because read-only mode never binds
merged cells. It is also a concrete illustration of §08's requirement that Sightline's own workbook
survive a CSV round-trip "which the current instrument does not".

The RSI file is structurally unlike the 3.1.3 file, which is why item 05 ships both: one sheet
(`Course Review`), 18 criteria numbered 1–18 on rows 10–42 with the text in column B, **categories
as section rows rather than a column**, interleaved instruction rows that are long text but not
criteria, no composite code, no priority flag, no origin flag — and `max_row` reporting 1048561.
§03's claim that its criterion 8 is ACD 5 verbatim is confirmed.

**2026-09-08 — item 20 declined, item 25 filed in its place.** A multi-course comparison view was
labelled NEEDS DISCUSSION. It is not being built: it offers a designer no verification and no
knowledge the individual reports do not already carry, and ranking courses against one another is
the nearest thing to the aggregate quality score §11 forbids. The condition for revisiting it is
that the *app* learn something from the comparison, which is item 12b's territory rather than a
screen's. Recorded so it is not rediscovered as a feature idea.

What the discussion did surface is a signal the roadmap had no item for: **the changes made to a
review after it is handed to the user**. In-app overrides (item 16, §09) capture only what a
designer changes before the artifact leaves. Item 25 covers what comes back.

**2026-09-08 — item 24 filed.** The calibration corpus produced a number worth shipping rather than
filing: independent human passes over the same course agree 72–91% of the time, every disagreement
observed was exactly one band, and 29 of 34 were Sufficiently Present versus Minor Revision. That
is a fact about the difficulty of the task, and stating it supports §01 more directly than any
amount of hedging. It also sets the design constraint item 12a carries: **be least assertive at the
Sufficiently Present / Minor boundary** — the boundary with the smallest computed effort, where a
naive implementation is most confident and where humans are least consistent.
