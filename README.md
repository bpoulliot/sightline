# Sightline

An automated first pass over online Canvas courses against an online-course review rubric.
Sightline gathers evidence, computes remediation effort, and hands an instructional designer a
pre-populated action plan.

**It does not grade courses and it does not grade faculty.** That framing is a product
requirement, and it constrains the wording everywhere in the app and its output.

The governing design rule, which every part of this repository is subject to:

> The model produces **checkable artifacts, never verdicts.**

Every machine-authored line of output is a computed count, a verbatim quote with a location, or an
explicit statement that no evidence was found. A reviewer must be able to falsify any finding in
seconds.

`docs/SPEC.md` is canonical for design. `docs/ROADMAP.md` is canonical for item status.

---

## Running it

Sightline is containerized: one FastAPI process in one container. There is no broker, no worker
container and no second service. `docker compose up` on a developer machine is the entire
deployment story.

```sh
cp .env.example .env      # then fill in your Canvas and Ollama endpoints
docker compose up --build
```

The app is then at <http://127.0.0.1:8099>. Health is at `/healthz`, which is also what the
container's `HEALTHCHECK` calls.

Working data — course exports, caches, run state, generated reports — lives in the `sightline-data`
named volume, mounted at `/data`. It is deliberately **not** part of the image, so an image rebuild
does not wipe a half-finished batch.

## Screens

| Path | What it does |
|---|---|
| `/` | Status: configuration presence, the data root, recent runs, the determinacy-tier legend. |
| `/settings` | Canvas base URL and token, **Test connection**, the model host and model picker, and the token-scope table below. |
| `/courses` | Resolve a course from a pasted URL, a bare id, or an uploaded CSV. |

There is also a command-line entry point, and its first form needs no Canvas token at all:

```sh
python -m sightline.acquire --bundle examples/sample-course   # the shipped synthetic course
python -m sightline.acquire 4410                              # a course on your instance
```

Everything on the web screens works with JavaScript switched off — each action is a form post that
re-renders the page with its result. `/settings` also exposes the connection test as JSON at
`POST /api/canvas/test`, the model-host check at `POST /api/models/check` and install progress at
`GET /api/models/installs`. Each is the same code path as its button, not a second implementation.

### Test connection

The connection test answers four questions and says which is which: **which host answered**, **who
the token belongs to**, **how many courses it can see**, and — if a course id or URL is supplied —
whether that course resolves and whether its content can actually be read. A token can very often
see a course it may not read, which is why those are two checks and not one.

A check that could not run because an earlier one failed is reported as `skipped` with the reason,
never omitted: an absent check looks like a check nobody needed. Every failure names what to
change. Three Canvas behaviours are encoded rather than left to be rediscovered later:

- **401 means two different things.** A rejected token comes back with a `WWW-Authenticate` header;
  a valid token making a call it is not permitted to make comes back without one. Reissue the token
  versus grant a permission — opposite remedies, so they are reported as different failures.
- **403 is also how Canvas rate-limits**, with `Rate Limit Exceeded` in the body rather than a 429.
- **404 means "not found *or* not visible to you"**, and Canvas does not distinguish them, so the
  remedy covers both instead of guessing.

### Resolving a course

A pasted URL, a bare numeric id and a CSV column all resolve to the same course id. URL shapes that
work include the course root, a trailing `/pages/…`, `/assignments/…`, `/modules#module_4` or a
query string, a URL with no scheme, and Canvas's `sis_course_id:` form. A URL naming a **different**
host than the configured one is refused by name rather than resolved — two Canvas instances both
have a course 4410, and reviewing the wrong one looks like success.

For a CSV, the column headed `course_url`, `course_id`, `id` or a near spelling is used; without
one, the column that parsed the most rows is used and the screen says which and why. Rows that
could not be read are listed with a reason — a CSV importer that skips what it did not understand
is silent under-detection (SPEC §11) in miniature.

## Acquisition and the document model

Stages 1 and 2 of the pipeline in SPEC §04. A course goes in; one `CourseDocument` comes out, and
every detector reads that and never a Canvas payload.

**Two sources, each used for what it is good at.** The Canvas API knows the *structure* of a
course — modules, assignments, quizzes, discussions, announcements, attached rubrics, assignment
groups, navigation settings — and it knows it accurately and now. A content export knows the
*bytes* of its documents. Page bodies come from the API, which is authoritative; the export fills
in a body the API would not hand over and supplies the files.

**Everything normalizes to eight kinds** — pages, files, assignments, quizzes, discussions,
announcements, modules and embeds, plus the syllabus and module items — each with a stable id, a
human title and a **deep link into the Canvas UI**. The link is item-level and never an editor
(SPEC §08): a reviewer following a finding should land on what a learner sees. It is stored as a
*path* and joined to the base URL on the way out, which is what lets a cached document — and the
shipped sample course — carry no institution hostname at all. Where only a search link is possible,
because a file exists in the export and not in the API's file list, the artifact says
`link_exact: false` rather than presenting a weaker link as the same thing.

**Every collection reports what it examined.** A course with the Quizzes tab switched off is a
normal course, so a refused collection is not an error; it is an `Examination` record naming the
endpoint, what came back, and what to change. `empty`, `refused` and `absent` are three different
answers, because a course with no quizzes and a course whose quizzes could not be read look
identical downstream unless acquisition says which (SPEC §11).

**The cache is keyed on the course, not on the clock.** The cheap index phase always runs — a
dozen listing requests — and its result is reduced to a signature. The expensive phase, one request
per page body plus a download, is skipped when that signature matches something already cached. A
key built on the course's own `updated_at` would look right and be wrong: Canvas does not bump a
course's timestamp when a page inside it is edited, and the symptom would be a review of
yesterday's course reported as today's. Values that rotate per request are excluded from the
digest, and so are reply counts — Sightline reviews the design of a course, not the activity in it.

**Sightline reads a content export; it never asks Canvas to make one.** Creating an export is a
`POST`, and every request this package makes is a `GET`. So acquisition looks for a finished export
the course already has, uses it, and when there is none says exactly that with the steps to create
one. Whether Sightline should ever make that one write is a product decision and is recorded as an
open question in `docs/ROADMAP.md`, not settled by whichever item found it convenient.

### Reuse from AccessLens

The fetch and unzip layer is AccessLens's, ported rather than rewritten — a validated download
host, a capped download, a capped extraction, and the IMS manifest parse that recovers module names
from an export. Its analysis half is deliberately left behind; Sightline's analysis is
rubric-scoped and much narrower (SPEC §11, *Scope creep toward AccessLens*).

One thing changed on the way across. AccessLens guards against Zip Slip by comparing a member's
realpath against a prefix; here every member name goes through `sightline.paths`, the same write
gate the whole application uses and the one `tests/test_paths.py` proves structurally. A second
implementation of a security check is a second thing to get wrong.

### The course bundle, and running without a token

A cached document is one large JSON file — right for a machine, useless to a reviewer. A **bundle**
is the same document with the HTML pulled out into real files:

```
course.json     structure, with body_file references instead of inline bodies
bodies/*.html   the HTML, kept as HTML because course HTML is only legible as HTML
```

`examples/sample-course/` is one, hand-written and synthetic, with a deliberate defect for each
detector in SPEC §05 and a registry of them in `defects.json` that the test suite checks is still
true. It is the fixture the rest of the pipeline is built against and it is what a new user runs
first:

```sh
python -m sightline.acquire --bundle examples/sample-course
```

See `examples/sample-course/README.md` for what is planted in it, and for the three detectors it
cannot exercise yet and why.

## Detectors

Stage 3 of the pipeline in SPEC §04. A `CourseDocument` goes in; typed findings come out. A finding
is a **computed count, a verbatim quote with a location, or an explicit record that something could
not be read** — never a judgement, and never a bare number without the thing it was measured on.

```sh
python -m sightline.detect --bundle examples/sample-course
```

**Every detector reports what it examined, and zero examined is an error.** SPEC §11 names the
failure this prevents: the dangerous bug is not a red test, it is a detector that quietly finds
nothing because it broke, handed to a designer as a course with nothing wrong. It is enforced
structurally rather than by care — a detector never touches the document, the only way it can reach
an artifact is through the `Survey` it is handed, and the survey counts every artifact it gives
out. `Detector.run` is final; a subclass that tries to override it fails at class-definition time.

A zero count is then checked against what acquisition recorded. A collection that could not be read
is reported as that, with the remedy. Every declared collection recorded `empty` is a course that
genuinely has none of these. A collection recorded `ok` paired with a detector that opened none of
it explains nothing, and raises. None of the three is a pass: `DetectorResult.clean` is true only
when a detector actually ran, found nothing, and had nothing left it could not read.

**A finding says what it could not tell you.** `Undetermined` is a first-class record beside the
findings — a PDF whose bytes were never downloaded is not an accessible PDF and is not an
inaccessible one, and reporting it as either is a fabrication. So is reporting it as silence.

**A detector never names a rubric criterion.** Detectors emit facts about artifacts; a rubric pack
projects them onto its own rows, which is what makes adding a rubric a mapping file rather than
code (SPEC §04). The criteria column in SPEC §05 is documentation of the 3.1.3 pack.

What a detector *does* declare is a **capability tag** and a plain-language description of what it
contributes to that concept — `alt_text`, `due_date_coverage` — from the closed vocabulary in
[`src/sightline/capabilities.yaml`](src/sightline/capabilities.yaml). A pack binds its own rows to
tags, so the join runs in one direction and one detector serves every rubric that cares about the
same concept. See [Rubric packs](#rubric-packs) below and
[`docs/RUBRIC-PACKS.md`](docs/RUBRIC-PACKS.md).

### The Canvas structure cluster, D01–D20

These read what the API knows about a course's *shape* rather than the markup inside its pages.

| | |
|---|---|
| D01 | no syllabus at all, an empty one, or one short enough to be a pointer — with the length measured |
| D02 | a syllabus with no headings to divide it, and an attached printable copy carrying neither tags nor an outline |
| D03 | modules with no item named as an overview, introduction or objectives listing |
| D04 | no schedule anywhere, or a schedule with no calendar dates written on it |
| D05 | no welcome announcement posted for **this** offering — an announcement predating the course start belongs to a previous one |
| D06 | navigation entries a learner sees with nothing behind them, and tools Canvas exposes no content for at all |
| D07 | assignment group weights that do not total 100, and weighting applied to only some groups |
| D08 | graded assignments with no rubric attached, and rubrics attached to nothing |
| D09 | activities with no description, and graded assignments Canvas offers nowhere to submit |
| D10 | graded items with no due date, each carrying the course's coverage count with it |
| D11 | a course recording no learning outcomes, and outcomes recorded with an empty description |
| D12 | external tool launches no page, syllabus or assignment text says anything about |
| D13 | no links to campus policies; and — only when link checking is switched on — links the far end says are gone |
| D14 | no links to learner support anywhere, and a syllabus carrying none when the course does |
| D15 | a time limit set in Canvas that the assessment's own description never mentions — tier B, quoted |
| D16 | no practice quiz, survey, or zero-point assignment: nothing a learner can attempt without it counting |
| D17 | no discussions at all, and discussions opening with no prompt |
| D18 | nothing in the course titled or written as an introduction to the instructor — tier B |
| D19 | an assessment carrying over a quarter of the graded points with no draft, proposal or milestone beside it — tier B |
| D20 | no survey, feedback discussion or course-evaluation link: nowhere a learner can say how it is going |

**Some of these answer with an absence, and an absence still has to open somewhere.** A finding
about the course as a whole is located on the course itself, which `by_id` resolves and
`artifacts()` does not yield — the course is the container, not one of its contents. Building that
location hands out no artifact and so counts nothing, which is what stops a course-level detector
from becoming exempt from the zero-examined invariant.

**What a detector cannot see, it says.** Canvas exposes no content behind Collaborations, Chat or
Attendance, so D06 reports those entries as *unevidenced* rather than as unused, in those words.
Canvas records no link between an outcome and a module, so D11 establishes the course-level half
and does not infer the other. D15 reads an assessment's own description and says so, rather than
claiming a time limit is disclosed nowhere in the course.

**D13 is the only detector that touches the network, and it is off by default.** With
`SIGHTLINE_RESOLVE_LINKS` unset — or without `--resolve-links` on the command — it reports which
policy links are *present* and claims nothing about where they go. Switched on, it works within a
per-run budget, a short timeout, a minimum interval between requests, and a cache that holds
failures as well as successes. **A request that fails is `UNKNOWN`, never a broken link**: a 403 is
a login wall, a 429 is a rate limit, and a DNS timeout is a network. Only a 404 or a 410 from the
far end is reported as gone. Reporting a working campus policy page as broken because a lookup
timed out is exactly the false finding that costs a tool its credibility.

### The accessibility cluster, D21–D30

| | |
|---|---|
| D21 | images with no alt text, a filename as alt text, over-long alt text, contradictory decorative marking |
| D22 | headings absent, skipping a level, empty, or bold text standing in for one |
| D23 | links labelled "click here", labelled with an address, with no text, or repeated to different places; addresses typed as prose |
| D24 | tables of data with no header cells or no scope, tables with no caption, tables used to position things |
| D25 | text whose declared colour misses the contrast threshold, reported with the background it was measured against |
| D26 | colour apparently carrying meaning on its own — tier B, located and quoted for a person to judge |
| D27 | audio and video with no caption track; hosted players whose caption state the course does not record |
| D28 | type below the minimum size, and typefaces that are not sans-serif, in pages and in attached Word files |
| D29 | images named like a document, and pages carrying more picture than prose — tier B |
| D30 | PDFs with no tag tree or no text layer, Word files formatted by hand, decks with missing, repeated or timed slides |

**The scope boundary, and it is not negotiable** (SPEC §11). Sightline checks that alt text
*exists*, is not a filename, and is not empty on an image nothing marks decorative. It does not
judge whether alt text is good, and it sends no image to a vision model. D29 reads filenames and
counts words; it never looks inside a picture, and its findings say so.

**PDF, DOCX and PPTX are read with the standard library.** `zipfile` and `defusedxml` for the
Office formats, and a structural probe over the decompressed streams for PDF. The obvious reason is
that a course attachment is a file somebody else wrote and every parser added is another one
pointed at untrusted bytes. The reason that decides it is licensing: this repository is MIT, and
the PDF library these tools reach for first is AGPL.

**Two detectors are tier B, and the difference is in the wording rather than in who computed it.**
D26 and D29 look for signals a person has to confirm. They locate and quote a passage and stop
there; SPEC §02 puts them at tier B for that, and a parser that quotes is a stricter form of the
same contract than a model that quotes, because a parser cannot invent the quote.

### The mobile cluster, D39–D45

Nearly free: every one of these rides a parse the clusters above already perform — the embeds
acquisition extracted once, the same HTML scan D21–D30 walk, the file list, the module structure.
No second traversal was added for any of them.

| | |
|---|---|
| D39 | an embedded player with no ordinary link to the same address beside it |
| D40 | module items and page content indented more levels deep than a narrow screen shows, and tables nested inside other tables |
| D41 | images floated left or right with text running alongside them |
| D42 | widths pinned to a number of pixels rather than declared as a proportion |
| D43 | embedded content that names a plugin technology — Flash, Java, Silverlight, a platform-specific player |
| D44 | attachments in a format a phone opens only with another application, or cannot open at all, counted against the ones it opens directly |
| D45 | every place the course sends a learner outside Canvas — tier C, recorded as a question rather than as a finding |

**A rubric pack decides whether any of this is reported.** A section may declare itself `optional`
and a pack may then switch it off; the rows are reported as *omitted* rather than disappearing, and
the detectors run either way. That is the projection principle at work: switching a block off
changes what is rendered and nothing about what was examined. See
[`docs/RUBRIC-PACKS.md`](docs/RUBRIC-PACKS.md).

**D40 and D24 share a capability on purpose, and never name the same element.** Layout tables are
D24's; indentation is D40's; a table nested inside another is reported by D24 on the outer table
and by D40 on the inner one. A row bound to that capability receives from both and their effort
sums, so an overlap would be a double charge rather than a duplicate line.

**D45 emits no findings at all, and that is what tier C means here.** The course names the tool;
whether an app for it is published on both mobile platforms is answered by two app stores and by no
course artifact. So each one is recorded as something opened and not readable, carrying the context
and the question, with no rating pre-selected. Whether a course has been *tested* on real handsets
is tier D — not determinable from course artifacts — and has no detector by design.

### Checking detector integrity

The invariant above is only worth what the guard over it is worth, and **a guard that has never
failed is indistinguishable from a guard that cannot fail**. One command checks it, and the same
command prints what it measured rather than a word like *ok*:

```sh
./scripts/dev.sh pytest tests/test_detector_integrity.py -q -s
```

`-s` is not decoration. Without it the entire output is one line saying how many tests passed,
which is the opaque green this check exists because of — on this network a 31-suite JS test set
gated nothing for months because the obvious one-line runner pointed at a directory, printed one
error and exited 0. With `-s` the command prints what it measured, which with the D01–D30 and
D39–D45 detectors in the registry reads:

```
37 detector(s) registered; 37 covered by 238 fixture test(s); 0 uncovered.
Observed by calling 436 test function(s) across 17 module(s): …
  1 registry sweep(s) credited to no detector: …
  D21: 5 test(s)
  …
The zero-examined invariant holds for 37 of 37 registered detector(s).
37 detector(s) declare 36 capability tag(s); 0 declaration(s) will not resolve; 10 vocabulary tag(s) have no detector yet.
```

Thirty-seven detectors declare **thirty-six** tags rather than thirty-seven: D24 and D40 share one
deliberately, which is the many-to-one direction the projection is built for.

Four guards, and each one is demonstrated failing in the same file, on purpose:

- **Every registered detector is covered by a fixture test.** Coverage is *observed*, not read off
  the source: the ledger calls the suite's detector tests with `Detector.run` instrumented and
  records the ids that came through it. A detector with no test fails the build **by name**.
- **A registry sweep is credited to nobody.** A test that runs every registered detector has tested
  the registry, not any detector in it. This rule exists because the first version of the guard did
  not have it, and a drill — a detector added to the shipping registry with no test, on purpose —
  came back *covered*: one test in the suite iterates the registry, so every detector added after it
  was born with a test nobody wrote.
- **The zero-examined invariant still holds for every registered detector.** Each one is run against
  a course holding nothing and required to refuse. Returning a result there is a fabricated count,
  and returning a *clean* result there is a course reported as having nothing wrong.
- **Every registered detector declares a capability tag a rubric can bind.** The tag is the only
  thing joining a detector to a rubric row, so a detector shipped without one runs, finds things,
  and is reported against nothing. Fired three ways — no tag, an unknown tag, a tag with no
  description — on the declarations rather than on the registry, because the detector interface
  already refuses an unknown tag at class-definition time and a guard whose input cannot be wrong
  is a guard that cannot fail.

Without Docker: `uv run pytest tests/test_detector_integrity.py -q -s`. CI runs the same line as a
step of its own, separate from the full suite, so the guard can be seen to go red on its own.

## Rubric packs

Stage 5 of the pipeline in SPEC §04, and the thing that makes stage 3 worth building once.
**Sightline reviews the course; the rubric is only the lens.** UCCS OSCQR 3.1.3 is the reference
instrument — not the schema, not a required intermediate, and not something a third-party rubric
gets aligned to. Quality Matters, an internal departmental checklist, or an instrument written last
week loads unchanged and gets a working review, and no step requires it to be crosswalked to 3.1.3
first. (The licensing reality makes that concrete: Sightline can never *ship* a Quality Matters
pack, so third-party rubric support is the only way those users exist.)

A pack is one YAML file, validated on load against a JSON Schema committed at
[`src/sightline/rubric/pack.schema.json`](src/sightline/rubric/pack.schema.json). The format is
documented in [`docs/RUBRIC-PACKS.md`](docs/RUBRIC-PACKS.md).

```sh
python -m sightline.rubric packs
python -m sightline.rubric coverage --pack rubrics/packs/uccs-oscqr-3-1-3.yaml
python -m sightline.rubric import --workbook "rubrics/source/UCCS OSCQR 3.1.3.xlsx" > pack.yaml
python -m sightline.rubric tags
```

### The two packs Sightline ships

`rubrics/packs/` carries two, generated from the blank instruments in `rubrics/source/` and
committed beside them:

| Pack | Rows | Instrument |
|---|---|---|
| `uccs-oscqr-3-1-3` | 68 in 7 categories | Ten sheets, a category column, a composite code, an OPEN SUNY number, an origin flag, a priority flag. |
| `uccs-oscqr-rsi` | 18 in 6 sections | One sheet, section headings sitting in the index column, **none** of those identifiers, and a file `openpyxl` refuses to open in its default mode. |

**They are peers and no crosswalk exists in either direction.** The RSI instrument's criterion 8
binds `link_text` because its own text is about link text — it reaches the same capability the
other instrument's ACD 5 reaches, independently, which is the whole of *detectors are primary,
rubrics are projections*. A test asserts that neither pack file carries the other's numbering.

Two structurally unalike instruments cost **one mapping file each and no code**: the tags, the
determinacy tiers and the section flags a workbook has no column for live in `rubrics/maps/`, and
one command regenerates both packs from them.

```sh
./scripts/build-packs.sh          # re-running it is byte-identical; `git diff rubrics/packs` is the check
```

**A pack with no bindings is normal, and it is a success case.** Binding is an optimization, not a
prerequisite: a freshly imported rubric loads, runs, and produces a usable review with its rows
routed to the extraction layer for quoted passages. An earlier design made *upload your rubric*
mean *write forty YAML bindings first*, which is a roadblock an open-source tool cannot justify.
So the importer's job is finished when the rows are in — and it has to be, because neither source
workbook has a column naming a detector and no import can produce a binding.

`coverage` is the command worth having. It needs no course and no token, so *"will Sightline
actually do anything useful with my rubric?"* has a checkable answer before anybody uploads
anything:

```
Tiny Synthetic Review Instrument 0.2: 4 of 10 criteria have deterministic coverage, 3 are
evidence-only, 2 are bound to a capability no detector implements yet, 1 are not determinable
from course artifacts.
```

### Three states, and conflating them is the failure this layer prevents

| State | Meaning | |
|---|---|---|
| `not_yet_implemented` | The bound capability exists; no detector implements it yet | Transient |
| `no_deterministic_coverage` | The row has no usable binding; it routes to extraction | **Normal, permanent** |
| `deterministic` + no findings | A detector ran and found nothing | **The only one meaning clean** |

If an absent detector or an unbound row could read *Sufficiently Present*, Sightline would report a
course clean on criteria it never examined — the silent under-detection of SPEC §11, and the single
worst thing this tool can do. So anything that is not `deterministic` *and* actually examined stays
at the instrument's own default cell state, with the reason on the row.

The tiers override the arithmetic rather than the other way round (SPEC §02/§07): a Tier C row stays
at *No Response* however many findings its bindings produced — the findings are carried as context
for the question a human answers — and a Tier D row defaults to *Not Applicable* with a note.

### Projection is many-to-many, both ways

One detector fans **out**: the syllabus check reaches four getting-started rows at once. Several
detectors fan **in**: GSOV 5 receives from both the schedule check and the due-date check. A row
fed by several detectors **sums their effort and bands the total** — one global rule for every row,
never worst-of, so there is one effort model to calibrate rather than sixty-eight.

Band thresholds are *pack* data, because they are printed on the instrument. The per-defect costs
summed into them are global configuration — see the effort model below.

### The effort model — an estimate, and it says so

Findings become hours, hours become a band, and the band is the rating. Three parts, three homes:

| Part | Where | Status |
|---|---|---|
| **Bands** (≤ ½ h / ½–2 h / 2 h +) | the pack's `rating_scale.bands` | The instrument's own: `Instructional Designer` `T4`–`V4`. Sufficiently Present has no band. |
| **Per-defect costs** | `src/sightline/rubric/costs.yaml` | **An uncalibrated placeholder**, every line marked. Roadmap item 12b fits it. |
| **Near-boundary margins** | the same file | Uncalibrated; the lowest margin is wider on purpose (below). |

**The cost table is keyed on the detector id**, not on the finding's severity: severity says what
a defect means for a learner, cost is what fixing it takes, and an uncaptioned video and a missing
alt attribute are both barriers an order of magnitude apart in work. Override any entry without a
code change by pointing `SIGHTLINE_EFFORT_COSTS` at a YAML file of the same shape; its entries
replace the shipped ones one by one. A cost of zero is refused. **A detector with no entry is not
free** — its findings cost the table's `default_hours`, the row says so by name, and
`python -m sightline.rubric coverage` lists every detector priced that way.

**Major Revision is deliberately hard to reach.** In five hand-completed reviews, humans rated 326
criterion instances and said Major twice. A suite property (`tests/test_rubric_effort.py`) holds
the shipped table to it: no row of the reference pack reaches Major on fewer than twenty findings
from its costliest detector, and ten missing alt attributes read Minor Revision.

**The near-boundary marker.** A banded row whose hours fall within a margin of a threshold carries
`near_boundary` — the threshold, the ratings either side, the distance in hours and the margin
applied. The margin at the **Sufficiently Present / Minor** boundary is wider than the others,
because that is where independent human reviewers disagree most (29 of 34 disagreements in the same
corpus) and where the arithmetic is smallest and so looks most certain. It is **a computed
distance, not a confidence score**: no percentage, no probability, checkable against the row's own
findings. It is computed once and persisted with the row in the review file, so every output
surface shows the same marker; nothing renders it yet (roadmap item 24 does). A row reading
Sufficiently Present carries none — it has no effort figure to measure from.

**Overrides are the calibration data.** `sightline.rubric.overrides.record_override` appends a
record to the run store's `rating_overrides` table: the machine proposal (rating, hours, marker,
the findings that were summed) beside the human rating, who set it, when, and the pack id and
version. Append-only — an override never overwrites the proposal. A run nobody overrode writes
nothing. The review screen (item 16) is what calls it.

**There is no course-level figure.** No score, index or percentage, and no total of hours across
rows: effort is not a schedule, and a course total is an aggregate score with a clock face on it.
A test reads the effort path's code for the arithmetic that would produce one.

### Validation is asymmetric, on purpose

A **detector** declaring a tag outside the vocabulary fails hard, at class-definition time: we
write the detectors, so a typo in one is our bug and it should stop the build. A **pack** binding
an unknown tag is a reported warning, and that row degrades to `no_deterministic_coverage`. Packs
are third-party, and a rubric somebody uploaded must never be able to hard-fail Sightline.

## The model layer

Sightline runs against a local [Ollama](https://ollama.com) host. The endpoint is configuration and
there is no default pointing at one. Everything below was measured against an AGX Orin 64 GB
running Ollama 0.20.6 on 2026-09-17; where a behaviour surprised us, the measurement is in the
source next to the code that handles it (`src/sightline/llm/`).

### Models are selected by capability, and excluding vision is the wrong rule

`/api/show` reports each installed model's capabilities. The obvious filter — drop anything that
reports `vision` — is wrong, and on this host it would drop the best model available: `gemma4:e4b`
reports `completion, vision, audio, tools, thinking`, because it is multimodal rather than a vision
model. So the rule selects on what the work needs:

> **Require `completion` and `tools`; exclude `embedding`.**

Over the eight models recorded in `tests/fixtures/ollama/`, that admits `gemma4:e4b`,
`llama3.1:8b`, `llama3.2:3b` and `qwen2.5-coder:14b`. It rejects `llava:13b` and
`llama3.2-vision:11b` for having no `tools` — not for having vision — rejects `deepseek-r1:8b` for
the same reason despite its `thinking`, and rejects `nomic-embed-text` as an embedding model, which
leaves it installed and available for embedding work exactly as intended.

Every rejection is rendered with the capability list that produced it, because a model missing from
a list is indistinguishable from a model nobody looked at.

### Three behaviours of the host that change what a correct client does

**A request must pin its context window.** Ollama sizes its allocation from the model's *declared*
context length unless the request says otherwise. On this host that means a 2 GB `llama3.2:3b` is
refused outright — *"model requires more system memory (52.2 GiB) than is available (48.7 GiB)"* —
because it declares a 131,072-token window. The same call with `num_ctx: 8192` answers in 3.7
seconds. So `OLLAMA_NUM_CTX` is pinned on every call, and that refusal maps to an error whose
remedy names the context window rather than the hardware.

**The thinking pin is capability-aware.** `think: true` against a model with no `thinking`
capability is an HTTP 400, not an ignored field. Sightline sends the field only when the model
reports the capability, and refuses the request before making it otherwise.

**Thinking off does not mean no thinking.** Measured on `gemma4:e4b`, twice each way at
`temperature: 0`: with `think: true` the reasoning arrives in `message.thinking` and
`message.content` holds the answer alone. With `think: false` the reasoning is *not* suppressed —
it arrives inside `message.content`, ahead of the answer, in one run trailed by a `<channel|>`
control token. An extractor reading `content` from a thinking model with the pin off gets prose
reasoning where it expected an answer. The pin is therefore explicit per call site, with no
default, and `ChatResult` keeps the two fields apart and reports which way the call was pinned.

### Recommendations are a curated file, not a catalogue

Ollama exposes no library-search endpoint, so "what could I install?" cannot be answered by asking
the host. `src/sightline/llm/recommended.yaml` is twelve hand-picked candidates sized for a host of
this class, each with its download size read from `registry.ollama.ai`.

The community mirror at `ollama-models.zwz.workers.dev` was probed as the freshness test: it serves
170 models, carries **no capability data at all**, and does not contain `gemma4` — the model
running on the target host. It is a usable seed for a human writing that file and never a runtime
source. Ollama's own registry does know about `gemma4`, and offers no search: you can only ask it
about a name you already have.

Nothing in that file decides anything. Capability lists there are *expectations*, each labelled
with how it was arrived at, and the settings screen states the difference between the expectation
and what `/api/show` actually reported once a model is installed.

### Installing, and the thing deliberately missing

`POST /api/pull` installs a model as a background task in the same process, with progress readable
at `GET /api/models/installs` and rendered on the settings screen. An install outlives the request
that started it and survives the tab being closed.

There is no delete. `/api/delete` exists in Ollama and Sightline deliberately does not call it — a
review tool that can remove somebody's models eventually removes somebody's models. The suite
AST-scans the package to keep it that way.

### Concurrency, and logging that makes a failure loud

Concurrency defaults to **1** and one semaphore guards every call, discovery included. Every model
call logs its latency, its token counts and the host that served it, and when the host reports no
token counts that absence is logged as `tokens_reported: false` rather than omitted. This is not
decoration: an earlier local-AI pipeline on this network died from 48-way concurrency against
capacity that could not sustain it, and it died *silently*.

## The extraction layer

Ten model-backed extractors, `E01`–`E10` (SPEC §05). They locate and quote; they never rate, never
summarise and never judge. Each returns one fixed JSON shape — a list of findings carrying a
`quote` and a `location`, and nothing else — and **every quote is string-matched against the source
document before it is accepted**. A quote that does not match is discarded and logged, never
rendered.

```sh
python -m sightline.extract --bundle examples/sample-course --dry-run
python -m sightline.extract --bundle examples/sample-course --model gemma4:e4b
python -m sightline.extract --bundle examples/sample-course --model gemma4:e4b --runs 3
```

`--dry-run` calls nothing and needs no model host. It prints the passages each extractor would send
and the first prompt in full, which is the only way to read what a model is actually shown before
spending an hour of a local GPU finding out.

### One document per call, and the location is computed

An extractor is sent one artifact at a time and never asks the model which page a sentence came
from, so the model cannot invent an artifact it was never given. The schema does require it to say
where it found each quote — and **that string is never rendered**. Three models on the target host
answered that field with `"Course policies (page)"`, `"page"` and `"Week 2 overview"` for the same
question about the same page: it carries no consistent meaning, so believing it would be inventing
precision. What is rendered is computed instead — the artifact the harness sent, the source line of
the block the verified quote was found in, and the heading above that block.

A page longer than the word budget is **split, never truncated**. Truncating it would put the
passage that answers the criterion in the half nobody sent, and the model would correctly return
nothing.

### What verification does and does not catch

It catches invention. Normalization folds entities, typography, whitespace and case — two spellings
of the same characters — and nothing else: no edit distance, no token overlap, no ellipsis splice.
`three assignments` does not match `four assignments`, and `may` does not match `must`. What is kept
is the **source's own text** at the match, sliced out of the document, so a model that straightened
a curly apostrophe does not get its version rendered.

It does not catch irrelevance. A quote that is really on the page but has nothing to do with the
question is verified and shown. Measured on `llama3.1:8b` over the sample course, E09 verified 16
quotes and discarded 1, and only about four of the sixteen stated a policy about grades or late
work — the rest were real sentences about something else. That is the design working rather than
failing: a reviewer reads a quote with its location and dismisses it in seconds, which is exactly
the falsifiability SPEC §01 asks for, and it is why nothing here is allowed to skip straight to a
rating.

### Four outcomes, kept apart

| Outcome | What it means |
|---|---|
| `no evidence found` | Every passage was sent, answered and verified, and nothing bears on the question. A complete answer. |
| `nothing was examined` | There was nothing to send, and acquisition says why. Never a pass. |
| `none carried any text` | Artifacts were opened and every one was empty. Not the same as finding nothing in them. |
| `could not be read` | A call failed, or a response violated the schema. **Never** worded as "no evidence found". |

A schema violation is rejected rather than coerced. A response carrying a `rating` key is not a
response with a key to drop — it is a model doing something other than what it was asked, and
quietly deleting the key would hide that. Nothing is retried: a second sample at the same
temperature is a second answer to the same question, and choosing between them is the redundancy
SPEC §01 rules out. The 3× router below reports the disagreement instead of resolving it.

### Three runs, and what a disagreement is allowed to mean

`--runs 3` engages the disagreement router. The same extraction runs three times, the runs are
compared **on the extracted spans after normalization**, and a subject whose runs returned
different text is marked *needs attention*. Nothing votes, nothing averages, and there is no
confidence signal anywhere in the output.

**A subject whose runs agree is not marked at all.** Not "confident", not a tick, not "3 of 3". The
reason is the one in SPEC §01: a mildly agreeable model returns three identical confident answers
on a weak prompt, so a marker for agreement would be strongest exactly where the model is most
confidently wrong. Variance is a good triage signal and a bad truth signal, and only the first use
is allowed here. The run count that produced a span is recorded **only inside the disagreement
record**, where a reviewer resolving the difference needs it.

The evidence handed downstream is the **union** of the runs, never the intersection. Every span was
string-matched against the source before it was accepted, so a span one run returned is a passage
that demonstrably exists in the course — dropping it because the other two runs missed it would be
using agreement as a filter, which is the thing the router exists not to do.

Three outcomes per run per span are kept apart: returned, read-and-not-returned, and *the artifact
could not be read in that run*. A run that could not read a page did not fail to find a sentence in
it. And a passage that **every** run failed is not a disagreement at all — it is an incomplete
result, which the layer already reports on its own.

A span is identified by **where it sits as well as what it says**. One sentence can appear twice in
one artifact — a policy restated under two headings is ordinary in a syllabus — and each occurrence
is verified and located separately, so one run legitimately returns two quotes of the same text.
Keyed on the text alone the union would keep the first and drop the second. The position is safe to
compare on because the harness computes it, not the model.

An agreeing subject printing "exactly what one run produced" is meant literally: the counts and a
Tier C extractor's drafted question are there too, recomputed over the union so that the count
under a list of quotes counts the quotes above it.

### What three runs cost, measured

Over the synthetic sample course on `llama3.1:8b`, target host, 2026-09-17:

| | calls | wall clock |
|---|---|---|
| one pass, ten extractors | 127 | 953 s |
| three runs | 381 | 2 859 s |
| three runs, repeated later the same day | 381 | 2 865 s |

Exactly 3.0×. Concurrency is 1 by design, so nothing amortizes and the arithmetic is the whole
story. **On that course and that model the router flagged nothing**: all ten extractors returned
byte-identical evidence across three runs, and a fourth independent pass in a separate process 48
minutes later returned the same 226 quotes again. The whole three-run pass was then repeated from a
clean process and returned the same 226 quotes over the same ten extractors, on an identical
160 998 tokens, flagging nothing a second time. That is a measurement of one model on one synthetic
course, not a general claim — but it is the number to weigh `--runs 3` against, which is why the
default is one run.

### The verb table judges, not the model

E01 is the worked example of SPEC §01 and the pattern for every subjective criterion. The model
extracts the sentence stating what a learner must produce; `sightline.extract.bloom` takes the task
verb from the **verified quote** in code and maps it against a small, readable table; the
distribution is arithmetic. The model extracted, a lookup table judged, a human decides. A verb the
table does not carry is reported as *no table verb* and never guessed at — and a distribution is
not a score: a course whose activities sit at *remember* is not a worse course.

Tier C extractors (E05, E06, E07) assemble the same verified quotes plus the counts describing
them, and draft a **question** for the reviewer. They never pre-select an answer to it.

## Reviewing a batch of courses

Up to sixteen courses in one run, reviewed one after another against a single local model host,
resumable, and **nothing is exported**.

```sh
python -m sightline.batch run --bundle examples/sample-course --model gemma4:e4b
python -m sightline.batch run --course 4410 --course 4411 --pack uccs-oscqr-rsi
python -m sightline.batch status                 # recent runs
python -m sightline.batch status <run id>        # one run, per course and per stage
python -m sightline.batch resume <run id>        # continue one that was interrupted
python -m sightline.batch open-review <course id>
```

`--bundle` needs no Canvas token and is the way to watch the whole thing work before any
credentials exist. `--course` takes course ids or Canvas URLs. The two cannot be mixed in one run.

### Five stages, and one of them is not where the spec printed it

| Stage | What it does | What it counts |
|---|---|---|
| `acquire` | Canvas API and content export, normalized into the document model | artifacts normalized |
| `detect` | Every deterministic detector. No model | artifacts opened |
| `project` | Findings onto the chosen rubric's rows; routing decided | rows projected |
| `extract` | The ten topic extractors, then every routed row | artifacts opened |
| `score` | Effort summed and banded, action plan ordered | rows rolled up |

SPEC §04's table lists *extract* before *project*. The runner projects first, and that is a
correction rather than a preference: `sightline.rubric.routing` decides which rows a model is asked
about from each row's **coverage**, which does not exist until the detection report has been
projected onto the pack. Extracting first would mean either asking about all 68 rows of the
reference instrument or deciding coverage twice, in two places.

**A stage that examined nothing fails that course.** Item 06 established the rule for one detector
and the batch runner applies it to a stage: zero examined is an unread course, not a clean one, and
the loop enforces it regardless of what a pipeline reports.

### What happens when something fails

A course that fails fails **alone**. Its stage, its reason and its remedy go onto its row and into
the run log, the next course starts, and the run summary names it:

```
3 course(s) in this run; 2 reviewed; 1 failed.
Course 1: reviewed. 18 row(s) rolled up: …
Course 2: failed at the detect stage. The course export never arrived. Ask Canvas for a fresh one.
Course 3: reviewed. 18 row(s) rolled up: …
```

A run where some courses were reviewed and others failed is `partial`, which exists because the
alternatives both lie: `succeeded` over a batch with a failed course reads as done, and `failed`
throws away two finished reviews.

What does **not** isolate is the batch's shared dependency: an unreachable model host, a missing
credential, a rubric pack that is not on disk. Every remaining course would fail the same way, one
timeout at a time, so the batch stops, the run is left `interrupted` — resumable, with nothing known
to be wrong with the courses — and the summary says which never ran and why.

### Resume is per course, and says so

Killing the process and starting again continues the run. A finished course is not repeated and is
not silently absent either: it is reported as carried over. A course that was mid-flight starts
again at its first stage, and the log says which stage the earlier attempt stopped in. Within one
course the stages hand typed objects to each other, so there is nothing complete to resume from
half way through the expensive one.

The application resumes on startup by itself; the `resume` command is for a run started from a
terminal.

### Nothing exports until a human opens the review

A completed run leaves a reviewable result per course under `<data root>/runs/<run>/courses/<id>/`
and leaves `reports/` **empty**. The only path to a file under `reports/` is
`sightline.batch.gate.ExportGate.destination`, which refuses for a course whose review nobody has
opened, and `tests/test_export_gate.py` scans the package's syntax tree so that the XLSX writer and
the PDF report (roadmap items 13 and 14) cannot quietly reach past it when they are written.

It is not access control — Sightline is one local process with no login. It stops a *program*
exporting unattended, which is the failure the rule is about: a report delivered to a faculty
member that no instructional designer ever read.

### What a batch costs, measured

The `project` stage prints the price before anything is spent, because the routed rows cost **one
call per passage of the course, per row** (item 04 states it plainly). Over the shipped sample
course, which is 43 artifacts and 15 passages:

| Pack | Rows | Routed | Model calls for the routed rows |
|---|---|---|---|
| `uccs-oscqr-rsi` | 18 | 12 | about 180 |
| `uccs-oscqr-3-1-3` | 68 | 47 | about 705 |

Plus the ten topic extractors, which answer the course once rather than once per row. Measured on
the target host with `qwen3-coder:30b` (item 33, 2026-09-28): the 180 calls took 202–220 s and
the 705 took 685–720 s, about a second a call, with the printed estimate equal to the calls made
every time. **Most of a call's time is the quotes it writes, not the call itself** — about 25 ms per
generated token against a fraction of a second of fixed cost — so the price grows with how many
passages a course has and how much evidence they hold. The sample's passages average 64 words; a
real course's run up to 900, so its per-call cost is higher and its passage count is the
multiplier.

**Nothing bounds it, on purpose.** Item 33 measured three ways of making fewer calls — several rows
per call, a word-overlap pre-filter, and embedding similarity — and every one lost verified quotes
the one-row-per-call run found, well beyond two identical runs' disagreement with each other.
Several rows per call was also *slower*. The lever is the reviewer's: fewer routed rows (a pack
with bindings routes fewer), or fewer courses per batch. The measurement and its instrument are in
`docs/ROADMAP.md` (item 33) and `scripts/measure_row_extraction.py`.

## Canvas token scopes

So an institution can issue a least-privilege token rather than an administrative one, here is
every request Sightline makes today. `sightline.canvas.scopes` is the source of this table and
`tests/test_canvas_scopes.py` fails the build if the two drift apart.

| Scope | Request | Why |
|---|---|---|
| `url:GET\|/api/v1/users/:id` | `GET /api/v1/users/self` | Names the authenticated user in the connection test, so a token is identifiable. |
| `url:GET\|/api/v1/courses` | `GET /api/v1/courses` | Counts the courses the token can see, so an empty result is visible up front. |
| `url:GET\|/api/v1/courses/:id` | `GET /api/v1/courses/{id}` | Resolves a pasted URL, id or CSV row to a named course. |
| `url:GET\|/api/v1/courses/:course_id/pages` | `GET /api/v1/courses/{id}/pages` | Confirms course content is readable, not merely that the course is listed. |
| `url:GET\|/api/v1/courses/:course_id/pages/:url` | `GET /api/v1/courses/{id}/pages/{page_url}` | Reads a page's body. The listing endpoint returns titles only, never the HTML. |
| `url:GET\|/api/v1/courses/:course_id/modules` | `GET /api/v1/courses/{id}/modules` | Reads the module structure every artifact's place in the course is derived from. |
| `url:GET\|/api/v1/courses/:course_id/modules/:module_id/items` | `GET /api/v1/courses/{id}/modules/{module_id}/items` | Re-reads a long module's items. Canvas caps the items it includes with a module listing and gives no signal that it did. |
| `url:GET\|/api/v1/courses/:course_id/assignments` | `GET /api/v1/courses/{id}/assignments` | Reads assignments, their due dates, submission types and attached rubrics. |
| `url:GET\|/api/v1/courses/:course_id/quizzes` | `GET /api/v1/courses/{id}/quizzes` | Reads quizzes, including whether each is timed and how it is weighted. |
| `url:GET\|/api/v1/courses/:course_id/discussion_topics` | `GET /api/v1/courses/{id}/discussion_topics` | Reads discussions and announcements. Canvas models both as topics and separates them with a query parameter, not a second endpoint. |
| `url:GET\|/api/v1/courses/:course_id/assignment_groups` | `GET /api/v1/courses/{id}/assignment_groups` | Reads the assignment groups and their weights, which one criterion expects to total 100. |
| `url:GET\|/api/v1/courses/:course_id/rubrics` | `GET /api/v1/courses/{id}/rubrics` | Reads the rubrics defined in the course, so an unattached one is still visible. |
| `url:GET\|/api/v1/courses/:course_id/tabs` | `GET /api/v1/courses/{id}/tabs` | Reads the course navigation menu, including which entries are hidden from learners. |
| `url:GET\|/api/v1/courses/:course_id/files` | `GET /api/v1/courses/{id}/files` | Lists the course's files, so a document with no bytes in the export is still named. |
| `url:GET\|/api/v1/courses/:course_id/outcome_group_links` | `GET /api/v1/courses/{id}/outcome_group_links` | Lists the course's learning outcomes and the outcome group each is filed under, in one call rather than one per group. |
| `url:GET\|/api/v1/courses/:course_id/content_exports` | `GET /api/v1/courses/{id}/content_exports` | Finds a content export the course already has. This is the path taken when the fresh-export request is switched off on the Settings screen. |
| `url:POST\|/api/v1/courses/:course_id/content_exports` | `POST /api/v1/courses/{id}/content_exports` | Asks Canvas to package a copy of the course under review. It does not modify course content: an export artifact appears in the course's export list and nothing a learner or instructor sees changes. A setting turns this request off. |
| `url:GET\|/api/v1/courses/:course_id/content_exports/:id` | `GET /api/v1/courses/{id}/content_exports/{export_id}` | Polls one export until Canvas finishes building it. An export is asynchronous and a large course takes minutes, so the state has to be read back rather than assumed. |

**Sightline does not modify course content; it asks Canvas to package a copy of the course it is
reviewing.** Exactly one request in that table is not a `GET` — the `POST` that asks for a content
export — and `tests/test_canvas_scopes.py` fails the build if a **second** write ever appears.
The guard is a count that must stay at one, not a ban: a ban gets deleted the first time it is
inconvenient, while a number has to be changed in front of a reviewer.

The write is there because the checks that read a document's own bytes — whether a PDF is tagged,
whether a Word file uses real headings — have nothing to read without an export, and the Canvas
API does not hand those bytes over. What appears in Canvas is an export artifact in the course's
own export list, the same one a teacher creates with **Settings → Export Course Content**. Nothing
a learner or an instructor sees changes.

**A setting turns it off, and off is a supported configuration.** Uncheck *Ask Canvas to build a
fresh export of the course* on the Settings screen, or set
`SIGHTLINE_REQUEST_CONTENT_EXPORT=false`, and Sightline reads an export the course already has and
reports the steps to create one when there is none — every request it makes is then a read. An
institution with a no-writes policy keeps a working tool; what it loses is the checks that need a
document's own bytes, and those report that they had nothing to examine rather than reporting the
course as clean.

**A fresh export is always requested, never a recent one reused.** Canvas does not bump a course's
`updated_at` when a page inside it is edited, so an existing export cannot be shown to match the
course as it is now, and reviewing last week's course as though it were this week's is exactly the
silent wrongness this tool exists to avoid.

Requesting an export needs a permission that reading does not — the one behind Canvas's own
**Export Course Content** button — so a token that can read a course may still be refused one. The
refusal says so and names what to change.

Two kinds of Canvas token exist and they behave differently:

- A **personal access token** (Account → Settings → New Access Token) carries the full permissions
  of the user who created it and **cannot be scoped**. Least privilege for one of these means a
  service account with a restricted Canvas role, not a restricted token.
- A token issued through a **developer key** can be scoped, and the strings above are what to
  grant. Canvas spells a scope with the route's parameterised path — `/api/v1/courses/:id`, never
  `/api/v1/courses/4410`.

A scope grants access to a route; it does not grant the underlying permission. The token's user
also needs `Read course content` and `Course Content - view` in each course under review. Later
roadmap items extend this list rather than starting a second one.

## Tests, lint and format

These are the exact commands. Later roadmap items and prompts cite them.

| Task | Command |
|---|---|
| Test | `./scripts/dev.sh pytest -q` |
| Detector integrity | `./scripts/dev.sh pytest tests/test_detector_integrity.py -q -s` |
| Lint | `./scripts/dev.sh ruff check .` |
| Format check | `./scripts/dev.sh black --check .` |
| Reformat | `./scripts/dev.sh black .` |
| Scan for secrets | `./scripts/scan-secrets.sh` |

`scripts/dev.sh` builds the image's `dev` stage once and runs the given command inside it, so the
interpreter and the dependency set are the same ones CI uses. It propagates the exit code, and it
is the reason lint and format are run as **separate** commands: chaining them behind `&&` has
hidden a `black` failure before now.

Without Docker, the same three commands run against a local [uv](https://docs.astral.sh/uv/)
environment:

```sh
uv sync --extra dev
uv run pytest -q
uv run ruff check .
uv run black --check .
```

## Configuration

Every deployment-specific value is configuration with an environment fallback. There is no default
pointing at any institution or host — a hardcoded university domain or LAN address anywhere in this
repository is a bug, and `tests/test_config.py` fails the build over one.

| Variable | Meaning |
|---|---|
| `CANVAS_BASE_URL` | Base URL of your Canvas instance. **Treated as a secret**: it identifies the institution. |
| `CANVAS_API_TOKEN` | Canvas API token. Read access, plus permission to export course content when the setting below is on — see **Canvas token scopes**. |
| `SIGHTLINE_REQUEST_CONTENT_EXPORT` | Whether to ask Canvas to build a fresh content export of each course reviewed. Default `true`. Fallback for the setting of the same name on the Settings screen, which wins over it. |
| `SIGHTLINE_RESOLVE_LINKS` | Whether the policy-link check may request the addresses a course links to. Default `false` — the only traffic Sightline makes outside Canvas. With it off the check reports which policy links are present and claims nothing about where they go; a request that fails is reported as unknown, never as a broken link. |
| `SIGHTLINE_SECRET_KEY` | Key for credentials entered in the UI. Unset means Sightline manages a key file under the data root. |
| `SIGHTLINE_DATA_DIR` | Root for everything downloaded or generated. Default `./data`; `/data` in the container. |
| `SIGHTLINE_EFFORT_COSTS` | A YAML file overriding entries of the per-defect cost table (`src/sightline/rubric/costs.yaml`). Unset means the shipped, uncalibrated table. See *The effort model*. |
| `SIGHTLINE_BATCH_CAP` | Courses per run. Default `16` (SPEC §09). Lower it if you want results sooner; raising it buys a longer wait rather than more throughput, because courses are reviewed one after another. |
| `OLLAMA_BASE_URL` | Ollama endpoint. No default. Fallback for the endpoint on the Settings screen, which wins over it. |
| `OLLAMA_CONCURRENCY` | Concurrent model calls. Default `1`, ceiling `8`. |
| `OLLAMA_NUM_CTX` | Context window pinned on every model call. Default `8192`. Not a quality knob — see **The model layer**. |
| `SIGHTLINE_ESCALATION_BASE_URL` | Optional OpenAI-compatible endpoint. Sightline runs fully without it. |
| `SIGHTLINE_ESCALATION_API_KEY` | Key for the above. |
| `SIGHTLINE_TOKENS_CSS` | Override the location of `docs/tokens.css`. |
| `SIGHTLINE_PORT` | Host port for the compose service. Default `8099`. |
| `SIGHTLINE_LOG_LEVEL` / `SIGHTLINE_LOG_FORMAT` | `INFO` and `json` by default; `console` is easier to read locally. |

`CANVAS_BASE_URL` and `CANVAS_API_TOKEN` are the **fallback**. Normally both are entered on the
settings screen and held encrypted in the run store; the environment is what a container uses when
nothing has been entered yet, and a value entered in the UI wins over it so that a restart does not
silently undo a change.

Both the Canvas base URL and the token are registered with `sightline.redaction` wherever they
arrive, so they are scrubbed from log events, stored run events and error text — including
tracebacks and the HTTP client's own request logging — without any call site having to remember
they exist. The connection test renders the host on screen deliberately, because a test that cannot
say which host answered is not a test; the persistent status page still reports presence only.

### Credentials at rest

Credentials entered in the UI are encrypted with a key that is not in the database. The ciphertext
lives in the run store's `app_secrets` table; the key lives in `<data root>/secrets/credentials.key`
at mode `0600`, or in `SIGHTLINE_SECRET_KEY` if you set it.

Stated precisely, because overstating this would be worse than not doing it: the run store is the
artifact that travels — copied for a bug report, backed up, opened in a SQLite browser during a
screen-share — and encrypting means none of that exposes a token. It does **not** defend against
someone who can read the whole data root, because by default the key is a file in it. If that is
the threat, put the key in `SIGHTLINE_SECRET_KEY` and keep it somewhere the volume is not.

## What is in this repository, and what never is

This repository is **public**. Anything committed is world-readable forever, and git history keeps
it after a later deletion.

Committed on purpose:

- `rubrics/source/` — the blank rubric workbooks. Scanned sheet by sheet; no personal data.
- `docs/`, `examples/` (a **synthetic** sample course and sample report), `.env.example`.

Never committed, and enforced by `.gitignore`, `.dockerignore` and the `secrets` CI job:

- Everything under `SIGHTLINE_DATA_DIR` — course exports, caches, run state, reports.
- `.env`, `*.imscc`, `private/`.

A Canvas content export carries no student submissions and no grades. It does carry
instructor-authored content, page text naming real people, and unpublished drafts, which is why
exports stay under the data root and never move out of it. Docs, tests, fixtures and examples all
use `canvas.example.edu`.

### Fixtures, and the sanitizer

The Canvas client is tested against recorded fixtures in `tests/fixtures/canvas/`, never a live
instance: a test suite that needs a real Canvas token is a test suite that does not run in CI, and
the failure paths that matter — a rejected token, a refused course, a course visible but unreadable
— cannot be produced on demand against a real instance anyway.

Every committed fixture is **synthetic**, written by hand to the shape Canvas returns.
`tests/test_canvas_fixtures.py` asserts that mechanically: no host but `canvas.example.edu`, no
authorization header, no token shape, no address outside `example.edu`, and every personal name
drawn from a declared cast of invented reviewers.

If you ever record against a live instance to get a response shape right, it has to be scrubbed
before it can be committed, and by a script rather than by hand so the scrubbing is auditable:

```sh
python -m sightline.canvas.sanitize capture.json \
    --replace "Real Name=Reviewer A" --forbid 'your-real-host\.edu' \
    > tests/fixtures/canvas/thing.json

python -m sightline.canvas.sanitize --check tests/fixtures/canvas/thing.json
```

It keeps headers by allowlist rather than dropping them by denylist, renumbers ids through one
shared table so the capture still hangs together, replaces people and instructor-authored prose,
and then **re-reads its own output and refuses to emit anything with a residue in it**. The
sanitized capture goes to stdout and the report to stderr, so a refusal writes nothing at all
rather than half a fixture. "Mostly clean" is not a state it has.

### Design tokens

`docs/tokens.css` is the approved palette and the **single source of colour**. It is served
verbatim at `/assets/tokens.css` rather than copied into the package, because a copy is a second
source and a second source drifts. `tests/test_templates.py` fails the build if a component
stylesheet or template declares a colour literal, and it checks that every `var(--token)` a
component references is actually defined.

The token file names IBM Plex and Instrument Serif with full fallback stacks. Sightline does not
load web fonts: a locally-run review tool should not make an outbound request to render a page.

## Continuous integration

Gitea Actions, five jobs on every pull request:

| Job | What it does |
|---|---|
| `lint` | `ruff check .` |
| `format` | `black --check .` |
| `test` | `pytest -q` |
| `secrets` | `gitleaks` over the full history, plus a scan for token shapes and for the configured Canvas hostname |
| `image` | Builds the container image, so a `Dockerfile` that nobody builds cannot rot |

The `secrets` job's hostname check reads a repository secret, `FORBIDDEN_HOST_RE` — a regular
expression matching hostnames that must never appear in the tree. The real Canvas hostname lives
there and nowhere else. With the secret unset the job still runs gitleaks and the token-shape scan,
and says which check it skipped rather than passing quietly.

## Repository layout

```
docs/            SPEC.md, ROADMAP.md, RUBRIC-PACKS.md, tokens.css
examples/        synthetic sample course and sample report — never anything real
rubrics/source/  the blank rubric workbooks
rubrics/maps/    the authored mapping files: capability tags, determinacy tiers, section flags
rubrics/packs/   the two generated rubric packs, committed
scripts/         dev.sh (container-backed test/lint/format), build-packs.sh, scan-secrets.sh
src/sightline/   the application
  canvas/        the Canvas connector: client, connection test, reference resolution, sanitizer
  acquire/       acquisition and normalization: the API reader, the fetch/unzip layer, the cache
  detect/        the detector framework and the accessibility cluster D21-D30
  llm/           the Ollama client, the capability filter, and the curated model manifest
  extract/       the ten model-backed extractors, the response schema, and quote verification
  rubric/        the pack format, its JSON Schema, the importer, the mapping file, the projection
  capabilities.yaml  the capability-tag vocabulary: the one thing detectors and packs share
  model.py       the normalized document model every detector reads
tests/           the suite
  fixtures/      synthetic Canvas responses and rubric packs — never anything real
  fixtures/ollama/  captured /api/show payloads: model metadata only, guarded by a key allowlist
```

## Licence

MIT. See `LICENSE`.
