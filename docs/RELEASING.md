# Releasing Sightline

A release is a container image of the `runtime` stage, published to the Gitea registry as
`registry.bitmapserv.org/bitmap/sightline`. The workflow is
[`.gitea/workflows/release.yml`](../.gitea/workflows/release.yml) (roadmap item 49).

**The operator cuts a release by pushing a `vX.Y.Z` tag. A merge never publishes one**, so an
unattended queue merge cannot publish (roadmap item 49, *Decided 2026-11-02*). Versions are
semantic: a patch (`x.y.N`) for fixes, a minor (`x.N.0`) for backward-compatible features, a major
(`N.0.0`) for breaking changes.

## Cutting a release

1. Bump `version` in `pyproject.toml` in an ordinary PR, and merge it. (Skip this for a version
   `pyproject.toml` already says.)
2. Tag the merge commit on `main` with an annotated tag and push that one tag:

   ```sh
   git fetch origin
   git tag -a vX.Y.Z -m "Sightline vX.Y.Z" origin/main
   git push origin vX.Y.Z
   ```

3. Watch the `Release` run in Gitea's Actions tab. It runs on the **casper** runner.

A failed tag run is not fixed by moving or re-pushing the tag: read the log, fix forward in a PR,
and either re-run the job or cut the next patch version.

## What the run does

One job, under a `concurrency:` group so two releases never push at once (concurrent pushes of the
same layers raced to `blob unknown`: `measurements/49-registry-push/`, run 3564).

1. **Refuses a tag whose version is not `pyproject.toml`'s**, and any tag that is not a plain
   `vMAJOR.MINOR.PATCH` (no pre-release suffix). `scripts/release.py check-version`.
2. Builds `--target runtime` (with `--pull`), labelled with the version and the commit.
3. Runs the health check CI's `image` job runs, `scripts/image-healthcheck.sh`: the image must
   start and answer `/healthz`.
4. Logs in to `registry.bitmapserv.org` as `bitmap` with the repository secret `REGISTRY_TOKEN`
   (`--password-stdin`; a `write:package` token, `~/docker/SECRETS.md` §3.21).
5. Pushes `:X.Y.Z` and `:X.Y`, and `:latest` only when X.Y.Z is the highest `vX.Y.Z` tag **by
   version order**, not by date, so a patch to an older minor does not take `latest` back
   (`scripts/release.py names`). Then reads each name's manifest back from the registry and fails
   unless all of them serve the digest that was pushed.
6. Writes the release note to the log: the Conventional Commits since the previous `vX.Y.Z` tag by
   version order (every commit when there is none), merges excluded, grouped by type
   (`scripts/release.py notes`). It then asks Gitea, with the job's own token, to create the
   release for the tag with that note and the image digest. A refusal is a warning, not a
   failure: the image is already published and the note is in the log.

The registry name is the LAN TLS name. Never push through `git.bitmapserv.org` (behind Cloudflare,
which caps a request at 100 MB), and `gitea:3000` resolves only on chromaserv.

## The dry run

*Actions → Release → Run workflow* on any branch (or `POST
/api/v1/repos/bitmap/sightline/actions/workflows/release.yml/dispatches` with
`{"ref": "<branch>"}`). `dry_run` defaults on. It runs every step against the scratch package
`bitmap/sightline-dryrun`, pushing the single tag `dryrun-<run id>`; it checks a synthetic tag built
from `pyproject.toml` (or the `version_tag` input, which is how a mismatch refusal is shown); it
prints the names a real release would push and the note, creates no release, and finally deletes
the pushed version with the same token and fails unless the version then answers 404.

Gitea keeps an internal `_upload` row for any package that has received a blob; it is not a
version, is not listed or pullable, and the dry run cannot delete it.

A dispatch with `dry_run` off publishes for real, and is refused unless it runs on a `v*` tag ref.

## Pulling the image

Gitea serves no anonymous pulls. A host pulls after `docker login registry.bitmapserv.org -u
bitmap` with a token that has `read:package`. The compose files build `sightline:local` from the
checkout; to run a release instead, point `image:` at `registry.bitmapserv.org/bitmap/sightline:X.Y.Z`.

## Retention: where the rule lives

Gitea's package cleanup rules are per owner, not per repository: *user `bitmap` → Settings →
Packages → Cleanup rules* in the web UI, stored in Gitea's `package_cleanup_rule` table. One rule
covers every `bitmap` container image, Sightline's and AccessLens's alike; it was set by AccessLens
item 95 and is recorded in `~/docker` TODO I59. Item 49 read it (2026-11-10) and did not change it.

Gitea 1.27.3 compiles each pattern as `(?i)\A<pattern>\z` and, for each package, skips the
`keep_count` newest versions, then keeps a version that is `latest`, or matches the keep pattern, or
is newer than `remove_days`, or does **not** match the remove pattern; anything else is deleted.
With the rule as stored (keep 10, keep `^(main|v.*)$`, remove `^sha-`, remove days 0), Sightline's
`X.Y.Z` and `X.Y` are kept because they do not match the remove pattern, and `latest` is always
kept. A rule edited later must keep that true; roadmap item 49 has the reading in full.
