"""SQLite leases and retry state for transcription and event delivery."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, cast

from sqlalchemy import or_, update
from sqlmodel import Session, select

from koebako.shared.db import engine
from koebako.shared.models.note import Note, NoteStatus
from koebako.shared.notifications import (
    NotificationDraft,
    enqueue_notification,
    failure_notification,
    recovery_notification,
    retry_notification,
)

logger = logging.getLogger(__name__)
columns = cast(Any, Note).__table__.c

LEASE_SECONDS = 600
MAX_ATTEMPTS = 3
BASE_RETRY_SECONDS = 5


class Stage(StrEnum):
    """Background operation currently associated with a note."""

    TRANSCRIBE = "transcription"
    DELIVER = "delivery"


@dataclass(frozen=True)
class ClaimedNote:
    """A note snapshot and the lease acquired by the worker."""

    note: Note
    stage: Stage
    claimed_status: NoteStatus


def normalize_utc(value: datetime) -> datetime:
    """Normalize timestamps to naive UTC for SQLite comparisons."""
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _retry_delay(attempt_count: int) -> int:
    """Return the exponential retry delay for an attempt count.

    Parameters
    ----------
    attempt_count : int
        Number of processing attempts already made.

    Returns
    -------
    int
        Delay before retrying, in seconds.
    """
    return BASE_RETRY_SECONDS * 2 ** max(0, attempt_count - 1)


def _ready_status(stage: Stage) -> NoteStatus:
    """Return the pending note status for a processing stage.

    Parameters
    ----------
    stage : Stage
        Stage whose pending status is requested.

    Returns
    -------
    NoteStatus
        Status indicating that the stage is ready to run.
    """
    return {
        Stage.TRANSCRIBE: NoteStatus.RECEIVED,
        Stage.DELIVER: NoteStatus.TRANSCRIBED,
    }[stage]


def _running_status(stage: Stage) -> NoteStatus:
    """Return the in-progress note status for a processing stage.

    Parameters
    ----------
    stage : Stage
        Stage whose running status is requested.

    Returns
    -------
    NoteStatus
        Status indicating that the stage is running.
    """
    return {
        Stage.TRANSCRIBE: NoteStatus.TRANSCRIBING,
        Stage.DELIVER: NoteStatus.DELIVERING,
    }[stage]


def _stage_for_status(status: NoteStatus) -> Stage:
    """Return the worker stage associated with a note status.

    Parameters
    ----------
    status : NoteStatus
        Current persisted note status.

    Returns
    -------
    Stage
        Transcription or delivery stage for the status.
    """
    if status in (NoteStatus.RECEIVED, NoteStatus.TRANSCRIBING):
        return Stage.TRANSCRIBE
    return Stage.DELIVER


def recover_expired_claims(now: datetime) -> None:
    """Return interrupted work to its pending state or mark it exhausted."""
    expired = or_(columns.lease_expires_at.is_(None), columns.lease_expires_at <= now)
    with Session(engine) as session:
        notes = session.exec(
            select(Note).where(
                columns.status.in_([NoteStatus.TRANSCRIBING, NoteStatus.DELIVERING]),
                expired,
            )
        ).all()
        for note in notes:
            stage = _stage_for_status(note.status)
            note.lease_expires_at = None
            if note.attempt_count >= MAX_ATTEMPTS:
                note.status = NoteStatus.ERROR
                note.next_attempt_at = None
                note.error_message = (
                    f"{stage.value} interrupted after {note.attempt_count} attempts"
                )
            else:
                note.status = _ready_status(stage)
                note.next_attempt_at = now + timedelta(
                    seconds=_retry_delay(note.attempt_count)
                )
                note.error_message = f"{stage.value} interrupted; retry scheduled"
            session.add(note)
            if note.status == NoteStatus.ERROR:
                draft = failure_notification(
                    note.id,
                    stage.value,
                    note.attempt_count,
                    "WorkerInterrupted",
                )
            else:
                draft = recovery_notification(note.id, stage.value, note.attempt_count)
            enqueue_notification(session, note.id, draft)
        if notes:
            session.commit()


def claim_next_note(now: datetime) -> ClaimedNote | None:
    """Atomically claim the oldest note eligible for either worker stage."""
    eligible = or_(columns.next_attempt_at.is_(None), columns.next_attempt_at <= now)
    with Session(engine) as session:
        note = session.exec(
            select(Note)
            .where(
                columns.status.in_([NoteStatus.RECEIVED, NoteStatus.TRANSCRIBED]),
                eligible,
                columns.lease_expires_at.is_(None),
            )
            .order_by(columns.created_at)
            .limit(1)
        ).first()
        if note is None:
            return None

        stage = (
            Stage.TRANSCRIBE if note.status == NoteStatus.RECEIVED else Stage.DELIVER
        )
        claimed_status = _running_status(stage)
        lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        result = session.exec(
            update(Note)
            .where(
                columns.id == note.id,
                columns.status == note.status,
                eligible,
                columns.lease_expires_at.is_(None),
            )
            .values(
                status=claimed_status,
                attempt_count=columns.attempt_count + 1,
                lease_expires_at=lease_expires_at,
                error_message=None,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            session.rollback()
            return None
        session.commit()
        session.refresh(note)
        return ClaimedNote(note=note, stage=stage, claimed_status=claimed_status)


def complete_stage(
    claim: ClaimedNote,
    *,
    notification: NotificationDraft | None = None,
    **changes: object,
) -> None:
    """Persist stage output only if the worker still owns the lease.

    Parameters
    ----------
    claim : ClaimedNote
        Claim whose status and lease must still match the database row.
    notification : NotificationDraft or None, optional
        Lifecycle event to enqueue atomically with the stage update.
    **changes : object
        Note fields to update with the completed stage's output and status.
    """
    with Session(engine) as session:
        result = session.exec(
            update(Note)
            .where(
                columns.id == claim.note.id,
                columns.status == claim.claimed_status,
                columns.lease_expires_at == claim.note.lease_expires_at,
            )
            .values(
                **changes,
                attempt_count=0,
                next_attempt_at=None,
                lease_expires_at=None,
                error_message=None,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            logger.warning(
                "Discarding stale %s result for note %s", claim.stage, claim.note.id
            )
            session.rollback()
            return
        if notification is not None:
            enqueue_notification(session, claim.note.id, notification)
        session.commit()


def record_failure(claim: ClaimedNote, now: datetime, exc: Exception) -> None:
    """Schedule bounded retry or make this stage terminal after its final try."""
    attempts = claim.note.attempt_count
    if attempts >= MAX_ATTEMPTS:
        status = NoteStatus.ERROR
        next_attempt_at = None
        error_message = f"{claim.stage.value} failed after {attempts} attempts ({type(exc).__name__})"
    else:
        status = _ready_status(claim.stage)
        next_attempt_at = now + timedelta(seconds=_retry_delay(attempts))
        error_message = (
            f"{claim.stage.value} failed ({type(exc).__name__}); retry scheduled"
        )

    with Session(engine) as session:
        result = session.exec(
            update(Note)
            .where(
                columns.id == claim.note.id,
                columns.status == claim.claimed_status,
                columns.lease_expires_at == claim.note.lease_expires_at,
            )
            .values(
                status=status,
                next_attempt_at=next_attempt_at,
                lease_expires_at=None,
                error_message=error_message,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            logger.warning(
                "Discarding stale %s failure for note %s", claim.stage, claim.note.id
            )
            session.rollback()
            return
        if status is NoteStatus.ERROR:
            draft = failure_notification(
                claim.note.id,
                claim.stage.value,
                attempts,
                type(exc).__name__,
            )
        else:
            draft = retry_notification(
                claim.note.id,
                claim.stage.value,
                attempts,
                type(exc).__name__,
            )
        enqueue_notification(session, claim.note.id, draft)
        session.commit()
