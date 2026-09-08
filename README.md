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

Everything works with JavaScript switched off — each action is a form post that re-renders the
page with its result. `/settings` also exposes the connection test as JSON at
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

All four are read-only, and nothing in Sightline writes to Canvas.

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
| `CANVAS_API_TOKEN` | Canvas API token. Read-only access is sufficient — see **Canvas token scopes**. |
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
docs/            SPEC.md, ROADMAP.md, tokens.css
examples/        synthetic sample course and sample report — never anything real
rubrics/source/  the blank rubric workbooks
scripts/         dev.sh (container-backed test/lint/format), scan-secrets.sh
src/sightline/   the application
  canvas/        the Canvas connector: client, connection test, reference resolution, sanitizer
tests/           the suite
  fixtures/      synthetic recorded Canvas responses — never anything real
```

## Licence

MIT. See `LICENSE`.
