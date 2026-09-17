# Sightline — build specification

**Rev B · 8 September 2026**

An automated first pass over online Canvas courses against the UCCS OSCQR 3.1.3 rubric, a local
adaptation of SUNY OSCQR. It gathers
evidence, computes remediation effort, and hands an instructional designer a pre-populated action
plan. **It does not grade courses and it does not grade faculty.**

The shareable rendering of this document lives at
<https://claude.ai/code/artifact/d9f97cbe-ba46-420c-b6bb-057c81454cdb>. This file is canonical.

---

## §01 The verdict, and the condition on it

Buildable, and the rubric in use is unusually well suited to it — but only if the product never
issues a verdict on a subjective item.

The risk was never that a language model can't judge whether a course develops higher-order
thinking. It's that a model *will* judge it, fluently, and be wrong often enough that faculty
discard the whole report — including the forty-odd findings that were arithmetic. One bad call on a
pedagogical item costs every alt-text finding in the document.

> **Design rule.** The model produces checkable artifacts, never verdicts. Every machine-authored
> line is a computed count, a verbatim quote with a location, or an explicit statement that no
> evidence was found. A reviewer can falsify any of those in seconds. A rating cannot be falsified
> without redoing the review, which is why a wrong one is so expensive.

### Why redundancy doesn't rescue the subjective items

Majority-voting over N samples works on maths and code because those have a ground-truth attractor:
correct answers concentrate, wrong ones scatter. Rubric judgment has no attractor. Five samples
recover the model's prior, not the right answer — so a mildly agreeable model returns five confident
agreements on a weak discussion prompt, with zero variance. That manufactures high confidence in a
wrong call, strictly worse than one uncertain call. Verbalized self-confidence fares worse; small
local models cluster at the top of any scale offered them.

Redundancy earns its place as a **router, not a validator**. See §06 and roadmap item 11.

### What replaces judgment

Decompose each subjective criterion into extraction questions that are individually near-objective,
then let a deterministic rule or a person judge. ENIN 5 — *"Activities help learners develop
higher-order thinking and problem-solving skills, such as critical reflection and analysis"* — is
built like this:

1. **Extract.** Every graded activity, with the verbatim sentence stating what the student must
   produce, and its Canvas location.
2. **Extract.** The task verb from each sentence.
3. **Compute.** Map verbs against a Bloom's table in code. Produce a distribution.
4. **Present.** "17 activities: 12 identify/describe/summarize, 3 apply, 2 analyze, 0
   evaluate/create" — every item linked to source.

The model extracted, a lookup table judged, a human decided.

---

## §02 Determinacy tiers

Every criterion carries a tier. The tier governs how the finding is computed, how confidently it is
worded, and whether Sightline may pre-select a rating at all.

| Tier | Name | Handling |
|---|---|---|
| **A** | Deterministic | Computed by a parser. No model. Rating pre-selected, stated declaratively. |
| **B** | Evidenced | Model locates and quotes the passage. Rating proposed, worded as evidence. |
| **C** | Judgment | Pedagogical decision. Context assembled, question drafted, **never** pre-rated. Left at `No Response`. |
| **D** | Out of scope | Not determinable from artifacts. Defaults to `N/A` with a note. |

Ink density encodes tier throughout the UI (see `docs/tokens.css`): filled is computed, washed is
evidenced, dashed is judgment, dotted is unreachable.

### Coverage

| Tier | Criteria |
|---|---|
| A — deterministic | 29 |
| B — evidenced | 21 |
| C — judgment | 14 |
| D — out of scope | 2 |
| Unmapped, human-only for now | 2 |
| **Total** | **68** |

> **This table does not reconcile with §05 and has not been rewritten.** Roadmap item 05 authored a
> tier for every row of both shipped packs and could not reproduce these counts under any mechanical
> reading of §05's detector inventory: taking the strongest tier reaching each criterion gives
> 41 / 16 / 9 / 1, taking the weakest gives 34 / 23 / 9 / 1, and §05's own "human-only" pair is
> given tiers C and D there while appearing here as a separate row of 2. The packs ship at
> **23 / 35 / 9 / 1**, by the rule stated at the top of `rubrics/maps/uccs-oscqr-3-1-3.yaml`: a row
> is Tier A only where the capabilities bound to it, run clean, answer the criterion as written.
> That is the conservative direction — demoting a row adds located evidence and never manufactures
> a clean reading. Reconciling this table is a judgement call left to a human. §05 also maps neither
> a detector nor an extractor to **GSOV 3**, the one criterion of the 68 with neither.

Tier A is large because the accessibility and mobile clusters — ACD 1–11 and MOB 1–9, twenty
criteria — are almost entirely parser work. Those are also where reviewers currently spend the most
tedious time. That's the wedge, and it is roadmap item 06.

> **Presentation constraint.** Tier A findings and Tier C prompts must never share a visual channel.
> Separate blocks in the PDF, distinct treatment in the workbook, different language registers.
> Blending them transfers the uncertainty of the weakest finding onto the strongest.

---

## §03 What the rubric actually is

Read directly from the workbooks committed at `rubrics/source/`. The reference instrument is
**UCCS OSCQR 3.1.3** — a local adaptation, **not** stock SUNY OSCQR 4.0 — carrying **68 criteria
across 7 categories**, not 50. The differences are load-bearing.

| Code | Category | Criteria |
|---|---|---|
| COAL | Course Alignment | 7 |
| GSOV | Getting Started & Overviews | 12 |
| OREP | Organization & Representation | 11 |
| ACD | Accessible Course Design | 11 |
| ENIN | Engagement & Interaction | 12 |
| ASFE | Assessment & Feedback | 6 |
| MOB | Mobile Standards | 9 |

### Three properties that make this automate well

**The rating scale is denominated in effort, not quality.** *Sufficiently Present / Minor Revision /
Moderate Revision / Major Revision / Not Applicable*, with bands: ≤ ½ hour, ½–2 hours, 2+ hours.
Materially better for automation than a quality scale — for Tier A the rating becomes arithmetic,
and arithmetic is defensible to a skeptical faculty member in a way a quality score never is.

**The crosswalk already exists.** Every criterion row carries a sequential number, a letter, a
composite key (`20a`), an OPEN SUNY number, a reviewer origin flag, and a category code with a
within-category index.

**The instrument already wants evidence.** There is a *Course Example/Evidence of Compliance*
column, an *Action Plan* column, an *Additional Narrative* block, and an `Essential`/`Important`
flag per criterion. The default cell state is `NR / No Response` — abstention is native to the
sheet. Sightline fills columns that already exist.

### Findings from the files

- **ASFE skips index 6.** The category runs 1, 2, 3, 4, 5, 7. The importer must key on the
  composite code, never on positional index.
- **The two CSV exports use different encodings.** The `Instructional Designer` sheet is clean
  UTF-8 with curly quotes intact; the RSI export contains mojibake (`?click here?`). Spreadsheet
  exports are authoritative; CSV is a lossy fallback.
- **`UCCS OSCQR RSI Review.xlsm` will not open in openpyxl at all.** It raises on a malformed
  `mergeCell` range — already unreadable by the standard Python toolchain, which is independent
  confirmation that macro-free, UTF-8, CSV-clean output is correct rather than merely preferred.
  It also carries a 4.6 KB compiled `vbaProject.bin`, which Sightline neither reads nor reproduces.
- **Mobile criteria carry no Essential/Important flag.** Only the UCCS-origin criteria are
  prioritised; the integrated mobile set has a blank priority column. Action-plan ordering must
  tolerate a null priority rather than assuming one.

### Sheet inventory and disposition

| Sheet | State | Extent | Disposition |
|---|---|---|---|
| Course Profile | visible | A1:J23 | Blank metadata template — import as run metadata |
| **Instructional Designer** | visible | A1:AD531 | **Canonical criterion source** |
| Faculty | visible | A1:AD531 | Duplicate — ignore |
| Reviewer | visible | A1:AD531 | Duplicate — ignore |
| Reviewer 2 | hidden | A1:AD532 | Duplicate — ignore |
| Action Plan | visible | A1:AF1001 | Aggregate — regenerate, don't import |
| Self-Assessment | hidden | A1:AD522 | Ignore |
| Self-Assessment Action Plan | hidden | A1:AF984 | Ignore |
| CONFIG | hidden | A1:EU1919 | Cross-reference only — never a source |
| Change Log | hidden | A2:C9 | Ignore |

`CONFIG` holds defined names including `RubricConversion`, `timeToFix`, `PriorityList`, `standNum`
and `scale` — a crosswalk and an effort model already present as data. It is **a cross-reference,
never a source**: hand-maintained, and it drifts from the working sheet. Disagreement between the
two is reported rather than silently resolved.

The workbooks contain no personal data. A scan across every sheet found zero email addresses, and
`Course Profile` is an unfilled template; the apparent name hints in `CONFIG` are dropdown options
(*Current instructor*, *Previous instructor*), not people.

### The RSI rubric is the same instrument at lower resolution

`UCCS OSCQR RSI Review` collapses the 68 into 18: its criterion 8 is ACD 5 verbatim, and its
criterion 6 folds together ACD 2 and part of ACD 3. That many-to-one relationship is what dictates
§04 — detectors are primary and rubrics are projections over them.

---

## §04 Architecture

### Detectors are primary; rubrics are projections

The naive shape — one routine per criterion — forces a rewrite per rubric and makes user-uploaded
rubrics impossible. Invert it. Build ~50 detectors once, each producing typed findings. A **rubric
pack** is a mapping from detector findings onto its own rows.

One `link_text` detector fires once. The 3.1.3 pack renders it at ACD 5; the RSI pack at criterion
8; an uploaded rubric wherever its mapping says. Adding a rubric costs a mapping file, not code.

A detector declares a **capability tag** — a course-quality concept from a closed, committed
vocabulary — and never a criterion; a pack binds each of its rows to zero or more tags. **An
unbound row is normal**, and routes to the extraction layer rather than reading as clean, so a
rubric nobody has written a mapping for still produces a review. The pack format, the vocabulary
and the three coverage states are documented in `docs/RUBRIC-PACKS.md` (roadmap item 04).

### Pipeline

| # | Stage | Detail |
|---|---|---|
| 1 | Acquire | Canvas API + content export. Reuse the AccessLens fetch/unzip layer. |
| 2 | Normalize | One document model: pages, files, assignments, modules, embeds — each with a Canvas deep link. |
| 3 | Detect | Deterministic parsers. No model. Typed findings with locations and examined counts. |
| 4 | Extract | Model-backed extractors. JSON schema, verified quotes only, 3× for routing. |
| 5 | Project | Map findings onto the selected rubric's rows. |
| 6 | Score | Effort model turns defect counts into revision tiers. |
| 7 | Review | Designer confirms, overrides, annotates. |
| 8 | Emit | XLSX workbook + PDF course report + narrative. |

### Runtime

Single FastAPI process, SQLite for run state, asyncio for background work. No Celery, no Redis, no
worker container. At 1–16 courses the work is I/O-bound against Ollama and there is nothing to
distribute. Run state persists to SQLite so a restart resumes. Follows the van1sh footprint
deliberately.

### Nothing institution-specific is compiled in

Sightline is intended to be reusable and open source. Every deployment-specific value is
configuration, entered and tested in the UI with an environment-variable fallback: Canvas base URL,
Canvas API token, Ollama endpoint, optional OpenAI-compatible endpoint. Rubric packs ship as data
files, not code. Each connection setting gets a **Test connection** action reporting which host
answered, what it returned, and what to change if it didn't.

---

## §05 Detector inventory

`D` detectors are deterministic parsers; `E` detectors are model-backed extractors returning quotes
with locations. Criteria are given by category code and within-category index.

### Canvas structure — API (item 07)

| ID | Detector | Criteria | Tier |
|---|---|---|---|
| D01 | Syllabus present and non-empty | GSOV 7–10 | A |
| D02 | Syllabus printable; PDF tagged and sectioned | GSOV 12 | A |
| D03 | Module overview present per module | GSOV 4 · OREP 3 | A |
| D04 | Course schedule with dated activities | GSOV 5 | A |
| D05 | Welcome announcement posted | GSOV 6 | A |
| D06 | Navigation menu pruned of unused tools | OREP 11 | A |
| D07 | Gradebook structure; group weights total 100 | ASFE 7 | A |
| D08 | Graded assignment has an attached rubric | ASFE 2 | A |
| D09 | Assignment has description, due date, submission types | ACD 11 | A |
| D10 | Due-date coverage across graded items | GSOV 5 · ACD 11 | A |
| D11 | Outcomes defined at course and module level | COAL 1–2 · GSOV 9 | A |
| D12 | External tool and LTI inventory | GSOV 1 · ACD 1 | A |
| D13 | Campus policy links resolve | GSOV 2 | A |
| D14 | Student support resource links present | GSOV 11 | A |
| D15 | Timed assessments flagged to learners | ACD 10 | B |
| D16 | Ungraded / low-stakes formative opportunities exist | ASFE 4 | A |
| D17 | Discussion inventory, graded and ungraded | ENIN 3 | A |
| D18 | Instructor presence artifacts — bio, intro media | ENIN 1 | B |
| D19 | Scaffolded long-duration assessments | ASFE 5 | B |
| D20 | Course feedback mechanism present | ENIN 11 | A |

### Content accessibility — HTML and documents (item 06)

| ID | Detector | Criteria | Tier |
|---|---|---|---|
| D21 | Image alt text: missing, empty on meaningful, filename-as-alt, over-long, decorative marking | ACD 8 | A |
| D22 | Heading structure: presence, order, skipped levels, bold-as-heading | ACD 3 · OREP 5 | A |
| D23 | Link text: "click here", bare URLs, duplicate labels | ACD 5 | A |
| D24 | Table semantics: headers, scope, caption, summary, layout misuse | ACD 6 · MOB 2 | A |
| D25 | Contrast ratio from inline and sheet styles | ACD 2 | A |
| D26 | Colour as sole carrier of meaning | ACD 7 | B |
| D27 | Caption track present on media and embeds | ACD 9 | A |
| D28 | Font family sans-serif, size ≥ 12pt in documents | ACD 2 | A |
| D29 | Text rendered as image (density heuristic, no vision model) | ACD 3 | B |
| D30 | Document accessibility: PDF tagging and OCR, DOCX styles, PPTX layouts, unique slide titles, non-automatic transitions | ACD 3–4 | A |
| D31 | Attribution or licence text near materials | OREP 6 | B |
| D32 | OER signals: known repositories and licences | **OREP 8** | B |
| D33 | Material-type variety per module | OREP 9 | A |
| D34 | HTML as primary delivery vs attached files | OREP 7 | A |
| D35 | Content chunking: page length and section breaks | OREP 5 · MOB 9 | A |
| D36 | Documents printable and downloadable | ACD 4 | A |
| D37 | Page titles present, descriptive, unique | OREP 4 | A |
| D38 | Accessibility statement for each external tool | ACD 1 | B |
| D46 | Extraneous / prior-semester content present | OREP 1 | B |
| D47 | Module naming and structural consistency | OREP 2 | A |

### Mobile (item 08)

Section renders only when the rubric pack enables it. Of the nine, four carry real independent
value; D40 duplicates ACD 6, MOB 9 duplicates OREP 5 via D35, D43 targets Flash, and MOB 5–6 are
largely undetectable.

| ID | Detector | Criteria | Tier |
|---|---|---|---|
| D39 | Embedded content accompanied by a hyperlink | MOB 1 | A |
| D40 | Layout tables and nested indent levels | MOB 2 | A |
| D41 | Text floated beside images | MOB 3 | A |
| D42 | Fixed pixel widths instead of percentages | MOB 4 | A |
| D43 | Legacy or non-mobile embeds | MOB 7 | A |
| D44 | Attachment format census | MOB 8 | A |
| D45 | Required app platform availability | MOB 6 | C |
| — | Tested on multiple mobile devices | MOB 5 | D |

**How D40's overlap with D24 was resolved, item 08.** MOB 2 reads "the course avoids the use of
tables and multiple levels of indents", and the *tables* half is already computed by D24, whose
criteria column above is `ACD 6 · MOB 2`. Both detectors declare the same `table_semantics`
capability, so a row bound to it receives from both and their effort sums — which makes an overlap
a double charge rather than a duplicate line. D40 therefore computes the **indent** half, from the
module items' own `indent` field and from markup nesting, plus the one table fact D24 structurally
cannot reach: a table *inside* another table, reported on the inner table where D24 reports the
outer one. Neither detector names the same element twice.

**D45 emits no findings.** The course names the tool; whether an app for it is published on both
platforms is answered by two app stores and by no course artifact. Each place the course sends a
learner outside Canvas is recorded as something opened and not readable, carrying the context and
the question, which is what tier C asks for and what keeps a rating from being pre-selected.

### Model-backed extraction (item 10)

Each returns JSON conforming to a fixed schema: a list of findings, each with `quote` and
`location`, and nothing else. No ratings, no prose, no summarization. **An empty list is a valid and
expected answer.**

| ID | Extractor | Criteria | Tier |
|---|---|---|---|
| E01 | Objectives and their task verbs | COAL 1–3 · ENIN 5 | B |
| E02 | Alignment references between content, activity, assessment and objective | COAL 4–6 | B |
| E03 | Interaction expectations and response-time commitments | ENIN 2 · ENIN 4 | B |
| E04 | Community-building activities | ENIN 3 | B |
| E05 | Real-world / authentic task framing | ENIN 6 | C |
| E06 | Complexity progression across modules | ENIN 9 | C |
| E07 | Source and author diversity in materials | OREP 10 | C |
| E08 | Evaluation criteria and exemplars | ASFE 2 | B |
| E09 | Grading and late-submission policy | GSOV 10 | B |
| E10 | Technical-skill scaffolding and guided practice | ENIN 12 | B |
| E11 | Objectives stated within assessment information | ASFE 1 | B |
| E12 | Varied engagement options and interaction modes | ENIN 7 · ENIN 8 | C |
| E13 | Learner contribution of outside resources | ENIN 10 | C |
| E14 | Multiple means of demonstrating achievement | ASFE 3 | C |

**E01–E10 are built (item 10); E11–E14 are not.** The criteria column is documentation of the
reference pack, not a property of an extractor: an extractor names no criterion and declares no
capability tag, and reaches a row through the routing contract in `sightline.rubric.routing`.

#### How extraction works, as built

- **One artifact per call.** The model is sent one page, one assignment, one syllabus, and is never
  asked which page a sentence came from. A page over the word budget is **split, never truncated**.
- **The location is computed, not believed.** The schema requires the model to say where it looked,
  and that string is never rendered. Asked the same question about the same page, three models on
  the target host answered `"Course policies (page)"`, `"page"` and `"Week 2 overview"`. What is
  rendered is the artifact the harness sent, the source line of the block the verified quote landed
  in, and the heading above it.
- **Every quote is string-matched against the document before acceptance**, and what is kept is the
  *source's* text at the match rather than the model's string. Normalization folds entities,
  typography, whitespace and case. Nothing approximate: a changed number, a dropped word or an
  ellipsis splice is discarded and logged.
- **Verification catches invention, not irrelevance.** A quote that is on the page but bears on
  nothing is verified and shown, with its location, for a reviewer to dismiss in seconds. That is
  the falsifiability §01 asks for, and it is why no extractor may skip to a rating.
- **Four outcomes are kept apart**: *no evidence found* (sent, answered, verified, nothing bears on
  it), *nothing was examined*, *nothing carried any text*, and *could not be read*. The last is
  never worded as the first.
- **The thinking pin is on where the model reports the capability**, because with it off a thinking
  model's reasoning arrives inside `message.content` — which is what the extractor parses. The
  response schema also goes to the host in `format`, which constrains generation; the response is
  validated regardless, since a constraint the host promises is not a constraint this code checked.
- **Nothing retries.** A second sample at the same temperature is a second answer to the same
  question. Item 11 is a router over three runs, not a validator.

### Human-only, no detector

| Criterion | Text | Tier |
|---|---|---|
| COAL 7 | Technology tools clearly support learner achievement and elevate learner engagement | C |
| MOB 5 | The course is tested on multiple mobile devices | D |

---

## §06 The model layer

Target host is an AGX Orin 64GB running Ollama 0.20.6. **The endpoint is configuration, never
hardcoded.** Concurrency defaults to 1. Every call logs latency, token counts and the serving model
from the first commit — the failure mode that killed an earlier local-AI pipeline on this network
was silent, not loud.

### Model selection is by capability, not by exclusion

Filtering out vision models is wrong and would remove the best available model. `/api/show` reports
`gemma4:e4b` as `['completion','vision','audio','tools','thinking']` — it is multimodal. Select on
what the work needs instead:

> **Require `completion` and `tools`; exclude `embedding`.**

That admits gemma4, rejects `llava:13b` (no `tools`) as a side effect, and leaves
`nomic-embed-text` available for background embedding exactly as intended.

### Discovery and recommendation

- **Installed models:** `/api/tags`, then `/api/show` per model for `capabilities`. Live and
  authoritative.
- **Not-yet-installed:** Ollama exposes no library-search endpoint, so recommendations ship as a
  curated manifest in the repo — 8–12 candidates sized for this host, not a 170-model browser.
- **Installing:** `/api/pull` with progress in the UI. `/api/delete` exists and is deliberately not
  wired up.
- **Third-party catalogues are unreliable.** The community mirror at `ollama-models.zwz.workers.dev`
  serves 170 models and does not contain `gemma4` — the model currently running on the target host.
  Usable as a seed for the manifest, never as a runtime source. The freshness test is exactly that:
  probe it for models known to exist.
- `thinking` is pinned explicitly per call site rather than left to model defaults, since it changes
  output determinism.

### Measured on the target host, 2026-09-17 (item 09)

Three behaviours were measured while building the client. They do not change the design above; they
say what a correct implementation of it has to do, and item 10 depends on all three.

- **A request must pin its context window.** Ollama 0.20.6 sizes its allocation from the model's
  *declared* context length unless told otherwise, so `llama3.2:3b` — a 2 GB model declaring a
  131,072-token window — is refused with *"model requires more system memory (52.2 GiB) than is
  available (48.7 GiB)"*. The same call with `num_ctx: 8192` answers in 3.7 s. `OLLAMA_NUM_CTX`
  is pinned on every call for this reason and is not a quality setting.
- **The thinking pin is capability-aware.** `think: true` against a model with no `thinking`
  capability is an HTTP 400, not an ignored field. The field is sent only when the model reports
  the capability, and omitted — not sent as `false` — when it does not.
- **Thinking off does not mean no thinking.** On `gemma4:e4b`, twice each way at `temperature: 0`:
  with `think: true` the reasoning arrives in `message.thinking` and `message.content` holds the
  answer alone; with `think: false` the reasoning arrives *inside* `message.content` ahead of the
  answer, once trailed by a `<channel|>` control token. An extractor reading `content` from a
  thinking model with the pin off therefore reads prose reasoning. Item 10 chooses the pin per
  call site with this in front of it; `ChatResult` keeps the two fields apart either way.

### The 3× router, built 2026-09-17 (item 11)

Each extraction runs three times and the runs are compared **on the extracted spans, after
normalization** — not on prose, not on a count of findings, and never on a rating. A subject whose
runs returned different spans is marked *needs attention*.

- **Agreement is not marked.** Not "confident", not `agreed: true`, not a run count beside a quote.
  A subject whose runs agreed carries exactly what one run produced. The zero-variance failure in
  the section above is the reason: three identical confident answers is what a mildly agreeable
  model returns on a weak prompt, so a marker for it would be strongest exactly where it is most
  wrong.
- **The evidence downstream is the union, never the intersection.** Every span was string-matched
  against the source before acceptance, so a span one run returned is a passage that demonstrably
  exists. Filtering by how many runs agreed would be variance used as a truth signal, which is the
  thing the router exists not to do.
- **Three run outcomes per span, kept apart**: returned, read-and-not-returned, and *the artifact
  was unreadable in that run*. The third is not a disagreement — a run that could not read a page
  did not fail to find a sentence in it, and counting it as one reports a single failure twice and
  as the wrong kind of thing.
- **A span's identity is its position as well as its text.** One sentence can appear twice in one
  artifact — a policy restated under two headings is ordinary in a syllabus — and extraction
  verifies and locates each occurrence separately, so one run legitimately returns two quotes of
  the same text. Keyed on text alone the union keeps the first and drops the second: a verified
  quote, with a location a reviewer could have opened, lost to the comparison rather than to any
  filter. The position is safe to key on because the *harness* computes it and not the model; the
  model's own account of where it looked is kept apart and never read here.
- **The counts and the Tier C question are recomputed over the union**, not taken from a run. A
  single run's summary describes that run's evidence, so printing it beside the union's quotes
  would put a count next to a list it does not count — a machine-authored line a reviewer
  falsifies by counting what is under it. Both are pure functions of the evidence and neither goes
  near a model, so this is arithmetic and not a second opinion. Three runs of a Tier C criterion
  are still three extractions and none of them is a rating.
- **Cost is linear and is not amortized.** Concurrency is 1, so three runs take three times as
  long. Measured over the synthetic sample course on `llama3.1:8b`: **127 model calls a pass, 381
  for three runs**. That is the number a per-extractor selection UI would exist to reduce, and it
  is why the router is asked for by `--runs` rather than being the default.
- **The disagreement set is recorded as a document** — per flagged subject, the extractor id or the
  whole rubric-row request, plus exactly the artifacts that differed — so escalation re-runs that
  subset rather than the course.
- **Where the flag is surfaced.** The extraction screen names it, and the serialized comparison
  carries a `needs_attention` object — present only when the runs differed, absent rather than
  false when they agreed, so a renderer cannot read the key as a boolean to sort on. The review
  screen of §09 is roadmap item 16 and is not built yet; that payload is what it renders.

### Escalation

An OpenAI-compatible adapter lets a higher-capability model re-run *only the disagreement set* from
the 3× router — typically a handful of criteria per course. Configured through the same UI. **Held
as blocked** pending confirmation of ChatGPT Edu API access; the app functions fully without it.

---

## §07 Effort model and ratings

| Rating | Band | Assigned when |
|---|---|---|
| Sufficiently Present | — | No qualifying defects found |
| Minor Revision | ≤ 0.5 h | Rolled-up cost within the first band |
| Moderate Revision | 0.5–2 h | Rolled-up cost within the second band |
| Major Revision | 2 h + | Rolled-up cost above the second band |
| Not Applicable | — | Tier D, or the feature is absent by design |
| No Response | — | **Every Tier C criterion.** Never pre-selected |

Action-plan ordering is two-axis: the `Essential`/`Important` flag crossed with the computed band.
Essential + Major sorts to the top. MOB criteria have no priority flag, so ordering must tolerate a
null.

> **The bands above are the instrument's own and need no calibration.** They are printed on the
> `Instructional Designer` sheet as column headers: row 3 carries the rating names, row 4 the
> ranges directly beneath them — `T4` "1/2 hour or less", `U4` "1/2-2 hours", `V4` "2+ hours". The
> RSI instrument prints the same three on its rows 6–7. Sufficiently Present has no band, because
> it is "no qualifying defects found" rather than zero hours.
>
> **The per-defect cost table is a different matter and remains uncalibrated.** Nothing in either
> workbook prices a defect. `CONFIG`'s `timeToFix` (`CONFIG!$H$15:$K$19`) is **not** the effort
> table an earlier revision of this spec claimed it was: it maps **rating → hours**, the inverse of
> what Sightline computes, and its `VALUE` column (Major 1, Moderate 2, Minor 3, SP 4) is an
> inverted scale that exists to be averaged — precisely the aggregate score §11 forbids. Do not
> seed costs from it. Costs ship as a labelled placeholder (roadmap item 12a); bands display as
> estimates and are trivially overridable until item 12b fits them.

---

## §08 Outputs and voice

### Two artifacts

- **XLSX workbook** — every criterion on the selected rubric, with proposed rating, evidence column
  filled with quotes and counts, action-plan column filled with remediation steps, and a Canvas link
  per finding. Macro-free, UTF-8, single header row per sheet, no merged cells. Must survive a
  round-trip through CSV without loss, which the current instrument does not.
- **PDF course report** — summary, prioritized action plan, accessibility and interaction sections
  in full, and the closing narrative.

### Canvas links

Every finding links to the page or item in the Canvas UI, built from the configured base URL.
Item-level, not the editor.

### The voice contract

Hedging is proportional to determinacy, not applied globally. Constant hedging reads as evasive and
gets tiresome; hedging nothing reads as a machine grading a colleague. Register is a function of
tier, and it is testable.

| Tier | Register | Example |
|---|---|---|
| A | Declarative. States the count and location. No hedge. | "38 images across 12 pages have no alt text. Module 3 accounts for 21 of them." |
| B | Attributed to evidence. Quotes the source, names what was searched. | "The syllabus states a 48-hour response commitment for email. No equivalent commitment appears for discussion replies." |
| C | Poses the question. Presents the distribution, asks for the call. | "Of 17 graded activities, 12 use recall-level verbs and none reach evaluate or create. Whether that suits a 1000-level survey is your call." |
| D | States the limit plainly. | "Device testing can't be checked from course materials." |

Phrasing variety comes from a per-tier phrase bank with rotation, not from letting the model
freestyle — freestyle produces inconsistent register, which is the thing the contract exists to
prevent. The closing narrative is honest and short: what is strong, what needs work, what to do
first, and what Sightline could not determine.

---

## §09 Interface decisions

Server-rendered HTML with light progressive enhancement. No SPA framework — four screens and a small
deployment footprint is a priority. All colour comes from `docs/tokens.css`.

| Screen | Purpose |
|---|---|
| Settings | Canvas URL and token, Ollama endpoint, model picker, escalation endpoint. Every field has **Test connection**. |
| New run | Paste course URLs or IDs, upload a CSV, pick a rubric. Batch capped at 16. |
| Run progress | Per-course, per-stage. Live log. Failures visible, never silent. |
| Review | Criterion by criterion. Evidence, Canvas links, proposed rating, override, notes. |

### Decisions taken

- **Tier is visible in the review UI** with a consistent visual token, so a designer always knows
  whether they are confirming arithmetic or making a judgment.
- **Tier C criteria open at `No Response`** with evidence shown and no rating pre-selected. There is
  no bulk-accept for them.
- **Overrides are first-class and persisted**, with the original machine proposal retained. That
  record is the calibration data for the effort model.
- **Rubric selection is per run**, defaulting to OSCQR 3.1.3. RSI ships as a selectable alternative.
- **Nothing exports until a human has opened the review.** No unattended report generation.

### Open for discussion

- Whether uploaded rubrics get a guided mapping screen or a simpler unmapped mode where rows fall
  through to extraction-only.
- Whether the review screen groups by rubric category or by severity.
- Whether multi-course runs get a comparison view, or stay strictly one report per course.

---

## §10 Roadmap

See `docs/ROADMAP.md`. It is canonical for item status; this document is canonical for design.

---

## §11 Risks

### Adoption, not accuracy, is the real risk

The tool can be technically correct and still fail if faculty read it as automated grading. Three
guards, all product decisions rather than engineering ones: the vocabulary throughout is *review*
and *refresh*, never *evaluation* or *score*; there is no aggregate course score anywhere in the
output; and nothing exports until a designer has opened the review.

### Silent under-detection

The dangerous failure is a detector that finds nothing because it broke, reported as a course with
nothing wrong. Every detector reports **what it examined** alongside what it found, and a detector
that examined zero artifacts is an error, not a pass. Enforced in the framework (item 06) and proven
by a guard that is deliberately fired (item 21). Same class of bug as a test suite that collects
nothing and exits green.

### Quote fabrication

An extractor that invents a plausible quote is the failure that would destroy trust fastest, because
a fabricated quote looks exactly like the evidence the design depends on. Mitigation is mechanical:
every returned quote is string-matched against the source document before acceptance, and a
non-matching quote is discarded and logged, never rendered. Part of item 10's definition of done,
and built there — `sightline.extract.verify`, with the normalizations it allows and the two things
it deliberately does not rescue documented in the module itself.

### Scope creep toward AccessLens

Sightline evaluates against rubric criteria only. It checks that alt text exists, is not a filename,
and is not empty on a meaningful image. It does not judge whether the alt text is *good*, and it
feeds no images to a vision model. That boundary keeps the workload feasible on this host and the
output within what the rubric asks.

### Working data never enters the repository

The repository is **public on Gitea, with no GitHub remote**. Three rules follow.

The **Canvas base URL is treated as a secret** alongside the token, because it identifies the
institution. Docs, tests, fixtures and `.env.example` all use `canvas.example.edu`.

**Course exports never enter the repository.** A Canvas content export carries no student
submissions or grades, but it does carry instructor-authored content, page text naming real people,
and unpublished drafts. Everything downloaded or generated lives under `SIGHTLINE_DATA_DIR`
(default `./data`), which is gitignored in full. What ships instead is `examples/` — a synthetic
sample course carrying deliberate defects, and a sample report generated from it, so a new user sees
the shape of both without a Canvas token.

**The rubric workbooks are committed on purpose.** They sit at `rubrics/source/` and are a blank
review template with no personal data. Publishing them and the packs derived from them is settled;
the packs may later be revised toward stock OSCQR 3.1 and 4.0, and the versioned-pack design in
*Rubric drift* below is what makes that a data change rather than a code change.

### Rubric drift

The reference instrument is maintained by another department and will change. Rubric packs are versioned
data files retaining the source workbook's own identifiers, so a re-import diffs cleanly rather than
silently renumbering criteria.
