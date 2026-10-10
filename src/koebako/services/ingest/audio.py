"""Audio persistence utilities for memo uploads."""

import logging
import shutil
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile

from koebako.settings import MEMO_SAVE_DIR
from koebako.shared.timezone import ts_to_local

logger = logging.getLogger(__name__)


def save_memo_audio(recorded_at: int, audio: UploadFile, note_id: UUID) -> Path:
    """Save a memo under a timezone-organized directory with a unique name."""
    local_dt = ts_to_local(recorded_at)
    folder = MEMO_SAVE_DIR / f"{local_dt:%Y-%m-%d}"
    folder.mkdir(parents=True, exist_ok=True)
    recorded_time = f"{local_dt:%Y%m%d-%H%M%S}-{local_dt.microsecond // 1000:03d}"
    path = folder / f"{recorded_time}-{note_id.hex}.m4a"

    logger.debug("Streaming audio to %s", path)
    with path.open("xb") as audio_file:
        shutil.copyfileobj(audio.file, audio_file)
    logger.info("Saved audio (%d bytes) to %s", path.stat().st_size, path)
    return path


def is_same_size(path: Path, expected: int | None) -> bool:
    """Check the optional client-reported size and delete a mismatched file."""
    if expected is None:
        logger.warning("No expected size provided for %s, skipping size check", path)
        return True

    actual = path.stat().st_size
    if actual != expected:
        logger.warning(
            "Audio size mismatch: expected=%d got=%d, deleting %s",
            expected,
            actual,
            path,
        )
        path.unlink(missing_ok=True)
        return False
    return True
