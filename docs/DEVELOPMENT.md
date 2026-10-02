# Development workflow

This workflow applies to AccessLens, Sightline and SyllabusLock only.

## Discuss and record

Work from the canonical roadmap, with bugs before features. Discuss one item, record the
facts and decisions, and label its readiness. READY items can be implemented. NEEDS
DISCUSSION / NEEDS DECISION items require the outstanding human decision. NEEDS MEASUREMENT
items require evidence: run a bounded experiment, commit its reusable harness under
`measurements/<item>-<slug>/`, record results and update readiness. A measurement session
does not implement the proposed feature. Recheck external blockers before spending work.

## Prepare and release work

Write one complete HEAD per item in the project's `staged/` directory. Include evidence,
settled decisions, scope, known traps and acceptance criteria. Begin with:

```html
<!-- model: gpt-6-astra -->
<!-- effort: medium -->
```

GPT-6 Astra is the selected queue model. Medium is the default; use high or xhigh where
complexity warrants it. Xhigh is the ceiling. Preserve explicit effort when migrating an
existing HEAD. Review old staged items against today's roadmap before releasing them.

Move a finished HEAD atomically into `queue/` only when its work is authorized. Never write
part of an item into an active queue. Do not concatenate standing rules into HEAD files.
The runner reads the current shared rules and project tail for every fresh session.
Queues may run at any time; “overnight” is only the historical path and service name.
No daily planning ceremony or fixed nightly schedule is introduced.

## Execute, validate and merge

One fresh session works one item, on a feature branch from current origin/main. Read
AGENTS.md and the roadmap first. Keep commits focused and use Conventional Commits.
Run relevant tests and each required lint/format command, checking their exit codes
separately. Record results and remaining work in the roadmap in the same PR.

Open the PR on Gitea. End its description with `Assisted by Codex`; do not invent a
co-author identity or add an assistant Co-Authored-By trailer. Poll required CI checks
within the same session. Missing or pending checks are not successful checks.
Immediately before squash merge, re-read the PR's full head SHA, main base and all
required statuses at that exact SHA. Confirm main moved after merging. Do not bypass
branch protection, force-push, merge HOLD work or merge unrelated PRs.

An unresolved decision, unavailable required capability or persistent failed check is
a STOP: report the exact condition and leave durable work reviewable. Never claim a
successful test or merge without evidence. A runner crash, timeout or missing completion
event must retain the queue item and its side-effect ledger for a fresh session.

Merging does not authorize deployment, release publication or restarting production.
Use synthetic test data; do not contact live Canvas or expose institution identifiers,
course exports or credentials. Changes to other projects' processes are out of scope.

## Queue status during the transition

The Codex adapter is prepared but unattended activation remains blocked pending permission
review, hook validation and the migration replay gates. Do not bypass this gate or run the
old staged replacement scripts. Interactive work follows the repository instructions and
Gitea gate now. Existing staged work remains staged until explicitly released.
