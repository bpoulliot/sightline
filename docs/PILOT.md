# The pilot: first live runs

This is the operator's runbook for the pilot: one Sightline instance in sign-in mode on the
operator's own host, behind the TLS terminator (SWAG) and the Authentik instance already running
there (ROADMAP *Deployment*, decided 2026-11-02). It is roadmap item 50.

**No session runs this checklist.** Every step after deployment reaches live Canvas, which the
project's development rules forbid a session to do, and one of them writes to Canvas (the
content-export request). The operator runs it, in order, and records what each step showed.

**What goes back to the roadmap is aggregates.** Each step ends with *Send back*: counts, yes/no
answers, permission and setting names, durations. Never a course, a course id or URL, a person, a
quote from a course, or the institution or its Canvas host. Where this file needs a host it says
`canvas.example.edu`; groups are named only as examples (`sightline-users`, `admins`).

- [1. Deploy](#1-deploy)
- [2. The master key and backups](#2-the-master-key-and-backups)
- [3. The checklist](#3-the-checklist)
- [Restoring from a snapshot](#restoring-from-a-snapshot)

## 1. Deploy

Follow [SIGN-IN.md](SIGN-IN.md): it is the deployment, and nothing here repeats it. On this host
Authentik already exists, so start at its step 1, and point SWAG's site for the address people type
at the outpost's loopback port (`127.0.0.1:9000` by default) as its step 7 says. The host's own
compose file and SWAG configuration live in the host's repository, not this one.

Two settings are new since SIGN-IN.md's first draft and both are required before the first sign-in:

- **Group access** (item 51): SIGN-IN.md steps 2 and 5 — the application bound to both groups in
  Authentik, and `SIGHTLINE_SIGNIN_USER_GROUP` and `SIGHTLINE_SIGNIN_ADMIN_GROUP` set to the same
  groups. Checked in [step 3.1](#31-access-by-group).
- **The master key**, `SIGHTLINE_SECRET_KEY` ([section 2](#2-the-master-key-and-backups)). The app
  refuses to start without it.

Then run SIGN-IN.md's [checks after it is up](SIGN-IN.md#checks-after-it-is-up) before anything
below.

## 2. The master key and backups

**The master key.** Generate it once, put it in the host's secret environment beside
`SIGHTLINE_SIGNIN_SECRET`, and back it up through the host's secrets handling:

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

It encrypts every person's Canvas token. **Changing it means every person enters their token
again**, and a snapshot restored under a different key restores every token unreadable. There is
no rotation command. It is never in a backup of the data, on purpose.

**Backups.** `python -m sightline.backup DEST` takes a consistent snapshot of Sightline's state —
the run store, and under the data root `runs/`, `rubrics/`, `reports/` and `private/` — into
`DEST/sightline-backup-<UTC stamp>/`. It never copies `exports/`, `cache/` or `secrets/`. It is safe
to run while a run is going: the store is copied through SQLite's online backup API, not as a file.
It runs inside the app's container, so give the container a backup directory the host keeps
elsewhere, in the host's compose file, owned by the container's user (uid `10001`):

```yaml
  sightline:
    volumes:
      - sightline-data:/data
      - /srv/sightline-backups:/backups
```

```
docker compose -f docker-compose.signin.yml exec sightline python -m sightline.backup /backups
docker compose -f docker-compose.signin.yml exec sightline \
  python -m sightline.backup --check /backups/sightline-backup-20261105T031500Z
```

The backup prints a check of what it wrote and exits non-zero if anything is wrong; a destination
inside the data root is refused. Schedule it with the host's own scheduler (a nightly timer is
enough for a pilot) and keep the host's retention; Sightline does neither. Take one by hand before
step 3.6 and after the last step, at least.

## 3. The checklist

In this order. Each step says what to do, what to look at, and what to send back. A step that shows
something unexpected is a result: record it and stop there rather than working around it, because
the later steps assume the earlier ones held.

### 3.1 Access by group

Before anybody else signs in. In Authentik the Sightline application is bound to the users group
and the admin group (for example `sightline-users` and `admins`), and `.env` sets
`SIGHTLINE_SIGNIN_USER_GROUP` and `SIGHTLINE_SIGNIN_ADMIN_GROUP` to those names exactly. The app's
startup log has **no** `signin_any_identity_admitted` line; if it has one, the users group is
unset.

**Check:** one person in the users group signs in and reaches the run list; one person in the admin
group signs in and sees the instance settings' forms on Settings; one person in neither group is
refused by Authentik's binding and never reaches the app. If that person does reach the app, it
answers with a page naming the users group — the app's check held, but the binding did not, and
that is the result to record.

**Send back:** for each of the three people, reached / refused, and by what (Authentik or the app).

### 3.2 Test connection

On Settings, an admin sets the Canvas base URL (`https://canvas.example.edu` stands for yours);
then each person stores their own Canvas token and presses **Test connection**.

**Look at:** the host it reports, the user it names, the count of courses the token sees, and
whether course content is readable. A refusal names what to change.

**Send back:** passed / refused for each token type tried (personal access token, developer-key
token), the refusal's summary line if any, and the course count as *zero* or *more than zero* only.

### 3.3 The content-export request, on one course

The one write Sightline makes to Canvas (README, *Canvas token scopes*). With **Ask Canvas to build
a fresh export of the course** on, start a run of **one** course the operator may export, and watch
its acquisition stage.

**Look at:** whether Canvas accepted the `POST` and the export finished, and how long it took; the
export appearing in the course's own export list; and, if refused, the refusal's remedy. Item 23
could not name the Canvas permission the request needs without a live instance: find which role
permission made the difference (by Canvas's own name for it, the one behind **Export Course
Content**), by trying a token whose role lacks it if one is available.

**Send back:** accepted / refused; the permission's name as Canvas spells it; the export's build
time in minutes; whether turning the setting off and reading an existing export also worked.

### 3.4 Check the model host

On Settings, an admin presses **Check the model host**.

**Look at:** that it reaches the host, the Ollama version, which installed models it lists as
usable and which it excludes and why, and the model selected for runs.

**Send back:** reached / not; the Ollama version; the count of usable models; the selected model's
name.

### 3.5 Propose bindings on a live host

On **Rubrics**, a person opens the rubric pack the pilot reviews against and presses **Propose
bindings**, then reviews and accepts or declines what it proposes. Bindings are that person's own
(item 48).

**Look at:** that proposing finishes, how long it takes, how many rows it proposed a binding for,
and how many the person accepted, declined or left.

**Send back:** the duration; rows proposed, accepted, declined, left; any proposal that failed, by
its error's summary line.

### 3.6 One course with known human ratings: Start, review, export

Take a backup first ([section 2](#2-the-master-key-and-backups)). Then one course that already has
a completed human review: **Start** a run of it, follow it to the end, open its review, record
ratings where the reviewer disagrees, and **Export** the workbook and the PDF report.

**Look at:** each stage's duration on the run page; any stage that failed and its remedy; on the
review, for how many criteria the proposal and the human rating agree, differ by one band, or
differ by more; whether the exported workbook and PDF open, and read as evidence rather than
verdicts.

**Send back:** the run's wall-clock time, and per stage; stages that failed, by reason; the count
of criteria in each of agree / one band / more; whether both exports opened. Never the course or a
quote from it.

### 3.7 The calibration corpus (items 12b and 52)

Only after 3.6 held. Start runs of the calibration corpus's courses — the courses with completed
human reviews held outside the repository — a few per run, one run at a time (the model host
serves one call at a time, and later runs queue behind the first).

**Look at:** each course's acquisition; for a failure, its stage and reason.

**Send back:** how many corpus courses were acquired and how many failed, by reason (for example
*export refused*, *course not found*, *timed out*); total wall-clock time. These runs stay on the
pilot host: items 12b and 52 read them there later and commit only fitted numbers and aggregates.

### 3.8 The Authentik session cookie at browser close

Item 47 could not determine whether the outpost's own cookie is a session cookie. Sign in, close
the browser completely (every window), reopen it and go to Sightline.

**Look at:** whether it asks you to sign in again; in the browser's developer tools before closing,
the outpost cookie's *Expires* value (a date, or *Session*). A browser set to restore the previous
session can keep a session cookie alive; note the setting.

**Send back:** asked again / straight in; the cookie's expiry kind (session / dated, and the
duration if dated); the browser and whether it restores sessions. Eight hours after signing in it
must ask again whatever the browser did: send that back too.

### 3.9 What comes back (item 25)

From here on, for as long as the pilot runs. Every workbook and action plan that comes back
**edited** — after a designer or the faculty member changed it — is kept, unchanged, beside the run
it came from, under the data root's `private/`:

```
<data root>/private/returned/<run id>/<the file as it came back>
```

`private/` is in every backup; `exports/`, where course exports go, is not, and is not the place
for these. Nothing else reads them: item 25 is specified against them once a few exist.

**Send back:** how many came back, and of how many delivered. Never the files or what changed in
them.

## Restoring from a snapshot

Deliberately a procedure, not a command: restoring replaces the state people are working in, and
deserves somebody looking at each step.

1. **Check the snapshot**: `python -m sightline.backup --check <snapshot>` reports *OK*. It needs no
   configuration, so it runs anywhere the snapshot is.
2. **Have the same master key.** `SIGHTLINE_SECRET_KEY` must be the key in force when the snapshot
   was taken. Under any other key the runs restore and every stored token is unreadable, so every
   person enters theirs again.
3. **Stop the app**: `docker compose -f docker-compose.signin.yml stop sightline`. A restore under a
   running app is not a restore.
4. **Put the snapshot's directories in the data root**, replacing what is there. Move the current
   `runs/`, `rubrics/`, `reports/` and `private/` aside rather than deleting them, and with them
   the old store's `runs/sightline.sqlite-wal` and `-shm`: a stale log beside a restored store is
   not its log. The snapshot's `runs/sightline.sqlite` is the store, already where the app expects
   it. Run it as the container's user, so the files are its own, for example:

   ```
   docker compose -f docker-compose.signin.yml run --rm --no-deps \
     -v /srv/sightline-backups:/backups:ro sightline sh -c '
       set -e
       snap=/backups/sightline-backup-20261105T031500Z
       mkdir /data/before-restore
       for d in runs rubrics reports private; do
         if [ -e "/data/$d" ]; then mv "/data/$d" /data/before-restore/; fi
       done
       for d in runs rubrics reports private; do
         if [ -d "$snap/$d" ]; then cp -R "$snap/$d" /data/; fi
       done'
   ```

   `/data/before-restore/` is not in any later backup; delete it once the restore has been looked
   at. `exports/` and `cache/` are left as they are: the next run fetches what it needs again.
5. **Start the app** and look: the run list shows the runs `--check` listed; a person's **Test
   connection** passes with their stored token, which proves the key; an exported report from
   `reports/` still downloads.
