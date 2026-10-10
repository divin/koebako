# API reference

## Upload API

### `GET /`

A health check returning `200` and `{"status":"ok"}`. It does not verify speech-to-text or transcript-receiver connectivity.

### `POST /memo`

Accepts an audio upload from Pebble Index. Authenticate with:

```http
Authorization: Bearer <WEBHOOK_TOKEN>
```

Submit `multipart/form-data` fields:

| Field/header | Required | Description |
|---|---|---|
| `recordedAt` | Yes | Recording time in Unix milliseconds. |
| `client` | Yes | Recording client identifier. |
| `audio` | For a real upload | Audio recording. |
| `transcription` | No | Transcript supplied by the client; if present, Koebako reuses it instead of calling STT. |
| `X-Audio-Size` | No | Expected audio byte count, checked against the saved file when supplied. |

The audio limit is 25 MiB. Configure the reverse proxy's total multipart-body limit slightly above that to account for form data.

| Result | Response |
|---|---|
| Audio accepted and saved | `202 {"status":"accepted"}` |
| Request has valid authentication and required fields but no audio | `200 {"status":"test_event"}`; no note is created. |
| Audio exceeds the limit | `413`; an alert may be queued. |
| Supplied `X-Audio-Size` does not match the saved file | `400`; the mismatched audio is removed. |

An audio-less request checks only that the upload URL and token work; it does not exercise transcription or delivery.

Example local request (replace the token and file):

```sh
curl -X POST http://127.0.0.1:8000/memo \
  -H 'Authorization: Bearer REPLACE_WITH_WEBHOOK_TOKEN' \
  -F 'recordedAt=1760000000000' \
  -F 'client=pebble' \
  -F 'audio=@voice-note.m4a;type=audio/mp4'
```

## Transcript delivery

After saving a transcript and its Markdown sidecar, the worker POSTs JSON to `TRANSCRIPT_WEBHOOK_URL`:

```json
{
  "schema_version": 1,
  "note_id": "stable-note-uuid",
  "source": "memo",
  "recorded_at_ms": 1760000000000,
  "transcript": "recognized words"
}
```

`transcript` may be `null` if no transcript could be obtained. The receiver must accept HTTP requests from the worker. Optional authentication is configured with both `TRANSCRIPT_WEBHOOK_AUTH_HEADER` and `TRANSCRIPT_WEBHOOK_AUTH_VALUE`; for Koemichi, use the `Authorization` header and value `Bearer <token>` when its receiver token is enabled.

Any HTTP `2xx` response marks delivery successful. On network errors or non-`2xx` responses, Koebako retries delivery. A request may reach the receiver even when Koebako does not receive its response, so delivery is at-least-once: use `note_id` as an idempotency key. Koemichi returns `accepted` for a new note and `duplicate` for a previously accepted identical note.
