# Notifications and privacy

Koebako writes notification events to its SQLite outbox. The worker sends them to Pushover separately from transcription and transcript delivery, so a Pushover outage does not block note processing. Set `PUSHOVER_API_TOKEN` and `PUSHOVER_USER_KEY` for the worker, or set `NOTIFICATION_MODE=off`.

| Mode | Behavior |
|---|---|
| `normal` | Notices for accepted uploads, completed transcriptions, and successful transcript delivery. Terminal processing failures are urgent. The transcription notice includes the recognized transcript. |
| `debug` | Normal notices plus low-priority retry/recovery details. The transcription message also includes elapsed time and STT model. |
| `off` | No notifications are queued or delivered; Pushover credentials are not required. |

Oversized authenticated uploads produce at most one alert per hour. Messages are limited to 1,024 characters; longer content is visibly marked as truncated.

Transcripts are personal data. When notifications are enabled, transcript text is sent to Pushover, appears in the notification outbox, and may be visible on device lock screens. Configure Pushover and its receiving devices accordingly.
