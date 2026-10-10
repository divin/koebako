"""Tests for Koebako's upload API."""

import os
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("WEBHOOK_TOKEN", "unit-test-token")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from koebako.services.ingest import audio as ingest_audio
from koebako.services.ingest import main as ingest_main
from koebako.settings import WEBHOOK_TOKEN
from koebako.shared import notifications as notification_service
from koebako.shared.db import get_session
from koebako.shared.models.note import Note, NoteSource, NoteStatus
from koebako.shared.models.notification import NotificationMode, NotificationOutbox


@pytest.fixture
def database_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Engine]:
    """Provide an isolated SQLite database for upload tests."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(
        notification_service, "NOTIFICATION_MODE", NotificationMode.NORMAL
    )
    monkeypatch.setattr(notification_service, "engine", engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a temporary audio directory for upload tests."""
    root = tmp_path / "audio"
    monkeypatch.setattr(ingest_main, "AUDIO_STORAGE_ROOT", root)
    monkeypatch.setattr(ingest_audio, "MEMO_SAVE_DIR", root / "memos")
    return root


@pytest.fixture
def client(database_engine: Engine) -> Iterator[TestClient]:
    """Provide a test client connected to the isolated database."""
    original_overrides = ingest_main.app.dependency_overrides.copy()

    def override_get_session() -> Iterator[Session]:
        """Yield a session connected to the test database."""
        with Session(database_engine) as session:
            yield session

    ingest_main.app.dependency_overrides[get_session] = override_get_session
    test_client = TestClient(ingest_main.app)
    try:
        yield test_client
    finally:
        test_client.close()
        ingest_main.app.dependency_overrides.clear()
        ingest_main.app.dependency_overrides.update(original_overrides)


def _headers(**extra: str) -> dict[str, str]:
    """Build authenticated request headers with optional additions."""
    return {"Authorization": f"Bearer {WEBHOOK_TOKEN}", **extra}


def _notes(engine: Engine) -> list[Note]:
    """Return all notes persisted in the supplied database."""
    with Session(engine) as session:
        return list(session.exec(select(Note)).all())


def test_root_returns_ok(client: TestClient) -> None:
    """Verify the health endpoint reports that the API is available."""
    response = client.get("/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_upload_persists_audio_and_transcription_metadata(
    client: TestClient, database_engine: Engine, storage_root: Path
) -> None:
    """Verify an upload persists its audio and supplied transcript metadata."""
    payload = b"audio test payload"
    recorded_at = 1_700_000_000_000
    response = client.post(
        "/memo",
        headers=_headers(**{"X-Audio-Size": str(len(payload))}),
        data={
            "recordedAt": str(recorded_at),
            "client": "pebble",
            "transcription": "Device transcript",
        },
        files={"audio": ("recording.m4a", payload, "audio/mp4")},
    )

    assert response.status_code == 202
    notes = _notes(database_engine)
    assert len(notes) == 1
    note = notes[0]
    assert note.source == NoteSource.MEMO
    assert note.status == NoteStatus.RECEIVED
    assert note.client == "pebble"
    assert note.recorded_at_ms == recorded_at
    assert note.transcript == "Device transcript"
    assert note.audio_key.startswith("memos/")
    saved_audio = storage_root / note.audio_key
    assert saved_audio.read_bytes() == payload
    assert note.id.hex in saved_audio.name
    with Session(database_engine) as session:
        notifications = session.exec(select(NotificationOutbox)).all()
    assert len(notifications) == 1
    assert notifications[0].dedupe_key == f"{note.id}:received"


def test_audio_less_test_event_does_not_create_work(
    client: TestClient, database_engine: Engine
) -> None:
    """Verify an audio-less connectivity request creates no note."""
    response = client.post(
        "/memo",
        headers=_headers(),
        data={"recordedAt": "1700000000000", "client": "pebble"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "test_event"}
    assert _notes(database_engine) == []


def test_invalid_token_is_rejected(client: TestClient, database_engine: Engine) -> None:
    """Verify an invalid upload secret is rejected without saving a note."""
    response = client.post(
        "/memo",
        headers={"Authorization": "Bearer wrong-token"},
        data={"recordedAt": "1700000000000", "client": "pebble"},
        files={"audio": ("recording.m4a", b"audio", "audio/mp4")},
    )
    assert response.status_code == 401
    assert _notes(database_engine) == []


def test_oversized_audio_is_rejected(
    client: TestClient,
    database_engine: Engine,
    storage_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify audio exceeding the configured upload limit is rejected."""
    monkeypatch.setattr(ingest_main, "MAX_AUDIO_SIZE_BYTES", 4)
    response = client.post(
        "/memo",
        headers=_headers(),
        data={"recordedAt": "1700000000000", "client": "pebble"},
        files={"audio": ("recording.m4a", b"too large", "audio/mp4")},
    )
    assert response.status_code == 413
    assert _notes(database_engine) == []
    assert list(storage_root.rglob("*.m4a")) == []
    with Session(database_engine) as session:
        notifications = session.exec(select(NotificationOutbox)).all()
    assert len(notifications) == 1
    assert notifications[0].dedupe_key.startswith("oversized-upload:")
