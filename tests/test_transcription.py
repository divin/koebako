"""Tests for ffmpeg conversion and receiver request contract."""

import os
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("WEBHOOK_TOKEN", "unit-test-token")

import httpx
import pytest

from koebako.services.transcriber import audio as transcriber_audio
from koebako.services.transcriber import client as transcriber_client
from koebako.services.transcriber import delivery as transcript_delivery
from koebako.shared.models.note import Note, NoteSource


def test_to_wav_invokes_ffmpeg_and_returns_temporary_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify audio conversion invokes ffmpeg and returns a WAV file."""
    source = tmp_path / "recording.m4a"
    source.write_bytes(b"source audio")
    command: list[str] = []

    def fake_run(args: list[str], **kwargs: object) -> None:
        """Record ffmpeg arguments and create its expected output file."""
        command.extend(args)
        Path(args[-1]).write_bytes(b"wav audio")

    monkeypatch.setattr(transcriber_audio.subprocess, "run", fake_run)
    wav_path = transcriber_audio.to_wav(source)
    try:
        assert wav_path.suffix == ".wav"
        assert wav_path.read_bytes() == b"wav audio"
        assert command[0] == "ffmpeg"
        assert "16000" in command
        assert "1" in command
    finally:
        wav_path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_transcribe_normalizes_response_and_cleans_temporary_wav(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify transcription trims its response and cleans up converted audio."""
    wav_path = tmp_path / "converted.wav"
    wav_path.write_bytes(b"wav bytes")

    def handler(request: httpx.Request) -> httpx.Response:
        """Return a mocked transcript response for the request."""
        assert str(request.url) == "https://stt.test/transcribe"
        return httpx.Response(200, json={"text": "  recognized words  "})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        transcriber_client.httpx,
        "AsyncClient",
        lambda *, timeout: original_client(
            transport=httpx.MockTransport(handler), timeout=timeout
        ),
    )
    monkeypatch.setattr(transcriber_client, "STT_URL", "https://stt.test/transcribe")
    monkeypatch.setattr(transcriber_client, "STT_MODEL_NAME", "test-model")
    monkeypatch.setattr(transcriber_client, "to_wav", lambda _: wav_path)

    assert (
        await transcriber_client.transcribe(tmp_path / "source.m4a")
        == "recognized words"
    )
    assert not wav_path.exists()


@pytest.mark.asyncio
async def test_delivery_includes_version_and_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the transcript payload and idempotency request header."""
    note = Note(
        id=uuid4(),
        source=NoteSource.MEMO,
        client="pebble",
        recorded_at_ms=1_700_000_000_000,
        audio_key="memos/note.m4a",
        transcript="spoken words",
    )
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Capture the outgoing delivery request and accept it."""
        received.append(request)
        return httpx.Response(202)

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        transcript_delivery.httpx,
        "AsyncClient",
        lambda *, timeout: original_client(
            transport=httpx.MockTransport(handler), timeout=timeout
        ),
    )
    monkeypatch.setattr(
        transcript_delivery,
        "TRANSCRIPT_WEBHOOK_URL",
        "https://receiver.test/transcripts",
    )
    monkeypatch.setattr(
        transcript_delivery, "TRANSCRIPT_WEBHOOK_AUTH_HEADER", "Authorization"
    )
    monkeypatch.setattr(
        transcript_delivery, "TRANSCRIPT_WEBHOOK_AUTH_VALUE", "Bearer secret"
    )

    await transcript_delivery.post_transcript(note)

    assert len(received) == 1
    request = received[0]
    assert request.headers["Authorization"] == "Bearer secret"
    assert request.headers["Idempotency-Key"] == str(note.id)
    payload = __import__("json").loads(request.content)
    assert payload == {
        "schema_version": 1,
        "note_id": str(note.id),
        "source": "memo",
        "recorded_at_ms": 1_700_000_000_000,
        "transcript": "spoken words",
    }
