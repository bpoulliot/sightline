# Rubric packs

**Sightline reviews the course. The rubric is only the lens.**

Detectors emit facts about course artifacts — *"12 images have no alt text, at these locations."* A
**rubric pack** decides how those facts are reported and rated. UCCS OSCQR 3.1.3 is the reference
instrument; it is not the schema, not a required intermediate, and not something your rubric has to
be aligned to. Quality Matters, an internal departmental checklist, or an instrument you wrote last
week loads unchanged and gets a working review. Nothing here requires a pack to be an OSCQR
derivative, to carry an OPEN SUNY number, or to be crosswalked to anything.

A pack is one YAML file. Adding a rubric costs a mapping file, not code.

---

## The shortest useful pack

```yaml
schema_version: 1
id: departmental-checklist
name: Departmental Online Course Checklist
version: "2026.1"

sections:
  - code: A
    title: Before the term starts

criteria:
  - code: A1
    section: A
    text: The course shell is published before the first day of class.
    tier: B
```

That is valid and it runs. Its one row has no binding, so it comes back
`NO_DETERMINISTIC_COVERAGE` and is routed to the extraction layer for a quoted passage with a
location. **That is the ordinary state for a rubric Sightline does not ship a mapping for, and it
is a success case, not a degraded one.**

Check what a pack can answer before you point it at a course:

```
python -m sightline.rubric coverage --pack path/to/pack.yaml
```

```
Departmental Online Course Checklist 2026.1: 0 of 1 criteria have deterministic coverage,
1 are evidence-only.
```

## Importing a workbook

```
python -m sightline.rubric import --workbook "my-rubric.xlsx" --id my-rubric --name "My rubric" \
    --version 2026.1 > my-rubric.yaml
```

The pack goes to standard output and the report to standard error, so the command pipes. There is
no `--out`: `src/sightline/paths.py` is the only module in the package allowed to create a writable
path, and `tests/test_paths.py` enforces that structurally.

The importer finds the columns rather than assuming them, and it reports what it decided — which
column held the criterion text, which held the codes, how many rows, and anything it could not
find. Read that report; it is what an import is checked against. An import that yields **zero rows
is an error**, every time: a zero-row pack reported as an empty rubric would be a course reviewed
against nothing.

What comes out has **no bindings**, and it is finished. No rubric workbook has a column naming a
detector, so no import can produce one.

```
python -m sightline.rubric import --csv "my-rubric.csv" ...
```

CSV is a lossy fallback for a file no spreadsheet reader can open. The report says so every time,
and it counts any replacement characters the export has already lost.

---

## Bindings, and the three states

A pack binds each row to zero or more **capability tags** — names for course-quality concepts, from
the closed vocabulary in [`src/sightline/capabilities.yaml`](../src/sightline/capabilities.yaml).

```yaml
  - code: ACD 8
    section: ACD
    text: Images have meaningful alternative text.
    tier: A
    priority: Essential
    bindings: [alt_text]
```

A tag is never a detector id and never a criterion. `python -m sightline.rubric tags` prints the
vocabulary with the detectors behind each concept.

Three states come out of a binding, and Sightline keeps them apart everywhere:

| State | What it means | Normal? |
|---|---|---|
| `deterministic` | A bound capability a detector implements, and it ran | — |
| `not_yet_implemented` | The capability exists; no detector implements it yet | Transient |
| `no_deterministic_coverage` | No usable binding. Routes to the extraction layer | **Yes, permanently** |

Only a `deterministic` row whose detectors actually examined something and found nothing reads
**Sufficiently Present**. Nothing else can, ever. An unbound row or an absent detector producing a
clean reading would mean Sightline reporting a course clean on a criterion it never examined.

**Several detectors may share a tag, and a row may bind several tags.** Both directions work at
once. A row fed by several detectors sums their effort and bands the total — one rule for every
row, so there is one effort model to calibrate rather than sixty-eight.

### An unknown tag is a warning, not a failure

If a pack binds a tag this build does not recognise, the row degrades to
`no_deterministic_coverage`, a warning names the row and the tag, and the pack still loads and still
runs. This is deliberately **not** symmetrical with the detector side, where an unknown tag is a
hard build failure: we write the detectors, and a rubric you uploaded must never be able to
hard-fail the tool.

---

## The fields

### Top level

| Field | Required | Notes |
|---|---|---|
| `schema_version` | yes | `1` |
| `id` | yes | Lower-case slug. Chosen once and never renumbered, so a re-import diffs against it. |
| `name` | yes | What to call the instrument in the interface and the report. |
| `version` | yes | The instrument's own version, as a string — instruments are numbered like `3.1.3`. |
| `description` | no | |
| `source` | no | Where the rows came from: `title`, `workbook` (filename only), `sheet`, `imported_at`, `key_source`, `notes`. |
| `priority_order` | no | The instrument's own priority labels, most important first. |
| `rating_scale` | no | Defaults to the reference instrument's printed bands. |
| `sections` | yes | At least one. |
| `criteria` | yes | At least one. |

### `sections[]`

| Field | Required | Notes |
|---|---|---|
| `code` | yes | What criteria point at. |
| `title` | yes | |
| `optional` | no | Whether this section **may** be switched off. Default `false`. |
| `enabled` | no | Whether this pack switches it on. Default `true`. Requires `optional: true` to be `false`. |
| `note` | no | Why it is optional, or why it is off. Shown beside the omission. |

`optional` is the optional-render flag. A block that applies only to some courses — a
mobile-standards section, say — declares itself optional, and only then may `enabled: false` drop
its rows. A mandatory block cannot be switched off by a typo, and rows dropped this way are
*reported* as omitted rather than vanishing.

### `criteria[]`

| Field | Required | Notes |
|---|---|---|
| `code` | yes | **The key every lookup uses.** Never a position — see below. |
| `section` | yes | A section code declared above. |
| `text` | yes | The criterion as written on the instrument, verbatim. |
| `tier` | yes | `A` computed, `B` located and quoted, `C` judgement (never pre-rated), `D` not determinable. |
| `priority` | no | The instrument's own label, **or null**. |
| `bindings` | no | Capability tags. Zero is normal. |
| `note` | no | A statement carried into the report beside this row. |
| `source_ids` | no | The source instrument's own identifiers, verbatim. |

**Codes, not positions.** The reference instrument's assessment category runs 1, 2, 3, 4, 5, **7** —
it skips an index. Anything walking positions misaligns criteria silently, which is the quietest
possible way to report a finding against the wrong row.

**`priority` is nullable and null is not a gap.** The reference instrument's mobile block carries no
priority flag at all. Ordering tolerates null and sorts it after every labelled priority; nothing
fills it in with a default.

**`source_ids` is free-form on purpose.** A third-party instrument's identifiers are its own
business. Keeping them verbatim is what lets a re-import of a revised edition diff cleanly instead
of silently renumbering criteria.

### `rating_scale`

```yaml
rating_scale:
  sufficiently_present: Sufficiently Present
  not_applicable: Not Applicable
  no_response: No Response
  bands:
    - {rating: minor,    label: Minor Revision,    up_to_hours: 0.5,  range_label: 1/2 hour or less}
    - {rating: moderate, label: Moderate Revision, up_to_hours: 2.0,  range_label: 1/2-2 hours}
    - {rating: major,    label: Major Revision,    up_to_hours: null, range_label: 2+ hours}
```

**Band thresholds are pack data; per-defect costs are not.** The bands belong to the instrument —
the reference one prints these three ranges as column headers, and the RSI instrument prints the
same three. The per-defect costs summed into them are one global effort model, calibrated once, and
they are currently an obvious placeholder (`src/sightline/rubric/effort.py`). A band shows as an
estimate while that is true.

`up_to_hours` is an *inclusive* ceiling — "1/2 hour or less" includes a half hour — and must be
`null` on the last band, which is open-ended. Sufficiently Present has no band, because it means
"no qualifying defects found" rather than zero hours.

---

## Validation

Packs are validated on load against the JSON Schema committed at
[`src/sightline/rubric/pack.schema.json`](../src/sightline/rubric/pack.schema.json). A failure names
the offending path:

```
my-rubric.yaml: $.criteria[37].tier: 'E' is not one of ['A', 'B', 'C', 'D']
```

Beyond the schema, the loader also rejects: a duplicate row code, a duplicate section code, a row
in a section the pack does not declare, `enabled: false` without `optional: true`, bands that do
not ascend, and a closed top band.

An unrecognised capability tag is **not** in that list. It is reported and the row degrades.

---

## What is not in a pack

- **Detector ids.** A pack binds to concepts, so it survives a detector refactor.
- **Per-defect costs.** One global effort model, not sixty-eight.
- **Any other instrument's numbering.** No crosswalk is needed, wanted, or read.
- **A rating for anything.** Ratings are computed from findings at review time, and a Tier C row
  never gets one at all.
