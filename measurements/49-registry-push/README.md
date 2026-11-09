# Item 49 measurement: can the Actions job token push to Gitea's container registry?

**Answer: no.** On Gitea 1.27.3 the automatic job token is a valid credential for the REST API
but the container registry refuses it at authentication. Every form tried gets HTTP 401 from the
registry's token endpoint, and `docker login gitea:3000` with it fails `unauthorized`. A refused
login means a refused push. Publishing needs a separate token with the `write:package` scope,
stored as an Actions secret. Roadmap item 49 says which token and where.

Measured 2026-11-05, Gitea `1.27.3` (`/api/v1/version`), runner `chromaserv` (label `cserv`), job
container `node:20` with the CI docker-in-docker socket, docker CLI 27.5.1. Three
`workflow_dispatch` runs of the harness from throwaway branches: 3276, 3277 (adds the token
self-test) and 3280. Run 3280 used exactly the committed [`measure-49.yml`](measure-49.yml), which
passes every credential to curl as a config on stdin (`-K -`) instead of on the command line. The
earlier runs sent the same requests with `-u`/`-H`. All three gave the same result. The tables
below are from run 3280.

## Results

**The job token is real.** This self-test has to pass before a 401 means anything.

| Probe (`GET /api/v1/repos/bitmap/sightline`) | HTTP |
|---|---|
| job token (`Authorization: token …`, 40 characters) | **200** |
| bogus token | 401 |
| anonymous | 403 |

**The registry refuses it.** `GET http://gitea:3000/v2/token?service=container_registry&scope=repository:bitmap/sightline-measure-49:push,pull`:

| Credential | HTTP |
|---|---|
| Basic, user `bitmap` (the run's actor), password = job token | 401 |
| Basic, user `gitea-actions` / `x-access-token` / `token`, password = job token | 401 / 401 / 401 |
| Basic, job token as username, empty password | 401 |
| `Authorization: Bearer <job token>` | 401 |
| `Authorization: token <job token>` | 401 |
| control: Basic, bogus password | 401 |
| control: anonymous | 401 |

The registry returns the same 401 for the job token as for a bogus password. It does not
authenticate the job token and then deny it permission. It does not authenticate it at all.

**The docker CLI agrees:**

| Step | Result |
|---|---|
| `docker login gitea:3000`, bogus password (control) | exit 1, `Get "http://gitea:3000/v2/": unauthorized` |
| `docker login gitea:3000`, job token | exit 1, `Get "http://gitea:3000/v2/": unauthorized` |
| `docker push gitea:3000/bitmap/sightline-measure-49:run-<id>` (no credentials stored) | exit 1, `unauthorized` after five retries; no layer uploaded |

**Does the daemon need `gitea:3000` as an insecure registry? Yes, and it already has it.**

* `docker info` on the CI daemon lists `gitea:3000` with `"Secure": false` (the
  `--insecure-registry=gitea:3000` flag on `act_runner_dind` in `~/docker/act_runner`). Its
  insecure CIDRs are only `127.0.0.0/8`.
* Gitea serves plain HTTP on 3000. From the job, `http://gitea:3000/v2/` gives 401 and
  `https://gitea:3000/v2/` gives no TLS answer (curl `000`).
* Control: the same Gitea at its static `docker_ci` address `172.16.250.5:3000` is not on the
  daemon's list. `docker login` there fails with `Get "https://172.16.250.5:3000/v2/": http: server
  gave HTTP response to HTTPS client`. Without the flag, every login and push to `gitea:3000`
  would fail the same way. A future workflow must use the name `gitea:3000`, not the address.

## Nothing left behind

* **Package.** No `sightline-measure-49` package exists. The push never authenticated. Checked
  read-only in Gitea's database: `select count(*) from package where lower_name like '%measure%'`
  returned 0, and `package_blob_upload` is empty. The API token available
  to the session lacks `read:package`, so the API could not be used for this check.
* **Workflow.** It existed only on the throwaway branches `measure/49-run-throwaway` and
  `measure/49-run-throwaway-2`, both deleted from the remote.
  `GET /repos/bitmap/sightline/actions/workflows` lists only `.gitea/workflows/ci.yml`.
  The three runs remain in the Actions history as records.

## Re-running it

1. Branch off `origin/main` and copy `measure-49.yml` to `.gitea/workflows/measure-49.yml`. It
   must never merge.
2. Push the branch, then `POST /api/v1/repos/bitmap/sightline/actions/workflows/measure-49.yml/dispatches`
   with `{"ref": "<branch>"}`.
3. Read the job log (`GET /actions/runs/{id}/jobs`, then `/actions/jobs/{job_id}/logs`). Every
   line that matters prints `->` or `RESULT`. The token is never printed, and Gitea masks it as
   well.
4. If a push ever lands, step M6 deletes the manifest with the same token. Check with the database
   query above that the package row is gone too. Then delete the branch.

To test a `write:package` secret instead of the job token, point `JOB_TOKEN` at
`secrets.<NAME>`. A good result is 200 from the token endpoint, `Login Succeeded`, a pushed
digest, then a deleted manifest.
