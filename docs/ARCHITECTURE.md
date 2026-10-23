# Architecture

This document describes how the container works internally, which components
exist, and what happens in the important error cases.

## Components

```text
┌────────────────────────────── container (port 80) ──────────────────────────────┐
│                                                                                 │
│  status_http.py                                                                 │
│    /                 dashboard (HTML + hls.js player)                           │
│    /login            login page (Bosch browser login + token fallback)          │
│    /oauth/start      creates PKCE verifier/state, redirects to Bosch            │
│    /oauth/finish     exchanges pasted callback code, stores tokens              │
│    /hls/<token>/...  reverse proxy to MediaMTX HLS                              │
    /recordings      recording browser (list, player, download)                   │
    /api/recordings  recording list as JSON                                      │
    /media/<token>/  segment streaming with HTTP Range support                   │
│    /healthz          health endpoint                                            │
│    /status           JSON status                                                │
│                                                                                 │
│  main.py             orchestration, worker thread, watchdog, status             │
│  auth.py             refresh-token state, OAuth refresh requests                │
│  oauth.py            PKCE browser login flow                                    │
│  cloud.py            Bosch Residential API client + camera TLS pin              │
│  mediamtx.py         MediaMTX config generation, process supervision, HLS       │
│  retention.py        rolling deletion (age/size/free space)                     │
│                                                                                 │
│  MediaMTX process (child)                                                       │
│    source: rtsps+http://<temp-creds>@<camera>:443/rtsp_tunnel?...               │
│    record: fMP4 segments -> /data/recordings/eyes/*.mp4                         │
│    HLS:    127.0.0.1:8888 (only proxied through the app)                        │
│    API:    127.0.0.1:9997 (status + config reload)                              │
└─────────────────────────────────────────────────────────────────────────────────┘
```

The app process owns the lifecycle. MediaMTX is a child process but can be
restarted independently by the app; the app's HTTP server keeps running.

## Data flow at startup

1. `app.main.main()` loads `Config` from environment and constructs `App`.
2. `App.run()` starts the HTTP server on `HEALTH_PORT` (80 in the image) and the
   worker thread.
3. Worker:
   1. Checks for a usable refresh token (`volume state` → `BOSCH_REFRESH_TOKEN`
      env → optional community `bosch_config.json`).
   2. If missing → phase `need_setup`; the UI sends the user to `/login`.
   3. Resolves the camera through Bosch's Residential API (`/v11/video_inputs`).
   4. Requests temporary stream credentials
      (`PUT /v11/video_inputs/<id>/connection`) and builds the MediaMTX source
      URL (`rtsps+http` for `LOCAL`, `rtsps`/tunnel for `REMOTE`).
   5. Fetches the camera TLS certificate fingerprint and writes
      `/data/state/mediamtx.yml` with the source URL and pinned fingerprint.
   6. Starts MediaMTX. MediaMTX pulls the stream and starts recording + HLS.
   7. Sets phase `running`, then starts:
      - credential loop (refresh every `CREDENTIAL_REFRESH_SECONDS`, cached
        until needed),
      - watchdog loop (every 8 s; if source not ready for `STALL_SECONDS`,
        requests new credentials and rewrites config),
      - retention loop (every 5 min).
4. The dashboard polls `/status` indirectly (page refresh) and embeds the HLS
   player once `media.path_ready` is true.

## Auth token lifecycle

- **Refresh token**: created via browser login (`/login` → `/oauth/start` →
  Bosch → `/oauth/finish`), or supplied by `BOSCH_REFRESH_TOKEN`. Stored in
  `/data/state/bosch_token.json`.
- **Access token**: obtained via `grant_type=refresh_token`; valid ~1 h. The
  app renews it transparently on a 401 from the Bosch API.
- **Rotation**: every successful refresh returns a new refresh token. The app
  writes the new token atomically before using the access token. Only one
  consumer may use a refresh token chain: **do not share a token between
  instances**.
- **Failure**: if Bosch rejects the refresh token (expired, revoked, reused),
  the worker goes to `need_setup` and waits for a new login. The UI shows the
  state and offers `/login`.

## Camera credential lifecycle

Bosch's local/RTSPS credentials are short-lived ("temporary"). The app:

1. requests new credentials every ~20 minutes (or on demand),
2. caches them and only rewrites the MediaMTX source when needed (source down),
3. keeps the current stream alive as long as MediaMTX's connection persists.

If the camera closes the connection (credential expiry, network drop, camera
reboot), the watchdog notices `path_ready=false`, requests fresh credentials,
rewrites `mediamtx.yml`, and MediaMTX reloads the source. Recording gaps are
limited to the reconnect time.

## Recording

MediaMTX's built-in recorder writes fMP4 segments containing the original
camera tracks. Segment length is `recordSegmentDuration`
(`SEGMENT_MINUTES`, default 10). The `recordPath` includes `%path` (required by
MediaMTX) and a timestamp, e.g.
`/data/recordings/%path/%Y-%m-%d_%H-%M-%S-%f` → `/data/recordings/eyes/…mp4`.

`recordDeleteAfter` is set to `RETENTION_DAYS * 24h`. The Python retention loop
additionally enforces `MAX_STORAGE_GB` and `MIN_FREE_GB` and removes empty
camera directories. Both layers are intentionally redundant.

## HLS live view

MediaMTX generates HLS on `127.0.0.1:8888` (`hlsAlwaysRemux` enabled). The app
mounts hls.js at `/static/hls.min.js` and embeds a player pointing at
`/hls/<random token>/eyes/index.m3u8`. The proxy adds the token check and
forwards playlist/segment requests to MediaMTX. This keeps the stream on the
same origin and behind the app's optional Basic auth, without publishing
MediaMTX's ports.

The HLS master playlist contains a separate audio rendition
(`audio2_stream.m3u8`) that hls.js handles automatically.

## Recordings browser

Recordings are read from the file system (`/data/recordings/**/*.mp4`). Files
modified within the last 20 seconds are hidden because they are likely the
segment currently being written. The `/recordings` page groups segments by day,
embeds an HTML5 player, and streams segments through `/media/<token>/...` with
HTTP Range support (seek/scrub). "Ab hier" chains consecutive segments in
chronological order by advancing to the next (newer) segment when the player
reports `ended`.

## State and files

| Path | Purpose |
|---|---|
| `/data/recordings/eyes/*.mp4` | recording segments |
| `/data/state/bosch_token.json` | refresh token + cached access token (mode 600) |
| `/data/state/mediamtx.yml` | generated MediaMTX config (contains temporary creds) |
| `/data/state/last_status.json` | last status snapshot on graceful shutdown |

## Failure handling

| Failure | Behavior |
|---|---|
| Cloud unreachable | Existing stream continues until the camera connection drops; worker retries; `/status.error` is set. |
| Refresh token rejected | `need_setup`; UI asks for a new login. |
| Camera offline / privacy mode | `path_ready=false`; watchdog retries with fresh credentials; dashboard says "stream not ready". |
| MediaMTX crashes | Supervisor restarts it with backoff; recording resumes when the source is available. |
| Disk full | Retention loop deletes oldest segments (if `MAX_STORAGE_GB`/`MIN_FREE_GB` configured); MediaMTX may fail writes until space is available. |
| Container restart | Worker re-authenticates (refresh token from volume), resolves camera, resumes recording. The in-flight segment may be lost. |

## Design decisions

- **MediaMTX instead of ffmpeg**: proven RTSP-over-HTTPS handling, built-in
  recording + HLS, no re-encoding, one binary.
- **Python stdlib HTTP server**: small, dependency-free UI/proxy; adequate for
  a single-user dashboard.
- **No database**: file system is the source of truth; status is derived from
  MediaMTX API + files.
- **PKCE browser login with pasted callback**: the registered redirect URI is
  `http://localhost:8321/callback`, which a remote container cannot receive.
  Pasting the callback URL is the least-fragile option without browser
  automation and keeps the password inside the official Bosch login page.
