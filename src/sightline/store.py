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

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from .logging import get_logger
from .models import (
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
        note: str | None = None,
    ) -> str:
        """Record a new batch. Returns the run id."""
        if not course_refs:
            raise ValueError("a run needs at least one course reference")
        with self.session() as session:
            run = Run(rubric_pack=rubric_pack, note=note)
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
                    data={"courses": len(course_refs), "rubric_pack": rubric_pack},
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
            if status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
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
        note: str | None = None,
    ) -> int:
        """Append one override beside the proposal it replaces. Returns the new record's id.

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
        """Mark runs that were mid-flight when the process stopped.

        Called once at startup. A run left in ``running`` did not fail — the process went away —
        so it becomes ``interrupted``, which is resumable and says so. Returns the ids touched.
        """
        touched: list[str] = []
        with self.session() as session:
            statement = select(Run).where(Run.status == RunStatus.RUNNING)
            for run in session.scalars(statement):
                run.status = RunStatus.INTERRUPTED
                touched.append(run.id)
                session.add(
                    RunEvent(
                        run_id=run.id,
                        event="run_interrupted",
                        level="warning",
                        data={"reason": "process restarted while the run was in progress"},
                    )
                )
                for course in run.courses:
                    if RunStatus(course.status) is RunStatus.RUNNING:
                        course.status = RunStatus.INTERRUPTED
        if touched:
            log.warning("runs_interrupted", run_ids=touched, count=len(touched))
        return touched

    def resumable_runs(self) -> list[Run]:
        with self.session() as session:
            statement = (
                select(Run)
                .where(Run.status.in_([RunStatus.PENDING, RunStatus.INTERRUPTED]))
                .order_by(Run.created_at)
            )
            return list(session.scalars(statement))
