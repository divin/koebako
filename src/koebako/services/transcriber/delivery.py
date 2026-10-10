"""HTTP client for handing completed transcript payloads to a receiver."""

import logging
from uuid import UUID

import httpx

from koebako.settings import (
    TRANSCRIPT_WEBHOOK_AUTH_HEADER,
    TRANSCRIPT_WEBHOOK_AUTH_VALUE,
    TRANSCRIPT_WEBHOOK_URL,
)
from koebako.shared.models.note import Note

logger = logging.getLogger(__name__)
_DELIVERY_TIMEOUT_SECONDS = 30


def validate_config() -> None:
    """Validate the transcript receiver and optional authentication settings."""
    if not TRANSCRIPT_WEBHOOK_URL:
        raise RuntimeError("TRANSCRIPT_WEBHOOK_URL must be configured")
    if bool(TRANSCRIPT_WEBHOOK_AUTH_HEADER) != bool(TRANSCRIPT_WEBHOOK_AUTH_VALUE):
        raise RuntimeError(
            "TRANSCRIPT_WEBHOOK_AUTH_HEADER and TRANSCRIPT_WEBHOOK_AUTH_VALUE "
            "must be set together"
        )


async def post_transcript(note: Note) -> None:
    """Deliver a versioned transcript payload; retries are handled by the worker."""
    if not TRANSCRIPT_WEBHOOK_URL:
        raise RuntimeError("TRANSCRIPT_WEBHOOK_URL is not configured")

    headers: dict[str, str] = {}
    if TRANSCRIPT_WEBHOOK_AUTH_HEADER and TRANSCRIPT_WEBHOOK_AUTH_VALUE:
        headers[TRANSCRIPT_WEBHOOK_AUTH_HEADER] = TRANSCRIPT_WEBHOOK_AUTH_VALUE

    payload = {
        "schema_version": 1,
        "note_id": str(UUID(str(note.id))),
        "source": note.source.value,
        "recorded_at_ms": note.recorded_at_ms,
        "transcript": note.transcript,
    }
    headers["Idempotency-Key"] = str(note.id)
    async with httpx.AsyncClient(timeout=_DELIVERY_TIMEOUT_SECONDS) as client:
        response = await client.post(
            TRANSCRIPT_WEBHOOK_URL,
            json=payload,
            headers=headers,
        )
    response.raise_for_status()
    logger.info("Delivered transcript payload for note %s", note.id)
