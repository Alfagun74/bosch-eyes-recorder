# Troubleshooting

Start with the logs:

```bash
docker logs -f <container-name>
# CapRover: Apps → your app → Logs
```

`/status` returns JSON and contains the current `phase`, `error`, `camera`,
`media` (MediaMTX state), `storage`, and `retention` fields.

## Login and authentication

### Phase `need_setup`

No refresh token is available or the previous token was rejected.

1. Open `https://<app>/login`.
2. Click **Sign in with Bosch**, complete the SingleKey ID login.
3. Copy the complete callback URL (`http://localhost:8321/callback?...`) and
   paste it into the login page.
4. If it still fails, check the app logs for `Refresh-Token wurde von Bosch
   abgelehnt` and use a new login.

### Callback URL is rejected / "Login session expired"

- Pending logins expire after 30 minutes; start the login again.
- Make sure you copy the **entire** address bar, including `code` and `state`.
- If the URL contains `error=access_denied`, the Bosch login was cancelled or
  failed; try again.
- The app must be opened in the same browser profile that completed the Bosch
  login so the pending-login cookie is available. Reload `/login` after
  returning from Bosch.

### `Refresh-Token wurde von Bosch abgelehnt`

The token is invalid, was rotated by another process, or the session was
revoked (e.g. reuse of an old refresh token). Create a new login. Never run two
containers with the same token.

### "Client not allowed for direct access grants"

This is expected: Bosch's OAuth client does not support username/password
grants. Use the browser login flow (or a refresh token). There is no
password-based alternative that this project supports.

## No stream / stream not ready

`/status` shows `media.path_ready=false`:

1. Is the camera online and privacy mode off? Check the Bosch app.
2. Can the container reach the camera IP on TCP 443? Try
   `docker exec -it <container> python -c "import socket; socket.create_connection(('CAMERA_IP',443),5); print('ok')"`
   (for `LOCAL`).
3. Check logs for `Kamera-Quelle seit ... nicht bereit` and MediaMTX errors.
4. Try `CONNECTION_TYPE=REMOTE` if the server is not in the camera's LAN.
5. If the camera has been rebooted or the account changed, re-login.

### Camera credentials expire quickly / frequent reconnects

The app renews credentials every `CREDENTIAL_REFRESH_SECONDS` and on demand.
Frequent reconnects usually mean network issues or that Bosch revokes the
temporary account. Check the logs; a camera reboot also drops the stream.

## Video and audio

- **Black player / no image**: HLS may take a few seconds. Reload the page; if
  `path_ready` is true but the player stays black, check `/status` and the
  browser console.
- **No sound**: browsers block autoplay with audio; click the player's volume
  control. Check the camera's audio setting.
- **Choppy playback**: HLS adds buffering latency; check server CPU/network.
  The recording itself is not affected by playback.
- **Safari**: HLS plays natively; hls.js is used where supported.

## Recordings and storage

### No files in `/data/recordings`

- `phase` must be `running` and `media.path_ready=true`.
- Check `SEGMENT_MINUTES`: the first segment is only finalized after that
  duration (default 10 minutes). Use `/data/recordings` after a few minutes.
- Check that the volume is mounted at `/data` and writable.

### Disk fills up

Set/lower `RETENTION_DAYS`, set `MAX_STORAGE_GB`, and set `MIN_FREE_GB`
(default 5 GB). Retention runs every 5 minutes and at startup; MediaMTX also
deletes segments older than `RETENTION_DAYS`.

### Segments unplayable

The last segment may be incomplete after an abrupt stop. Earlier segments are
finalized and playable. Play with VLC/ffmpeg; concatenate losslessly:

```bash
printf "file '%s'\n" /data/recordings/eyes/*.mp4 > /tmp/list.txt
ffmpeg -f concat -safe 0 -i /tmp/list.txt -c copy /tmp/full.mp4
```

## Recording playback

- **A segment is missing from `/recordings`**: only finalized segments are
  listed. Wait for the current segment to finish (`SEGMENT_MINUTES`).
- **Video does not start / browser shows an error**: download the segment and
  play it in VLC to check the file. If that works, try another browser (H.264
  + AAC in fragmented MP4 is supported by current Chrome/Firefox/Edge/Safari).
- **Seeking jumps back**: some browsers buffer conservatively; try the native
  player or VLC.
- **Old recordings are gone**: they exceeded `RETENTION_DAYS` or were removed
  by `MAX_STORAGE_GB`/`MIN_FREE_GB`.
- **`/media/...` returns 404**: the segment was deleted by retention or the
  token is from a previous app run (reload `/recordings`).

## AV1/Opus transcoding (Intel QSV)

- **`Encoder fehlen: av1_qsv` / worker inactive**: the container is not the
  amd64 image, FFmpeg is missing, or the Jellyfin FFmpeg build did not include
  QSV. Check:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.qsv.yml exec bosch-recorder \
    ffmpeg -hide_banner -encoders | grep -E 'av1_qsv|libopus'
  ```
- **Device errors (`/dev/dri`, permission denied, failed to initialise)**:
  pass the render node and the host render GID:
  ```yaml
  devices: ["/dev/dri:/dev/dri"]
  group_add: ["122"]   # getent group render | cut -d: -f3 on the host
  ```
  The container currently runs as root, which normally suffices for device
  access. Verify with:
  ```bash
  docker compose -f docker-compose.yml -f docker-compose.qsv.yml exec bosch-recorder \
    vainfo --display drm --device /dev/dri/renderD128
  ```
- **`Could not open encoder before EOF` / `Nothing was written into output
  file`**: this happens for segments whose audio stream has no packets. The
  worker now probes audio first and falls back to video-only; update to
  1.4.1+. The original recording is kept until a transcode succeeds.
- **`global_quality` / encoder option errors**: try
  `TRANSCODE_BITRATE=1200k` instead of quality mode, or a different
  `TRANSCODE_PRESET`.
- **No storage savings**: with a low-quality source (`HIGH_QUALITY=false`) the
  H.264 stream is already very small; re-encoding can even increase size. Use
  `HIGH_QUALITY=true` and verify with a few segments.
- **CapRover**: CapRover's app deployment does not expose `/dev/dri`; use the
  Docker Compose + qsv override path for GPU transcoding.
- **Old segments are not converted**: `TRANSCODE_EXISTING=false` excludes
  files created before the worker starts; set it to `true` (default).

## Playback of recordings

- **Play button does nothing**: update to 1.4.0+; older versions attached
  player handlers before the list existed. Hard-refresh the page after updating.
- **Video fails to play / MEDIA_ERR_SRC_NOT_SUPPORTED**: the segment may be the
  currently written file (hidden after ~20 s), or the browser lacks the codec
  (Safari with WebM/AV1+Opus). Download the file and play it in VLC, or set
  `TRANSCODE_CONTAINER=mkv`/`mp4` as appropriate.
- **Seeking resets**: some browsers handle fragmented MP4 conservatively; try
  another browser or VLC. Byte-range serving is enabled.

## Web UI / access

- **HTTP Basic auth prompt**: `WEB_PASSWORD` is set. User: `admin`, password:
  your configured value.
- **401 even with the right password**: check for stray whitespace in the
  CapRover env var; redeploy after changing it.
- **`/healthz` returns non-200**: the container is not running or the port is
  wrong; check `HEALTH_PORT` and CapRover's Container HTTP Port (80).
- **CapRover build fails**: check the build logs. The Dockerfile downloads
  MediaMTX; the build server needs Internet access. Supported build
  architectures: `amd64`, `arm64`.

## Logs and diagnostics

Useful commands:

```bash
# app + MediaMTX logs
docker logs --tail 200 <container>

# container health and status
docker inspect --format '{{json .State.Health}}' <container>
curl -s http://<server>:80/healthz
curl -s http://<server>:80/status | jq .
```

Please redact camera IPs, tokens, and account identifiers before opening an
issue. Never post `/data/state/bosch_token.json` or `mediamtx.yml` contents.
