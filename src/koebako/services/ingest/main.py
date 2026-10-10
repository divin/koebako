"""FastAPI application accepting voice notes for Koebako."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse
from sqlmodel import Session

from koebako.settings import AUDIO_STORAGE_ROOT
from koebako.shared.db import close_db, create_db_and_tables, get_session
from koebako.shared.models.note import Note, NoteSource
from koebako.shared.notifications import (
    enqueue_notification,
    oversized_upload_notification,
    received_notification,
)

from .audio import is_same_size, save_memo_audio
from .token import verify_token

logger = logging.getLogger(__name__)
MAX_AUDIO_SIZE_BYTES = 25 * 1024 * 1024


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    """Create the database schema on startup and close the pool at shutdown."""
    create_db_and_tables()
    try:
        yield
    finally:
        close_db()


app = FastAPI(
    title="Koebako API",
    description="Pebble-compatible voice-note upload and transcription pipeline.",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
def health_check() -> dict[str, str]:
    """Respond to basic API and webhook connectivity checks."""
    return {"status": "ok"}


def _persist_note(
    session: Session,
    *,
    note_id: UUID,
    source: NoteSource,
    client: str,
    recorded_at: int,
    transcription: str | None,
    audio_file: Path,
) -> None:
    """Persist note metadata and remove the audio if its DB write fails."""
    audio_key = audio_file.relative_to(AUDIO_STORAGE_ROOT).as_posix()
    note = Note(
        id=note_id,
        source=source,
        client=client,
        recorded_at_ms=recorded_at,
        audio_key=audio_key,
        transcript=transcription,
    )
    try:
        session.add(note)
        enqueue_notification(session, note_id, received_notification(note_id, source))
        session.commit()
    except Exception:
        session.rollback()
        try:
            audio_file.unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not remove orphaned audio file %s", audio_file)
        raise
    logger.info("Persisted %s note %s", source.value, note_id)


def _reject_oversized_audio(audio: UploadFile, session: Session) -> JSONResponse | None:
    """Reject audio uploads above the configured size limit.

    Parameters
    ----------
    audio : UploadFile
        Uploaded recording to check.
    session : Session
        Database session used to enqueue a rate-limited alert.

    Returns
    -------
    JSONResponse or None
        HTTP 413 response when oversized, otherwise ``None``.
    """
    if audio.size is None or audio.size <= MAX_AUDIO_SIZE_BYTES:
        return None
    logger.warning(
        "Rejecting audio upload of %d bytes; configured limit is %d bytes",
        audio.size,
        MAX_AUDIO_SIZE_BYTES,
    )
    enqueue_notification(
        session,
        None,
        oversized_upload_notification(audio.size, MAX_AUDIO_SIZE_BYTES),
    )
    session.commit()
    return JSONResponse(
        status_code=413,
        content={"detail": "Audio exceeds the configured maximum size"},
    )


@app.post("/memo", dependencies=[Depends(verify_token)])
def receive_memo(
    audio: UploadFile | None = File(None),  # noqa: B008
    transcription: str | None = Form(None),
    recorded_at: int = Form(..., alias="recordedAt"),
    client: str = Form(...),
    x_audio_size: int | None = Header(None),
    session: Session = Depends(get_session),  # noqa: B008
) -> JSONResponse:
    """Accept a memo recording; audio-less connectivity tests are no-ops."""
    logger.info(
        "Received /memo from client=%r recordedAt=%d audio=%s",
        client,
        recorded_at,
        audio is not None,
    )

    if audio is None:
        return JSONResponse(status_code=200, content={"status": "test_event"})
    oversized_response = _reject_oversized_audio(audio, session)
    if oversized_response is not None:
        return oversized_response

    note_id = uuid4()
    audio_file = save_memo_audio(recorded_at, audio, note_id)
    if not is_same_size(audio_file, x_audio_size):
        return JSONResponse(
            status_code=400,
            content={"status": "error", "detail": "Audio size mismatch"},
        )

    _persist_note(
        session,
        note_id=note_id,
        source=NoteSource.MEMO,
        client=client,
        recorded_at=recorded_at,
        transcription=transcription,
        audio_file=audio_file,
    )
    return JSONResponse(status_code=202, content={"status": "accepted"})
