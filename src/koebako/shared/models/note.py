"""SQLModel schema for recordings owned by Koebako."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime
from sqlmodel import Field, SQLModel


class NoteSource(StrEnum):
    """Pebble memo source recorded with a voice note."""

    MEMO = "memo"


class NoteStatus(StrEnum):
    """Durable state of transcription and transcript delivery."""

    RECEIVED = "received"
    TRANSCRIBING = "transcribing"
    TRANSCRIBED = "transcribed"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    ERROR = "error"


class Note(SQLModel, table=True):
    """Audio metadata, transcript, and retry state stored by Koebako."""

    __tablename__: Any = "voice_note"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    source: NoteSource
    client: str = Field(max_length=255)
    status: NoteStatus = Field(default=NoteStatus.RECEIVED, index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        index=True,
        sa_type=DateTime(timezone=True),
    )
    recorded_at_ms: int
    audio_key: str = Field(max_length=1024)
    transcript: str | None = None
    error_message: str | None = Field(default=None, max_length=255)
    attempt_count: int = Field(default=0)
    next_attempt_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    lease_expires_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
