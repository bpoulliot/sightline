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
| `CANVAS_API_TOKEN` | Canvas API token. Read-only access is sufficient. |
| `SIGHTLINE_DATA_DIR` | Root for everything downloaded or generated. Default `./data`; `/data` in the container. |
| `OLLAMA_BASE_URL` | Ollama endpoint. No default. |
| `OLLAMA_CONCURRENCY` | Concurrent model calls. Default `1`. |
| `SIGHTLINE_ESCALATION_BASE_URL` | Optional OpenAI-compatible endpoint. Sightline runs fully without it. |
| `SIGHTLINE_ESCALATION_API_KEY` | Key for the above. |
| `SIGHTLINE_TOKENS_CSS` | Override the location of `docs/tokens.css`. |
| `SIGHTLINE_PORT` | Host port for the compose service. Default `8099`. |
| `SIGHTLINE_LOG_LEVEL` / `SIGHTLINE_LOG_FORMAT` | `INFO` and `json` by default; `console` is easier to read locally. |

Both the Canvas base URL and the token are registered with `sightline.redaction` at startup, so
they are scrubbed from log events, stored run events and error text — including tracebacks —
without any call site having to remember they exist.

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
tests/           the suite
```

## Licence

MIT. See `LICENSE`.
