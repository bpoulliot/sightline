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
| `/settings` | Canvas base URL and token, **Test connection**, and the token-scope table below. |
| `/courses` | Resolve a course from a pasted URL, a bare id, or an uploaded CSV. |

There is also a command-line entry point, and its first form needs no Canvas token at all:

```sh
python -m sightline.acquire --bundle examples/sample-course   # the shipped synthetic course
python -m sightline.acquire 4410                              # a course on your instance
```

Everything on the web screens works with JavaScript switched off — each action is a form post that
re-renders the page with its result. `/settings` also exposes the connection test as JSON at
`POST /api/canvas/test`, which is the same code path, not a second implementation.

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
error and exited 0. With `-s` the command prints what it measured, which with the D01–D30
detectors in the registry reads:

```
30 detector(s) registered; 30 covered by 145 fixture test(s); 0 uncovered.
Observed by calling 382 test function(s) across 16 module(s): …
  1 registry sweep(s) credited to no detector: …
  D21: 5 test(s)
  …
The zero-examined invariant holds for 30 of 30 registered detector(s).
30 detector(s) declare 30 capability tag(s); 0 declaration(s) will not resolve; 16 vocabulary tag(s) have no detector yet.
```

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
summed into them are global configuration and are currently a labelled placeholder
(`src/sightline/rubric/effort.py`); a band shows as an estimate while that is true.

### Validation is asymmetric, on purpose

A **detector** declaring a tag outside the vocabulary fails hard, at class-definition time: we
write the detectors, so a typo in one is our bug and it should stop the build. A **pack** binding
an unknown tag is a reported warning, and that row degrades to `no_deterministic_coverage`. Packs
are third-party, and a rubric somebody uploaded must never be able to hard-fail Sightline.

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
| `OLLAMA_BASE_URL` | Ollama endpoint. No default. |
| `OLLAMA_CONCURRENCY` | Concurrent model calls. Default `1`. |
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
  rubric/        the pack format, its JSON Schema, the importer, the mapping file, the projection
  capabilities.yaml  the capability-tag vocabulary: the one thing detectors and packs share
  model.py       the normalized document model every detector reads
tests/           the suite
  fixtures/      synthetic recorded Canvas responses and synthetic rubric packs — never anything real
```

## Licence

MIT. See `LICENSE`.
