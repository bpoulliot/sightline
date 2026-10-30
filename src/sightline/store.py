"""The run store: everything about a batch that must survive a process restart.

The engine is created against a path handed out by :class:`sightline.paths.DataPaths`, so the
database file cannot land outside the configured data root. SQLite is opened with foreign keys on
and WAL, which matters because one process reads the store from a request handler while the
background task writes to it.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Engine, create_engine, event, select, update
from sqlalchemy.orm import Session, sessionmaker

from .logging import get_logger
from .models import (
    TERMINAL_STATUSES,
    AppSecret,
    AppSetting,
    Base,
    RatingOverride,
    Run,
    RunCourse,
    RunEvent,
    RunStatus,
    as_utc,
    utcnow,
)
from .ownership import RunOwner, orphan_reason
from .paths import DataPaths
from .redaction import scrub, scrub_object
from .rubric.states import Rating

log = get_logger("sightline.store")

#: Columns added to the schema after an earlier item shipped it, and the whole of this project's
#: migration mechanism. ``Base.metadata.create_all`` creates missing *tables* and never alters an
#: existing one, so a column a later item adds is simply absent from a data root somebody already
#: has — and the symptom is ``no such column`` on the first query, at runtime, in front of them.
#: There is no Alembic here on purpose: one process, one SQLite file, and a handful of additive
#: columns is not a migration framework's problem. Anything that is not an additive column (a
#: rename, a type change, a backfill) is where that stops being true, and is not silently
#: expressible here.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    # Roadmap item 17: the export gate. Null for every course a previous version recorded, which
    # is the correct reading — nobody opened their review.
    "run_courses": {"review_opened_at": "DATETIME"},
    # Roadmap item 43: the owner record. Null on every run a previous version recorded, which
    # recovery reads as "no owner" — the same orphaned run it always took over.
    "runs": {
        "owner_host": "VARCHAR(255)",
        "owner_pid": "INTEGER",
        "owner_heartbeat_at": "DATETIME",
        # Roadmap item 22, part two: the accepted bindings a run uses. Null on every run a
        # previous version recorded, which reads as the shipped pack — what those runs ran with.
        "bindings_digest": "VARCHAR(80)",
    },
    # Roadmap item 44: the signed-in subject beside the reviewer's display name. Null on every
    # override a previous version recorded, and on every one recorded in local mode — those keep
    # their name and have no id.
    "rating_overrides": {"reviewer_subject": "VARCHAR(255)"},
}


def create_store_engine(url: str, *, echo: bool = False) -> Engine:
    """Create the SQLite engine used by the run store."""
    engine = create_engine(url, echo=echo, future=True)

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine


class RunStore:
    """Thin, explicit data access over the three run tables."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @classmethod
    def open(cls, paths: DataPaths, *, echo: bool = False) -> RunStore:
        """Open (creating if needed) the store beneath ``paths``."""
        paths.ensure()
        store = cls(create_store_engine(paths.database_url(), echo=echo))
        store.create_all()
        return store

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self.apply_added_columns()

    def apply_added_columns(self) -> list[str]:
        """Add any column in :data:`ADDED_COLUMNS` that an older data root does not have yet.

        Returns what it added, so a caller — and the suite — can assert on it rather than trust
        it. A fresh database gets every column from ``create_all`` and this finds nothing to do.
        """
        applied: list[str] = []
        with self.engine.begin() as connection:
            for table, columns in ADDED_COLUMNS.items():
                rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
                present = {str(row[1]) for row in rows}
                if not present:  # pragma: no cover - create_all has just made every table
                    continue
                for name, ddl in columns.items():
                    if name in present:
                        continue
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                    applied.append(f"{table}.{name}")
        if applied:
            log.info("schema_columns_added", columns=applied)
        return applied

    def dispose(self) -> None:
        self.engine.dispose()

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._sessions()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # -- writes ------------------------------------------------------------------------------

    def create_run(
        self,
        course_refs: Sequence[str],
        *,
        rubric_pack: str,
        bindings_digest: str | None = None,
        note: str | None = None,
    ) -> str:
        """Record a new batch. Returns the run id."""
        if not course_refs:
            raise ValueError("a run needs at least one course reference")
        with self.session() as session:
            run = Run(rubric_pack=rubric_pack, bindings_digest=bindings_digest, note=note)
            run.courses = [
                RunCourse(ordinal=index, course_ref=ref) for index, ref in enumerate(course_refs)
            ]
            session.add(run)
            session.flush()
            run_id = run.id
            session.add(
                RunEvent(
                    run_id=run_id,
                    event="run_created",
                    data={
                        "courses": len(course_refs),
                        "rubric_pack": rubric_pack,
                        "bindings": bindings_digest,
                    },
                )
            )
        log.info("run_created", run_id=run_id, courses=len(course_refs), rubric_pack=rubric_pack)
        return run_id

    def set_run_status(self, run_id: str, status: RunStatus) -> None:
        with self.session() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise KeyError(run_id)
            run.status = status
            if status is RunStatus.RUNNING and run.started_at is None:
                run.started_at = utcnow()
            if status in TERMINAL_STATUSES:
                run.finished_at = utcnow()
            session.add(RunEvent(run_id=run_id, event="run_status", data={"status": status.value}))

    def set_course_status(
        self,
        course_id: str,
        status: RunStatus,
        *,
        stage: str | None = None,
        error: str | None = None,
    ) -> None:
        with self.session() as session:
            course = session.get(RunCourse, course_id)
            if course is None:
                raise KeyError(course_id)
            course.status = status
            if stage is not None:
                course.stage = stage
            course.error = scrub(error) if error else None
            session.add(
                RunEvent(
                    run_id=course.run_id,
                    course_id=course_id,
                    event="course_status",
                    level="error" if status is RunStatus.FAILED else "info",
                    stage=stage,
                    data={"status": status.value, "error": course.error},
                )
            )

    def mark_review_opened(self, course_id: str) -> datetime:
        """Record that a person opened this course's review. The one door the export gate has.

        Called only from :mod:`sightline.batch.gate`, which is the module that states what it
        means; this method is the write and nothing else. Re-opening a review does not move the
        timestamp: the question a report asks is *whether* a human has looked, and the first time
        is the honest answer to it.
        """
        with self.session() as session:
            course = session.get(RunCourse, course_id)
            if course is None:
                raise KeyError(course_id)
            if course.review_opened_at is None:
                course.review_opened_at = utcnow()
                session.add(
                    RunEvent(
                        run_id=course.run_id,
                        course_id=course_id,
                        event="review_opened",
                        data={"ordinal": course.ordinal},
                    )
                )
            opened = as_utc(course.review_opened_at)
            assert opened is not None  # just set, or already present
            return opened

    def set_course_stage(self, course_id: str, stage: str) -> None:
        """Move a running course to its next stage. No event: ``stage_started`` is the event.

        Separate from :meth:`set_course_status` because a stage change is not a status change, and
        going through that method would write a second ``course_status`` row per stage — five per
        course saying nothing that the stage events do not already say better.
        """
        with self.session() as session:
            course = session.get(RunCourse, course_id)
            if course is None:
                raise KeyError(course_id)
            course.stage = stage

    def append_event(
        self,
        run_id: str,
        event_name: str,
        *,
        course_id: str | None = None,
        level: str = "info",
        stage: str | None = None,
        **data: Any,
    ) -> None:
        """Append to the run log. Payloads are scrubbed before they are stored."""
        with self.session() as session:
            session.add(
                RunEvent(
                    run_id=run_id,
                    course_id=course_id,
                    event=event_name,
                    level=level,
                    stage=stage,
                    data=scrub_object(data) if data else None,
                )
            )

    # -- rating overrides --------------------------------------------------------------------
    #
    # Append-only on purpose: there is a write and there are reads, and there is no update and no
    # delete. See sightline.models.RatingOverride for why the record is the calibration data.

    def record_override(
        self,
        course_id: str,
        *,
        criterion: str,
        pack_id: str,
        pack_version: str,
        machine_rating: str,
        machine_hours: float | None,
        machine_near_boundary: dict[str, Any] | None,
        machine_findings: Sequence[dict[str, Any]],
        effort_calibrated: bool,
        human_rating: str,
        reviewer: str,
        reviewer_subject: str | None = None,
        note: str | None = None,
    ) -> int:
        """Append one override beside the proposal it replaces. Returns the new record's id.

        ``reviewer_subject`` is the signed-in subject's stable id in sign-in mode (roadmap item
        44) and ``None`` in local mode. Neither it nor the name goes into the run event.

        Both ratings must be internal rating names; a label ("Minor Revision") is refused rather
        than stored, because a record 12b cannot key on is a record lost.
        """
        for field_name, value in (
            ("machine_rating", machine_rating),
            ("human_rating", human_rating),
        ):
            if value not in {rating.value for rating in Rating}:
                raise ValueError(f"{field_name} {value!r} is not a rating name")
        if not reviewer or not reviewer.strip():
            raise ValueError("an override records who made it; reviewer is empty")
        with self.session() as session:
            course = session.get(RunCourse, course_id)
            if course is None:
                raise KeyError(course_id)
            record = RatingOverride(
                run_id=course.run_id,
                course_id=course_id,
                criterion=criterion,
                pack_id=pack_id,
                pack_version=pack_version,
                machine_rating=machine_rating,
                machine_hours=machine_hours,
                machine_near_boundary=machine_near_boundary,
                machine_findings=list(machine_findings),
                effort_calibrated=effort_calibrated,
                human_rating=human_rating,
                reviewer=reviewer.strip(),
                reviewer_subject=(reviewer_subject or "").strip() or None,
                note=note,
            )
            session.add(record)
            session.add(
                RunEvent(
                    run_id=course.run_id,
                    course_id=course_id,
                    event="rating_overridden",
                    data=scrub_object(
                        {
                            "criterion": criterion,
                            "machine_rating": machine_rating,
                            "human_rating": human_rating,
                        }
                    ),
                )
            )
            session.flush()
            return record.id

    def list_overrides(
        self, *, run_id: str | None = None, course_id: str | None = None
    ) -> list[RatingOverride]:
        """Override records, oldest first. Filtered by run or course when either is given."""
        with self.session() as session:
            statement = select(RatingOverride).order_by(RatingOverride.id)
            if run_id is not None:
                statement = statement.where(RatingOverride.run_id == run_id)
            if course_id is not None:
                statement = statement.where(RatingOverride.course_id == course_id)
            records = list(session.scalars(statement))
            for record in records:
                record.created_at = as_utc(record.created_at)  # type: ignore[assignment]
            return records

    # -- reads -------------------------------------------------------------------------------

    def get_run(self, run_id: str) -> Run | None:
        with self.session() as session:
            return session.get(Run, run_id)

    def get_course(self, course_id: str) -> RunCourse | None:
        with self.session() as session:
            return session.get(RunCourse, course_id)

    def list_runs(self, *, limit: int = 25) -> list[Run]:
        with self.session() as session:
            statement = select(Run).order_by(Run.created_at.desc()).limit(limit)
            return list(session.scalars(statement))

    def running_runs(self) -> list[Run]:
        """Every run the store records as running, oldest first.

        Wider than the background runner's own task list: a run started by ``python -m
        sightline.batch run`` executes in another process and is visible here and nowhere else.
        Some of these may have an owner that is gone; :func:`sightline.ownership.owner_is_live`
        tells them apart.
        """
        with self.session() as session:
            statement = select(Run).where(Run.status == RunStatus.RUNNING).order_by(Run.created_at)
            return list(session.scalars(statement))

    def list_events(self, run_id: str, *, limit: int = 200) -> list[RunEvent]:
        with self.session() as session:
            statement = (
                select(RunEvent)
                .where(RunEvent.run_id == run_id)
                .order_by(RunEvent.id.desc())
                .limit(limit)
            )
            return list(session.scalars(statement))

    def events_in_order(self, run_id: str, *, limit: int = 2000) -> list[RunEvent]:
        """The run log oldest-first, which is the order progress has to be replayed in.

        :meth:`list_events` answers "what just happened" and is newest-first for a screen. Reading
        a course's stage history out of that means reversing a truncated window, which silently
        drops the *earliest* events — exactly the ones that say a stage started.
        """
        with self.session() as session:
            statement = (
                select(RunEvent)
                .where(RunEvent.run_id == run_id)
                .order_by(RunEvent.id.asc())
                .limit(limit)
            )
            return list(session.scalars(statement))

    def counts_by_status(self) -> dict[str, int]:
        with self.session() as session:
            rows = session.execute(select(Run.status)).scalars().all()
        counts: dict[str, int] = {}
        for value in rows:
            key = value.value if isinstance(value, RunStatus) else str(value)
            counts[key] = counts.get(key, 0) + 1
        return counts

    # -- encrypted credentials ---------------------------------------------------------------
    #
    # Ciphertext in, ciphertext out. This class never sees a plaintext credential and never logs
    # one, because it cannot: decryption lives in sightline.credentials, which holds the key.

    def get_secret_blob(self, name: str) -> bytes | None:
        with self.session() as session:
            row = session.get(AppSecret, name)
            return None if row is None else bytes(row.ciphertext)

    def put_secret_blob(self, name: str, ciphertext: bytes) -> None:
        with self.session() as session:
            row = session.get(AppSecret, name)
            if row is None:
                session.add(AppSecret(name=name, ciphertext=ciphertext))
            else:
                row.ciphertext = ciphertext
                row.updated_at = utcnow()

    def delete_secret_blob(self, name: str) -> bool:
        """Remove a stored credential. Returns whether there was one."""
        with self.session() as session:
            row = session.get(AppSecret, name)
            if row is None:
                return False
            session.delete(row)
            return True

    def secret_updated_at(self, name: str) -> datetime | None:
        """When a credential was last written. Shown on the settings screen; never the value."""
        with self.session() as session:
            row = session.get(AppSecret, name)
            return None if row is None else row.updated_at

    # -- preferences -------------------------------------------------------------------------
    #
    # Plain text, and the opposite of the block above on purpose: a preference must be readable
    # from the database or nobody can answer "why did this deployment behave that way?". Nothing
    # secret goes through here — see sightline.models.AppSetting.

    def get_setting(self, name: str) -> str | None:
        with self.session() as session:
            row = session.get(AppSetting, name)
            return None if row is None else row.value

    def put_setting(self, name: str, value: str) -> None:
        with self.session() as session:
            row = session.get(AppSetting, name)
            if row is None:
                session.add(AppSetting(name=name, value=value))
            else:
                row.value = value
                row.updated_at = utcnow()

    def delete_setting(self, name: str) -> bool:
        """Forget a preference so the environment fallback applies again."""
        with self.session() as session:
            row = session.get(AppSetting, name)
            if row is None:
                return False
            session.delete(row)
            return True

    # -- restart semantics -------------------------------------------------------------------

    def recover_interrupted(self) -> list[str]:
        """Mark interrupted the ``running`` runs whose owner is gone. Returns the ids touched.

        Called at app startup and by ``python -m sightline.batch resume``. A run left ``running``
        by a process that went away did not fail, so it becomes ``interrupted``, which is
        resumable and says so. A run whose owner is still live is another process's work in
        progress — a command-line run beside the app, say — and is left alone: taking it over is
        how one run came to execute twice at once (roadmap item 43). What "gone" means is
        :func:`sightline.ownership.orphan_reason`, and its answer is the reason the run log gets.
        """
        touched: list[str] = []
        left: list[str] = []
        now = utcnow()
        with self.session() as session:
            statement = select(Run).where(Run.status == RunStatus.RUNNING)
            for run in session.scalars(statement).all():
                reason = orphan_reason(run, now=now)
                if reason is None:
                    left.append(run.id)
                    continue
                if self._interrupt(session, run, reason=f"Recovered: {reason}."):
                    touched.append(run.id)
        if left:
            log.info("runs_left_to_their_owners", run_ids=left, count=len(left))
        if touched:
            log.warning("runs_interrupted", run_ids=touched, count=len(touched))
        return touched

    def interrupt_owned(self, owner: RunOwner) -> list[str]:
        """Mark interrupted the ``running`` runs ``owner`` holds. The app's shutdown sweep.

        Only this process's own runs: a run another process owns is not this shutdown's business,
        however it looks from here.
        """
        touched: list[str] = []
        with self.session() as session:
            statement = select(Run).where(
                Run.status == RunStatus.RUNNING,
                Run.owner_host == owner.host,
                Run.owner_pid == owner.pid,
            )
            for run in session.scalars(statement).all():
                reason = "The process executing it shut down before the run finished."
                if self._interrupt(session, run, reason=reason):
                    touched.append(run.id)
        if touched:
            log.warning("runs_interrupted", run_ids=touched, count=len(touched))
        return touched

    def _interrupt(self, session: Session, run: Run, *, reason: str) -> bool:
        """Move one ``running`` run, as read in ``session``, to ``interrupted`` and clear its owner.

        A compare-and-set against the status and owner record that were read: if the owner beat,
        finished or handed over in between, nothing is written and this returns false.
        """
        result = session.execute(
            update(Run)
            .where(Run.id == run.id, Run.status == RunStatus.RUNNING, *_same_owner(run))
            .values(
                status=RunStatus.INTERRUPTED,
                owner_host=None,
                owner_pid=None,
                owner_heartbeat_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            return False
        session.add(
            RunEvent(
                run_id=run.id, event="run_interrupted", level="warning", data={"reason": reason}
            )
        )
        for course in run.courses:
            if RunStatus(course.status) is RunStatus.RUNNING:
                course.status = RunStatus.INTERRUPTED
        return True

    # -- ownership (roadmap item 43) ---------------------------------------------------------
    #
    # See sightline.ownership for what an owner is and when one is gone. Every write below is a
    # compare-and-set on the owner record, because the processes that race for a run share
    # nothing but this file.

    def claim_run(self, run_id: str, owner: RunOwner) -> bool:
        """Record ``owner`` as executing ``run_id``, unless a live owner already is.

        The update matches only the owner record that was read, so of two processes that both
        found the run orphaned, exactly one write lands and the other is told no.
        """
        with self.session() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise KeyError(run_id)
            if orphan_reason(run, me=owner) is None:
                log.info("run_claim_refused", run_id=run_id, reason="owned by a live process")
                return False
            result = session.execute(
                update(Run)
                .where(Run.id == run_id, *_same_owner(run))
                .values(
                    owner_host=owner.host,
                    owner_pid=owner.pid,
                    owner_heartbeat_at=utcnow(),
                    updated_at=Run.updated_at,
                )
                .execution_options(synchronize_session=False)
            )
            claimed = result.rowcount == 1
        if claimed:
            log.info("run_claimed", run_id=run_id, host=owner.host, pid=owner.pid)
        else:
            log.info("run_claim_refused", run_id=run_id, reason="claimed by another process first")
        return claimed

    def heartbeat_run(self, run_id: str, owner: RunOwner) -> bool:
        """Refresh ``owner``'s heartbeat. False when ``owner`` no longer holds the run.

        Leaves ``updated_at`` alone: that column says the run changed, and a heartbeat is not a
        change anybody watching the run needs to see.
        """
        with self.engine.begin() as connection:
            result = connection.execute(
                update(Run)
                .where(Run.id == run_id, Run.owner_host == owner.host, Run.owner_pid == owner.pid)
                .values(owner_heartbeat_at=utcnow(), updated_at=Run.updated_at)
            )
            return result.rowcount == 1

    def release_run(self, run_id: str, owner: RunOwner) -> bool:
        """Clear ``owner``'s record from ``run_id``. False when ``owner`` no longer held it.

        A run still ``running`` here did not finish — its owner stopped part-way, by a cancel or a
        crash it did not record — so it is marked interrupted now rather than left for the next
        recovery to find.
        """
        with self.session() as session:
            run = session.get(Run, run_id)
            if run is None or (run.owner_host, run.owner_pid) != (owner.host, owner.pid):
                return False
            if RunStatus(run.status) is RunStatus.RUNNING:
                reason = "The process executing it stopped before the run finished."
                return self._interrupt(session, run, reason=reason)
            result = session.execute(
                update(Run)
                .where(Run.id == run_id, *_same_owner(run))
                .values(owner_host=None, owner_pid=None, owner_heartbeat_at=None)
                .execution_options(synchronize_session=False)
            )
            return result.rowcount == 1

    def resumable_runs(self) -> list[Run]:
        with self.session() as session:
            statement = (
                select(Run)
                .where(Run.status.in_([RunStatus.PENDING, RunStatus.INTERRUPTED]))
                .order_by(Run.created_at)
            )
            return list(session.scalars(statement))


def _same_owner(run: Run) -> tuple[ColumnElement[bool], ...]:
    """Conditions that match ``run``'s owner record exactly as it was read, nulls included."""
    return (
        Run.owner_host.is_not_distinct_from(run.owner_host),
        Run.owner_pid.is_not_distinct_from(run.owner_pid),
        Run.owner_heartbeat_at.is_not_distinct_from(run.owner_heartbeat_at),
    )
