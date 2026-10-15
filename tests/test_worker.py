"""Tests for durable worker stages and transcript handoff."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("WEBHOOK_TOKEN", "unit-test-token")

import pytest
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from koebako.services.worker import queue as worker_queue
from koebako.services.worker import runner as worker_runner
from koebako.shared import notifications as notification_service
from koebako.shared.models.note import Note, NoteSource, NoteStatus
from koebako.shared.models.notification import (
    NotificationMode,
    NotificationOutbox,
    NotificationStatus,
)
from koebako.shared.notifications import (
    enqueue_notification,
    received_notification,
    transcribed_notification,
)


@pytest.fixture
def database_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    """Provide a test database and redirect the worker queue to it."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'worker.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(worker_queue, "engine", engine)
    monkeypatch.setattr(notification_service, "engine", engine)
    monkeypatch.setattr(
        notification_service, "NOTIFICATION_MODE", NotificationMode.NORMAL
    )
    try:
        yield engine
    finally:
        engine.dispose()


def _create_note(
    engine: Engine,
    transcript: str | None = None,
    status: NoteStatus = NoteStatus.RECEIVED,
) -> Note:
    """Create and persist a note for a worker test."""
    note = Note(
        id=uuid4(),
        source=NoteSource.MEMO,
        client="pebble",
        status=status,
        recorded_at_ms=1_700_000_000_000,
        audio_key="memos/recording.m4a",
        transcript=transcript,
    )
    with Session(engine) as session:
        session.add(note)
        session.commit()
        session.refresh(note)
    return note


def _get_note(engine: Engine, note_id: object) -> Note:
    """Load a persisted note by its identifier."""
    with Session(engine) as session:
        note = session.get(Note, note_id)
        assert note is not None
        return note


@pytest.mark.asyncio
async def test_worker_transcribes_then_delivers_versioned_payload(
    database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the worker transcribes a note before delivering its payload."""
    note = _create_note(database_engine)
    delivered: list[object] = []

    async def fake_transcribe(queued_note: Note) -> str:
        """Return a deterministic transcript for the queued note."""
        assert queued_note.id == note.id
        return "A spoken note"

    async def fake_deliver(queued_note: Note) -> None:
        """Record the note sent to the transcript receiver."""
        delivered.append(queued_note)
        assert queued_note.transcript == "A spoken note"

    monkeypatch.setattr(worker_runner, "transcribe_note", fake_transcribe)
    monkeypatch.setattr(worker_runner, "post_transcript", fake_deliver)
    now = datetime(2025, 1, 1, tzinfo=UTC)

    assert await worker_runner.process_next_note(now) is True
    transcribed = _get_note(database_engine, note.id)
    assert transcribed.status == NoteStatus.TRANSCRIBED
    assert transcribed.transcript == "A spoken note"

    assert await worker_runner.process_next_note(now) is True
    delivered_note = _get_note(database_engine, note.id)
    assert delivered_note.status == NoteStatus.DELIVERED
    assert delivered_note.attempt_count == 0
    assert len(delivered) == 1
    assert isinstance(delivered[0], Note)
    assert delivered[0].id == delivered_note.id
    assert delivered[0].transcript == delivered_note.transcript
    with Session(database_engine) as session:
        events = session.exec(select(NotificationOutbox)).all()
    assert {event.dedupe_key for event in events} == {
        f"{note.id}:transcribed",
        f"{note.id}:delivered",
    }
    transcript_event = next(
        event for event in events if event.dedupe_key.endswith(":transcribed")
    )
    assert transcript_event.message == (
        f"Transcript [{note.id.hex[:8]}]:\nA spoken note"
    )
    assert await worker_runner.process_next_note(now) is False


def test_debug_transcript_notification_keeps_transcript_and_diagnostics(
    database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Include transcript text in debug mode alongside lower-priority details."""
    note = _create_note(database_engine)
    monkeypatch.setattr(
        notification_service, "NOTIFICATION_MODE", NotificationMode.DEBUG
    )
    with Session(database_engine) as session:
        enqueue_notification(
            session,
            note.id,
            transcribed_notification(note.id, "spoken words", 2.5, "test-model"),
        )
        session.commit()

    event = next(
        event
        for event in _get_notifications(database_engine)
        if event.note_id == note.id
    )
    assert "spoken words" in event.message
    assert "in 2.5s using test-model" in event.message
    assert event.priority == -1


def test_transcript_notification_is_truncated_with_a_marker(
    database_engine: Engine,
) -> None:
    """Keep long transcripts within Pushover's limit without silent clipping."""
    note = _create_note(database_engine)
    draft = transcribed_notification(note.id, "x" * 2_000, 1.0, "test-model")
    with Session(database_engine) as session:
        enqueue_notification(session, note.id, draft)
        session.commit()

    event = next(
        event
        for event in _get_notifications(database_engine)
        if event.note_id == note.id
    )
    assert len(event.message) == 1024
    assert event.message.endswith("… [truncated]")


def _get_notifications(engine: Engine) -> list[NotificationOutbox]:
    """Return all outbox messages for the test database."""
    with Session(engine) as session:
        return list(session.exec(select(NotificationOutbox)).all())


@pytest.mark.asyncio
async def test_delivery_retries_use_ten_then_thirty_seconds_without_retranscribing(
    database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify retry delays grow while a saved transcript is reused."""
    note = _create_note(
        database_engine,
        transcript="already transcribed",
        status=NoteStatus.TRANSCRIBED,
    )
    deliveries = 0

    async def unexpected_transcribe(_: Note) -> str:
        """Fail if a delivery retry unexpectedly invokes transcription."""
        raise AssertionError("a saved transcript should not be transcribed again")

    async def fail_twice(_: Note) -> None:
        """Fail twice, then accept the delivery without retranscribing."""
        nonlocal deliveries
        deliveries += 1
        if deliveries < 3:
            raise RuntimeError("receiver temporarily unavailable")

    monkeypatch.setattr(worker_runner, "transcribe_note", unexpected_transcribe)
    monkeypatch.setattr(worker_runner, "post_transcript", fail_twice)
    start = datetime(2025, 1, 1, tzinfo=UTC)

    assert await worker_runner.process_next_note(start) is True
    failed = _get_note(database_engine, note.id)
    assert failed.status == NoteStatus.TRANSCRIBED
    assert failed.attempt_count == 1
    first_retry_at = start.replace(tzinfo=None) + timedelta(seconds=10)
    assert failed.next_attempt_at == first_retry_at

    assert await worker_runner.process_next_note(start + timedelta(seconds=9)) is False
    assert await worker_runner.process_next_note(start + timedelta(seconds=10)) is True
    failed_again = _get_note(database_engine, note.id)
    assert failed_again.status == NoteStatus.TRANSCRIBED
    assert failed_again.attempt_count == 2
    second_retry_at = start.replace(tzinfo=None) + timedelta(seconds=40)
    assert failed_again.next_attempt_at == second_retry_at

    assert await worker_runner.process_next_note(start + timedelta(seconds=39)) is False
    assert await worker_runner.process_next_note(start + timedelta(seconds=40)) is True
    recovered = _get_note(database_engine, note.id)
    assert recovered.status == NoteStatus.DELIVERED
    assert recovered.transcript == "already transcribed"
    assert deliveries == 3


@pytest.mark.asyncio
async def test_notification_delivery_marks_outbox_event_sent(
    database_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify Pushover delivery completes its durable outbox event."""
    note = _create_note(database_engine)
    with Session(database_engine) as session:
        enqueue_notification(
            session,
            note.id,
            received_notification(note.id, NoteSource.MEMO),
        )
        session.commit()

    sent: list[tuple[str, str, int]] = []

    async def fake_send(message: str, title: str, priority: int) -> None:
        """Record a fake Pushover request without making a network call."""
        sent.append((message, title, priority))

    monkeypatch.setattr(worker_runner, "send_message", fake_send)
    now = datetime(2025, 1, 1, tzinfo=UTC)

    assert await worker_runner.process_next_notification(now) is True
    with Session(database_engine) as session:
        event = session.exec(select(NotificationOutbox)).one()

    assert event.status == NotificationStatus.SENT
    assert event.sent_at == now.replace(tzinfo=None)
    assert sent == [(event.message, "Koebako", 0)]
    assert await worker_runner.process_next_notification(now) is False
