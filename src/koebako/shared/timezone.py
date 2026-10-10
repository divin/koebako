"""Timestamp conversion helpers using Koebako's configured timezone."""

from datetime import UTC, datetime

from koebako.settings import TIMEZONE


def ts_to_local(timestamp_ms: int) -> datetime:
    """Convert Unix milliseconds into an aware datetime in the local timezone."""
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).astimezone(TIMEZONE)
