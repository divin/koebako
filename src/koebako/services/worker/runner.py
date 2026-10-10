"""Poll durable SQLite state and execute transcription or delivery work."""

import asyncio
import logging
import time
from datetime import UTC, datetime

from koebako.integrations.pushover import PushoverError, send_message
from koebako.services.transcriber.client import check_stt_health
from koebako.services.transcriber.delivery import post_transcript, validate_config
from koebako.services.transcriber.processor import transcribe_note
from koebako.settings import NOTIFICATION_MODE, STT_MODEL_NAME, STT_URL
from koebako.shared.db import close_db, create_db_and_tables
from koebako.shared.models.note import NoteStatus
from koebako.shared.models.notification import NotificationMode
from koebako.shared.notifications import (
    claim_next_notification,
    complete_notification,
    delivered_notification,
    fail_notification,
    recover_expired_notifications,
    transcribed_notification,
    validate_notification_config,
)

from .queue import (
    Stage,
    claim_next_note,
    complete_stage,
    normalize_utc,
    record_failure,
    recover_expired_claims,
)

logger = logging.getLogger(__name__)
POLL_INTERVAL_SECONDS = 1


def _utc_now() -> datetime:
    """Return the current UTC time as a naive datetime for SQLite."""
    return datetime.now(UTC).replace(tzinfo=None)


async def process_next_note(now: datetime | None = None) -> bool:
    """Process one leased stage, recording failures for a later retry."""
    current_time = normalize_utc(now) if now is not None else _utc_now()
    recover_expired_claims(current_time)
    claim = claim_next_note(current_time)
    if claim is None:
        return False

    try:
        if claim.stage == Stage.TRANSCRIBE:
            started = time.perf_counter()
            transcript = await transcribe_note(claim.note)
            duration_seconds = time.perf_counter() - started
            logger.info(
                "Transcribed note %s in %.1fs with %s",
                claim.note.id,
                duration_seconds,
                STT_MODEL_NAME or "client-provided transcript",
            )
            complete_stage(
                claim,
                notification=transcribed_notification(
                    claim.note.id,
                    duration_seconds,
                    STT_MODEL_NAME or "client-provided transcript",
                ),
                status=NoteStatus.TRANSCRIBED,
                transcript=transcript,
            )
        else:
            await post_transcript(claim.note)
            complete_stage(
                claim,
                notification=delivered_notification(claim.note.id),
                status=NoteStatus.DELIVERED,
            )
    except Exception as exc:  # noqa: BLE001 - stage failures are persisted for retries
        record_failure(claim, current_time if now is not None else _utc_now(), exc)
        logger.error(
            "%s failed for note %s (%s)",
            claim.stage.value,
            claim.note.id,
            type(exc).__name__,
        )
    return True


async def process_next_notification(now: datetime | None = None) -> bool:
    """Deliver one ready Pushover outbox event without a database transaction.

    Parameters
    ----------
    now : datetime or None, optional
        Time to use for claim and retry calculations. Defaults to current UTC.

    Returns
    -------
    bool
        ``True`` if an event was claimed, or ``False`` if the outbox is idle.
    """
    current_time = normalize_utc(now) if now is not None else _utc_now()
    recover_expired_notifications(current_time)
    event = claim_next_notification(current_time)
    if event is None:
        return False

    try:
        await send_message(event.message, event.title, event.priority)
    except PushoverError as exc:
        failure_time = _utc_now() if now is None else current_time
        fail_notification(
            event,
            failure_time,
            str(exc),
            retryable=exc.retryable,
        )
        logger.error(
            "Pushover delivery failed for event %s (%s)",
            event.dedupe_key,
            str(exc),
        )
    else:
        sent_at = _utc_now() if now is None else current_time
        complete_notification(event, sent_at)
    return True


async def _run_pipeline() -> None:
    """Poll for work continuously, sleeping when the queue is empty."""
    while True:
        if not await process_next_note():
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _run_notifications() -> None:
    """Poll and deliver notification outbox events until shutdown."""
    while True:
        if not await process_next_notification():
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def run_worker() -> None:
    """Initialize storage and run transcription, delivery, and notice loops.

    Raises
    ------
    RuntimeError
        If required speech-to-text configuration is missing or notifications are
        enabled without both Pushover credentials.
    """
    if not STT_URL or not STT_MODEL_NAME:
        raise RuntimeError(
            "STT_URL and STT_MODEL_NAME must be configured for Koebako worker"
        )
    validate_config()
    validate_notification_config()
    create_db_and_tables()
    await check_stt_health()
    logger.info(
        "Koebako transcription worker started (notifications=%s)", NOTIFICATION_MODE
    )
    try:
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(_run_pipeline())
            if NOTIFICATION_MODE is not NotificationMode.OFF:
                tasks.create_task(_run_notifications())
    finally:
        close_db()


def main() -> None:
    """Configure logging and start the asynchronous worker."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker())
