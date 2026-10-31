# Sightline — roadmap

Canonical for item status. `docs/SPEC.md` is canonical for design.

Labels: **READY** · **NEEDS DISCUSSION** · **NEEDS MEASUREMENT** · **BLOCKED**.
Nothing unready enters the queue. One item per conversation, one PR per item.

Queue lives at `~/docker/overnight/projects/sightline/` (moved from `~/docker/overnight-sightline/`
on 2026-09-22). Next free item number: **48** (41 is claimed by open PR #40; 42 and 43 were filed
by item 36's session, PR #43; 44–47 split from item 40 on 2026-10-30).

---

## Status

| # | Item | Tier | Label | Status |
|---|---|---|---|---|
| 01 | Repo skeleton, config, run store, **container** | 0 | READY | **DONE** — PR #1 |
| 02 | Canvas connector and connection test | 0 | READY | **DONE** — PR #2 |
| 03 | Acquisition and normalization | 0 | READY | **DONE** — PR #3 |
| 04 | Rubric pack format + capability tags | 0 | READY | **DONE** — PR #9 |
| 05 | Ship the two rubric packs | 0 | READY | **DONE** — PR #10 |
| 06 | Detector framework + accessibility cluster | 0 | READY | **DONE** — PR #7 |
| 07 | Canvas structure detectors D01–D20 | 1 | READY | **DONE** — PR #11 |
| 08 | Mobile detectors + optional-section flag | 1 | READY | **DONE** — PR #12 |
| 09 | Ollama client, capability filter, model picker | 1 | READY | **DONE** — PR #14 |
| 10 | Extraction layer with verified quotes | 1 | READY | **DONE** — PR #15 |
| 11 | Three-run disagreement router | 1 | READY | **DONE** — PR #17 |
| 12a | Effort model — ship it | 1 | READY | **DONE** — PR #20 |
| 12b | Effort model — fit the costs | 1 | **BLOCKED** — item 06/07/08 detectors have all landed; unblocked in code, still needs the calibration corpus | — |
| 13 | XLSX writer | 2 | READY | **DONE** — PR #28 |
| 14 | PDF course report | 2 | READY | **DONE** — PR #31 |
| 15 | Narrative generator and voice contract | 2 | READY | **DONE** — PR #27 |
| 16 | Review UI | 2 | READY — Q1 (a) and Q2 (b) decided 2026-10-29 | **DONE** — PR #42. The review screen; the Reviewer setting (Q1); exports carry a recorded rating as the reviewer's (Q2) |
| 17 | Batch runner | 2 | READY | **DONE** — PR #18 |
| 18 | Escalation adapter | 3 | **PARKED** 2026-10-29 — ChatGPT Edu API not available | — |
| 19 | Uploaded-rubric mapping screen | 3 | **SUPERSEDED by 22** | — |
| 20 | Multi-course comparison view | 3 | **DECIDED — not building it** | see 25 |
| 21 | Detector integrity guard | 0 | READY | **DONE** — PR #8 |
| 22 | Model-proposed bindings, human-confirmed | 3 | **DONE** | **DONE** — part 1 PR #32 (proposal engine + command-line review); part 2 PR #49 (the Rubrics screens: propose, confirm, revert; runs record the bindings they used) |
| 23 | Canvas-requested content export | 1 | READY | **DONE** — PR #6 |
| 24 | Agreement and near-boundary transparency | 2 | READY | **DONE** — PR #29 (data, guard, docs) and PR #45 (the statement and the marker in the PDF and on the review screen) |
| 25 | Returned-review ingestion | 3 | **BLOCKED** — needs reviews delivered and returned | — |
| 26 | Wire document bytes from the export to the detectors | 1 | READY | **DONE** — PR #21 |
| 27 | One detector's crash must not take the whole review with it | 1 | READY | **DONE** — PR #22 |
| 28 | A binding says *relevant*; rating reads it as *sufficient* | 1 | READY | **DONE** — PR #23 |
| 29 | `Scan.text` fuses two words at every block seam | 1 | READY | **DONE** — PR #13 |
| 30 | Every HTML detector re-parses every body | 2 | READY | **DONE** — PR #24 |
| 31 | `plain_text()` reads a tag with a regex, and two shapes of course HTML defeat it | 1 | READY | **DONE** — PR #25 |
| 32 | The suite reads the developer's `.env`; the fixture that promises it does not, does not | 1 | READY | **DONE** — PR #16 |
| 33 | A routed row costs one call per passage, and nothing bounds the total | 2 | MEASURED | **DONE** — PR #26. Outcome (d): nothing changes |
| 34 | Acquisition drops every outcome when it records an export note | 1 | READY | **DONE** — PR #33 |
| 35 | The workbook's action plan holds located items; SPEC §08 promises remediation steps | 2 | DECIDED (a) 2026-10-29 | **DONE** — SPEC §08 corrected in this PR |
| 36 | Run-progress and New run screens | 2 | READY — item 16, the review screen it links to, is DONE (PR #42) | **DONE** — PR #43. Start form on `/courses` with a rubric picker; `/runs` and `/runs/{id}` per course and per stage |
| 37 | Host `__pycache__` ships in locally built images, carrying the developer's home path | 1 | READY | **DONE** — PR #34. 177 → 0 files in the dev image, 94 → 0 in runtime |
| 38 | A bound Tier D row is counted in two coverage buckets, and `coverage` exits 1 calling it a bug | 1 | READY | **DONE** — PR #35. Tier first; 42 → 41 of 68, exit 1 → 0 |
| 39 | Every other bare pattern in `.dockerignore` is root-only too | 3 | READY | **DONE** — PR #41. Planted nested files 19 → 0 in the dev image, 15 → 0 in runtime |
| 40 | Sign-in (SSO) and multiple users | 3 | **DECIDED** 2026-10-30 — split into 44–47 | Nothing to build under this number; the decisions are its entry |
| 42 | A run that completed with failures records no finish time | 1 | READY — filed 2026-10-29 by item 36 | **DONE** — PR #44. Every terminal status stamps `finished_at`; `interrupted` stays unset |
| 43 | The app's restart recovery takes over a run another process is executing | 1 | **READY** — (a) an owner record, decided 2026-10-30 | **DONE** — PR #48. Runs record host, pid and a heartbeat; recovery, shutdown and Start read them |
| 44 | Signed-in identity behind a trusted proxy | 3 | READY | **DONE** — PR #50 (sign-in mode behind a proxy secret, the subject on overrides, CSRF on every form post) |
| 45 | Runs per user, an admin role, and per-user Canvas tokens | 3 | READY | **DONE** — PR #51 (a run is its starter's, 404 to anyone else; admin group alone changes instance settings; each person's own Canvas token) |
| 46 | Queue a run behind the active one | 3 | READY | **DONE** — PR #NN (a Start while a run is active is queued FIFO; *Queued, N ahead*; starter or admin cancels, recorded; survives a restart; waits behind a live command-line run) |
| 47 | Reference deployment: identity provider, proxy and app | 3 | **BLOCKED** — on 44 (DONE, PR #50); Authentik decided 2026-10-30 | — |

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
05-sightline-item21-detector-integrity-guard.md  # DONE — PR #8
05a-sightline-item04-rubric-pack-format.md       # DONE — PR #9. Item 08's stop is cleared.
05b-sightline-item05-rubric-packs.md             # DONE — PR #10. 68 + 18 rows, no new reader.
06-sightline-item07-canvas-structure-detectors.md   # DONE — PR #11
07a-sightline-item29-scan-text-block-seam.md     # DONE — PR #13. Nothing in the suite moved.
07-sightline-item08-mobile-detectors.md          # DONE — PR #12. The pack flag was already there.
08-sightline-item09-ollama-client.md              # DONE — PR #14. Three host behaviours measured.
09-sightline-item10-extraction-layer.md          # DONE — PR #15. 10 extractors, quotes verified.
09a-sightline-item32-dotenv-leaks-into-tests.md  # DONE — PR #16. Two holes, not one.
10-sightline-item11-disagreement-router.md      # DONE — PR #17. Union, not intersection.
11-sightline-item17-batch-runner.md              # DONE — PR #18. The gate landed with it.
11a-sightline-item12a-effort-model.md            # DONE — PR #20. Keyed on the detector, not severity.
```

Item 21 runs immediately after 06 by design: 06 builds the under-detection invariant into the
detector interface, and 21 proves it holds by firing it deliberately. A guard that has never failed
is indistinguishable from a guard that cannot fail.

Item 08's mobile detectors were independent of item 04, but its **optional-section pack flag was
not** — its prompt told the session to stop rather than invent a pack format. Item 04 was queued at
**05a**, which sorts after the detector framework that defines the typed finding shape it projects,
and before the structure and mobile detectors. **The ordering worked and the stop never fired**:
item 04 shipped `sections[].optional` / `sections[].enabled` and the loader that refuses to switch
off a section which has not declared itself optional, item 05 shipped the reference pack with its
mobile block flagged optional and switched on, and item 08 wrote no pack-format code at all —
it exercised the flag through the projection and showed the block appearing and being reported as
omitted. Item 04 also adds a test asserting every registered detector declares a capability tag,
which is what forced items 07 and 08 to comply without their prompts needing to know about tags.

**Batch 3 — output and UI. Composed in `staged/`, decided 2026-09-08.**

```
12-sightline-item15-voice-contract.md    # DONE — PR #27. sightline.voice; the lint is proven to fire
13-sightline-item13-xlsx-writer.md       # DONE — PR #28. sightline.workbook; CSV-clean, all text
14-sightline-item14-pdf-report.md       # SUPERSEDED 2026-09-29 — re-specified, PR #30; ran as 99i-, DONE — PR #31
15-sightline-item16-review-ui.md        # SUPERSEDED 2026-09-29 — re-specified, PR #30; ran as 99q-, DONE — PR #42
16-sightline-item24-agreement-and-uncertainty.md   # PART 1 DONE — PR #29; the rest re-runs after 14 + 16
```

Item 15 leads the batch because the workbook, the PDF and the review screen all render sentences,
and a voice contract written after two of them have shipped is a rewrite rather than a contract.
The XLSX writer sits at `13-`, between the voice contract it depends on and the PDF that defers to
it. Item 16 consumes the voice layer and satisfies the export gate — **which item 17 built rather
than item 14**, because the batch runner is the thing that could have exported unattended, so
items 13 and 14 now write *through* `sightline.batch.gate` instead of inventing it. **Item 24 is
last on purpose**: it annotates the PDF and the review screen that items 14 and 16 build, and it
surfaces the near-boundary marker item 12a computes but deliberately leaves without a UI.

**Item 12a closes batch 2 rather than opening batch 3**, because every artifact in batch 3 renders
a band: the workbook, the PDF and the review screen all read the effort model, and shipping any of
them against a model that does not exist yet would mean three sessions each inventing one.

**Wave 2 — re-specified 2026-09-29 (PR #30).** The `14-` and `15-` prompts above were written on
2026-09-08 and went stale as items 17, 12a, 13 and 15 landed; they are retired, and the queue
carries `99i-` (item 14) and `99j-` (item 16) instead, each gated on its entry under *Notes
carried forward* reading READY. Item 16 does not yet (two questions); item 36 waits on it.

**Wave 3 — released 2026-10-29 (operator).** In order: `99p-` item 39 (the latent bug first),
`99q-` item 16 (re-issued from `99j-` with Q1 and Q2 decided), `99r-` item 36, `99s-` item 24's
re-run (from `99l-`). Each
later HEAD gates on its predecessors being DONE on `main` and stops otherwise. Item 22 part 2 is
not queued: 36 is DONE (PR #43), so part 2 is ready for its own specification pass, which comes
before any queue entry. `99ra-` (item 42, filed by item 36's session) sorted before `99s-` because
it is silent wrongness: a finished run with no finish time. It ran DONE — PR #44. `99s-` (item 24's
re-run) ran DONE — PR #45.

**Wave 4 — composed 2026-10-30, not released.** In order: `99u-` item 43 (the bug first),
`99v-` item 22 part 2, `99w-` item 44, `99x-` item 45, `99y-` item 46. Each gates on its
predecessors being DONE on `main`, then `99z-` item 47 (decided the same day: Authentik). `99u-`
(item 43) ran DONE — PR #48. `99v-` (item 22 part 2) ran DONE — PR #49. `99w-` (item 44) ran
DONE — PR #50. `99x-` (item 45) ran DONE — PR #51. `99y-` (item 46) ran DONE — PR #NN.

---

## Not queued, and why

**30 is DONE (PR #24)** — measured first, as its prompt required, and the measurement said build
it. See *Notes carried forward*.

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

**15 — DONE, PR #27.** Built as `sightline.voice`: `phrases.yaml` (the bank, by tier and fact
shape) and `register.yaml` (the lint's word lists), both enforced at load; one renderer every
surface calls; the four-slot narrative from computed aggregates. Items 13, 14 and 16 call
`sightline.voice.render_review` and must not write a sentence of their own. Two things they
inherit: each row's `notes` and each detector's `observed` string are still English written outside
this layer (by the projection and the detectors). The renderer never prints `notes`: it states
the facts behind a rating through the bank, with two known omissions — a pack's own per-row `note`
(four rows of the reference pack, each restating what the row's sentence already says) and the
"priced at the table's default" note for a detector with no cost entry (none today; possible under
a `SIGHTLINE_EFFORT_COSTS` override). It inserts `observed` as a *measured value* — a verbatim
span the lint masks, like a quote — so a surface should give it that treatment rather than style it
as Sightline's prose. Rotation is keyed on the criterion code and the finding's identity; the
Tier C question an extractor drafts (`ExtractorResult.question`) is likewise not rendered — the
bank's `C.question` shapes state the same distribution.

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
adoption risk. **The build contract is item 14's entry under *Notes carried forward*** (re-specified
2026-09-29).

**16 — the review screen groups by rubric category, in the instrument's own order, with severity
as a sort and filter within it.** The screen's job is confirming a rubric and its output is
category-ordered, so the designer walks the instrument they already know; triage already has a home
in the action plan. Grouping by band and grouping by tier were both considered and rejected — the
tier option is the tempting one, since §02 does demand Tier A and Tier C be visually separate, but
that is satisfied by distinct treatment *within* a category rather than by tearing the rubric apart.
**The build contract is item 16's entry under *Notes carried forward*** (re-specified 2026-09-29;
review screen only, per operator decision D2 — the run-progress and New run screens are item 36).

**One thread runs through all three, and it is the same failure §11 names.** Item 04 kept
`NOT_YET_IMPLEMENTED`, `NO_DETERMINISTIC_COVERAGE` and `SUFFICIENTLY_PRESENT` apart in the data
model. Each of these three items is a fresh chance to collapse them at the last moment before a
human reads the result — in a sentence, in a table cell, in a table row. All three prompts carry
that trap explicitly and all three acceptance lists test for it.

**18 — escalation adapter · PARKED 2026-10-29 (operator).** ChatGPT Edu API access is not
available. The app functions fully without it. *Noted, not decided:* the adapter is
OpenAI-compatible and Ollama serves that API, so a larger model on the existing model host could be
the escalation endpoint. Whether one that fits beside the extraction model resolves the
disagreement set better than the three runs is a measurement question against the calibration
corpus, to be filed as its own item if it is wanted.

**19 · SUPERSEDED by 22.** Its premise — that an uploaded rubric needs a mapping screen before it
is usable — is dead. Under item 04 an unbound rubric runs immediately, extraction-only. What remains
worth building is item 22.

**22 — model-proposed bindings, human-confirmed · DONE: part 1 (PR #32) the proposal engine and a
command-line review; part 2 (PR #49) the confirmation screen** (split by operator decision
2026-09-25, recorded with the measurement under *Notes carried forward*). Give the model
the detector catalogue and the uploaded criterion text; it proposes tag bindings a human confirms
**once per rubric, never per course**. An accelerator, not a prerequisite.

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

**24 — agreement and near-boundary transparency · READY; PART 1 DONE (PR #29), the PDF and review-screen
rendering follows items 14 and 16 — see *Notes carried forward*.** Ship, in the product,
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

**Item 40 (decided 2026-10-30) changes the story without changing the rule.** A shared instance
is three containers — a self-hosted identity provider, an auth proxy and the app — one process
each. Item 47 writes a reference compose file and a runbook for it, host-agnostic; where it runs
and who operates it is undecided, and a session never deploys it. Without the proxy the app keeps
today's single-user local mode.

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

**22 · DONE. Part 1 2026-09-29 (PR #32): model-proposed bindings with a command-line review.
Part 2 2026-10-30 (PR #49): the screen that proposes, confirms and reverts them.**

*As built (part 2, PR #49).* SPEC §09 *As built (item 22, part 2)* is the description; in short:
- **Screens:** `sightline.rubric_screens` — `/rubrics`, `/rubrics/{pack}` (bindings in force with
  their coverage, revert, *Propose bindings* and its progress, kept sets, history) and
  `/rubrics/{pack}/proposals/{set}` (the confirmation screen). A *Rubrics* entry in the navigation;
  the New run picker links each choice to its page.
- **Propose:** `sightline.proposing` runs `propose()` in the app (new optional `progress`
  callback), review model from Settings, client at concurrency 1. Refused while a run executes
  (`new_run.active_runs`, item 43's live owner); Start refused while proposing
  (`new_run.proposing_blocker`); each names the other. Progress: rows to ask, asked, could not be
  asked.
- **Confirm:** every 2026-09-08 decision as part 1 recorded it; *Accept* is offered only after the
  delta of exactly the checked set is shown (a hidden token compares the two). Accept and revert
  record `current_reviewer`, the time, the numbers and the delta, and are refused without a
  Reviewer.
- **Apply:** `sightline.rubric.accepted` — states under their sha256 digest, an append-only
  history, `resolve_pack` the one resolver (views, `python -m sightline.batch`,
  `sightline.batch.service`, `sightline.report.loader`). `runs.bindings_digest` is an added
  column; the review file names `pack.bindings` only when bindings were accepted, so the committed
  sample is unchanged.
- **Calls made, reversible:** a set is accepted once, as a unit (accepting changes the pack, so
  part 1's fingerprint refuses the rest; a revert reopens it); a state made over a shipped pack
  that has since changed is not applied to new runs, and the page says so; for a join-key pack
  (both shipped packs) `accept()`'s base mapping is `rubrics/maps/<pack>.yaml` with the bindings
  in force written in; part 1's failure prefix "the host failed:" became "the model host
  returned an error:" because the register lint reserves "failed".
- **Not built:** a confidence anything; a bulk accept that covers conflicts; a change to the
  proposal call's context window; anything of items 44–47; proposing from the command line into
  the app's store (the CLI keeps printing files, unchanged).
- **Evidence:** 36 tests in `tests/test_rubric_screens.py`; screenshot QA at 1400 and 390 px, 14
  measurements, all `scrollWidth == innerWidth` after one fix (a set-state chip overflowed at 390).

*2026-10-29 (item 36's session, PR #43): part 2 is now ready for its specification pass.* Item 36
built the New run screen's rubric picker. It is the Start form on `/courses`, and its options are
`sightline.new_run.RubricChoice` values from `rubric_choices()`, one per shipped pack. A
confirmation screen attaches there: for example a per-choice state ("bindings proposed, not
confirmed") and a link from the choice, without rebuilding the picker. The specification pass
decides the rest. Part 2 is not queued until that pass makes it READY.

*Part 2 specified 2026-10-30 (operator, interactive). READY.* Three decisions:
1. **The screen makes the proposals.** A *Propose bindings* action on a rubric runs `propose()`
   in the background with the review model chosen in Settings, through item 09's client at
   concurrency 1. It is refused while a review run is active, and a review run is refused while
   it is going — both name the other, as item 36's start check does. Progress is rows asked and
   rows failed, never a percentage. The command line keeps working unchanged.
2. **Accepting applies to the app's copy of the pack.** `accept()`'s mapping is written under the
   data root, beside the pack id, and every run the app or `python -m sightline.batch` starts
   against that pack uses the shipped pack with the accepted bindings applied. The shipped YAML
   in the repository never changes. Each acceptance is recorded with the reviewer (item 16's
   `current_reviewer`, item 44's identity once it lands), the time, the accepted proposal
   numbers and the coverage before and after, and the latest acceptance can be reverted, which
   restores the bindings before it. History is kept, never rewritten.
3. **A run may start while proposals are unconfirmed.** Bindings are an accelerator, not a
   prerequisite (§09). The picker shows *bindings proposed, not confirmed* beside the choice, with
   a link to the confirmation screen, and the run uses only confirmed bindings.

The screen's contract is the 2026-09-08 decisions above and what part 1 shipped: every box starts
unchecked; check-all and accept-checked exist; each proposal shows what (the tag and the
detectors behind it), why (the verified span, or *no supporting span*) and how (`consequence()`);
the coverage delta of the checked set is shown before it is accepted; conflicts are listed apart,
are never covered by check-all, and each needs its own box; Tier D rows read *not asked* with the
reason; a failed row reads as a failure, never as none proposed. No confidence signal, ranking or
model order anywhere, and the vocabulary scan from part 1 covers the screen.

*A trap the decisions create.* A run records its rubric by pack id, and the report loader reloads
the pack by id. Once bindings can change after a run, an old run's report must still render
against the bindings that run used: each accepted state is stored immutably under its digest, the
run records the digest it ran with, and the loader resolves by it. A run recorded before this item
has no digest and reads the shipped pack, which is what it ran with.

*Left as it is:* the context window for the proposal call (part 1 measured `gpt-oss:20b`
running out of it on 3 of 18 RSI rows). Those rows show as failures; widening the window is a
measurement for later, not part of this screen.

*Operator decision, 2026-09-25 (bitmap, interactive):* **build the proposal engine with a
COMMAND-LINE review now; the confirmation SCREEN comes later.** Why it had to be decided: this entry
put confirmation "on the rubric import/selection path, shown once per pack", and no such screen
exists — `python -m sightline.rubric` was the only rubric surface, and the New run screen, which
carries the rubric picker, is item 36. So the item is split there. **Part 2 (the screen) waits on
item 36's rubric picker**, which itself waits on item 16. When 36 is DONE, part 2 needs its own
specification pass against what part 1 left, not a straight queue: the screen consumes a
`ProposalSet` and `accept()`, and the decisions of 2026-09-08 above (unchecked boxes, check-all,
the three things per proposal, the delta) are its contract.

*What shipped.* `sightline.rubric.proposals`: `propose(pack, client, model)` → `ProposalSet` (one
schema-constrained call per row through item 09's client, concurrency 1, the picker's usability
rule; the thinking pin from `extract.runner.thinking_for`); `review()` → the text a person reads;
`accept(proposals, pack, names=|accept_all=|conflicts=, base=)` → `Acceptance` (the accepted
proposals, a `PackMapping`, the updated pack, and `CoverageDelta` over `pack_coverage` before and
after). `sightline.rubric.mapping.dumps` — the loader's exact inverse, tested on both shipped
mappings. `python -m sightline.rubric propose | review | accept`. Instrument:
`scripts/measure_binding_proposals.py` (`selftest`, `run`, `compare`).

*How each 2026-09-08 decision landed.*
- **No confidence signal.** The response schema has `tag` and `span` and nothing else, and a
  response carrying any other key is refused, not trimmed. Proposals are numbered in instrument
  order and alphabetically within a row, so not even the model's own order survives. A test scans
  every key of the file and every printed word for confidence-shaped vocabulary, and a second test
  proves the scan fires on a planted field.
- **Nothing by inertia.** `propose` changes nothing and prints a proposal file whose every
  proposal is unaccepted. `accept` with no `--accept`, `--accept-all` or `--accept-conflict` is
  refused (exit 2, empty stdout) rather than printing a mapping that looks like a decision.
- **WHY is a checkable artifact.** The span is verified against the criterion text by
  `sightline.extract.verify.verify`; what is kept is the criterion's own words at the match. An
  empty span reads *no supporting span*; a span not in the text is discarded and its words are
  never shown. There is no rationale field.
- **HOW it changes the review** is computed (`consequence()`) from the tier, the coverage before and
  after, and the detectors behind the tag. **The entry's wording "moves a row from evidence-only
  Tier B to deterministic Tier A arithmetic" is not what the code does, and the review says what
  the code does:** a binding never changes a row's tier. A Tier B row that gains a binding becomes
  deterministic in *coverage*, its observations are banded, and a clean run still reads No Response
  (item 28); only a Tier A row gets arithmetic that can read Sufficiently Present. A Tier C row
  stays at No Response whatever it binds to, and the review says so.
- **Existing bindings are never replaced.** Acceptance only adds. A proposal on a row already bound
  to other tags is a conflict: `--accept-all` leaves it out and it needs `--accept-conflict N`.
- **The output is a mapping file** that `import --map` applies. It carries everything the pack
  already said that a mapping can express plus the accepted tags. A pack imported with a declared
  join key records the key's values but not its column, so `accept` refuses it without `--map` and
  checks a supplied mapping against the pack's tiers and bindings first. A proposal file made
  against another state of the pack (a sha256 over id, version and every row's code, text, tier and
  bindings) is refused.

*Readings this session took where the entry left room, each stated so it can be reversed.*
- **Tier D rows are not asked.** A Tier D row reads Not Applicable bound or not, so a proposal could
  change nothing, and binding one tripped item 38 (fixed, PR #35). Each is recorded as *not asked*
  with the reason.
- **The row code is not sent to the model**, only the criterion text: a code the model recognises
  from another rubric is an invitation to answer from memory. Nothing assumes an OSCQR derivative.
- **The catalogue sent is the whole vocabulary**, including tags no detector implements yet, each
  with what its detectors report; a proposal of an unimplemented tag says it changes nothing
  computed and, on Tier A, keeps the row from reading Sufficiently Present.
- **A row whose call failed is a failure, never "none proposed"**, and `propose` exits 1 when any
  row failed, as `python -m sightline.extract` does.

*Measured 2026-09-29, once, against the live host (Ollama 0.34.2; `/api/tags` read first; nothing
pulled or deleted).* Each shipped pack with every binding stripped (tiers, codes and text kept),
proposed over by `propose` exactly as shipped, and scored against the shipped bindings as sets of
(row, tag) pairs. **These are a fact about two models on these packs on this day. They are not a
product claim, the tool prints nothing like them, and the shipped bindings are one author's
reading, not ground truth** — a proposed-not-shipped pair is a disagreement with that reading.
Sample: every row of both packs, one run per model per pack (the host is not deterministic at
temperature 0, so a second run would move these). Re-run: `scripts/measure_binding_proposals.py`.

| Pack (rows, shipped pairs) | Model | Proposed | Proposed & shipped | Proposed, not shipped | Shipped, not proposed | Rows with none | Rows failed | Spans verified / discarded / absent | Wall time |
|---|---|---|---|---|---|---|---|---|---|
| RSI (18, 18) | `qwen3-coder:30b` | 4 | 4 | 0 | 14 | 14 | 0 | 4 / 0 / 0 | 27 s |
| RSI (18, 18) | `gpt-oss:20b` | 19 | 15 | 4 | 3 | 3 | 3 | 14 / 5 / 0 | 1172 s |
| 3.1.3 (68, 58) | `qwen3-coder:30b` | 23 | 18 | 5 | 40 | 46 | 0 | 23 / 0 / 0 | 80 s |
| 3.1.3 (68, 58) | `gpt-oss:20b` | not run to the end — see below | | | | | | | |

What the numbers say, and what they do not:
- **`qwen3-coder:30b` abstains heavily.** It returned an empty list on 14 of 18 and 46 of 67 asked
  rows. What it proposed agreed with the shipped reading 22 times in 27. Fast (~0.6 s per empty
  answer).
- **`gpt-oss:20b` (thinking on) proposes far more and agrees with far more of the shipped reading
  on RSI (15 of 18), at ~65 s a call.** Its 3 failed rows are all `done_reason=length`: reasoning
  used the 8192-token window (up to ~5,800 completion tokens) before the answer. They are recorded
  as failures, not as "none". Whether a wider window for this one call is worth it is a question
  for part 2's specification, not a change made here.
- **The span verifier fired on live output.** `gpt-oss:20b` returned 5 spans that are not in the
  criterion text (RSI 3 ×4, RSI 8 ×1). All were discarded, and those proposals read *no supporting
  span*. No unknown tag was returned by either model: the `enum` in the schema constrained them.
- **`gpt-oss:20b` over the 3.1.3 pack was started and not finished.** It ran 10 of 67 calls
  (2 of them `done_reason=length`) in 11 minutes before the session that ran it hit its time
  limit, and at ~65 s a call the full pass is ~75 minutes, so the next session did not repeat
  it. No number is reported for it. The instrument re-runs it unchanged.
- Not determined: agreement between two runs of one model; any model not on the host today; whether
  a reviewer shown these proposals accepts more, fewer or different ones than the shipped reading.

*The coverage defect this surfaced is item 38*, filed below, not fixed here: `PackCoverage` counts
a bound Tier D row twice.

**38 · DONE 2026-09-29 (PR #35). Filed by item 22's session (PR #32). A Tier D row with a binding
was counted in two coverage buckets, so the report's own arithmetic check failed and `coverage`
exited 1, saying the report had a bug.** The entry below stays as the contract.

*What shipped.* `PackCoverage.deterministic` and `.not_yet_implemented` exclude Tier D, as
`.evidence_only` already did, so the four buckets are disjoint by construction: tier first, then
coverage. `detail()`'s per-section "N of M with coverage" counts the same way, so a bound Tier D row
no longer counts there either, and its pending tags no longer appear under "awaiting a detector".
`RowCoverage.coverage` itself is unchanged: it still says what the row's bindings resolve to, which
is a fact about the bindings, not a bucket. The projection, the pack format, the shipped packs and
item 22's `_bucket` (already tier-first) are untouched; `scripts/build-packs.sh` re-run from the
branch is byte-identical.

*Measured.* The reference pack with MOB 5 bound to `alt_text`, through `python -m sightline.rubric
coverage` in the dev image: on main, 42 of 68 deterministic and exit 1 with the "bug here" line; on
the branch, 41 + 8 + 18 + 1 = 68 and exit 0, the same counts as the unbound pack. Three tests
added, each failing on main and passing on the branch: the reproduction (MOB 5 in exactly one
bucket, `ok` True), the same with a build where no detector implements the tag (the
not-yet-implemented bucket), and `coverage_delta` against `pack_coverage` before and after with MOB
5 and one Tier B row bound together (only the Tier B row moves; every bucket's change equals what
moved in less what moved out). Full suite in the dev image, each from a `git archive` extract: main
1844 passed, 4 skipped; branch 1847 passed, 4 skipped.

*Evidence.* `PackCoverage.deterministic` and `.not_yet_implemented` filter on coverage alone;
`.not_determinable` filters on tier alone; `.evidence_only` is the only bucket that excludes Tier
D. Reproduced on the reference pack by binding its one Tier D row (MOB 5) to `alt_text` in memory:
counts go from 41 + 8 + 18 + 1 = 68 (`ok: True`) to 42 + 8 + 18 + 1 = 69 against 68 criteria
(`ok: False`), and `detail()` prints "the buckets above do not account for every row, which is a
bug here". Not reachable from either shipped pack (MOB 5 is unbound) or from item 22's proposer
(which does not ask about Tier D rows). It is reachable by any hand-written mapping that binds a
Tier D row, which the format allows. It is loud rather than silent: the exit code and the line both
say so.

*Fix.* Tier first, as `evidence_only` already does: a Tier D row is in `not_determinable` whatever
its bindings, and in neither `deterministic` nor `not_yet_implemented`. Test: the reproduction
above reads `ok: True` with the row counted once, under not determinable. Check that the
projection's rating for such a row stays Not Applicable, which `_rate` already does, and that
item 22's `CoverageDelta` still agrees with `coverage` before and after.

**14 · DONE 2026-09-29 (PR #31). The PDF course report, written through the export gate, and
the review file read back.** Built to the re-specified entry below, which stays as the contract.

*What shipped.* `sightline.report`: `loader` (`read_review` / `parse_review` / `load_review` →
`LoadedReview`, which `render()`s through `render_review`), `model` (`build_report(review,
created=)` → `ReportModel`, linted), `pdf` (`to_pdf(model)` → bytes, `export_report(gate, course,
data)` → `ExportGate.save`), and `python -m sightline.report export COURSE`, which writes
`report.pdf` and `review.xlsx`. `from_dict` inverses on `Location`, `Finding`, `Undetermined`,
`DetectorResult`, `DetectionReport`, `Evidence`, `Discarded`, `Failure`, `CallRecord`,
`ExtractorResult`, `RowExtraction`, `Margins`, `NearBoundary`, `Contribution`, `RowResult` and
`Projection` (which takes the pack for `priority_order` and `rating_scale` and refuses another
version). No `to_dict` needed a new key: the renderer reads the effort model's `calibrated` flag,
which the file carries; the per-detector cost table it does not carry is not needed, since every
hour figure is on its row. Fonts vendored under `assets/fonts/` with their licence texts; WeasyPrint
70.0 and the native libraries recorded in the README with versions and licences read from the
packages. `examples/sample-report/` made by `scripts/make-sample-report.sh` (D1).

*Readings this session took where the entry left room, each stated so it can be reversed.*
- **The action plan lists criteria, not items.** One entry per criterion with a located item or a
  question, in `action_plan_order()`, with its priority, reading and a count of what was located,
  linking to its entry below. The items themselves are printed once — in full for ACD and ENIN,
  and in the workbook for everything — rather than twice in the PDF.
- **A compact line's "link" is one link per Canvas item something was located in**, titled with
  the item's name. "No check ran" replaces an observation count on a row nothing examined, and a
  Tier C row's reading is "no rating proposed: the reviewer's call", never No Response.
- **Blocks follow the register, in the order A, B, C, D** — counted, located and quoted, the
  question, the limits of the pass — each with a bank heading and its own tokens treatment.
- **`write_pdf` is guarded when given a target**, not by name: the bare name would refuse the one
  legitimate call. Proven by a planted `document.write_pdf("/tmp/escaped.pdf")` in
  `report/pdf.py`, which failed the guard, before it was removed.
- **SOURCE_DATE_EPOCH.** WeasyPrint 70.0 stamps no clock of its own (dates only from the
  document's `dcterms` metadata, pinned to the opened-at moment; no file identifier). Its
  fontTools fallback subsetter — used where `libharfbuzz-subset` is absent — stamps each embedded
  subset's `head.modified` from the clock unless `SOURCE_DATE_EPOCH` is set, so the render sets it
  to the same moment under a lock and restores it. Two renders differed before that and not after.
- **Byte identity is checked where it can hold.** `render.json` records the render environment
  (pango, harfbuzz, fontconfig, freetype and the Python packages); the comparison runs where it
  matches and is required in the dev image (`SIGHTLINE_SAMPLE_REPORT_REQUIRED`), and CI's image job
  runs it there. The runner-host `test` job skips it, naming what differs.
- **The RSI pack has no ACD or ENIN section**, so a report on it prints every row compactly.

*Not done here.* The near-boundary marker and the agreement statement (item 24's re-run), a
reviewer's overrides in exports (item 16, Q2), remediation wording (item 35).

**14 · RE-SPECIFIED 2026-09-29 (PR #30). READY. The PDF course report, written through the export
gate. This entry is the build contract.** The 2026-09-08 decisions under *Not queued, and why* are
where it came from; where the two differ, this entry is newer and wins.

*Operator decision D1, 2026-09-25 (bitmap, interactive) — verbatim, do not re-open:*

> HOW `examples/sample-report/` GETS MADE. Item 14's old prompt says "generate it and commit it",
> but two structural guards make that impossible from inside the package: `tests/test_paths.py`
> fails the build if any module but `paths.py` creates a writable location, and
> `tests/test_export_gate.py` fails it if any module but `paths.py`/`gate.py` names `reports`.
> `examples/README.md` repeats the first guard as a promise.
> DECIDED: (a) the sample course goes through the NORMAL gated export into a SCRATCH data folder (a
> run over `examples/sample-course`, the review opened with `python -m sightline.batch
> open-review`, the export through `ExportGate`), and a script under `scripts/` (outside
> `src/sightline`, so the package guards stay whole) copies the result into
> `examples/sample-report/`. A test re-renders the sample and compares it with the committed copy
> (byte-identical for the PDF; cell-by-cell for the workbook, per item 13's bar).

*Decided 2026-09-08 by a human — do not re-open.*
1. **Audience.** Addressed to the instructional designer and written so it can be forwarded to the
   faculty member unchanged. One document, one register: no faculty edition, no internal edition.
   "Forwardable unchanged" is a test applied to every line, and it is what enforces §11's
   vocabulary.
2. **Structure — action-plan led, evidence tiered**, in this order: (a) *what this document is*;
   (b) a summary of counts by category and by band, with no aggregate of any shape; (c) the
   prioritized action plan, in `Projection.action_plan_order()`; (d) ACD and ENIN **in full** —
   every sentence `render_review` gives those rows, with quotes, counts, locations and links;
   (e) every other row as **one compact line** — composite code, criterion text, rating, number of
   findings, coverage state, link, no quotes — and the section says that the workbook
   (`review.xlsx`) holds everything; (f) the closing narrative.
3. **§02's presentation constraint is explicit for the PDF.** Tier A findings and Tier C questions
   never share a visual channel: separate, labelled blocks, chosen from each `Sentence`'s
   `register` and `role`, never by parsing its text.
4. **Page one carries a short *what this document is* block:** a review pass and not an evaluation
   of the instructor; ratings denominated in remediation effort, not quality; Tier C items are
   questions and carry no rating; no aggregate course score is computed anywhere. One short
   paragraph, from the phrase bank.

*Routine calls already made — build them as stated.*
- **HTML, then WeasyPrint.** No headless browser: one process in one container. **This entry
  authorises, in so many words, adding** `weasyprint` (pinned in `uv.lock`), the system libraries
  it needs in the Dockerfile stages that render and in CI's `test` job, and the vendored fonts
  below. Each is recorded in the PR and the README with its version and its licence as read from
  the package.
- **US Letter.**
- **Light palette, pinned** by stamping `data-theme="light"` on the root element: both dark blocks
  in `docs/tokens.css` (`:root:not([data-theme="light"])` under the media query, and
  `:root[data-theme="dark"]`) exclude it. Every colour comes from a token —
  `tests/test_templates.py` scans every `.css` and `.html` under `src/sightline`.
- **Fonts vendored under `assets/fonts/`** with their licence files: Instrument Serif, IBM Plex
  Sans and IBM Plex Mono (`--f-display`, `--f-body`, `--f-mono` in `tokens.css`), static weights
  only. Read the licence text of what is downloaded; do not take "OFL" on trust. The Dockerfile
  copies no `assets/` today — add it to every stage that renders.
- **Byte-identical re-renders**, with the PDF metadata pinned.
- **Every finding links to the item in Canvas**, not the editor: `Sentence.link`, built by
  `render_review(base_url=…)`. `link_exact` is false when the link finds the item by search.

*What it consumes from `main` (all present at `a76181c`).*
- **The gate, item 17 (PR #18) — not built here.** `sightline.batch.gate.ExportGate`:
  `require_open(course_id)` returns the opened-at timestamp or raises `ReviewNotOpenedError`;
  `save(course_id, filename, data)` writes bytes through `DataPaths.save_bytes`. No module here
  names `reports` as a string or `.reports` as an attribute — `DataPaths.reports` included
  (`tests/test_export_gate.py`).
- **The voice, item 15 (PR #27).** `sightline.voice.render_review(projection, report=,
  extractions=, base_url=, course_id=)` → `RenderedReview` (`rows`, `row(code)`, `narrative`,
  `sentences()`); `RenderedRow.by_role()`; `Sentence.lint()` against `default_register()`.
  Headings and fixed text are `phrases.yaml` entries (`labels`, `formats`; `Voice.label()`,
  `Voice.fragment()`). A template writes no sentence of its own.
- **The workbook, item 13 (PR #28).** `sightline.workbook.build_workbook`, `to_xlsx`,
  `export_workbook(gate, course_id, data)`, `WORKBOOK_FILENAME` (`review.xlsx`).
- **The effort model, item 12a (PR #20).** Each row's `rating`, `label` and `hours`, shown as an
  estimate the way the workbook does (`effort_placeholder` / `effort_calibrated`). The
  near-boundary marker and `sightline.voice.agreement.brief()` are **not** rendered here: item 24's
  re-run adds both to this PDF.
- **The coverage states, item 04 (PR #9).** `Coverage.DETERMINISTIC`, `NOT_YET_IMPLEMENTED`,
  `NO_DETERMINISTIC_COVERAGE`, and `Rating.SUFFICIENTLY_PRESENT`, which only a Tier A row with every
  binding examined can reach (item 28, PR #23).
- **Overrides: none.** Item 14 renders the machine proposal. How a reviewer's override reaches an
  exported artifact is open question Q2 under item 16.

*What item 14 builds.*
1. **Reading a finished course back.** After a run, a course exists only as
   `runs/<run>/courses/<course>/review.json` (`BatchRunner._save_review`). `render_review` takes
   live objects, and nothing on `main` rebuilds them: `Projection`, `RowResult`, `Contribution`,
   `DetectionReport`, `DetectorResult`, `Finding`, `Location`, `Undetermined`, `RowExtraction` and
   `ExtractorResult` have a `to_dict` and no `from_dict`, and `Projection.to_dict` drops
   `priority_order` and `rating_scale`. Follow the answer the code already gives. The review file
   is what a screen reads (`sightline.batch.runner`), a screen works from the persisted dictionary
   (`MachineProposal.from_review`), and persisted shapes round-trip as inverse pairs (`DisagreementSet`
   in `sightline.extract.router`). So:
   - add `from_dict` inverses;
   - take the two dropped fields from the pack the review names (`shipped_pack(review["pack"]["id"])`),
     refusing when its version differs from `review["pack"]["version"]`;
   - add one loader that returns what `render_review` takes.

   A field the renderer needs that `to_dict` drops is added to `to_dict` as a new key. A review
   file without that key is refused with a stated reason, never re-derived. **No stage is re-run at
   export**: not detect, not project, not extract. Item 16 reads through the same loader.
2. **The report model**, pure and testable, then the HTML templates and print stylesheet on
   `tokens.css`, then **the PDF as bytes**: `HTML(...).write_pdf()` with no target, handed to
   `ExportGate.save`.
3. **An export command**, with its own `__main__`, outside `sightline.batch`. That module's
   docstring says it "does not export anything and cannot be made to". The command takes a
   run-store course id, writes the PDF and the workbook through the gate, and prints the gate's own
   message for a course nobody opened. The README documents it.
4. **One timestamp, and not the clock.** The PDF's metadata date, and any date it prints, is the
   value `ExportGate.require_open` returns. It is passed in the same way `build_workbook(created=)`
   takes it. Check whether the installed WeasyPrint honours `SOURCE_DATE_EPOCH`. If it does not,
   pin the metadata explicitly, and the PR says how.
5. **D1's script and test**, below.
6. **`write_pdf` added to `WRITE_CALLS`** in `tests/test_paths.py`, proven by a planted call that
   fails the guard before the plant is removed. `Workbook.save(path)` stays outside the guard:
   `save` itself cannot be listed, because `ExportGate.save` and `book.save(buffer)` are legitimate.
   The rule is to render to bytes (item 13 already does).

*D1, made concrete.*
- **The script** (under `scripts/`) does four things:
  1. Runs `python -m sightline.batch run --bundle examples/sample-course` with `SIGHTLINE_DATA_DIR`
     set to a scratch directory. The bundle is given as a **repo-relative** path, because the
     review file records the reference exactly as typed.
  2. Passes a model host and model through. The extract stage aborts a run without one
     (`ReviewPipeline._model_card`). Read the host's `/api/tags` for what is installed rather than
     naming a model from memory.
  3. Runs `open-review`, then the export command.
  4. Copies into `examples/sample-report/` the PDF, the workbook, the review file, and whatever
     else the re-render reads — at least the opened-at timestamp.

  Nothing in `src/` writes into `examples/`. The model's output varies between runs, so the
  committed review file is the input and only the script regenerates it.
- **The test** re-renders from committed files alone: no model host, no network, no clock. It
  asserts the PDF is byte-identical to the committed copy and the workbook equal cell by cell. A
  second test renders twice and compares the bytes.
- **Nothing committed carries** an absolute path, a home directory, a host other than
  `*.example.edu`, or anything token-shaped. A test greps the committed files for them.
- **`.dockerignore` excludes `examples/sample-report/` today.** Drop that line, or the test crashes
  in the dev image. This is the same trap item 24 hit with `docs/`.
- **Byte identity depends on the native stack** (pango, harfbuzz, fontconfig) as well as on
  `uv.lock`. CI's `test` job runs on the runner host, not in the image. `./scripts/dev.sh` runs the
  dev image with no mounts, and that image does not copy `scripts/`.

  The committed PDF and the comparison must come from **one environment**. The comparison must
  **run, not skip,** in at least one CI job in that environment; the `image` job already builds the
  dev image. If that cannot be made to hold, STOP with the byte diff. Do not lower the bar: it is
  D1's.
- **The base image tag floats.** A pango update can move the bytes with no code change. The remedy
  is to re-run the script, and the README says so.

*Traps.*
- **The coverage states collapsing in a compact line.** `NO_DETERMINISTIC_COVERAGE`,
  `NOT_YET_IMPLEMENTED` and a clean `SUFFICIENTLY_PRESENT` must read differently. So must a Tier B
  row left at No Response (item 28) and a Tier C row at No Response. §11 calls this the worst
  failure available.
- **MOB rows carry a null priority.** `action_plan_order()` sorts null last. Never default it.
- **ASFE skips index 6.** Key on the composite code and never renumber for display.
- **No aggregate of any shape:** no percentage, grand total, completion bar, "N of 68", or
  course-level hours (12a removed `Rollup.hours`). Counts per category and band are counts.
- **No run count beside a quote.** A batch review carries no attention payload in any case (see
  item 16).
- **Long Canvas links and long quotes overflow a paged box.** Look at the sample's pages.
- **No Canvas contact.** Use the sample course only. The configured base URL appears in a generated
  report because that is the report's job; in the sample it is `canvas.example.edu`.
- **The dev image carries `docs/SPEC.md` and `docs/tokens.css` and no other doc.** A test that
  reads another doc crashes there.
- **`./scripts/dev.sh python -` with a heredoc hangs.** Pass `-c`.

*Not item 14's:*
- the review screen (16);
- the run-progress and New run screens (36);
- how overrides appear in exports (item 16, Q2);
- the agreement statement and the near-boundary marker (24's re-run);
- a comparison report (20 is decided: not built);
- remediation wording — item 35, NEEDS DISCUSSION. The action plan lists located items, as the
  workbook does.

*Acceptance — demonstrated, not asserted.*
- The PDF is byte-identical on a rerun.
- The committed sample is byte-identical to a re-render from committed inputs, run in CI as above.
- The workbook matches cell by cell.
- The committed files contain no real host, name, email, absolute path or token.
- Every rendered finding carries an item-level Canvas link, tested over the sample.
- No block holds both Tier A and Tier C lines (tested).
- The three coverage states are distinguishable in the compact section (tested).
- Item 15's lint passes over the PDF's extracted text, and a grep of that text finds no grading
  vocabulary, percentage or total.
- The export command is refused for an unopened course (tested).
- Nothing in the item writes except through `ExportGate.save`.
- `write_pdf` is on the guard list, with the planted-call proof.
- The review-file round trip holds: `render_review` over the loaded file equals `render_review`
  over the live run, sentence for sentence (tested).
- Both images build in CI.
- Page 1 and one evidence page are rendered to PNG and looked at. The 1400/390 rule is for
  screens, not a fixed-width page.
- The full suite is green, ruff and black exit codes are read separately, and all CI jobs are
  green.

**16 · DONE 2026-10-29 — PR #42.** Re-specified 2026-09-29 (PR #30); READY from 2026-10-29, when Q1
and Q2 below were decided.

*As built (PR #42).* `sightline.review` at `/review/<run-store course id>`: an index in the pack's
category order with counts by proposed reading and questions with no rating recorded, a page per
category sorted and filtered by query parameters, one rating form per row, and the export control
(`sightline.report.export.export_course`, which the command now calls too). The Reviewer setting is
on Settings; `sightline.review.identity.current_reviewer` is the one lookup. `build_workbook` and
`build_report` take `overrides`; every Q2 addition is conditional on a recorded rating, so the
committed sample does not move. Two calls the decisions left open, made in the build and open to
the operator: the exports carry the reviewer's *rating* and not their *name* (the record keeps the
name); Tier A, B and D forms open with the proposal selected, so recording it unchanged records a
confirmation, while a Tier C form never has a rating selected. Nothing below is changed by the build.

*Operator decisions, 2026-10-29 (bitmap, interactive). Do not re-open:*
- **Q1: (a).** A *Reviewer* setting on the Settings screen, kept with the other preferences.
  Every override records it. Until it is set, the override form refuses and names Settings, and
  the form says whose name it will record. A reviewer can log an override, with a note, on every
  row. When item 40 (sign-in) lands, the signed-in identity replaces the setting. Build the
  lookup as one function the override path calls, so 40 swaps its source without touching the
  form or the record.
- **Q2: (b), built in item 16.** Where an override was recorded, the workbook and the PDF carry
  the reviewer's latest rating, labelled as the reviewer's, with the machine proposal and its
  effort figures beside it unchanged: never re-banded, never back-computed. A row with no
  override carries the proposal, labelled as proposed. This needs bank labels and an override
  input to `build_workbook` and to item 14's report model. **The earlier build instruction "no
  change to the PDF or workbook beyond gating their export" is lifted to exactly that extent.**
  The committed sample has no overrides, so its bytes, and item 14's byte-identical test, do
  not move.

*Operator decision D2, 2026-09-25 (bitmap, interactive) — verbatim, do not re-open:*

> ITEM 16's SCOPE. SPEC SS09 says the run-progress screen "is part of that item [16] rather than a
> second design", and the "New run" screen is built by no roadmap item.
> DECIDED: (a) item 16 is the REVIEW SCREEN ONLY. File a NEW roadmap item (the next free number,
> read from the roadmap) for the run-progress screen and the New run screen, and correct SPEC
> SS09's sentence to point at it.

That item is **36**, below. Item 16 builds no navigation to a screen that does not exist.

*Q1 — who does an override record?* `record_override(..., reviewer=)` refuses an empty reviewer
(`RunStore.record_override`). The column is documented as "whatever identifier the review screen
has for the person". The screen has none: there is no login, and no preference holds a name.
- (a) A *Reviewer* setting on the Settings screen, kept with the other preferences. Until it is
  set, the override form refuses and says why; the form names who it will record.
- (b) A required free-text field on every override.
- (c) A fixed identifier. This empties the column that items 12b and 25 would join on.

**Recommendation: (a).** Sightline is a single-user local tool (`sightline.views`), so the name is
typed once, and the record still says who.

*Q2 — does an exported artifact carry the reviewer's rating?* Neither `build_workbook` nor the
renderer takes an override. The queued build HEAD for item 16 says to change neither beyond gating
their export.
- (a) Exports carry the machine proposal only, and overrides stay calibration data. Every Tier C
  row then reads No Response in every report ever exported, whatever the designer decided.
- (b) Where an override was recorded, the export carries the reviewer's latest rating, labelled as
  the reviewer's. The machine proposal and its effort figures sit beside it unchanged: never
  re-banded, never back-computed. A row with no override carries the proposal, labelled as
  proposed. This needs bank labels, and an override input to the workbook and to item 14's report
  model. The committed sample has no overrides, so its bytes do not move.
- (c) As (b), but in a separate "reviewer's ratings" section.

**Recommendation: (b), built in item 16.** The build HEAD's "no change to the PDF or workbook" line
then has to be lifted in so many words.

*Answered by existing code or by the operator's queued HEAD — not open.*
- **Opening a review is loading it.** The first request for any page of a course's review calls
  `ExportGate.open_review(course_id)`, which is idempotent and never moves the timestamp. The gate
  docstring says this: "the review screen (roadmap item 16) when it lands". The export endpoint
  refuses an unopened course with the gate's own message; test it by calling the endpoint directly.
  A closed export control, with its reason, is shown where a course is listed but not yet opened —
  item 36's run screen, and `python -m sightline.batch status`, which already prints it.
- **Reached by the run-store course id** — the id that `status` prints and `open-review` takes. The
  path is the build's choice, with no host and no Canvas id in it. `status` prints it for each
  reviewable course. No login, no CSRF token (`sightline.views`, SPEC §09).
- **Read through item 14's review-file loader.** Nothing is re-run.
- **Overrides go through item 12a's record.** `MachineProposal.from_review(review["projection"],
  code)` → `sightline.rubric.overrides.record_override`, which is append-only. The latest record
  is what the screen shows. The note is the record's `note`. A note without a rating change records
  the machine rating as the human one, a confirmation that 12a counts as calibration data. No
  second table. A Tier C row is *unresolved* until a record exists for it.
- **Item 11's attention flag.** The router runs only from `python -m sightline.extract --runs 3`. A
  batch runs one pass and never builds a `Comparison` (grep `sightline.batch`), so a review file
  holds no attention payload. Item 16 renders none, and builds no counterpart for agreement. If a
  later item puts one in the review file, it renders `ATTENTION_NOTE` with it.

*Decided 2026-09-08 by a human — do not re-open.* **Group by rubric category, in the instrument's
order** — COAL → GSOV → OREP → ACD → ENIN → ASFE → MOB (the pack's own order). Severity is a sort
and a filter *within* a category, never a second grouping. Grouping by band and grouping by tier
were considered and rejected. §02's separation is met by distinct treatment within a category.

*SPEC §09's bindings:*
- Tier visible on every row.
- Tier C opens at No Response with evidence shown and nothing pre-selected.
- **No bulk-accept of any kind** for ratings. Item 22's bulk accept is for tag bindings and does not
  transfer.
- Overrides are first-class, with the proposal retained.
- The rubric was chosen per run, before this screen.
- Nothing exports until the review is opened.

*Routine calls already made:*
- Server-rendered HTML with light progressive enhancement: no SPA, no build step. Every action
  works as a form post with JavaScript off, as `sightline.views` does today.
- A review index — the categories the projection carries, with per-category counts by band and
  unresolved Tier C counts — plus one page per category.
- Sort and filter are query parameters, so a view is linkable.
- A jump to unresolved Tier C.
- Ink density encodes tier: the `.tier-a`…`.tier-d` classes in `tokens.css`.
- Sentences come from `render_review`. A missing sentence becomes a bank entry, never template text.
- The export control runs item 14's export (PDF and workbook through the gate) and offers each
  written file back, read through `ExportGate.destination`.

*Consumes from `main`:*
- the gate and the voice, as for item 14;
- item 14's loader and export — **item 14 must be DONE first**;
- `Projection.omitted` — a switched-off optional section is neither shown nor counted as
  unresolved;
- `Coverage` and `Rating` (item 04).

The near-boundary marker and `agreement.brief()` belong to item 24's re-run, not here.

*Traps:*
- the three coverage states collapsing in a row;
- at 390 px a row reflows to a stacked card, with `scrollWidth == innerWidth` measured in the DOM;
- MOB's null priority must survive every sort and filter (nine rows);
- ASFE skips 6;
- no progress bar, completion percentage or course score. A count of unresolved Tier C rows is work
  remaining, and is fine;
- no run count beside a quote;
- an override never re-bands and never back-computes effort, and the action plan keeps the
  machine's figures;
- no Canvas contact — every screen is driven from a run over `examples/sample-course` (`--bundle`)
  and recorded fixtures;
- `templates/index.html`'s lede still says the review screens "arrive with later roadmap items".
  Correct it when this lands.

*Not item 16's:*
- the run-progress and New run screens (36);
- item 22's binding-confirmation screen;
- a comparison view (20);
- the agreement statement and near-boundary marker (24's re-run).

*Acceptance:*
- Categories render in instrument order, with a linkable in-category sort and filter.
- A test shows no Tier C row ships a pre-selected rating and no bulk-accept control exists.
- **The headline test:** an override round-trips through 12a's record, with the machine proposal
  and the human value both readable.
- `NO_DETERMINISTIC_COVERAGE` is distinguishable from `SUFFICIENTLY_PRESENT` in the rendered HTML.
- Export is refused before opening and allowed after, with the reason shown.
- The nine MOB rows survive every sort and filter.
- Item 15's lint passes over the rendered pages.
- Q1's and Q2's answers are tested as they are decided.
- Screenshot QA is done at 1400 px and 390 px on every screen built, with the CDP target closed
  afterwards.
- The full suite is green, ruff and black exit codes are read separately, and all CI jobs are green.

**43 · DONE 2026-10-30 — PR #48.** Filed 2026-10-29 by item 36's session (PR #43). **The
application's restart recovery takes over a run another process is executing.**

*Evidence.* `RunStore.recover_interrupted()` marks **every** run whose status is `running` as
`interrupted`; it cannot tell whose it is. Three places call it:
- `BackgroundRunner.recover()` at app startup, after which `resume_all()` submits every
  interrupted run;
- `BackgroundRunner.shutdown()` at app shutdown;
- `python -m sightline.batch resume`, which calls it before executing.

A run started by `python -m sightline.batch run` executes in its own process and is `running` only
in the store. So starting or stopping the app while a CLI run is going marks that run interrupted,
and an app start then **executes the same run a second time, concurrently**. That means two
processes writing one run's courses and events, competing for one model host, and with the
fresh-export setting on, a second content-export POST to each course. Observed in item 36's
screenshot QA: a run marked `running` in the store, as a CLI run leaves it, was flipped by the app's
shutdown (`runs_interrupted` logged with its id). The double execution was not reproduced; it
follows from `resume_all()` reading `resumable_runs()` after `recover()`.

*Why it needs a decision.* The store has no notion of which process owns a run. The options trade
differently:
- (a) an owner record on the run (pid and host, or a heartbeat timestamp), which recovery checks
  before taking a run over;
- (b) one process at a time: the CLI refuses to run while the app is up, or the reverse;
- (c) recovery only touches runs this process started, and a run orphaned by a dead CLI process
  stays `running` until somebody resumes it by hand.

Item 36's start check already treats a store-`running` run as busy, so (a) or (c) would also stop
a stale `running` row from blocking Start forever after a CLI crash.

*Shares the cause:* none. It is a defect in sightline's own run ownership, not a process gap.

*Decided 2026-10-30 (operator, interactive): (a), an owner record. READY.* A run that is executing
records its owner — host name and process id — and a heartbeat time the owner refreshes while it
works. Recovery, at app start, at app shutdown and in `batch resume`, takes over only a run whose
owner is gone: on the same host, a process id that is not alive; on any host, a heartbeat older
than the stale limit. The app's shutdown marks interrupted only the runs it owns. Item 36's start
check counts a `running` run as busy only while its owner is live, so a command-line crash no
longer blocks Start forever. A `running` row with no owner was written before this item and is
treated as orphaned, as today.

*Trap.* The app container and a command-line run in another container can share the data volume
but not a process namespace, and the host name is the container's. A process id means something
only when the host name matches; across hosts the heartbeat is the whole test.

*As built (PR #48).* `runs` gains `owner_host`, `owner_pid` and `owner_heartbeat_at` through
`ADDED_COLUMNS`. `sightline.ownership` holds the rule and its two constants: `HEARTBEAT_INTERVAL`
is 30 seconds and `STALE_AFTER` is ten intervals, five minutes. The heartbeat is a thread, not an
asyncio task, so a stage that awaits the model host or holds the event loop in a long synchronous
parse cannot delay it. `RunHold` claims a run with a compare-and-set on the owner record, so of two
processes reaching for one orphaned run exactly one wins. It beats while the run executes and
clears the record on the way out. A run still `running` at that point is marked interrupted
instead of being left for the next recovery. The background runner claims before it marks a run
`running`. `python -m sightline.batch run|resume` execute under a hold, and `resume` of a run with
a live owner exits 2 and names `status`. App shutdown interrupts only this process's runs
(`RunStore.interrupt_owned`). Start counts a store-`running` run as busy only while its owner is
live. One case the decision did not name, decided here: a record that carries **this** process's
own pid on this host is live only while this process holds the run, because a restarted container
gives its app the same small pid under the same host name. Two limits remain:
- Reuse of a pid by an unrelated process on the same host is invisible to a pid check. The
  heartbeat ends it within `STALE_AFTER`.
- An owner taken over after five silent minutes (a suspended machine, say) stops beating when it
  notices, but it does not stop executing.

Tests: `tests/test_ownership.py`, and the Start tests in `tests/test_run_screens.py`. They use real
child processes, live or reaped, and a host name that is not this one. With the liveness rule
disabled, 9 of them fail.

**42 · DONE 2026-10-29 — PR #44.** Filed 2026-10-29 by item 36's session (PR #43). **A run that
completed with failures records no finish time.**

*Evidence.* `RunStore.set_run_status` sets `finished_at` only for `succeeded`, `failed` and
`cancelled`. `partial` is terminal too (`TERMINAL_STATUSES` in `sightline.models`), and the batch
runner ends a run with some courses reviewed and some failed in exactly that status. Seen in item
36's screenshot QA: a partial run's page read *Finished: Not yet*. The page now says *Not recorded*
for a finished run with no time; `GET /api/runs/{id}` and `python -m sightline.batch status` still
carry `finished_at: null`. This is wrong output with no error.

*Fix.* Set `finished_at` for every status in `TERMINAL_STATUSES` (leaving `interrupted`, which is
resumable, unset), and test each terminal status. Then the run page's *Not recorded* branch is
reached only by runs recorded before the fix.

*As built (PR #44).* `set_run_status` stamps `finished_at` for every status in `TERMINAL_STATUSES`;
`running` and `interrupted` leave it unset. Runs already in a store are not backfilled, so the run
page keeps its *Not recorded* branch, tested with a terminal run built directly in the store.
Local: full suite 1935 passed, 3 skipped; `ruff check` exit 0, `black --check` exit 0.

*Shares the cause:* none. It is a defect in this store's write, not a process gap.

**40 · FILED 2026-10-29 (operator, interactive). NEEDS DISCUSSION. Sign-in through the
institution's single sign-on, so one Sightline instance can be offered to several users.**

*The ask, verbatim in substance:* run Sightline behind SSO and offer it to multiple users with a
login, and take the reviewer name an override records from the signed-in identity (item 16 Q1).

*Why it is a discussion and not a build.* It reverses a premise the code and SPEC rest on.
`sightline.views` and §09 describe a single-user local tool: no login, no CSRF token, one set of
preferences, and "no production deployment" (*Deployment*, above). Several users on one host
change each of those. Questions to settle before it is READY:
1. **Identity provider and protocol.** OIDC against which provider. A self-hosted identity
   provider fronting the institution's SSO is one route; registering with the institution's
   provider directly is another. Which claim is the reviewer name (display name, email, or
   subject id with a display name beside it)?
2. **Where it runs.** A shared host is a deployment. The roadmap has none, and AGENTS.md forbids
   deploying from a session. Who operates it, and does the one-process-per-container rule hold
   (an auth proxy in front is a second container, not a second process in this one)?
3. **In the app, or in front of it.** A forward-auth proxy passing a verified identity header is
   the smallest change to the app. Native OIDC in FastAPI is self-contained. Either way the app
   must refuse a request that did not come through it.
4. **Canvas credentials.** Today one token in Settings. Per user, or one service token? A shared
   token means every user acts as its owner in Canvas, and content export POSTs write remote
   state as that owner.
5. **Whose data.** Runs, reviews, overrides and exports are one store today. Does every user see
   every run, or only their own? Who may open a review, which is what satisfies the export gate?
6. **Settings scope.** The model host, model and Canvas URL are instance settings; the reviewer
   name becomes the identity. Who may change instance settings (a role)?
7. **What changes with a login.** CSRF protection on every form post, session expiry, and the
   model host's concurrency of 1: two users' runs would queue on one model host (item 36
   already refuses a second active run).

*Depends on:* item 16's reviewer lookup (Q1), which this item replaces. *Blocks:* nothing queued.

*Decided 2026-10-30 (operator, interactive). Split into items 44–47; nothing is built under 40.*
1. **Identity provider and protocol:** a self-hosted identity provider federates the
   institution's SSO over OIDC, and an auth proxy in front of the app passes a verified identity.
   **Reviewer name:** the stable subject id is the identity; the display name is shown beside it,
   so a renamed person keeps their history.
2. **Where it runs:** undecided. Build host-agnostic, with a reference compose file (item 47). A
   person deploys; a session never does. One process per container holds: the identity
   provider, the proxy and the app are three containers.
3. **In the app or in front:** in front. The app trusts the identity headers only from the proxy
   and refuses any request that did not come through it.
4. **Canvas credentials:** each user's own token, encrypted at rest with the existing
   `sightline.credentials` mechanism. Reads and content-export POSTs happen as the person who
   started the run. The Canvas base URL stays an instance setting.
5. **Whose data:** users see, review and export their own runs; an admin sees every run.
   Opening a review — what satisfies the export gate — needs access to that run.
6. **Settings scope:** an admin role, granted by membership in an identity-provider group, may
   change instance settings (model host, model, Canvas base URL). Everyone else has per-user
   settings only.
7. **With a login:** a Start while another run is active is queued first-in-first-out, shows its
   position, and its owner (or an admin) can cancel it while it waits. Sessions last 8 hours and
   end when the browser closes; the proxy holds the session, so this is the proxy's
   configuration (item 47). Every form post carries a CSRF token (item 44).

*A reading taken here, stated so it can be reversed:* signing in is a mode, off by default. With
no proxy configured the app is today's single-user local tool, unchanged, and the Reviewer
setting still names the reviewer.

**44 · DONE 2026-10-30 — PR #50.** Filed 2026-10-30 (split from 40). **Signed-in identity behind a trusted proxy.**
A sign-in mode, off by default. When on, every request must carry the proxy's identity headers
(subject, display name, groups; names configurable) and a shared secret the proxy adds, or it is
refused with 401 before any route runs — a request that did not come through the proxy is not
served. `current_reviewer` returns the signed-in identity, and an override records the subject id
beside the display name (a new column; old records keep their name and no id). Every form post
carries and checks a CSRF token, in both modes. The Settings screen's Reviewer field is hidden in
sign-in mode. Session lifetime is the proxy's (item 47), not the app's. Header names are
configuration, with defaults that match Authentik's proxy outpost, and nothing else is
provider-specific: oauth2-proxy in front of Entra ID must work by changing configuration alone. *Does not* scope data by
user, add roles or touch Canvas tokens: that is 45.

*As built (PR #50).* `sightline.signin` and `sightline.csrf`, two middlewares ahead of every
route; SPEC §09 *As built (item 44)* has the detail and three readings taken. What 45 and 47 start
from: the identity is `request.state.identity` (`sightline.signin.Identity`: subject, name,
groups), read through `sightline.review.identity.current_reviewer`; groups are parsed with
`SIGHTLINE_SIGNIN_GROUPS_SEPARATOR` and nothing reads them yet; the secret header defaults to
`X-Sightline-Proxy-Secret`, which 47's runbook tells the proxy to add; `GET /healthz` is the one
unauthenticated path. The rubric screens' acceptance history still records the display name only
— if 45 wants the subject there too, it is a small change in `sightline.rubric.accepted`.

**45 · DONE 2026-10-30 — PR #51.** Filed 2026-10-30 (split from 40). **Runs per user, an admin
role, and per-user Canvas tokens.** A run records the subject who started it. The run list, run pages,
reviews and exports show a user only their own runs, and anything else answers 404, not 403.
Admin is membership in a configured identity-provider group, read from 44's groups header; an
admin sees every run and alone may change instance settings. Each user stores their own Canvas
token, encrypted with `sightline.credentials`; a run uses its starter's token, including when it
resumes after a restart, and a run whose starter has no token fails with that remedy. In
local mode there is one implicit user who is admin, so nothing changes.

*As built (PR #51).* `sightline.access` (`viewer`, `visible_run`, `visible_course`) is the one
lookup; SPEC §09 *As built (item 45)* has the detail and four readings taken. What 46 starts from:
a run's starter is `Run.starter_subject` (`None` for command-line and older runs), the test for
"its owner (or an admin)" is `viewer(request).can_see(run)`, and Start's refusal for somebody
else's active run already names it as another person's run without its id. *Open, for the
operator:* proposing, accepting and reverting rubric bindings is not admin-only. The decision
names the model host, the model and the Canvas base URL as instance settings, and this item adds
the export preference. Accepted bindings apply to every later run, so whether they are an instance
setting too is a decision, not a reading.

**46 · DONE 2026-10-30 — PR #NN.** Filed 2026-10-30 (split from 40). **Queue a
run behind the active one.** Start while a run is active accepts the run as queued instead of refusing it. Queued runs
execute one at a time in the order they were started; the run page reads *queued, N ahead*; the
starter or an admin can cancel a queued run. A command-line run with a live owner (43) counts as
the active run. No priority, no reordering, no estimate of when it starts. *The dependency on 43
is met (DONE 2026-10-30, PR #48): `sightline.ownership.owner_is_live` is the test for "a live
owner", and `sightline.new_run.active_runs` already applies it. The dependency on 45 is met too (DONE
2026-10-30, PR #51): a run's starter is `Run.starter_subject`, and
`viewer(request).can_see(run)` (`sightline.access`) is "its starter or an admin", the test for
who may cancel a queued run.*

*As built (PR #NN).* SPEC §04 *Runtime* and §09 *As built (item 46)* are the description. A new
status `queued`; `BackgroundRunner` holds the one queue task (`ahead_of_queue`, `must_queue`,
`queue_positions`, `start_queue`); `RunStore.queued_runs`, `take_queued` and `cancel_queued` are the
store's side, the last two compare-and-sets on the status. `POST /runs/{id}/cancel` in
`sightline.run_screens`. `runs` gains `cancelled_by_subject` and `cancelled_by_name` through
`ADDED_COLUMNS`. Three readings taken, in §09: who the page names as the canceller; `batch resume`
refuses a queued run; proposing is refused while runs are queued. Tests: `tests/test_run_queue.py`
(order, one at a time, positions, cancel by starter / by admin / refused for anybody else with the
404 of an unknown run, a restart keeping the queue, waiting behind a live command-line run on this
host and another, waiting while proposing), and item 36's three refusal tests in
`tests/test_run_screens.py` rewritten as queue tests. Each of five deliberate breakages of the queue
(ignore a live command-line run, start while busy, last-in-first-out, ignore the resume backlog,
cancel without the access check) fails at least one of them. Screenshot QA at 1400 and 390 px: a
queued run, the run list, a cancelled run and the Start form's notice, `scrollWidth == innerWidth`
on all eight.

**47 · FILED 2026-10-30 (split from 40). NEEDS DECISION: which identity provider and which
proxy.** A reference compose file — identity provider, auth proxy, app; three containers — and a
runbook: federating the institution's SSO, the admin group, the 8-hour browser-session cookie,
the shared secret 44 checks, and keeping the app unreachable except through the proxy. Host-
agnostic, with `canvas.example.edu` and fake values only; no session deploys it. The one open
question is the products (for example Authentik with its own outpost, or Keycloak with
oauth2-proxy). Depends on 44.

*Decided 2026-10-30 (operator): Authentik, and nothing Authentik-only in the app.* The reference
deployment uses Authentik and its proxy outpost; the operator already runs an Authentik instance,
so the compose file attaches to an existing one rather than requiring a new one, and says how to run
one where none exists. The app must stay compatible with other providers, Microsoft Entra ID
(Active Directory) named: the runbook carries a second worked example, oauth2-proxy in front of
Entra ID, and item 44's header names and the admin group are configuration, never Authentik
constants. *Entra's trap:* its group claim carries group object ids, not names, so the admin group
is configured as whatever string the proxy forwards, and the runbook says so.

**39 · FILED 2026-09-29 by item 37's session (PR #34). READY, queued 2026-10-29: latent, nothing
matches today. Every other bare pattern in `.dockerignore` excludes at the context root only.**

*Evidence.* Item 37 measured that Docker matches a bare `.dockerignore` pattern against the
context root only (177 host `.pyc` files reached the dev image through `__pycache__/`). The same
holds for the rest of the file: `.env`, `.env.*`, `*.imscc`, `*.pem`, `*.key`, `*.token`,
`config.local.*`, `*.sqlite`, `*.sqlite3`, `*.db`, `secrets/`, `private/`, `instance/` and the
cache directories. `.gitignore` means the same lines at any depth, so a `.env` or an `.imscc`
dropped under `examples/`, `src/` or `tests/` is kept out of git and still copied into the image.
A walk of the working tree on 2026-09-29 found **0** such nested files, so nothing has shipped.

*Fix.* Prefix each secret- and data-shaped pattern with `**/`, keep `.dockerignore` mirroring
`.gitignore`'s intent, and extend `tests/test_dockerignore.py` to assert the set. `data/` is a
judgement: it is the data root only at the top level, and `**/data/` would also drop any package
directory named `data` — check `src/` for one before widening it.

*DONE — PR #41.* Every secret-, data-, cache- and editor-shaped pattern in `.dockerignore` carries
`**/`, and `!.env.example` became `!**/.env.example`. `.git`, `.gitignore`, the root-only `docs/`
with its two exceptions, the Dockerfile and `.gitignore` are unchanged. **`data/` was widened to
`**/data/`.** `.gitignore`'s `data/` already ignores a directory named `data` at any depth
(`git check-ignore` matches `src/sightline/data/x.json`), so no tracked path can live under one,
and there is no `data` directory under `src/`, `tests/`, `examples/`, `rubrics/` or `assets/`.
`tests/test_dockerignore.py` now carries a Docker pattern matcher. It is self-tested both ways
(bare is root-only, `**/` is any depth, the last match decides). The test plants one nested file of
each of 19 shapes and asserts each is excluded, keeps `.env.example` at any depth, and checks that
every tracked file the Dockerfile copies stays in the context. Against main's `.dockerignore`,
19 of these tests fail.

*Measured* (item 37's method). Two `git archive` extracts of the branch, each with the same 19
nested files planted holding a marker string: one built with main's `.dockerignore`, one with the
branch's. Both stages of each were built, and planted files under `/app` were counted by content
(controls: an impossible marker reads 0, `OSCQR` reads non-zero). Dev image **19 → 0**, runtime
**15 → 0** (runtime copies no `tests/`). Files outside `/app/.venv`: dev 275 → 256, runtime
165 → 150. The main-minus-branch file list is exactly the planted set, and the branch image holds
no file the main image lacks, so the change drops nothing the app ships. The suite in the branch's
dev image (sample-report comparison required): 1848 passed / 25 skipped.

**37 · FILED 2026-09-29 by item 14's session (PR #31). READY. `.dockerignore` keeps host
bytecode out of the build context only at the top level, so a locally built image carries the
developer's `__pycache__` — and the home directory each `.pyc` was compiled under.**

*Evidence.* `.dockerignore` lists `__pycache__/` and `*.py[cod]`. Docker matches those against the
context root, so `src/sightline/**/__pycache__/` and `tests/__pycache__/` are copied by `COPY src`
and `COPY tests`. Measured on images built from a working tree that had run the suite: 106 of the
106 files in the dev image's `/app/tests/__pycache__/` contain the string `/home/<user>/dev/sightline`,
and 93 files under the runtime image's `/app/src` do; a pytest failure inside the dev image prints
the host path as the test's file name. A CI checkout has no bytecode, so CI's images are clean and
nothing has been published; it is a local-build leak of a username and a path, not of a secret.

*Fix.* `**/__pycache__` and `**/*.py[cod]` in `.dockerignore` (Docker's `**` matches any depth),
and a test that reads `.dockerignore` and asserts both patterns are there. Check it by building the
dev image from a tree with bytecode in it and counting the files under `/app` that contain the host
path (0 expected). Count by content, not by `.pyc` name: whether the image's own `uv sync` compiles
anything under `/app/src` was not checked.

*DONE — PR #34.* `**/__pycache__` and `**/*.py[cod]`, and `tests/test_dockerignore.py`. Measured
on a `git archive` extract of `058411b` after the suite had run in it on the host (177 `.pyc` under
`src/` and `tests/`), building each stage before and after, counting files under `/app` whose
content holds the extract's path (`grep -rlaF`; controls: a marker string that cannot occur reads
0, `OSCQR` reads non-zero): dev image **177 → 0**, runtime image **94 → 0**. The open question is
answered: the image's own `uv sync` compiles nothing under `/app/src` — no `.pyc` outside
`/app/.venv` in either image after the fix. In the dev image the suite read 1844 passed / 2 skipped
before and 1844 / 4 after (the new test skips where there is no build context; CI's checkout runs
it), and before the fix pytest named every test file by the host path. The test fails against the
old `.dockerignore` and passes against the new one.

**36 · DONE 2026-10-29 — PR #43.** Filed 2026-09-29 (PR #30) under operator decision D2
(2026-09-25; quoted in item 16's entry); READY once item 16 landed (PR #42). The run-progress
screen and the New run screen. SPEC §09 lists both, and until D2 no item built either.

*As built.* SPEC §09 *As built (item 36)* is the description; in short:
- **New run** is `/courses` extended. The courses Canvas resolved come back with a Start form,
  each ticked where its content can be read, and a rubric picker over `shipped_packs()` with
  `uccs-oscqr-3-1-3` selected. `POST /runs` re-checks, calls `create_batch_run` and
  `BackgroundRunner.submit`, and redirects to the run's page. The picker's options are
  `sightline.new_run.RubricChoice` values, which is where item 22 part 2 attaches.
- **Refusals,** each naming Settings or the run in the way: no Canvas credential, no model host,
  no model chosen, and a run that is running, meaning one of `active_run_ids` or one the store
  records as `running` (`RunStore.running_runs`). It also refuses a non-id, an unknown rubric, no
  course chosen, and the cap (`BatchTooLargeError`'s own message).
- **Run progress** is `sightline.run_screens`: `/runs` and `/runs/{id}`, per course and per stage
  with each stage's examined count in its own unit and its statement. It shows a failed stage's
  summary and remedy, the run's own failures from its log, and the run log. Each course with a
  result links to its review; each course nobody has opened shows a closed export with the gate's
  reason. An interrupted run names the resume command and has no button.
- **The live view** is an optional script that polls the API and reloads on change.
- **Calls made:** the stored stage name `score` is shown by its title, never as the word. The
  Canvas credential is a start blocker as well as the model host, because acquisition aborts
  without it exactly as extract does.
- **Not built:** item 22 part 2, anything of item 40, a resume button, a comparison view, and
  export-wait progress inside a run (a batch passes no export-progress callback).
- **Found and filed:** items 42 and 43.
- **Evidence:** 22 tests in `tests/test_run_screens.py`. One starts a run from the screen through
  `live_pipeline_factory` against the recorded `course-acquisition` Canvas fixture and the recorded
  model host. Screenshot QA at 1400 and 390 px read `scrollWidth == innerWidth` on all 18
  measurements.

*Why it was blocked:* the run screen's reason to exist is taking a designer from a finished course
to its review. That target is item 16's, which was NEEDS DISCUSSION until 2026-10-29. Nothing else
was open. The entry below is the contract it was built to, unchanged.

*What exists to build on (at `a76181c`):*
- **Execution:** `sightline.runner.BackgroundRunner`, wired in `sightline.app`'s lifespan to
  `BatchRunner.handle`. It runs one asyncio task per run in the app process, and a restart recovers
  and resumes. `submit(run_id)` has no HTTP caller yet.
- **Starting a run:** `sightline.batch.create_batch_run(store, references, rubric_pack=, cap=)`,
  which enforces the 16-course cap (`BatchTooLargeError`).
- **Progress:** `sightline.batch.progress_of(store, run_id)`, already served as
  `GET /api/runs/{run_id}`, and the same data `python -m sightline.batch status` prints — including
  each course's opened / not-opened export state.
- **Course resolution:** `GET/POST /courses` (paste or CSV, looked up in Canvas; item 02).
- **Rubric choice:** `sightline.rubric.shipped.shipped_packs()`.
- **Preferences:** model host and model, `PreferenceStore`.

*Build:*
1. **New run.** Extend the `/courses` flow rather than add a parallel one. Accepted courses, plus
   a rubric picker defaulting to `uccs-oscqr-3-1-3`, go to Start, which calls
   `create_batch_run` → `BackgroundRunner.submit`.
   - Before starting, show whether a model host and model are set. The extract stage aborts a run
     without them, so refuse to start and name the Settings screen.
   - **Refuse to start while `BackgroundRunner.active_run_ids()` is non-empty**, and name the
     active run. Two batches would compete for one model host; `runner.py` gives that reason for
     resuming serially. A run started from the CLI is another process, and shows only as `running`
     in the store — show it the same way.
   - Bundles stay on the CLI. SPEC §09's New run takes course URLs, ids or a CSV.
2. **Run progress.**
   - A list of recent runs (`RunStore.list_runs`), and a page per run.
   - Per course and per stage: what each stage examined and its statement, never a percentage. Every
     failure and its remedy are shown.
   - The live log is the page re-read on reload, as the model-install progress already is. An
     optional script polls `GET /api/runs/{run_id}`.
   - Each reviewed course links to item 16's review.
   - A closed export control, with the gate's reason, sits beside each course nobody has opened.
   - An interrupted run is shown as interrupted, with `python -m sightline.batch resume <id>` as the
     remedy. No resume button: the app already resumes what it was running when it restarts.
3. **Correct the docstring** of `GET /api/runs/{run_id}` in `sightline/views.py`, which still says
   the screen is part of item 16.

*Traps:*
- no progress bar or completion percentage — a count of courses reviewed is a count;
- `partial` must read as "Completed with failures" (`STATUS_LABELS`);
- nothing logs a course reference (item 17);
- starting a run from the screen requests a Canvas content export (item 23) — in tests and QA, use
  the recorded-fixture transport only, and never a real course;
- item 22's binding-confirmation screen attaches to the rubric picker later; it is not built here.

*Acceptance:*
- A run is started from the screen against recorded fixtures, and its progress renders per course
  and per stage.
- A second start is refused while one is active.
- Start is refused without a model host.
- A failed stage renders its remedy.
- Every reviewed course links to its review, and every unopened one shows the closed export with its
  reason.
- Screenshot QA is done at 1400 px and 390 px, with `scrollWidth == innerWidth` measured.
- The full suite is green, ruff and black exit codes are read separately, and all CI jobs are green.

**24 · DONE 2026-10-29 (PR #29, then PR #45). How often two reviewers choose the same rating, and
the near-boundary marker, on every surface a human meets.**

*Part 2, 2026-10-29 (PR #45), the re-run the operator decision below asked for.* Its gate held:
12a (PR #20), 14 (PR #31) and 16 (PR #42) were DONE on `main`. Part 1 (below) was all on `main`
and none of it was redone; this run rendered it.
- **The PDF.** The *what this document is* block carries `brief()`, then — only in a report where
  some row carries a marker — `near_boundary_legend()`, the marker's one explanation. Both sit
  before the reviewer's-rating paragraph (item 16, Q2), so neither reads as describing a rating
  somebody recorded. A marked row carries 12a's persisted marker: a *Near a band threshold* field
  after the proposal's *Estimated effort* in a row in full, and a `Near a band threshold: …` fact
  after the proposed reading in a compact line, before any reviewer's-rating fact.
- **The review screen.** The marker is a field after *Estimated effort*. Every row closes with
  `brief()`, collapsed under *How often two human reviewers choose the same rating*. It sits after
  the decision block, outside it: near the rating controls, but not a banner and not under the
  *Reviewer's rating* heading. On a marked row the collapsed statement also carries the legend.
  There is no new grouping, filter or sort.
- **The marker is rendered, never recomputed.** Tests tamper a persisted marker and see the tampered
  distance on both surfaces. It reads as hours, a distance, a threshold and a margin. It has no
  percentage, star, colour or hedge word.
- **The guard, on the surfaces.** The PDF's register reading now runs sentence by sentence, because
  `self_assessment` is a per-sentence rule. Read as one blob, the human figures sat "beside" every
  mention of Sightline in the document. The reading also plants "Sightline agrees with reviewers
  91% of the time" and fires. The screen test plants the four forbidden constructions.
- **Tests replaced:** items 14's and 16's "item 24 is not rendered yet" absence tests became
  presence and placement tests. Item 16's field-prefix check now stops at *Proposed rating*.
- **The sample report** was regenerated by `scripts/make-sample-report.sh`.
- **Screenshot QA**, 1400 px and 390 px: the index, ACD, MOB and ENIN, plus the expanded statements
  on ACD 1 and MOB 8. `scrollWidth == innerWidth` on all 12.
- **Two bank entries:** `near_boundary_fact` and `agreement_heading`.
- *Noted, not changed:* `near_boundary_legend()` says the lowest margin is wider. That holds for the
  shipped table (0.25 h against 0.1 h), but an effort override file could set the lowest margin
  equal to or below the others, and the sentence would then be false. It needs a guard if
  overrides with other margins are ever used.

*Part 1, 2026-09-29 (PR #29), queue entry `99g-`: one data file, the guard, the README. The PDF and
review-screen halves waited for those surfaces.*

*Why only part of it.* The item annotates the PDF (item 14) and the review screen (item 16), and
when it ran neither was on `main` (both still staged). **Operator decision, 2026-09-25 (bitmap,
interactive): re-run item 24 after item 16 lands, gated on 14 and 16 being on `main`, finishing
whatever this run left undone.** So this run built everything that does not need those surfaces,
and built it so the re-run only has to call it.

*What shipped.*
- `src/sightline/voice/agreement.yaml` — the pairwise human-agreement figures, **the only place
  they live**, with the corpus size, scope, provenance and the three limits (small sample; too few
  Major Revision ratings to say anything about that boundary; the pairs are different pairings,
  one of them the same role twice on a later instrument revision). The loader refuses a file that
  drops a limit, a key nobody decided to publish (a rating distribution is refused by name in the
  tests), and inconsistent counts.
- `sightline.voice.agreement` — templated sentences from that data and `phrases.yaml`, no model:
  `brief()` (the two-sentence form **for the PDF's *what this document is* block and the review
  screen**), `full()` / `markdown()` (the long form; the README block must equal `markdown()`), and
  `near_boundary_legend()` (the marker's one explanation, margins in hours, never a percentage).
  A template that the data stops supporting ("every disagreement was one band") raises rather than
  renders.
- The register lint's **`self_assessment` rule**, every tier: accuracy / confidence / reliability /
  "unsure" vocabulary in Sightline's own words, and **any percentage in a sentence that names
  Sightline or its output** — read across spans, because every figure is inserted as a span.
  `tests/test_agreement.py` plants the forbidden constructions in every register and proves it
  fires, with correctly worded controls that must pass.
- A test that no figure from the data is typed anywhere in `src/`, `docs/SPEC.md`,
  `docs/RUBRIC-PACKS.md`, `examples/` or the README outside its rendered block. The prose in
  `effort.py` and `costs.yaml` that hand-typed them now points at the data file.
- `workbook.py` says why the workbook carries no agreement statement. The per-row near-boundary
  marker is already in its own column (item 13).

*What the re-run does.* Render `brief()` in item 14's *what this document is* block and near
item 16's rating controls; render 12a's persisted `near_boundary` on the affected criterion in both
(no percentage, star, colour ramp or hedge word), explained once by `near_boundary_legend()`; lint
it; screenshot-QA the review screen at 1400 px and 390 px; keep 14's byte-identical PDF test green.
None of that needs new wording or new data.

**13 · DONE 2026-09-29 (PR #28). The workbook: `sightline.workbook`, every row of the pack, and
every cell text.**

*What shipped.* `build_workbook(projection, pack, base_url=, created=, report=, extractions=)`
returns a table model; `to_xlsx(model)` returns bytes; `export_workbook(gate, course_id, data)`
writes them through the new `ExportGate.save`. Never a path handed to a library: openpyxl's
`Workbook.save(path)` is not a call `tests/test_paths.py` can see. Two sheets. `Criteria`: one row
per rendered criterion in the pack's own order — Category, Code, Criterion, Priority,
Determinacy, Coverage, Proposed rating, Estimated effort, Near a band threshold, Evidence, Action
plan. `Action Plan`: regenerated, one line per located observation, quote or question with its
Canvas link, in `Projection.action_plan_order()`; a row with nothing located gets one line (its own
first sentence), so no criterion is dropped, including the null-priority rows.

*Decisions this session took, each small and each stated so it can be reversed.*
- **Every cell is text.** Text is what round-trips through CSV exactly, and a numeric column is the
  one somebody `=AVERAGE()`s. The effort figure reads "0.1 h, an estimate from the uncalibrated
  placeholder cost table". Cells are pinned to the string type, so course text starting with `=`
  is never a formula.
- **Two visible normalisations.** CR → LF (XML parsing turns `\r\n` into `\n` on read, so a CR
  cannot survive an `.xlsx`), and XML-illegal control characters → U+FFFD. Both happen in the model,
  where the round-trip test sees them; nothing is lost silently inside the zip.
- **Headers, sheet names and categorical values are bank `labels`** (29 entries in `phrases.yaml`,
  contract `bank.LABELS`, linted at load), plus four new `formats` (the effort and near-boundary
  cells, the overflow pointer). No second phrase bank; the register lint is unchanged.
- **Evidence vs Action plan.** Evidence = every rendered sentence except the per-finding lines;
  Action plan = the finding, quote and question lines. A Criteria cell that would pass Excel's
  32,767-character limit keeps whole lines and says how many more are on the Action Plan sheet,
  which carries all of them.
- **Document properties are rewritten after openpyxl saves** (it stamps `modified` with the clock
  and names itself as the application): no creator, no application, `created` = `modified` = a
  timestamp the caller passes (tz-aware, stored UTC). Zip entry times are pinned to 1980-01-01.
  The bar stays cell-by-cell, as the item asked; bytes happen to be stable too but are not tested.

*Checked.* The CSV round-trip runs on the sample course with one Tier A detector's findings
rewritten into hostile text (line breaks, commas, quotes, non-ASCII, emoji, a leading `=`, CRLF,
a vertical tab), and a self-test fails if the fixture ever stops carrying them. Both guards were
fired by mutation: disabling the CR normalisation failed the round-trip, and un-pinning the string
type failed the formula test.

*Not done here.* No sample workbook in `examples/sample-report/` (the 2026-09-25 decision puts it
behind a `scripts/` generator through the gate; item 14's re-spec owns it — item 14 shipped it).
No caller wired `export_workbook` into a screen or CLI; item 14's `python -m sightline.report
export` now does, after the human action that opens the gate. The action-plan column holds located items rather than remediation advice; that
gap against SPEC §08 is item 35.

**35 · FILED 2026-09-29 by item 13's session. NEEDS DISCUSSION. SPEC §08 says the workbook's
action-plan column is "filled with remediation steps"; nothing in the product can write one without
breaking §01.**

*Evidence.* No field anywhere carries remediation wording: `Finding` has `observed` and `evidence`
(values, by design no sentence), `Undetermined.remedy` is about unreadable files, and the phrase
bank has no remediation shapes. The instrument's own `Action Plan` column (`Instructional
Designer!X`) is a free-text field the reviewer writes. §01 allows a machine-authored line to be a
count, a verbatim quote with a location, or a statement that nothing was found — "add alt text to
the image on page X" is none of those.

*What item 13 shipped instead.* The Action plan column and sheet carry the located items — each
observation, quote or question, with where it is and a link. That is what to act on, in the voice
layer's words, and nothing more.

*The decision.* Either (a) SPEC §08 is corrected to say the column holds the located items and the
reviewer writes the plan, or (b) the bank gains a remediation shape per finding code (about 90
codes), each a fixed instruction reviewed like the rest of the voice, and §01 is amended to allow
it. (b) is a content-authoring job and a change to the governing rule; not something a build
session decides.

*Decided 2026-10-29 (operator, interactive): **(a).** SPEC §08 now says the action-plan column
holds the located items and the reviewer writes the plan. Nothing in the code changes; item 13
already built it that way. DONE in the PR that recorded the decision.*

**33 · MEASURED 2026-09-28 (PR #26). Outcome (d): nothing changes. Every way of making fewer calls
lost evidence the current behaviour finds, and on this host the calls were not the cost anyway.**

*Setup.* `scripts/measure_row_extraction.py` (committed; `selftest` and every report check the
comparator in both directions first). Target host, Ollama 0.34.2, **`qwen3-coder:30b` digest
`06c1097efce0`** for every arm, thinking not sent (the model has no `thinking` capability),
`num_ctx` 8192. Synthetic sample course only: 15 passages, 967 words. **Recall** is per row against
a baseline run: a baseline quote is recalled when the arm returned, for the same row, a verified
quote in the same passage whose source span overlaps it. The baseline is `answer_requests` as
shipped, **run twice**, and each arm is scored against both runs. Several rows per call and
passage-major order were run; the two narrowing arms were **simulated from the baseline runs**,
which is exact here because each baseline call sees one row and one passage, so skipping a passage
removes that call and changes no other.

*RSI pack, 12 routed rows × 15 passages.* Missed = baseline quotes not recalled, against run 1
(89 quotes) / run 2 (97 quotes). Extra = the arm's quotes that overlap nothing in run 1.

| Arm | Calls | Wall s | Quotes | Missed | Rows losing | Rows emptied | Extra |
|---|---|---|---|---|---|---|---|
| baseline run 1 | 180 | 220 | 89 | — / 0 | 0 | 0 | — |
| baseline run 2 | 180 | 202 | 97 | 0 / — | 0 | 0 | 0 |
| passage-major (same prompts) | 180 | 213 | 101 | 0 / 0 | 0 | 0 | 11 |
| 2 rows per call | 90 | 260 | 111 | 33 / 33 | 4 | 0 | 60 |
| 4 rows per call | 45 | 201 | 66 | 58 / 66 | 4 | 0 | 44 |
| 6 rows per call | 30 | 170 | 56 | 57 / 65 | 6 | 0 | 34 |
| 12 rows per call | 15 | 123 | 28 | 62 / 70 | 6 | 0 | 8 |
| lexical ≥ 1 shared term (sim.) | 49 | 65 | — | 54 / 54 | 4 | 2 | — |
| lexical ≥ 2 shared terms (sim.) | 11 | 31 | — | 61 / 61 | 7 | 4 | — |
| embedding top 3 of 15 (sim.) | 36 | 61 | — | 46 / 54 | 6 | 2 | — |
| embedding top 5 of 15 (sim.) | 60 | 99 | — | 23 / 23 | 6 | 1 | — |
| embedding top 8 of 15 (sim.) | 96 | 144 | — | 15 / 15 | 5 | 1 | — |
| embedding top 10 of 15 (sim.) | 120 | 168 | — | 10 / 10 | 3 | 0 | — |

A simulated arm's wall-clock is the sum of the kept calls' measured latencies in run 1. Embedding
top 12 of 15 (80 % of calls) still loses 7, all on rows 4 and 15; lexical ≥ 1 loses **every** one
of row 15's 40 quotes and all 7 of row 16's.

*Reference pack, 47 routed rows × 15 passages.* Against run 1 (280 quotes) / run 2 (271).

| Arm | Calls | Wall s | Quotes | Missed | Rows losing | Rows emptied | Extra |
|---|---|---|---|---|---|---|---|
| baseline run 1 | 705 | 720 | 280 | — / 1 | — / 1 | 0 | — |
| baseline run 2 | 705 | 685 | 271 | 11 / — | 3 / — | 0 | 1 |
| passage-major (same prompts) | 705 | 717 | 292 | 11 / 5 | 5 / 4 | 1 | 21 |
| 2 rows per call | 360 | 958 | 348 | 138 / 129 | 15 | 0 | 247 |
| lexical ≥ 1 (sim.) | 207 | 250 | — | 145 / 144 | 17 | 8 | — |
| lexical ≥ 2 (sim.) | 45 | 90 | — | 193 / 184 | 27 | 18 | — |
| embedding top 3 of 15 (sim.) | 141 | 214 | — | 132 / 131 | 23 | 10 | — |
| embedding top 5 of 15 (sim.) | 235 | 336 | — | 58 / 58 | 20 | 5 | — |
| embedding top 8 of 15 (sim.) | 376 | 459 | — | 35 / 37 | 17 | 5 | — |
| embedding top 10 of 15 (sim.) | 470 | 538 | — | 26 / 28 | 13 | 3 | — |

**The noise floor.** On the RSI pack two identical runs recalled each other completely (0 misses
at overlap; 4 and 12 at exact span — they split the same passages differently). On the reference
pack they did not: 11 misses on 3 rows (ASFE 3, ENIN 5, GSOV 6) one way, 1 the other, and
passage-major — the baseline's own prompts in another order — reached 11 misses on 5 rows and
emptied one row. So the floor is **up to about 11 misses on up to 5 rows, and it can empty a row**.
Every call-cutting arm is well above it; the closest, embedding top 10, loses 26–28 on 13 rows and
empties 3.

**Why fewer calls did not mean less time.** A least-squares fit over the RSI baseline calls
(first call excluded; it is a 9.7 s cold load): about **0.2–0.3 s fixed, 0.2–0.6 ms per prompt
token, and 25 ms per generated token**, the last stable across all six runs. An empty answer costs
0.60–0.65 s; the baseline's 5.3 k generated tokens are about 135 s of its ~210. Several rows per
call generates more (8.6 k tokens at two rows per call: quotes repeated under two codes, plus
misattributed ones), which is why it was slower. **Passage-major order** is the one lever that
cannot lose evidence, and it bought nothing measurable on these passages (213 s against 202–220 s).
At the full 900-word budget, on five synthetic passages cut from the sample's text, it saved 11 s of
174 s (prompt evaluation 26.1 s → 24.2 s): the host reuses an identical prefix (control: 1.68 s of
prompt evaluation, then 0.03 s) and already appears to hold several passages' prefixes at once, so
row-major order was mostly cached too. **Not determined:** whether that holds when a course has
more passages than the host keeps cached. A 15-passage run did not finish — one call exceeded the
probe's 300 s timeout and was not investigated. The saving is bounded by prompt evaluation's share
of a call, 15 % in the five-passage run. Deferred, not queued: it would not change this item's
answer, only whether a recall-neutral reordering is worth building.

**What a real course costs is arithmetic, not a measurement.** The routed rows cost 47 × P calls
against the reference pack. An empty answer on a full 900-word passage paid 0.41–1.68 s of fresh
prompt evaluation in the probe's controls, plus the ~0.6 s fixed cost. So a 100-passage course is
roughly 4 700 calls at 1–2.3 s, **about 1.3–3 hours before any quote is generated**. No real course
was read, and the passage count is the unknown that matters most.

**Taken: (d).** Keep the full corpus and one row per call, and keep printing the price. The printed
estimate matched the calls made on all four baseline runs and both passage-major runs (the
instrument refuses to write a run where it does not). The lever is the reviewer's: fewer routed
rows (a pack with bindings routes fewer, item 22) or fewer courses per batch. SPEC §06 carries the
design consequence. **Other models were not measured.** Items 10/11/17 measured `llama3.1:8b`,
which is no longer installed. Its 7.5 s a call against this model's 1.0–1.2 s means the wall-clock
figures above describe this model only.

**30 · DONE 2026-09-28 (PR #24). One parse per body per run, shared through the Survey.**
Measured before it was built, with `scripts/measure_parse_cost.py` (committed; it self-tests in
both directions before it reports). On a synthetic course of 281 bodies of 8.6 KB — the sample's
bodies concatenated and repeated with distinct ids — the detector stage made 3,696 `scan()` calls
(13.2 per body) and spent 6.4 s of its 7.2 s inside them. The parse was nine tenths of the stage,
so the saving was worth the framework change. It now makes 281 calls, one per body, and takes 1.2 s.
Findings over the sample course are byte-identical before and after.

**What later items inherit.** `run_detectors` makes one `Parses` per run and hands it to every
`Survey`. A detector reaches a tree with `survey.parse(body)` or `survey.parse(artifact)`, which
**refuses any artifact that survey did not hand out**, so the shared parses are never a route to
uncounted markup. Each detector keeps its own `Survey` and its own examined count. The cache is
keyed on id *and* markup, and lives exactly as long as the run: there is no module-level cache,
so course text does not outlive the review. A new HTML detector reads `self.pages(survey)` or
`survey.parse(...)`, never `scan(artifact.body_html)` directly; a direct call still works, but it
re-parses. `Element.attrs` is now a read-only mapping, because a shared tree must not be editable by
the detector holding it. `extract/passages.py` still parses for itself (a different stage, not
measured here). `structure.has_heading()` has no caller, and it was left alone.

**12a · DONE 2026-09-25 (PR #20). The effort model: detector-keyed costs, a near-boundary marker,
and the override record.**

**The cost key is the detector id, not the severity.** Item 04 keyed its throwaway table on
severity to avoid a parameter per finding code. Severity describes the learner's experience; cost
describes the work, and the two diverge by an order of magnitude inside one severity (an alt
attribute versus a caption file). The registry is enumerable before a course is read, so an
unpriced detector can be reported on the pack; finding codes are not declared anywhere. Thirty-seven
entries, two round values (0.05 h, 0.1 h), every line marked uncalibrated, in
`src/sightline/rubric/costs.yaml`. `SIGHTLINE_EFFORT_COSTS` overrides entry by entry. **12b may
find a detector is too coarse** — D01 prices "no syllabus" and "syllabus without headings" alike —
and the answer then is a per-code entry, not a per-row policy.

**What later items inherit.**

- **`RowResult.near_boundary`** is persisted in the review file's row dictionary: `below`,
  `above`, `threshold_hours`, `hours`, `distance_hours`, `margin_hours`, `lowest`. Items 13, 14, 16
  render it or ignore it; **item 24 surfaces it**. It is a distance, never a confidence, and
  Sufficiently Present rows carry none. Margins: 0.25 h at the lowest boundary, 0.1 h between bands.
- **`rating_overrides`** is a new table (from `create_all`, no `ADDED_COLUMNS` entry needed) and
  `sightline.rubric.overrides.record_override` is the write path item 16 calls.
  `MachineProposal.from_review(review["projection"], code)` builds the machine side from the
  persisted review file. Append-only: the store has no update or delete for it.
- **The rollup no longer carries `hours`.** Item 17 shipped a course-level total of hours in the
  score stage; it was the aggregate §11 forbids with a clock face on it, and 12a removed it.
  `rows_with_hours` (a count of rows) remains. `tests/test_rubric_effort.py` reads the effort path's
  code for the arithmetic that would bring it back.
- **`Band` refuses a rating outside minor/moderate/major**, so no banding arithmetic can produce
  Sufficiently Present — by construction, not only by the schema.
- **`_rate`'s zero-cost branch is now unreachable** (a zero cost is refused at load) and was left
  in place for item 28, which owns `_rate` next.
- **The Major-is-hard property** holds the shipped table to "no row of the reference pack reaches
  Major on fewer than twenty findings from its costliest detector". 12b's fitted table must pass it
  or argue it down with the corpus in hand.

**17 · DONE 2026-09-17 (PR #18). The batch runner, and the export gate that landed with it.**

Five stages a run performs unattended — acquire, detect, project, extract, score — with the *loop*
and the *work* in separate modules. `sightline.batch.runner` owns ordering, per-course isolation,
resume, the events every progress surface reads and the file a reviewer opens;
`sightline.batch.pipeline` owns the stages. What that buys is a suite: isolation and resume are
proved against a scripted pipeline, and the real pipeline is proved against a course bundle and
item 09's recorded host, so neither test needs Canvas or a GPU.

**Project runs before extract, and SPEC §04's table is corrected rather than quietly contradicted.**
The routing contract decides which rows a model is asked about from each row's coverage, which does
not exist until the detection report has been projected onto the pack. A test fires the dependency:
extracting before projecting has nothing to ask about.

**What later items inherit.**

- **`partial` is a run status.** Some courses reviewed, others failed. It exists because the other
  two readings both lie — `succeeded` over a batch with a failure reads as done at a glance, and
  `failed` discards two finished reviews. `STATUS_LABELS` in `sightline.models` is what a screen
  renders, so "Completed with failures" has one home.
- **A course failing is isolated; the batch's shared dependency failing is not.** An unreachable
  model host, a missing credential, a pack that is not on disk: every remaining course would fail
  identically one timeout at a time, so the batch stops, the run is left `interrupted` — resumable,
  nothing known to be wrong with the courses — and the summary names what never ran. This is the
  same split item 10 made inside one course, applied to a run.
- **Resume is per course and it is never silent.** A finished course is reported as carried over; a
  course that was mid-flight restarts at its first stage with the log recording which stage the
  earlier attempt stopped in; a course that failed stays failed until the run is started afresh.
- **Zero examined fails the stage**, enforced in the loop rather than by each stage remembering to.
  Item 06's invariant at stage scale.
- **The export gate is `sightline.batch.gate`, and it is item 17's, not item 14's.** The roadmap
  expected the PDF report to build it; the batch runner is the thing that could export unattended,
  so it landed here and items 13/14/16 write *through* it. `RunCourse.review_opened_at` is the
  gate, only a human action sets it, and `tests/test_export_gate.py` scans the package's syntax
  tree: no module but `paths.py` and `gate.py` may name `reports`. A completed run leaves that
  directory empty and a test asserts it.
- **The store's one migration mechanism is `ADDED_COLUMNS`**, applied when the store is opened.
  `create_all` never alters a table it did not make, so a column a later item adds is simply absent
  from a data root somebody already has, and the symptom is `no such column` at runtime in front of
  them. Additive columns only — a rename or a backfill is deliberately not expressible there.
- **Nothing here logs a course reference.** A Canvas course URL carries the host, which SPEC §11
  treats as a secret; log calls key on the course id and the ordinal, stored events are scrubbed,
  and a test fails the build if a reference reaches a log line.
- **Document bytes reach the detectors (item 26, PR #21).** The live factory passes
  `data_paths=paths` and the detect stage builds each course's source from the export its own
  document names; the `files=None` this bullet used to describe is gone.

**33 · FILED 2026-09-17 by item 17's session. A routed row costs one call per passage of the
course, and nothing bounds the product.** Measured over the shipped sample course — 43 artifacts,
15 passages — the routed rows cost about **180** model calls against the RSI pack and about **705**
against the reference instrument, plus the ten topic extractors that answer a course once. At a few
seconds a call that is roughly an hour for one course, so the cap of sixteen is a ceiling on a
day-long wait. Item 04 stated the price plainly, so this is not a surprise; it is the point at which
the number has been measured against a real pack and has to be decided about. **Nothing is wrong
today — the output is correct and slow**, and every way of making it faster trades calls for the
risk of not asking about the passage that held the evidence, which is the silent under-detection
§11 exists to prevent. So the item is **NEEDS MEASUREMENT** with four candidates and none
pre-selected (many rows per call, a deterministic pre-filter, embeddings, or nothing), recall
measured against current behaviour as ground truth, and a hard condition on any of them: a row
whose corpus was narrowed must say how much of the course it read. Sorts after every pending item —
it is slow, not wrong, and the project stage now prints the estimate before anything is spent.

**11 · DONE 2026-09-17 (PR #17). Three runs, compared on the spans, and the cost measured twice
rather than once.**

- **What `--runs 3` costs, from two independent three-run passes** over the synthetic sample course
  on `llama3.1:8b`, target host, 2026-09-17: **381 calls in 2 859 s** (19:21Z) and **381 calls in
  2 865 s** (21:56Z), against **127 calls in 953 s** for a single pass. 7.50 s a call in every one
  of them. Concurrency is 1 by design, so nothing amortizes and three runs cost 3.0× — the
  arithmetic is the whole story, which is why the default is one run and the router is asked for.
- **The two passes returned the same evidence, span for span.** 226 quotes over ten extractors,
  the same per-extractor distribution, and an identical 160 998 total tokens. **Neither pass
  flagged anything**: `0 subject(s) flagged for attention over 3 run(s)`, both times. A separate
  single pass 48 minutes after the first returned the same 226 quotes again.
- **That is a measurement of one model on one synthetic course, not a general claim** — and it is
  the honest number to weigh `--runs 3` against. Three times the calls bought no disagreement here.
  Zero variance is exactly what SPEC §01 predicts of an agreeable model on prompts it finds easy,
  which is why agreement is recorded nowhere in the output: the router reports difference, and the
  absence of difference is not evidence of correctness.
- **The figures were re-measured before the merge rather than carried over from the session that
  wrote them.** The first pass belonged to an attempt that ran out of session before it could
  publish; every number above was recomputed from the second run's own JSON.
- **The re-measurement found a defect nothing in the suite could: `--save-disagreements` checked
  its destination only after the run.** The confirming pass spent all 381 calls and 48 minutes and
  then died with an uncaught `PermissionError` about `mkdir`, because it had been pointed at a data
  root that exists inside the container and not on the host. The result survived by luck — the JSON
  had already gone to stdout. Fixed in **PR #19**: `DataPaths.ensure_writable()` proves the root by
  creating and removing a file *before* the first call is made, and the write at the end is now
  handled rather than raised, because a root that was writable an hour ago can be full by now. The
  proof is a real file and not `os.access`, since the configurations worth catching — a read-only
  mount, an unmounted container path — are exactly where the permission bits and the outcome
  disagree.

**10 · DONE 2026-09-17 (PR #15). The extraction layer: ten extractors, one schema, and the
string match that stands between a model and a fabricated quote.**

- **The accepted quote is the SOURCE's text, not the model's string.** Matching happens in
  normalized space — entities, typography, whitespace and case, each a fold of the same characters
  — and what is sliced out and rendered is the document's own bytes at the match. That is what
  makes those folds safe to have at all: a model that straightened a curly apostrophe does not get
  its version shown, and a reviewer can search the page for what they are handed. Nothing
  approximate is rescued, and an **ellipsis is a splice** — two real halves joined by one can
  assert what neither says, so it is discarded like any other non-matching quote.
- **The location is computed, not believed.** The schema requires the model to say where it looked
  and that string is never rendered: three models on the target host answered the same question
  about the same page with `"Course policies (page)"`, `"page"` and `"Week 2 overview"`. The
  artifact comes from the harness — **one artifact per call**, so a page can never be invented —
  the line from the block the verified quote landed in, and the heading from above it.
- **Verification catches invention, not irrelevance, and the README says so.** Measured on
  `llama3.1:8b` over the sample course: E09 verified **16** quotes and discarded **1**, and only
  about four of the sixteen stated a policy about grades or late work — the rest were real
  sentences about something else. A verified quote is a quote that *exists*, not a quote that
  bears on the question, which is exactly why a reviewer is handed the quote and its location
  rather than a rating. That discard also means the fabrication guard has now fired on live output
  and not only on a fixture.
- **Four outcomes are kept apart in the data and in the sentence**: *no evidence found* (sent,
  answered, verified, nothing bears on it), *nothing was examined*, *none carried any text*, and
  *could not be read*. The last is never worded as the first. A schema violation fired on the live
  host without being planted — a model returned an empty `quote` — and was recorded as a failure
  rather than coerced into an absence.
- **Split, never truncate.** A body over the word budget becomes several passages with a call each.
  The block rendering checks itself against the scanner's word count and rebuilds from the whole
  body rather than lose a word it cannot place — a passage nobody sent is a criterion nobody could
  answer, with no error anywhere.
- **The thinking pin goes ON where the model has the capability**, from item 09's measurement: with
  it off, a thinking model's reasoning arrives inside `message.content`, which is what this parses.
  **Ollama's top-level `format` takes the response schema** and constrains generation to it,
  verified end to end against the live host; `OllamaClient.chat` gained `response_format` for it.
  It is top-level and not one of `options` — in `options` the host accepts the request and ignores
  it. The response is validated regardless, because a constraint the host promises is not one this
  code checked.
- **Nothing retries.** A second sample at the same temperature is a second answer to the same
  question. Item 11 is a router over three runs and reports the disagreement; it is not a
  validator (§01).
- **E01 is the worked example and the pattern for the rest.** The model extracts the sentence, the
  verb table takes the task verb from the **verified** quote in code, the distribution is
  arithmetic. A verb the table does not carry is reported as *no table verb*. Tier C extractors
  draft a question and never answer it, asserted by a lint over every shipped string; a second
  guard reads SPEC §05's own table out of `docs/SPEC.md` and checks the registered tiers against
  it, so the inventory and the spec cannot drift apart silently.
- **Both models were run end to end against the live host, and they fail in opposite directions.**
  `gemma4:e4b` on E01 over the sample course: 15 of 15 passages answered, **5 quotes verified, 0
  discarded, 0 failures**, 316 s and 5 603 tokens — 21 s a call over fifteen short pages, and every
  quote genuinely a statement of what a learner must do. `llama3.1:8b` on E09 over ten of the same
  course's artifacts: 16 verified and 1 discarded at **7.5 s a call** — a third of the time for
  three times as many quotes, and only about four of the sixteen on topic. Precision is a property
  of the model, not of the harness; **the harness's job is to make every quote checkable**, and
  item 11's router is where the disagreement between two of them becomes information.
- **The host is not deterministic at `temperature: 0`, measured here.** Two identical E09 runs on
  `llama3.1:8b` over the same course returned 18 verified with 1 schema failure, then 16 verified
  with 1 discard. **Item 11 must expect run-to-run variance from the host itself**, not only
  disagreement between models — and nothing may read a difference between two runs as a difference
  between two prompts.
- **Four of E01's five verified sentences carried no verb the Bloom table knows** — *Read*,
  *Watch*, *Post*, *reply* — and were reported as *no table verb* rather than forced into a level.
  That is the designed behaviour and it is visible in the output, but it is also a measurement
  worth carrying: on this course the table classified one statement in five. Whether the table
  needs activity verbs beside cognitive ones is a question for item 12's calibration, and it is not
  answered by adding *read* to a level it does not belong to.
- **`answer_request()` implements the routing contract** and is the only thing in the layer that
  knows a criterion exists — because the row hands over its own text. It costs one call per passage
  per row, which is the honest price of an instrument Sightline ships no mapping for, and it is why
  the ten topic extractors exist beside it: they answer a course once, it answers a row.
- **Two defects outside the item, both fixed in its PR.** The integrity guard's register lint
  searched for `pass` as a *substring* of a statement that names the test modules it observed, so a
  file called `test_extract_passages.py` read as a verdict; it matches whole words now. And the
  coverage ledger was **calling async test functions**, which builds a coroutine, observes nothing
  and warns — an observation that looks like it happened. Both were found by running the suite
  rather than by reading it.

**07 · DONE 2026-09-17 (PR #11). The Canvas structure cluster D01–D20, and five things it
established that were not known before it ran.**

- **A structure detector's interesting answer is an absence, and an absence needs a location.**
  "This course has no syllabus" cannot be located on an artifact that does not exist. So
  `CourseDocument.course_artifact` was added: resolvable through `by_id`, **not** yielded by
  `artifacts()`. The asymmetry is load-bearing rather than tidy — the integrity guard's empty
  document must hold *zero* artifacts, and a course that contained itself would hold one.
  `Survey.course_location()` builds that location and hands out no artifact, so it counts nothing
  and a course-level detector cannot use it to fake an examined count.
- **D11 needed an endpoint nobody had acquired.** `outcome_group_links` was added to
  `INDEX_ENDPOINTS` (one call answers both which outcomes exist and which group each sits in), with
  the model type, the scope row and the sample-course outcomes to go with it. `SCHEMA_VERSION` was
  **not** bumped: the field is additive with a default, and the cache signature already moves
  because the collection set changed, so a stale document cannot survive the change.
- **SPEC §05 is optimistic about D11 and the gap is Canvas's, not the spec's.** §05 says "outcomes
  defined at course **and module** level". Canvas records no link between an outcome and a module —
  outcomes live in a tree of outcome *groups*, and alignments are to rubrics and assignments. D11
  establishes the course-level half and says in its own capability description that it does not
  infer the other. Closing the remaining half needs the alignment endpoints; it is not filed as an
  item because nothing depends on it yet.
- **Canvas exposes no content behind Collaborations, Chat, Attendance or Conferences.** D06 reports
  those menu entries as *unevidenced* — in those words — rather than as unused. Reporting them as
  unused would be a guess dressed as a count, and these four are default-on in Canvas, so it would
  be a guess made on nearly every course reviewed.
- **A graded discussion's due date is on the assignment behind it, never on the topic.** Reading
  `due_at` off the topic returns `None` for every graded discussion there has ever been, so D10 fed
  by that would report each one as undated. `Discussion.due_at` now normalizes from
  `assignment.due_at`.

**Link checking is off by default and that is a decision, not caution.** D13 is the only detector
that touches the network. `SIGHTLINE_RESOLVE_LINKS` defaults false and `--resolve-links` must be
typed; with it off, D13 reports which policy links are *present* and claims nothing about where
they go. Switched on it works inside a per-run budget, a short timeout, a minimum interval and a
cache that holds failures as well as successes. **Only a 404 or a 410 is reported as gone.** A 403
is a login wall, a 429 is a rate limit and a DNS timeout is a network, and all three are recorded
as *could not be read*. A working campus policy page reported broken because a lookup timed out is
the finding a faculty member remembers.

**29 · FILED 2026-09-17 by item 07's session. `Scan.text` fuses two words at every block seam, and
it is silent.** `sightline.detect.html_scan.scan()` builds `Scan.text` as `"".join(text_parts)`, so
`<h2>Course schedule</h2><p>Week 1</p>` reads back as `Course scheduleWeek 1`. Any word-boundary
regex over it fails at the seam and any word count over it is short by the number of seams.
**Found by it happening**: D04 reported a course with a schedule written into its syllabus as
having no schedule at all — wrong output, no error, and it took a failing fixture to see. Item 07
worked around it with a local `plain_text()` that puts a space where each tag was, so D01–D20 are
correct today. What is *not* fixed is `Scan.text` itself, which D26 and D29 read (and item 08's D35
will), and `Element.text`/`Element.own_text`, which have the same join. Queued at `07a-` because it
is silent wrongness in shipped code. Whoever takes it should expect word counts to move.

**DONE 2026-09-17 — PR #13.** The rule is `html_scan.INLINE_TAGS`, stated once: a separator goes
where the rendered page breaks one run of text from the next and nowhere else. So the block seam
separates and `<em>un</em>likely` stays one word — and the span soup a word processor pastes into
Canvas, `<span>Sch</span><span>edule</span>`, now reads `Schedule`, which a space-at-every-tag rule
would have split. An unknown tag separates on purpose: fusing invents a word that matches nothing,
separating leaves both halves findable.

**No word count moved anywhere the project can currently see, and that is the finding.** 1171 tests
passed before the change, and the 37 shipped detectors produce a byte-identical 64 findings over
`examples/sample-course` after it — because every fixture and every sample body is pretty-printed,
so whitespace already sits at every seam. The defect only appears in markup written the way an
editor writes it. **A sample course that is easier to read than real course HTML cannot catch this
class of bug**, which is worth knowing the next time a detector is proved on it. The numbers were
therefore demonstrated on markup that has the shape, against the pre-change scanner run over the
same fixtures: D26 read `...marked in redDue this week` across two table cells and `\bred\b` failed,
so a page telling a learner to look for a colour reported nothing — it now fires; D29 counted 59
words where a reader counts 60 and fired "more picture than prose" at two images, and now does not.
`WORDS_PER_IMAGE = 30` was **not** re-fitted; it always meant what it says and was being applied to
a short count, and a companion assertion pins 59 on the other side of the boundary.

Item 07's `plain_text()` was not retired. It now imports the same `INLINE_TAGS` instead of putting a
space at every tag, so the two helpers are one rule with two implementations, asserted over one
shared list of cases from both test files. What keeps it is the parse it avoids — which is item 30's
measurement, not this item's to pre-empt. The two places it still reads a tag worse than a parser
does are item 31.

**32 · FILED 2026-09-17 by item 10's session, which hit it. The test suite reads the
developer's `.env`, and the autouse fixture that promises it does not only clears environment
variables.** `Settings.model_config` sets `env_file=".env"`, so `Settings()` reads the repository's
own file whenever the working directory is the repository root — which is where the suite runs.
`tests/conftest.py`'s `_isolated_environment` clears every settings variable and states the
guarantee in as many words: *"Tests never read the developer's `.env` — a suite that picks up a
real Canvas URL is a suite that can leak one into an assertion message."* Clearing the environment
does not cover the file, so any test reaching `get_settings()` reads the real `CANVAS_BASE_URL` (a
secret by this project's own rule), the real token and the real `OLLAMA_BASE_URL`. Item 10's CLI
test for *"no model host is configured"* found the host that way and started calling the live LAN
model host, which is how it was found. **It is silent because it fails only where it matters:** CI
has no `.env` (`.dockerignore` keeps it out of the build context), so the guarantee holds exactly
where nobody needs it and fails exactly where the secret exists. Queued at `09a-`, ahead of the
rest, for that reason. Item 10 pinned its own module with a monkeypatched `get_settings` and the
item removes that workaround.

**DONE 2026-09-17 (PR #16). The sweep found a second hole, and the fix is one line in the
fixture that already promised the guarantee.** `_isolated_environment` now also neutralises
`env_file` on the settings class — `monkeypatch.setattr(Settings, "model_config", {...,
"env_file": None})` — so `Settings()` ignores the file wherever it is constructed, including by
the app factory and by every `python -m` entry point. Nothing in `sightline.config` changed:
reading `.env` is what it is for, and the defect was only ever the harness's claim about what it
isolates. Changing the working directory was rejected for the reason the item gave — the suite
resolves `examples/sample-course` and `docs/` from the repository root.

**The guard was shown failing before it was shown passing**, the item 21 discipline:
`tests/test_env_isolation.py` writes a `.env` carrying a reserved-TLD hostname into a directory it
then chdirs to, and against the unfixed fixture **4 of its 5 original tests failed**. The one that
passed is the one that should: the environment still reaches settings, which a fix that switched
off *both* sources would have broken silently. Proven at the suite level too, by the difference
between the two run paths: on the developer machine (a `.env` at the root) **1437 passed, 1
skipped**; in the container by `./scripts/dev.sh pytest -q` (no `.env` at all) **1436 passed, 2
skipped**. Same 1438 tests, and the single moved result is the one test *defined* to skip without
a `.env`. A file that changes no test result is what the item asked for.

**The sweep's finding: `SIGHTLINE_RUBRIC_PACKS_DIR` was never cleared either.**
`sightline.rubric.shipped` reads it with `os.environ.get` rather than through `Settings`, so the
hand-maintained `SETTING_ENV_VARS` list could not know about it — a developer with it exported
fails **43 tests** in `test_rubric_packs.py` alone, measured by exporting it. Loud rather than
silent, so a much smaller problem than the `.env` leak, but the same defect and the same fixture,
so it was fixed here rather than filed. What closes the *class*: a test now reads the validation
aliases off `Settings` and fails if one is missing from the cleared list, so the next setting
added cannot quietly escape isolation. Nothing else in the sweep: the only two working-directory
reads in the package (`assets.candidate_paths`, `shipped.candidate_paths`) each fall back to the
same file resolved from `__file__`, and `scripts/scan-secrets.sh` cds to the repository root
before it does anything.

**Worth recording, because it is what made this invisible for nine items.**
`test_canvas_exports.py::test_a_fixture_needs_a_token_or_it_skips` has asserted
`os.environ.get("CANVAS_API_TOKEN") is None` since item 02 — a guard aimed at exactly this, which
passed the whole time because it checked the *environment* and the leak was through a *file*. And
a test that checks for a leak must not print it: `assert settings.canvas_base_url is None` puts
the institution's hostname in the pytest output on failure, so every check that touches the real
values reduces to a bool first.

**31 · FILED 2026-09-17 by item 29's session. `plain_text()` reads a tag with a regular
expression, and two shapes of real course HTML defeat it — silently.** Found while checking item
29's claim that the string helper and the scanner now agree, case by case, rather than asserting it:
they agree on every seam, and they disagree in two places that have nothing to do with seams.
`_ANY_TAG` is `</?([a-zA-Z][^\s/>]*)?[^>]*>`, so **a `>` inside a quoted attribute value ends the
tag** — `<p class='a>b'>x</p>` reads as `b'>x` where the scanner reads `x`, putting markup into the
prose that D01–D20 match vocabulary against. And **an unclosed `<script>` leaves its code in the
prose**: `_SCRIPTISH` needs a closing tag to strip a block, so the scanner drops the code as CDATA
and this helper hands it to a detector as words. Both are wrong text with no error. The natural fix
is the one item 30 is already measuring — scan each body once and let the structure detectors read
`Scan.text` — so this is queued at `99c1-`, immediately after it, and should be taken with it rather
than before it. If item 30 lands the shared parse, this item is closed by it and says so; if item 30
measures the parse as too expensive to share, this one narrows the regex instead.
**UPDATE 2026-09-28, item 30's session: the shared parse landed (PR #24), but this item is NOT
closed by it.** Item 30 is a performance change and did not move a single detector off
`plain_text()`, so both regex defects are still live. What changed is the cost: a structure
detector can now read `survey.parse(artifact).text` without adding a parse. After item 30, the
regex is 12% of what is left of the detector stage on the 281-body synthetic course (142 ms of
1.2 s).
**DONE 2026-09-28 (PR #25): the regex helper is retired, not narrowed.** Both defects were
re-measured on `main` at `cded1c0` before anything changed: `<p class='a>b'>x</p>` read `b'>x` and
`<p>a</p><script>var x = 1;` read `a var x = 1;`, the scanner reading `x` and `a`. With item 30's
parse shared, every structure detector (D01, D04, D05, D09, D12, D15–D20) and D45's
`_ask_about_prose` now read `survey.parse(artifact).text`; `plain_text`, `body_text`, `_ANY_TAG`
and `_SCRIPTISH` are deleted, so the seam rule has one implementation and `SEAM_CASES` is asserted
against it. **No finding moved:** `run_detectors` over `examples/sample-course` gave 64 findings, 9
undetermined, 0 crashed, and its `to_dict()` is byte-identical before and after — the sample holds
neither shape. A test per shape fails on `cded1c0` and passes after, each through a detector
(D15 matched `minutes` in the tail of an `alt`; D09 took an unclosed script for a description; D45
quoted a script comment as a sentence). One knock-on: `body_text(None-body)` returned `""` without
parsing, and `survey.parse` would have run `scan("")` for an artifact with no markup, breaking item
30's one-parse-per-body test; `Parses` now hands every markup-less artifact one shared empty scan.
`scripts/measure_parse_cost.py` no longer times `plain_text`; on the 281 × 8.6 KB shape the shared
run is 281 parses, 1113 ms (was 281 parses + 1206 regex calls, 1171 ms; median of 3 each, same
host — a difference inside run-to-run noise, so read it as "no parse added", not as a speed-up).

**28 · FILED 2026-09-17 by item 05's session.** **A pack's `bindings` list carries two different
claims and the rating code can only read one of them.** Authoring the two shipped packs against
SPEC §05 made the ambiguity concrete: `syllabus_presence` is *relevant* to GSOV 7 — a course with
no syllabus is a finding against it — but it is not *sufficient*, because GSOV 7 asks what the
syllabus states. `project._rate` does not distinguish: any row with `DETERMINISTIC` coverage whose
detectors ran and found nothing reads **Sufficiently Present**, which is the one rating that means
clean. So a partial capability bound to a row it does not settle can close that row on a course
nobody examined for it — SPEC §11's silent under-detection, arriving through the pack rather than
through a detector.

**UPDATE 2026-09-17, item 07's session: this is no longer latent.** Item 07 landed the 20
structure detectors and the armed guard fired exactly as item 05 predicted it would. **27 Tier B
rows now have deterministic coverage** — 20 in the reference pack and 7 in the RSI pack — and each
of them reads *Sufficiently Present* once its detectors run clean, on a criterion no detector
settles. The read item 05 asked for was done and it confirmed the problem rather than dissolving
it; the two shapes of fix below are unchanged and neither was taken, because taking one is a design
decision this item owns and item 07 was not the place for it. What item 07 did instead is make the
debt **exact and closed**:
`tests/test_rubric_packs.py::TIER_B_ROWS_A_DETECTOR_NOW_ANSWERS` lists all 27 by pack and code, one
test fails if a *new* row joins the list, and a second test fails if an entry on it ever stops
being true. The guard therefore survived being fired instead of being widened into uselessness.

**What was true when this was filed, and why it was filed rather than fixed then.** Of the 46
capability tags, the 10 this build implements all land on Tier A rows in both packs —
`tests/test_rubric_packs.py::test_every_row_with_deterministic_coverage_today_is_tier_a` asserts
exactly that and is written to **fail the day item 07 or 08 lands**, which is the moment to decide.
Item 05 mitigated it as far as a pack can: the tier of every row was set by asking *if every
capability bound here ran clean, would this criterion be answered?*, so the 35 reference rows where
the answer is no are Tier B and gather located evidence as well. That is a mitigation, not a fix —
a Tier B row can still read Sufficiently Present.

Two shapes of answer, both cheap, neither chosen here: a second list (`supporting:` beside
`bindings:`) whose findings reach the row but cannot rate it, or a rule in `_rate` that only Tier A
reaches `SUFFICIENTLY_PRESENT` and Tier B proposes a band with its evidence and otherwise stays at
`No Response`. The second matches SPEC §02's own wording — *"A: rating pre-selected"* against
*"B: rating proposed, worded as evidence"* — which the code currently treats as identical. Filed
sorting **after** every pending item: it is not wrong output today, but it becomes wrong output
somewhere inside item 07.

**Item 05 — the two packs, and what is now true about them.**

- **Adding a rubric really does cost a mapping file.** Two structurally unalike instruments — ten
  sheets against one, a category column against section headings in the index column, a composite
  code and a priority flag against neither — produced two packs with **no new reader, no branch on
  the instrument and no `if` on a filename**. What was added is a format (`rubric/mapping.py`) that
  both use identically. Honest caveat: item 04 wrote the importer with both files already in its
  tests, so the reader was proven there; what item 05 proves is that *binding, tiering and shipping*
  them needed no per-instrument code either.
- **`rubrics/maps/` is the authored input, `rubrics/packs/` is the generated output.** Both are
  committed; `./scripts/build-packs.sh` regenerates and is **byte-identical** because the import
  date lives in the mapping file. `git diff rubrics/packs` after a run is the check.
- **A mapping naming a row the import did not produce raises.** That is §11's rubric drift caught
  at the only moment it is cheap. A row the mapping says nothing about is *reported*, not fatal.
- **The join key is column F and it is declared, not guessed.** Category-plus-index is the
  plausible wrong answer: that column mixes `float` and `int` inside one sheet and differently
  between sheets, so a join between two sheets of the same workbook matches **7 rows of 69** where
  column F matches **69 of 69**. Both numbers are reproduced from the file in the suite. The pack's
  row *codes* are still `COAL 1` — display and join are different jobs.
- **Coverage today: 10 / 39 / 18 / 1 on the reference pack and 5 / 9 / 4 / 0 on the RSI one**
  (deterministic, awaiting a detector, evidence-only, not determinable). The middle number is what
  items 07 and 08 move. The three are never summed, in the pack, the report or the tests.
- **SPEC §02's tier coverage table does not reconcile with §05.** §02 says 29 A / 21 B / 14 C /
  2 D and 2 unmapped. §05's per-detector inventory yields 41 / 16 / 9 / 1 read one way and
  34 / 23 / 9 / 1 read another, and neither is §02's. The packs ship at **23 / 35 / 9 / 1**, by the
  rule written at the top of `rubrics/maps/uccs-oscqr-3-1-3.yaml`; it is the conservative direction,
  since demoting a row adds evidence and never manufactures a clean reading. §02 has been annotated
  rather than rewritten — reconciling it is a judgement call for a human. §05 also maps neither a
  detector nor an extractor to **GSOV 3**, the one row of the 68 with neither.
- **No crosswalk exists in either direction and tests assert it.** The RSI pack contains no 3.1.3
  criterion identifier and neither mapping file names the other instrument. RSI criterion 8 and
  ACD 5 both bind `link_text` because both rows are about link text.
- **The mapping file refuses a key written twice.** YAML keeps the last of a duplicated pair and
  says nothing, so a second `ACD 5:` in a 68-row hand-edited file would drop the first block — its
  tier, its bindings, its note — and the pack would import, validate and ship with a row nobody
  meant to change, with correct counts and no signal anywhere downstream.
- **SPEC §07's bands are now read off both workbooks by a test** rather than asserted in three
  docstrings: the reference sheet's `T4:V4` and the RSI sheet's `D7:F7` both print `1/2 hour or
  less`, `1/2-2 hours`, `2+ hours`. The rating *names* differ — RSI prints them plural and prints
  `Not applicable` — and that difference is asserted too, so it reads as a known fact about the
  instrument rather than a bug in the pack.

**27 · FILED 2026-09-17 by item 21's session.** `run_detectors` catches `NothingExamined` and
nothing else, so a detector that raises anything unexpected — a `ValueError` out of a parser handed
markup it did not anticipate — **aborts the entire review**. Measured, not theorised: a probe
detector raising `ValueError` over a one-page course took the other ten down with it and
`run_detectors` produced no report at all. The module's own docstring says a detector that could
not run must never become "a detector quietly missing from the list"; an unexpected exception makes
*every* detector missing from the list. The vocabulary to report it already exists —
`ResultState.NOT_EXAMINED` with a `not_examined_because`, which `DetectorResult.clean` already
treats as not-a-pass and which the CLI already exits 1 over — so the fix keeps the failure loud
while the rest of the review survives. Filed sorting **after** every pending item: it is a crash
rather than silent wrongness, and nothing queued is blocked on it, though item 17's batch runner
would lose a whole course to one odd page.

**28 · DONE 2026-09-28 (PR #23). Only a Tier A row, with every binding examined, reads clean.**

- **The rule-in-`_rate` shape, not `supporting:`.** SPEC §02 reads "A: rating pre-selected" against
  "B: rating proposed", and the mapping file already tiers every row by the question a `supporting:`
  list would ask, so a second list would restate the tier per binding. A Tier B row whose detectors
  found nothing stays at **No Response** with a note naming what was examined; one whose detectors
  found something is proposed a band as before, noted as covering only what they can see.
- **The Tier A half is the same defect and was fixed with it.** A Tier A row's bindings settle it
  *together*: ACD 1 binds `external_tool_inventory` and `tool_accessibility_statement`, the second
  has no detector, and ACD 1 read clean off the inventory alone (OREP 5 and ACD 4 likewise). A
  binding with no detector, unknown to the build, or absent from the run now keeps a clean Tier A
  row at No Response and is named on it.
- **Measured on `examples/sample-course` (real registry, both packs):** rows reading clean went
  **5 → 1** on the reference pack (GSOV 7, 8, 10 and ENIN 3 reopened; GSOV 2, Tier A, remains) and
  **1 → 0** on RSI (row 13). No band moved. None of the five was a row its detector settles. On a
  synthetic all-clean run: reference 40 → 17, RSI 13 → 6.
- **`TIER_B_ROWS_A_DETECTOR_NOW_ANSWERS` (27) became `ROWS_READING_CLEAN_BELOW_TIER_A`, empty.**
  Its two guards now project a clean run and police the *rating*, which is the failure, rather
  than the coverage, which was only its precondition. Both fail on the pre-fix `_rate` listing
  exactly the 27.
- `_rate`'s zero-cost branch (left for this item by 12a) is still unreachable and was left alone.

**27 · DONE 2026-09-28 (PR #22). One detector's crash no longer takes the whole review with it.**

- **Measured before and after.** A probe detector that raises `ValueError` ran beside all 37 shipped
  detectors over a one-page synthetic course. On main, `run_detectors` raised and produced 0 of 38
  results. After the fix it produces 38 of 38, and the other 37 results are identical to a run
  without the probe.
- **The runner contains it; `Detector.run` still raises.** A crash becomes `NOT_EXAMINED`, never
  clean, with `DetectorResult.crashed` naming the exception type. `BaseException` (Ctrl-C) is not
  contained. Detector tests and the integrity guard call `run` directly and still see the traceback.
- **The report carries the type and the code location, never the message.** The message can quote
  course content, so it goes to the log together with the traceback, both scrubbed at the call
  site. The detect CLI now logs to stderr, so `--json` stays parseable, and it still exits 1.
- **The summary names a crash apart from an empty collection**: `N crashed and could not run (…)`
  comes before `M could not run (…)`.

**26 · DONE 2026-09-28 (PR #21). Document bytes reach the detectors from the export an acquired
course names.**

- **The document names its export.** `CourseDocument.export_files` (provenance key
  `content_export_files`) is the unpacked export's `files/` directory, relative to the data root,
  recorded for all three export origins. `sightline.detect.sources.export_file_source(document,
  paths)` builds the source from the document alone, so a cached document reaches the same bytes
  as a cold one.
- **`local_path` is export-relative, and the docstring now says so.** The code was right; the
  model docstring ("relative to the data root") was wrong and is corrected. A test asserts both
  directions.
- **A cache hit whose export is gone is a miss.** A document with file paths whose recorded
  directory is no longer a complete extraction — swept by hand, or cached before item 26 — is
  acquired again as on a first run (the fresh path therefore asks Canvas for a new export). A
  course with no file paths is served from the cache as before.
- **Reads go through `DataPaths.resolve`.** `ExportFileSource(paths, *root)` lost its own
  containment check; `python -m sightline.detect --export DIR` roots the gate at the named
  directory.
- **Measured:** on a synthetic course acquired from an export, byte-related Undetermined (D02 +
  D28 + D30) went 6 → 1 (the one left is a file the export does not carry); D30 went from 0
  findings to located `pdf_without_a_tag_tree` / `document_without_heading_styles` findings.
  `examples/sample-course` is unchanged (D30 5, D28 2, D02 1) because a bundle records no export.

**34 · FILED 2026-09-28 by item 26's session. Acquisition drops every outcome when it records an
export note.** `acquire/service.py::_with_examination` rebuilds the `CourseDocument` from a
hand-written field list that omits `outcomes`, and it runs whenever an export note exists — which is
every Canvas acquisition with `include_export` on (fresh: OK or REFUSED; existing: OK, ABSENT,
SKIPPED or REFUSED). Probed through the real acquisition with one outcome group link: 1 of 1
outcome with `include_export=False` (no note, the positive control), **0** with the note present,
both OK and ABSENT. D11 then sees an `outcomes` examination of `ok, examined=1` and zero outcomes,
and reports itself as *could not run* (`NothingExamined`) on every course that records outcomes. Not
silent — it surfaces as a detector fault — so filed sorting after every pending item. The fix is
small (`dataclasses.replace(document, examinations=...)` or an equivalent that cannot forget a
field) plus a test that fails when a `CourseDocument` field is added and not carried.

**DONE 2026-09-29, PR #33.** `_with_examination` is now `dataclasses.replace(document,
examinations=...)`. Tests in `tests/test_export_bytes.py`, through the real acquisition with one
outcome group link: 1 outcome without a note (control), 1 with an OK note, 1 with an ABSENT note —
the latter two failed on main (0). A fourth test builds a `CourseDocument` with a sentinel in every
field and asserts each comes back by identity, so a field added later and not carried fails it.
D11 and the cache signature are unchanged. Suite 1840 → 1844 passed, 2 skipped.

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
  leaving it in the registry, and `tests/test_detector_integrity.py` does exactly that.
- **`Undetermined` is a first-class record beside the findings.** A PDF whose bytes were never
  downloaded is not an accessible PDF and is not an inaccessible one. Reporting it as either is a
  fabrication and reporting it as silence is the failure in SPEC §11.
- **Detectors do not name criteria, per the item 04 decision.** The item 06 prompt listed a
  criterion mapping as a finding field; the later decision recorded in this file's corrections log
  supersedes it, and SPEC §04 agrees — rubric packs project findings onto rows. `Finding` carries
  `detector`, `code`, `severity`, `tier`, `location`, `observed`, `evidence`, `examined` and a
  free-form `context`, and `tests/test_detect_base.py` fails the build if a detector's title ever
  names a category code. **Item 04 backfilled `capability_tag`** (PR #9), which was deliberately left
  to it rather than invented here.
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

**Item 04 — what later items inherit. Every item that adds a detector or renders a row is subject
to this.**

- **A detector declares a `capability_tag` and a `capability_description`, and never a criterion.**
  The tag comes from the closed vocabulary in `src/sightline/capabilities.yaml`. An unknown tag
  raises `UnknownCapabilityTag` at class-definition time, and the integrity guard's fourth check
  fails the build by name for a missing tag or a missing description. **Items 07 and 08 must
  declare one per detector**; the vocabulary already carries a concept for every entry in SPEC §05's
  `D` inventory, so neither has to invent one.
- **Validation is asymmetric and that is the design.** A *detector* with an unknown tag fails hard;
  a *pack* with one is a reported `BindingWarning` and that row degrades to
  `NO_DETERMINISTIC_COVERAGE`. Packs are third-party and an uploaded rubric must never be able to
  hard-fail Sightline. Do not make the pack side strict.
- **THE THREE STATES LIVE IN `sightline.rubric.states` AND ARE DEFINED ONCE.** `DETERMINISTIC`,
  `NOT_YET_IMPLEMENTED`, `NO_DETERMINISTIC_COVERAGE`, plus `Rating.SUFFICIENTLY_PRESENT` — **the
  only value meaning clean**. `coverage_of()` is the single place the decision is made. Every output
  surface items 13, 14, 15, 16 and 24 build is a fresh chance to collapse two of them at the last
  moment before a human reads the result; `RowResult.clean` is true only for
  `SUFFICIENTLY_PRESENT`, and the row dictionary carries `coverage` alongside `rating` so no
  renderer has to re-derive it.
- **A pack with zero bindings loads, runs and produces a usable review.** Its rows route to the
  extraction layer. This is the ordinary steady state for a third-party instrument and **no surface
  may word it as degraded, incomplete, unsupported or missing.** `tests/test_rubric_coverage.py`
  asserts that vocabulary is absent from the coverage report; later items should keep doing the
  same for theirs.
- **Projection is many-to-many in both directions, and effort SUMS then bands.** One global rule
  for every row — not worst-of, not a per-row policy, so item 12b calibrates one model. Band
  thresholds are pack data (`RatingScale.bands`, from the ranges printed on the instrument);
  per-defect costs are global config and are currently a labelled placeholder in
  `sightline.rubric.effort` with `calibrated=False`. **Item 12a replaced `EffortModel.cost_of` and
  left the bands and the roll-up as they were** (PR #20).
- **Tiers override the arithmetic, checked before the bands.** A Tier C row stays at `No Response`
  however many findings its bindings produced — the findings ride along as context. A Tier D row
  defaults to `Not Applicable` with a note and is **never routed to extraction**.
- **`sightline.rubric.routing` is the contract item 10 implements against.** `routes_to_extraction`
  is the whole rule; `ExtractionRequest` has no field for a rating, a band or a score, and
  `EXPECTATIONS` says so in the words that reach a prompt. Item 10 implements extraction; it does
  not re-decide which rows get one.
- **Every lookup keys on the row code.** `RubricPack.criterion(code)` is the only accessor and
  there is no positional one. The reference instrument's assessment block runs 1, 2, 3, 4, 5, **7**.
- **`priority` is nullable.** `Projection.priority_rank` sorts null after every labelled priority.
  Nothing fills it in.
- **`sections[].optional` is the field that clears item 08's stop.** A section declaring it may set
  `enabled: false`; one that does not, cannot, and the loader refuses the combination. Switched-off
  rows are *reported* as omitted rather than vanishing.
- **`pack_coverage()` is a checkable artifact about the pack, computed with no course.** Four
  exhaustive buckets with an arithmetic self-check (`ok`). Item 22 shows the delta on it when a
  human accepts a proposed binding.
- **Packs are validated against `src/sightline/rubric/pack.schema.json`** and a failure names the
  offending path. The format is documented in `docs/RUBRIC-PACKS.md`; **item 05 authors the two
  real packs and should not need to touch the schema.**
- **The importer runs against both committed workbooks and the counts agree with SPEC §03.** 68
  rows on the reference instrument (COAL 7, GSOV 12, OREP 11, ACD 11, ENIN 12, ASFE 6, MOB 9) and
  18 on the RSI one, read from the files and asserted in `tests/test_rubric_importer.py` — so there
  is no disagreement to report this time. Verified alongside: `ASFE` keys 1, 2, 3, 4, 5, 7; the
  nine mobile rows carry a null priority; the index column mixes float and int inside one sheet; the
  RSI file raises `TypeError` in openpyxl's default mode and loads read-only; the RSI CSV export has
  already lost its curly quotes to U+FFFD and the reference one has not.
- **The `dev` image stage now copies `rubrics/`.** It did not, so the importer's tests could not see
  the instruments they are written against. Added in PR #9 with the reason in the Dockerfile.
- **There is no `--out` on `python -m sightline.rubric import`, and there must not be.** Item 01's
  structural guard — `sightline.paths` is the only module allowed to create a writable path — caught
  an earlier draft of that flag. Shell redirection does the job. Later CLI items should expect the
  same refusal.

**Item 21 — what later items inherit. Every item that adds a detector is subject to this.**

- **A detector with no fixture test fails the build, by name.** `tests/test_detector_integrity.py`
  is the guard and CI runs it as a step of its own. Adding a detector without a test is not a
  warning and not a review comment; it is a red build naming the id.
- **Coverage is observed, never read off the source.** `tests/detector_ledger.py` calls the suite's
  detector tests with `Detector.run` instrumented and records the ids that came through it. A
  detector a test *imports* and never runs does not count.
- **A registry sweep is credited to nobody, and this rule is the item's finding.** The first version
  of the guard counted every observed run. A drill — a detector added to the shipping registry with
  no test, on purpose — came back **covered**, because `test_detect_base.py` iterates the registry
  and runs everything in it, so every detector added after it was born with a test nobody wrote.
  The guard passed on precisely the thing it exists to catch and only the drill said so. A test that
  ran *every* registered detector is now credited to none of them. **Do not add a second
  registry-sweeping test that asserts per-detector behaviour** — write it against named detectors,
  or it silently stops counting.
- **The harness calls fixture-free test functions.** A test that asks for a pytest fixture cannot be
  called outside pytest and is skipped by the ledger, so a detector whose only test takes a fixture
  reads as **uncovered** and fails the build. Give every detector at least one test that takes no
  fixture. The direction is deliberate: the measurement fails loudly rather than assuming.
- **The invariant is checked across the whole registry, not spot-checked.**
  `sightline.detect.integrity.invariant()` runs every registered detector against an empty course
  and distinguishes three outcomes: refused (the only pass), *returned a result* (a fabricated
  count), and crashed. Both failing outcomes were demonstrated by sabotaging the real
  `Survey.examined` in `src/`, not only in a fixture.
- **The guard reports counts, like everything else here.** `./scripts/dev.sh pytest
  tests/test_detector_integrity.py -q -s` prints how many detectors are registered, how many are
  covered and by how many tests. `-s` is load-bearing: a guard whose whole output is a pass count is
  the opaque green this item was written after.

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

**2026-10-30 — item 46 built (PR #NN).** SPEC §04 *Runtime* says one run executes at a time and the
rest queue; §09's *As built (item 36)* no longer says a start is refused while another run is
running, and §09 gains *As built (item 46)*. The README's New run passage says a start is queued,
not refused. Item 46 is DONE.

**2026-10-30 — item 45 built (PR #51).** SPEC §09 gains *As built (item 45)*. The README's
*Sign-in behind a proxy* no longer says runs are not scoped by user, and it describes the admin
group and per-user tokens. `.env.example` and the README variable table carry
`SIGHTLINE_SIGNIN_ADMIN_GROUP`. Item 45 is DONE and item 46 is READY.

**2026-10-30 — item 44 built (PR #50).** SPEC §09 gains *As built (item 44)*; the README gains
*Sign-in behind a proxy* and the sign-in variables; `.env.example` carries them. Item 44 is DONE
and item 45 is READY. The `sightline.views` and review-route docstrings no longer say there is no
CSRF token.

**2026-10-30 — item 22 part 2 built (PR #49).** SPEC §09 gains *As built (item 22, part 2)*;
`docs/RUBRIC-PACKS.md` describes the Rubrics screen beside the command line. Item 22 is DONE.

**2026-10-30 — item 43 built (PR #48).** SPEC §04 *Runtime* and §09 *As built (item 36)* now
say that a run records its owner. They also say that recovery, shutdown and Start's check act on
whether that owner is live, not on the `running` status alone.

**2026-10-30 — decisions recorded (operator, interactive).** Item 43: (a), an owner record;
READY. Item 22 part 2 specified; READY. Item 40 decided and split into 44 (READY), 45 and 46
(BLOCKED on their predecessors) and 47 (Authentik, compatible with others including Entra ID;
BLOCKED on 44). SPEC §09's open question is replaced by the decisions.

**2026-10-29 — process docs describe the Claude runner.** `AGENTS.md` and `docs/DEVELOPMENT.md`
dropped the retired Codex queue engine: HEADs begin `<!-- model: opus[1m] -->`, attribution
follows the session's Claude Code lines, and the queue section describes the `work` lane and its
clean-`main` gate.

**2026-10-29 — decisions recorded (operator, interactive).** Item 16: Q1 (a), Q2 (b); READY.
Item 35: (a), SPEC §08 corrected. Item 18: parked. Item 40 (sign-in, multiple users) filed,
NEEDS DISCUSSION. SPEC §09's open questions are replaced by the two decisions and item 40.

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
