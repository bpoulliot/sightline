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
    Run,
    RunCourse,
    RunEvent,
    RunStatus,
    utcnow,
)
from .paths import DataPaths
from .redaction import scrub, scrub_object

log = get_logger("sightline.store")


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

    # -- reads -------------------------------------------------------------------------------

    def get_run(self, run_id: str) -> Run | None:
        with self.session() as session:
            return session.get(Run, run_id)

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
