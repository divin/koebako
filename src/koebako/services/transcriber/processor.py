"""Transcribe persisted audio and write a Markdown sidecar."""

from pathlib import Path
from uuid import UUID

from koebako.services.transcriber.client import transcribe
from koebako.services.transcriber.transcript import save_transcript
from koebako.settings import AUDIO_STORAGE_ROOT
from koebako.shared.models.note import Note


def audio_path_for_key(audio_key: str) -> Path:
    """Resolve a stored audio key without allowing it to escape the data root."""
    storage_root = AUDIO_STORAGE_ROOT.resolve()
    audio_path = (storage_root / audio_key).resolve()
    if not audio_path.is_relative_to(storage_root):
        raise ValueError("Audio key escapes storage root")
    if not audio_path.is_file():
        raise FileNotFoundError("Stored audio file is missing")
    return audio_path


async def transcribe_note(note: Note) -> str | None:
    """Reuse a client transcript or transcribe audio, then save the sidecar."""
    audio_path = audio_path_for_key(note.audio_key)
    transcript = note.transcript.strip() if note.transcript else None
    if transcript is None:
        transcript = await transcribe(audio_path)

    save_transcript(
        audio_path,
        note_id=UUID(str(note.id)),
        recorded_at_ms=note.recorded_at_ms,
        source=note.source,
        transcript=transcript,
    )
    return transcript
