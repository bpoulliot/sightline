# Sightline development instructions

- Gitea `bitmap/sightline` is the public primary repository. There is no GitHub remote;
  do not add one. Use feature branches and Gitea PRs, never direct pushes to main.
- `docs/ROADMAP.md` is canonical for status and decisions; `docs/SPEC.md` is canonical
  design. Read both and [Development workflow](docs/DEVELOPMENT.md) before work.
- Bugs before features; one item per session. READY permits implementation. NEEDS
  DISCUSSION/DECISION requires a human decision. NEEDS MEASUREMENT means measure,
  commit the reusable harness and results and update readiness, without feature work.
- Sightline presents evidence, not verdicts. Preserve the four determinacy tiers.
  Do not introduce grades, rankings or confidence scores. Do not turn a heuristic
  absence into a conclusive claim. Check SPEC and its corrections log before design work.
- The Canvas base URL itself is a secret. Use `canvas.example.edu` and fake tokens
  in examples. Use synthetic courses only; no live Canvas calls, even during QA.
  Content export POSTs write remote state. Real exports stay in the ignored data dir.
- Keep one process per container. UI changes need desktop and mobile review.
- Python 3.12 is the CI baseline. Install with `uv sync --frozen --extra dev --python 3.12`.
  Run `uv run ruff check .`, `uv run black --check .` and `uv run pytest -q`
  separately; inspect all exit codes. PDF rendering needs the native libraries and
  the image gate verifies the rendered sample in the pinned application environment.
- Required exact-head PR statuses are `CI / lint (pull_request)`,
  `CI / format (pull_request)`, `CI / test (pull_request)`,
  `CI / secrets (pull_request)` and `CI / image (pull_request)`.
  Poll within the session, reread full head SHA/main base/checks immediately before
  the merge (merge style `merge`), and independently confirm main moved. Missing/pending/red is not green.
- Use Conventional Commits, focused changes and roadmap updates in the same PR.
  PR descriptions end with `Assisted by Codex`; no invented assistant co-author.
- Never force-push, use hard reset, bypass protection, deploy, publish a release,
  restart production or change another project's development process.
- The local lane is `~/docker/overnight/projects/sightline/`. Compose HEAD-only files
  in staged/, then atomically release authorized complete items to queue/. Do not copy
  standing rules into HEADs. Codex unattended activation remains gated; see DEVELOPMENT.
