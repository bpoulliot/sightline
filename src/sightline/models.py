"""Run-store schema.

Run state lives in SQLite so a restart resumes rather than loses (SPEC §04). There is no broker
and no worker: background work is an asyncio task in the same process, and this table set is the
only thing that survives it.

Five tables, deliberately flat:

``runs``
    One row per batch a designer started.
``run_courses``
    One row per course in that batch, each with its own status and stage.
``run_events``
    Append-only log of what happened, so a failure is visible in the UI rather than only in the
    process log. Failures are never silent (SPEC §09).
``app_secrets``
    Credentials entered on the settings screen, stored as ciphertext. The key is never in this
    table — see :mod:`sightline.credentials` for where it lives and what that does and does not
    protect against.
``app_settings``
    Preferences set on the settings screen. Plaintext, deliberately: a preference nobody can read
    is a preference nobody can audit. Nothing secret goes in it — that is what ``app_secrets`` is.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class RunStatus(enum.StrEnum):
    """Lifecycle of a batch.

    ``interrupted`` is the state a run lands in when the process stopped while it was running. It
    is distinct from ``failed`` on purpose: a run that was interrupted is resumable and nothing is
    known to be wrong with it.
    """

    PENDING = "pending"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: States from which work can pick up again after a restart.
RESUMABLE_STATUSES: frozenset[RunStatus] = frozenset({RunStatus.PENDING, RunStatus.INTERRUPTED})

#: States that mean the run is over, one way or another.
TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    status: Mapped[RunStatus] = mapped_column(String(16), default=RunStatus.PENDING)
    rubric_pack: Mapped[str] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text, default=None)

    courses: Mapped[list[RunCourse]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="RunCourse.ordinal",
        lazy="selectin",
    )

    @property
    def is_terminal(self) -> bool:
        return RunStatus(self.status) in TERMINAL_STATUSES

    @property
    def status_label(self) -> str:
        """Human-facing status. The column is a plain string, so normalise on the way out."""
        return RunStatus(self.status).value.replace("_", " ").capitalize()


class RunCourse(Base):
    __tablename__ = "run_courses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    #: What the designer typed: a course id or a Canvas course URL. Not resolved here.
    course_ref: Mapped[str] = mapped_column(String(512))
    status: Mapped[RunStatus] = mapped_column(String(16), default=RunStatus.PENDING)
    #: Pipeline stage, one of the seven in SPEC §04. Free text until item 03 names them.
    stage: Mapped[str | None] = mapped_column(String(32), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    run: Mapped[Run] = relationship(back_populates="courses")


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[str | None] = mapped_column(
        ForeignKey("run_courses.id", ondelete="CASCADE"), default=None, index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    level: Mapped[str] = mapped_column(String(8), default="info")
    stage: Mapped[str | None] = mapped_column(String(32), default=None)
    event: Mapped[str] = mapped_column(String(128))
    data: Mapped[dict | None] = mapped_column(JSON, default=None)


class AppSecret(Base):
    """A credential the designer entered, encrypted.

    The column is named ``ciphertext`` rather than ``value`` on purpose: a schema is read by
    people, and a column called ``value`` invites the next person to put a plaintext token in it.
    Nothing in this table is readable without the key from :mod:`sightline.credentials`, which
    lives outside the database.
    """

    __tablename__ = "app_secrets"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AppSetting(Base):
    """A preference the designer set on the settings screen, in plain text.

    Separate from ``app_secrets`` rather than sharing it, because the two have opposite
    requirements. A credential must be unreadable without a key; a preference must be readable by
    anyone looking at the database, or "why did this deployment not ask Canvas for an export?"
    becomes unanswerable from the artifact that travels.

    **Nothing secret belongs here.** A value in this table is readable by anyone who can read the
    run store.
    """

    __tablename__ = "app_settings"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
