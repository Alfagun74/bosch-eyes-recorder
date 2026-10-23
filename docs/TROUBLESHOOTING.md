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
