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

## 2026-11-10: the operator's `REGISTRY_TOKEN` pushes, from casper

The operator created a `write:package`-only PAT for `bitmap` as the repository secret
`REGISTRY_TOKEN` (recorded in `~/docker/SECRETS.md` §3.21). Harness:
[`measure-49-casper.yml`](measure-49-casper.yml), run on the **casper** runner (operator's choice).

| run | result |
|---|---|
| 3559 | refused before any probe: casper's runner had no `valid_volumes`, so `-v /var/run/docker.sock` was ignored (`is not a valid volume`). Fixed in `~/docker` PR #762 (the same rule chromaserv has) |
| 3560 | **push landed** (`Login Succeeded`, digest pushed), but every curl probe and the delete got no reply: see the DNS trap below. Its version was deleted by 3561 |
| 3561 | **all pass.** Package API: token 200, bogus 401. Token endpoint: token 200, bogus 401. `docker login`: bogus exit 1, token exit 0. Push exit 0. Version delete: 200 → 204 → 404; 3560's leftover 204 → 404; no `sightline-measure-49` version remains |

**DNS trap on casper.** Job containers inside casper's dind get *public* DNS, where
`registry.bitmapserv.org` is chromaserv's WAN address (216.82.9.150); SWAG's direct guard drops that.
The dind daemon itself resolves through the LAN Pi-hole, so `docker login`/`push` work either way.
Anything a job ran itself (curl, an API call) needed
`--add-host=registry.bitmapserv.org:192.168.1.3` in the job container's `options`. *Fixed at the
runner the same day (below): no longer needed.*

**Timing.** casper: 6 s per job (setup ~2 s, docker CLI install ~2 s), dind peak ~0.9 core and
360 MiB, host load 0.14. chromaserv's 2026-11-05 runs took ~61 s, but ~50 s of that was the
job-token failure path and the by-IP control, which this harness does not run: these runs do not
compare build speed. A real `runtime` build on each runner would.

### Runner comparison: a real `runtime` build (run 3564)

[`measure-49-buildcmp.yml`](measure-49-buildcmp.yml): both runners at once, the same commit, a
300.5 MB image. Saturation sampled every ~3 s on each host's CI daemon while the jobs ran.

| | casper (8 CPU, 32 GB) | chromaserv (24 CPU, 64 GB) |
|---|---|---|
| cold build (`--no-cache --pull`) | 38.3 s | 37.1 s; 35.1 s run alone |
| warm build (cache) | 0.1 s | 0.2 s; 0.1 s |
| push, real image | 6.2 s (`registry.bitmapserv.org`, TLS via SWAG) | refused `blob unknown`; 4.3 s run alone (`gitea:3000`) |
| whole job | 50 s | 51 s; 45 s |
| host load during the job | 0.2 – 1.1 | 20.0 – 20.8 (its usual load) |
| CI daemon CPU, mean / peak | 125 % / 497 % | 84 % / 276 % |
| CI daemon memory, peak | 266 MiB | 547 MiB |

* **Speed is a wash.** The build is network- and single-thread-bound, so 3x the cores buys about a
  second. casper does it on an idle host; chromaserv adds it to a load of ~20.
* **Concurrent pushes of the same layers to one package race.** With both jobs pushing at once,
  chromaserv's push failed `blob unknown`; re-run alone it passed. The tag build must push from
  **one** job, under a `concurrency:` group, never from a matrix.
* **DNS.** `~/docker` PR #765 gave casper's dind `--dns 192.168.1.3 --dns 192.168.1.205`, and the
  operator deleted the public `registry` record the same day. A casper job now resolves
  `registry.bitmapserv.org` to 192.168.1.3 with no `--add-host` (this run's `getent`).

## Nothing left behind (2026-11-05 runs)

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

To test a `write:package` secret instead of the job token, use `measure-49-casper.yml` (casper), or
point `JOB_TOKEN` at `secrets.<NAME>` in `measure-49.yml` (chromaserv). A good result is 200 from the token endpoint, `Login Succeeded`, a pushed
digest, then a deleted manifest.
