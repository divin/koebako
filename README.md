# Koebako 🎙️

**Koebako is the recording and transcription service for [Pebble Index](https://repebble.com/index).** Its API saves uploaded audio; a background worker converts recordings to WAV, sends them to a speech-to-text service, saves transcripts, and POSTs them to a receiver you choose. Its Japanese-inspired name combines *koe* (声, “voice”) and *bako* (箱, “box”). Its companion, [Koemichi](https://github.com/divin/koemichi), classifies transcripts and dispatches them to an automation service. The projects keep separate code and storage; you choose which services process or receive your data.

AI tools were used during the development of this project.

## 🧭 Architecture

```mermaid
flowchart TD
    A[Pebble Index] -->|Recording| B[Koebako upload service]
    B --> C[(Audio files and Koebako database)]
    C -->|Saved work| D[Background worker]
    D -->|Convert audio to WAV| E[Speech-to-text service]
    E -->|Transcript| F[Save transcript]
    F -->|Send transcript over HTTP| G[Configured receiver]
```

The upload API and background worker are separate processes built from the same image. The worker uses Koebako's SQLite database and audio directory to track work, marks each job in progress while processing, and retries failures. No separate queue service is required. Delivery may repeat, so receivers should deduplicate transcripts by their stable `note_id`.

## 🚀 Run with Compose

You need Docker Compose, a reachable speech-to-text service, and a transcript receiver. Set their addresses in `.env`, then run these commands from the Koebako project directory.

```sh
cp example.env .env                 # Create your local configuration
# Edit .env: replace sample secrets and set service addresses.
mkdir -p ./data                     # Or create the directory set by HOST_DATA_DIR.
export HOST_UID="$(id -u)" HOST_GID="$(id -g)"
docker compose up -d --build
docker compose logs -f api worker   # Follow service logs; Ctrl+C exits
```

Compose runs both services as a non-root user matching the host account supplied through `HOST_UID` and `HOST_GID`. Creating the data directory first ensures it is owned by that account, so the container can write the SQLite database and recordings. If you use a custom `HOST_DATA_DIR`, create that directory first and make sure it is writable by the account whose IDs you pass to Compose.

Push a version tag such as `v1.0.0` to publish `ghcr.io/divin/koebako:v1.0.0` and `ghcr.io/divin/koebako:latest`. Tags containing a hyphen (for example, `v1.1.0-rc.1`) publish the version tag but do not move `latest`.

The upload service listens on `127.0.0.1:8000` and is not directly public. Pebble Index requires a publicly reachable HTTPS address, so expose only the `api` service through an HTTPS reverse proxy or tunnel—for example, [Cloudflare Tunnel](https://developers.cloudflare.com/tunnel/) or [Pangolin](https://github.com/fosrl/pangolin)—and configure that address in Pebble. Keep the `worker` off the public-facing network and do not publish its port; it only needs access to the configured speech-to-text service and transcript receiver. Both processes store data under `/data`.

Koebako can send transcripts to any HTTP receiver that accepts its JSON format. The worker must be able to reach both the speech-to-text service and receiver. With Koemichi, set `TRANSCRIPT_WEBHOOK_URL` to an address the Koebako worker can reach and use Koemichi's `TRANSCRIPT_INGEST_TOKEN` as `TRANSCRIPT_WEBHOOK_AUTH_VALUE` (`Authorization` header, prefixed with `Bearer `). Configure other receivers with their reachable URL and authentication details.

## 📥 Upload API

| Method and path | Authentication | Purpose |
|---|---|---|
| `GET /` | None | Checks that the upload service is responding; returns `200 {"status":"ok"}`. |
| `POST /memo` | Shared secret (`WEBHOOK_TOKEN`) | Accepts an audio recording; a request without audio can check the URL and secret. |


Pebble must send `Authorization: Bearer <WEBHOOK_TOKEN>`. For `POST /memo`, send form fields `recordedAt` (Unix time in milliseconds) and `client` (the recording app's identifier). Include the `audio` file for a real recording. If the app already has a transcript, it may send it in the optional `transcription` field. The optional `X-Audio-Size` header checks that the stored file size matches the size reported by the app; it is separate from the upload limit. Audio above 25 MiB (about 26 MB) is rejected with HTTP `413` (“too large”); a size mismatch returns HTTP `400` (“invalid request”).

```sh
# Local example only; replace the token. Pebble must use your public HTTPS address.
curl -X POST http://127.0.0.1:8000/memo \
  -H 'Authorization: Bearer REPLACE_WITH_WEBHOOK_TOKEN' \
  -F 'recordedAt=1760000000000' \
  -F 'client=pebble' \
  -F 'audio=@voice-note.m4a;type=audio/mp4'
```

A saved upload returns HTTP `202` with `{"status":"accepted"}`. An authenticated request with the required form fields but no audio returns HTTP `200` with `{"status":"test_event"}` and does not create a note. This audio-less test only checks that the upload URL and secret work; it does not test transcription or transcript delivery.

## 📤 Transcript payload

After transcription, Koebako sends this JSON payload to `TRANSCRIPT_WEBHOOK_URL`. The receiver can be any service that accepts this format:

```json
{
  "schema_version": 1,
  "note_id": "stable-note-uuid",
  "source": "memo",
  "recorded_at_ms": 1760000000000,
  "transcript": "recognized words"
}
```

Any successful HTTP response (status `200`–`299`) marks the payload delivered. If the receiver is unavailable or returns an error, Koebako retries later and eventually records a final failure. A receiver should save the payload before replying. Delivery can repeat, so use the stable `note_id` to recognize a transcript that was already received.

## ⚙️ Configuration

| Variable | Required by | Purpose |
|---|---|---|
| `WEBHOOK_TOKEN` | Upload service | Long, random secret Pebble sends with each upload. |
| `STT_URL` | Worker | Address of the speech-to-text service. |
| `STT_MODEL_NAME` | Worker | Name of the speech-recognition model used by the speech-to-text service. |
| `TRANSCRIPT_WEBHOOK_URL` | Worker | Compatible receiver endpoint for completed transcript payloads. |
| `TRANSCRIPT_WEBHOOK_AUTH_HEADER` | Worker | Optional name of the header used to authenticate with the receiver; set it together with the value below. |
| `TRANSCRIPT_WEBHOOK_AUTH_VALUE` | Worker | Optional secret sent in the authentication header above. |
| `NOTIFICATION_MODE` | API and worker | `normal` by default; supported values are `normal`, `debug`, and `off`. |
| `PUSHOVER_API_TOKEN` | Worker, unless notifications are off | Pushover application token. |
| `PUSHOVER_USER_KEY` | Worker, unless notifications are off | Pushover user or group key. |
| `HOST_DATA_DIR` | Compose | Host directory for Koebako's database and recordings; defaults to `./data`. |
| `DATA_DIR` | Local run | Application data directory; defaults to `./data`. |
| `DATABASE_PATH` | Optional | Database path; defaults to `${DATA_DIR}/koebako.db`. |
| `TZ` | Optional | Timezone used for recording folders and transcript metadata; defaults to `Europe/Berlin`. |

The speech-to-text service and transcript receiver must be reachable from the worker container. Audio is sent to the speech-to-text service, and transcripts are sent to the receiver; choose those services with the privacy implications in mind. Notifications are enabled by default, so configure both Pushover credentials or set `NOTIFICATION_MODE=off`. Create an application at [Pushover](https://pushover.net), then add its application token and your user or group key to `.env`.

## 🔔 Notifications

The worker stores notification events in SQLite and delivers them separately from transcription and transcript delivery, so a Pushover outage does not block note processing. Compose passes Pushover credentials only to the worker. Normal mode notifies you when a recording is accepted, transcribed, and delivered to the configured receiver; a final processing failure gets an urgent notice. Authenticated uploads over the size limit trigger at most one alert per hour. Debug mode adds quiet notices for retries and recovery, plus transcription timing and model details. Off mode disables notifications and does not require Pushover credentials. Messages do not contain transcript text. Notifications may appear on a device lock screen, so treat them as private.

## 🤖 Example speech-to-text setup

My setup uses the CPU container from [`audio.cpp`](https://github.com/0xShug0/audio.cpp) with the [Qwen3-ASR-1.7B GGUF model](https://huggingface.co/audio-cpp/audio.cpp-gguf/tree/main/Qwen3-ASR-1.7B-GGUF). For that setup, configure `STT_URL=http://audio-cpp:8080/v1/audio/transcriptions` and `STT_MODEL_NAME=qwen3-asr-1.7b-q8_0`; make sure the worker can reach the configured URL. These are my deployment choices; any compatible speech-to-text service can be used.

## 💾 Data and deployment notes

Koebako stores its SQLite database at `/data/koebako.db`, audio under `/data/memos/`, and a Markdown transcript file beside each recording. Back up the complete data directory. Use a local filesystem and run one worker. The receiver connects over HTTP and does not share Koebako's database or audio files.

Koebako uses its own fresh schema and data directory; it does not import another service's database.

For internet-facing deployments, expose the Pebble upload API through an HTTPS reverse proxy. Limit the total request size there to slightly above 25 MiB for multipart form data; Koebako checks the audio size after the request is parsed. Apply rate limits and store tokens as deployment secrets. Do not make the speech-to-text service or database administration tools publicly accessible.

## 🧪 Development checks

For development, use Python 3.12, [`uv`](https://github.com/astral-sh/uv), and [`just`](https://just.systems/man/en/):

```sh
uv sync --dev # Install development dependencies
just          # Format, lint, and sort imports
just check    # Check types, formatting, and lint without changing files
just test     # Run the test suite
```
