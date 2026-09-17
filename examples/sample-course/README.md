# Sample course

A whole Canvas course in Sightline's normalized document model, written by hand and committed
because it does three jobs at once:

- it is what a new user runs **without a Canvas token**,
- it is the test suite's fixture for everything downstream of acquisition, and
- it is the thing every detector is developed against, because it carries a defect for each one on
  purpose.

```sh
python -m sightline.acquire --bundle examples/sample-course
python -m sightline.acquire --bundle examples/sample-course --json | head -40
```

## It is synthetic, and that is not a formality

Nothing here came from a real course. The repository is public, and a Canvas content export —
which is where a real sample would come from — carries instructor-authored content, page text
naming real people, and unpublished drafts. (It carries no student submissions and no grades;
worth knowing, and not worth overstating.) So this course was invented: a fake institution at
`canvas.example.edu`, a fake course code, no person named anywhere, and no address outside
`example.edu`. `tests/test_sample_course.py` enforces all of that mechanically rather than
trusting that someone was careful.

## What is in it

```
course.json     the course, its structure, and one body_file reference per artifact
bodies/*.html   the HTML, kept as HTML because course HTML is only legible as course HTML
defects.json    every planted defect and every deliberately clean case
```

Four modules, seven pages, three assignments, one quiz, two discussions, two announcements, six
files, fifteen navigation entries, three gradebook groups and two rubrics. Small enough to read in
one sitting, which is the constraint that decided the size.

Embeds are **not** listed in `course.json`. They are re-scanned from the bodies when the bundle
loads, so the file cannot come to disagree with its own HTML about what it embeds. Module paths
work the same way: the modules say where each artifact sits, and the artifacts inherit it.

## The planted defects

`defects.json` is the registry, and it is a checkable artifact rather than prose. Each row names a
detector from SPEC §05, the artifact it applies to, and usually a literal marker — a string that
must still be present for the row to be true. `tests/test_sample_course.py` fails the build if a
row names a detector the spec does not list, an artifact this course does not contain, or a marker
that has drifted out of the body it claims to be in. Editing a body without updating the registry
breaks the build, which is the point: a fixture that quietly stops testing what it says it tests
is the same failure as a test suite that collects nothing and exits green.

Three expectations appear:

| Expectation | Meaning |
|---|---|
| `defect` | The detector should find something here. |
| `clean` | The detector should find nothing here — the case a detector that fires on everything gets wrong. |
| `not_covered` | The sample cannot exercise this detector yet, and the row says why. The detector must still record what it could not read. |

`clean` rows are half the value. A detector with only defects to look at passes its tests by
returning "found it" unconditionally.

Three detectors are `not_covered` today and each says so rather than being quietly absent:

- **D02, D30, D36** need a document's own bytes. This course ships file *metadata* and no file
  bytes, so PDF tagging, DOCX styles and printability cannot be read from it. Bytes arrive with a
  content export, which is acquired at run time and never committed. D02 still has a row and still
  has to speak: its `not_covered` case is satisfied by recording `syllabus.pdf` as something it
  could not read, not by coming back clean.
- **D45** is tier C: the sample supplies the external tool, not the answer about its mobile app.

**D11 was `not_covered` and is not any more.** It needed course outcomes, which nothing acquired;
item 07 added the `outcome_group_links` endpoint and this course now records two outcomes, one of
them with an empty description for D11 to find.

## Adding to it

Add the artifact to `course.json`, the body to `bodies/`, and the row to `defects.json` in the same
change. Run:

```sh
./scripts/dev.sh pytest -q tests/test_sample_course.py
```

Keep every hostname `canvas.example.edu` or a subdomain of `example.edu`, and name no person at
all. The tests check both.
