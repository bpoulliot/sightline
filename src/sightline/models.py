"""Run-store schema.

Run state lives in SQLite so a restart resumes rather than loses (SPEC §04). There is no broker
and no worker: background work is an asyncio task in the same process, and this table set is the
only thing that survives it.

Six tables, deliberately flat:

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
``rating_overrides``
    A reviewer's rating for a row, beside the machine proposal it replaced. Append-only, and the
    calibration data for the effort model (SPEC §09; roadmap items 12a, 16, 12b).
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid.uuid4().hex


def as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a timestamp read back from SQLite.

    SQLite has no timezone type, so a column declared ``DateTime(timezone=True)`` is written from
    an aware value and read back naive. Every timestamp this package stores is UTC — :func:`utcnow`
    is the only writer — so the tzinfo is restored rather than guessed at. Without this, the value
    a setter returns and the value the next read returns do not compare equal, which is a bug
    waiting in whichever surface subtracts them.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class RunStatus(enum.StrEnum):
    """Lifecycle of a batch.

    ``interrupted`` is the state a run lands in when the process stopped while it was running. It
    is distinct from ``failed`` on purpose: a run that was interrupted is resumable and nothing is
    known to be wrong with it.

    ``partial`` is the state a batch lands in when some courses were reviewed and others failed,
    and it exists because the alternatives both lie. ``succeeded`` over a batch with a failed
    course is the silent half of SPEC §11 — a glance at the run list would say the work is done.
    ``failed`` over the same batch throws away two finished reviews. Roadmap item 17.
    """

    PENDING = "pending"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: States from which work can pick up again after a restart.
RESUMABLE_STATUSES: frozenset[RunStatus] = frozenset({RunStatus.PENDING, RunStatus.INTERRUPTED})

#: States that mean the run is over, one way or another.
TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLED}
)

#: What each status is called on a screen. A mapping rather than a string transformation, because
#: "Partial" is not what a person needs to read about a batch that reviewed two courses of three.
STATUS_LABELS: dict[RunStatus, str] = {
    RunStatus.PENDING: "Pending",
    RunStatus.RUNNING: "Running",
    RunStatus.INTERRUPTED: "Interrupted",
    RunStatus.SUCCEEDED: "Completed",
    RunStatus.PARTIAL: "Completed with failures",
    RunStatus.FAILED: "Failed",
    RunStatus.CANCELLED: "Cancelled",
}


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
    #: The accepted bindings the run uses (roadmap item 22, part two): the digest of a state kept
    #: under the data root by :mod:`sightline.rubric.accepted`, recorded when the run is created.
    #: ``None`` is the shipped pack's own bindings, which is also what every run recorded before
    #: the column existed ran with.
    bindings_digest: Mapped[str | None] = mapped_column(String(80), default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    #: Who is executing this run: the host name and process id that claimed it, and when that
    #: process last said it was still working. Set while a process executes the run and cleared
    #: when it stops, so a ``running`` row with no owner was left by a process that never recorded
    #: one. Recovery reads these to take over only a run whose owner is gone (roadmap item 43);
    #: :mod:`sightline.ownership` says what "gone" means.
    owner_host: Mapped[str | None] = mapped_column(String(255), default=None)
    owner_pid: Mapped[int | None] = mapped_column(Integer, default=None)
    owner_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )
    #: Who started the run, in sign-in mode (roadmap item 45): the signed-in subject's stable id,
    #: and the display name beside it for the admin's run list. The run reads Canvas with this
    #: subject's own token, and only this subject or an admin may see it, review it or export it.
    #: ``None`` for a run started in local mode or on the command line, and for every run recorded
    #: before the column existed: :mod:`sightline.access` shows those to an admin only in sign-in
    #: mode, and to the one local user in local mode.
    starter_subject: Mapped[str | None] = mapped_column(String(255), default=None)
    starter_name: Mapped[str | None] = mapped_column(String(128), default=None)

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
        status = RunStatus(self.status)
        return STATUS_LABELS.get(status, status.value.replace("_", " ").capitalize())


class RunCourse(Base):
    __tablename__ = "run_courses"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    #: What the designer typed: a course id or a Canvas course URL. Not resolved here.
    course_ref: Mapped[str] = mapped_column(String(512))
    status: Mapped[RunStatus] = mapped_column(String(16), default=RunStatus.PENDING)
    #: The unattended stage this course reached. The names are :class:`sightline.batch.Stage`.
    stage: Mapped[str | None] = mapped_column(String(32), default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    #: When a person opened this course's review. **Null is the normal state and it is a gate.**
    #: Nothing may be exported for a course until this is set, and only a human action sets it
    #: (SPEC §09: nothing exports until a human has opened the review). The column lives here
    #: rather than on the run because a report is per course, and a batch where one course has
    #: been reviewed must not let the other fifteen out. See :mod:`sightline.batch.gate`.
    review_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
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


class RatingOverride(Base):
    """A reviewer's rating for one row, stored beside the machine proposal it replaces.

    **This table is the calibration data** (SPEC §09): roadmap item 12b fits the per-defect costs
    against it, so both halves are kept whole. The machine side is what the effort model proposed —
    the rating, the rolled-up hours, the near-boundary marker if there was one, and the findings
    that were summed — and the human side is the rating a person chose instead.

    **Append-only. An override never overwrites.** A second override of the same row is a second
    record, and the store has no update or delete for this table: a reviewer changing their mind is
    itself information, and a machine proposal edited after the fact is no longer the proposal.

    The pack id *and version* are recorded because a band means something only against the bands
    it was cut from, and a pack revision can move them.

    A run nobody overrode writes nothing here. That is the ordinary case, not a gap.
    """

    __tablename__ = "rating_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    course_id: Mapped[str] = mapped_column(
        ForeignKey("run_courses.id", ondelete="CASCADE"), index=True
    )
    #: The row, keyed on its code — never on a position.
    criterion: Mapped[str] = mapped_column(String(64))
    pack_id: Mapped[str] = mapped_column(String(64))
    pack_version: Mapped[str] = mapped_column(String(64))

    #: The machine proposal. Internal rating names (:class:`sightline.rubric.states.Rating`).
    machine_rating: Mapped[str] = mapped_column(String(32))
    #: Rolled-up hours, or null when the row was never banded (Tier C/D, no coverage).
    machine_hours: Mapped[float | None] = mapped_column(Float, default=None)
    #: The near-boundary marker the row carried, as persisted, or null.
    machine_near_boundary: Mapped[dict | None] = mapped_column(JSON, default=None)
    #: The detector findings that were summed, one entry per finding.
    machine_findings: Mapped[list] = mapped_column(JSON, default=list)
    #: Whether the cost table behind ``machine_hours`` had been fitted when it was computed.
    effort_calibrated: Mapped[bool] = mapped_column(default=False)

    #: The human side.
    human_rating: Mapped[str] = mapped_column(String(32))
    #: Who set it — whatever identifier the review screen has for the person. Free text: the
    #: Reviewer setting in local mode, the signed-in display name in sign-in mode.
    reviewer: Mapped[str] = mapped_column(String(128))
    #: The signed-in subject's stable id (roadmap item 44), or null. Null on every record made in
    #: local mode and on every record a previous version made, which keep their name and no id.
    reviewer_subject: Mapped[str | None] = mapped_column(String(255), default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
