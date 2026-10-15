# Deployment and configuration

## Compose setup

Copy the example configuration, then set real credentials and service addresses:

```sh
cp example.env .env
```

Use the provided Compose file as an example deployment and pull the published image; you do not need to build the Docker image locally. Required values include `WEBHOOK_TOKEN`, `STT_URL`, `STT_MODEL_NAME`, and `TRANSCRIPT_WEBHOOK_URL`. If transcript authentication is enabled, set both `TRANSCRIPT_WEBHOOK_AUTH_HEADER` and `TRANSCRIPT_WEBHOOK_AUTH_VALUE`. Notifications default to `normal`; provide both Pushover credentials or set `NOTIFICATION_MODE=off`.

Create the host data directory before starting Compose. The containers run as `HOST_UID:HOST_GID` (default `1000:1000`), so those IDs must have write access to `HOST_DATA_DIR`:

```sh
mkdir -p ./data
export HOST_UID="$(id -u)" HOST_GID="$(id -g)"
docker compose pull
docker compose up -d
docker compose logs -f api worker
```

`HOST_DATA_DIR` defaults to `./data`. Both API and worker mount it at `/data`. If you use a custom path, create it first and make it writable by the configured container user. Use a local filesystem and run one worker against a given database.

## Network exposure

The standalone example Compose file maps host port `8000` to container port `8000` on all host interfaces. To receive uploads from Pebble, expose only the API through an HTTPS reverse proxy or tunnel and configure Pebble with that public URL. Allow only required traffic through the host firewall; do not leave the upload port open to untrusted networks. If the proxy or tunnel joins a shared Docker network, omit the host `ports` mapping and attach only the API to that network; this is how the Hako/Pangolin deployment is wired. Keep the worker off the public-facing network; it needs access to the speech-to-text service, receiver, and Pushover when enabled. Do not expose the model service or database administration interfaces.

Set `STT_URL` and `TRANSCRIPT_WEBHOOK_URL` to addresses reachable **from the worker container**. With Koemichi, the receiver accepts transcripts at `/transcripts`; authentication is optional, but if enabled the sender must set the matching Bearer header. Keep the receiver private or protect it with HTTPS and a strong token when it crosses an untrusted network.

## Configuration reference

| Variable | Used by | Purpose |
|---|---|---|
| `WEBHOOK_TOKEN` | API and worker | Required shared secret Pebble sends as `Authorization: Bearer <token>`. |
| `STT_URL` | Worker | Speech-to-text endpoint, usually ending in `/v1/audio/transcriptions`. |
| `STT_MODEL_NAME` | Worker | Model identifier accepted by the speech-to-text service. |
| `STT_REQUEST_TIMEOUT_SECONDS` | Worker | Per-request STT timeout in seconds, clamped to a minimum of 60; defaults to 900. Raise this if CPU transcriptions time out and disrupt the STT server. |
| `TRANSCRIPT_WEBHOOK_URL` | Worker | Receiver endpoint for completed transcripts. |
| `TRANSCRIPT_WEBHOOK_AUTH_HEADER` | Worker | Optional receiver authentication header name; set with the value below. |
| `TRANSCRIPT_WEBHOOK_AUTH_VALUE` | Worker | Optional receiver authentication value, such as `Bearer <token>`. |
| `NOTIFICATION_MODE` | API and worker | `normal` (default), `debug`, or `off`. |
| `PUSHOVER_API_TOKEN` | Worker | Pushover application token, required unless notifications are off. |
| `PUSHOVER_USER_KEY` | Worker | Pushover user/group key, required unless notifications are off. |
| `HOST_DATA_DIR` | Compose | Host data directory; defaults to `./data`. |
| `HOST_UID`, `HOST_GID` | Compose | Container UID/GID; default to `1000:1000`. |
| `DATA_DIR` | Local run | Application data directory; defaults to `./data` (Compose uses `/data`). |
| `DATABASE_PATH` | Optional | SQLite file path; defaults to `${DATA_DIR}/koebako.db`. |
| `TZ` | Optional | Timezone for recording folders and transcript metadata; defaults to `Europe/Berlin`. |

The Compose file defines the service addresses and paths for the deployment; keep credentials in `.env` and do not commit that file.

## Data and operations

Koebako stores:

- SQLite database at `/data/koebako.db`
- Original recordings under `/data/memos/YYYY-MM-DD/`
- A Markdown transcript sidecar beside each recording

Back up the complete data directory. Keep Koebako's database and recordings on a local filesystem, and do not point Koebako at another application's data directory.

Failed processing stages are retried after 10 seconds and then 30 seconds before being marked as failed. STT transport and server errors also receive short in-request retries. A transcript may be delivered more than once after receiver errors or timeouts; receivers should use the stable `note_id` for idempotency. See [API reference](api.md#transcript-delivery) for the contract.

The image release workflow publishes version tags and `latest` from Git tags. Stable tags (for example, `1.0.0`) update `latest`; prerelease tags containing a hyphen do not. Use the repository's Compose file as a deployment example and adjust its settings to your environment.
