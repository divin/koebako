"""Configuration shared by the Koebako API and worker."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from koebako.shared.models.notification import NotificationMode

load_dotenv()


def _required_env(name: str) -> str:
    """Return a required non-empty environment variable.

    Parameters
    ----------
    name : str
        Environment variable to read.

    Returns
    -------
    str
        Configured value.

    Raises
    ------
    RuntimeError
        If the variable is unset or empty.
    """
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"{name} must be set and non-empty")
    return value


DATA_DIR = Path(os.getenv("DATA_DIR") or "data")
DATABASE_PATH = Path(os.getenv("DATABASE_PATH") or DATA_DIR / "koebako.db")
AUDIO_STORAGE_ROOT = DATA_DIR
MEMO_SAVE_DIR = DATA_DIR / "memos"

TIMEZONE = ZoneInfo(os.getenv("TZ") or "Europe/Berlin")

WEBHOOK_TOKEN = _required_env("WEBHOOK_TOKEN")
STT_URL = os.getenv("STT_URL")
STT_MODEL_NAME = os.getenv("STT_MODEL_NAME")
TRANSCRIPT_WEBHOOK_URL = os.getenv("TRANSCRIPT_WEBHOOK_URL")
TRANSCRIPT_WEBHOOK_AUTH_HEADER = os.getenv("TRANSCRIPT_WEBHOOK_AUTH_HEADER") or None
TRANSCRIPT_WEBHOOK_AUTH_VALUE = os.getenv("TRANSCRIPT_WEBHOOK_AUTH_VALUE") or None

PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN") or None
PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY") or None
NOTIFICATION_MODE = NotificationMode(
    (os.getenv("NOTIFICATION_MODE") or NotificationMode.NORMAL.value).strip().lower()
)
