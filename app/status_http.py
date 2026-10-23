"""Web UI, login and HTTP endpoints (dashboard, recordings, HLS proxy)."""
from __future__ import annotations

import base64
import datetime
import html
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

log = logging.getLogger('bosch-recorder.http')
STATIC_DIR = Path(__file__).resolve().parent / 'static'
HLS_PATH_RE = re.compile(r'^/hls/([A-Za-z0-9_-]+)/(.+)$')
MEDIA_PATH_RE = re.compile(r'^/media/([A-Za-z0-9_-]+)/(.+)$')
RECORDING_NAME_RE = re.compile(
    r'^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})-(\d{6})$')
RECORDING_EXTENSIONS = {'.mp4', '.webm', '.mkv'}
MIME_TYPES = {
    '.mp4': 'video/mp4', '.webm': 'video/webm', '.mkv': 'video/x-matroska',
    '.css': 'text/css; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
    '.svg': 'image/svg+xml', '.txt': 'text/plain; charset=utf-8',
}


def format_size(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return '%.2f GB' % (num_bytes / (1024 ** 3))
    if num_bytes >= 1024 ** 2:
        return '%.1f MB' % (num_bytes / (1024 ** 2))
    if num_bytes >= 1024:
        return '%.1f KB' % (num_bytes / 1024)
    return '%d B' % num_bytes


def parse_byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse an HTTP Range header. None means serve the full file."""
    if not header:
        return None
    value = header.strip()
    if not value.lower().startswith('bytes='):
        return None
    spec = value[6:].split(',', 1)[0].strip()
    if '-' not in spec:
        return None
    first, last = spec.split('-', 1)
    try:
        if first == '':
            suffix = int(last)
            if suffix <= 0 or size <= 0:
                raise ValueError
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(first)
            end = int(last) if last else size - 1
            if start < 0 or start >= size or end < start:
                raise ValueError
            end = min(end, size - 1)
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid byte range') from exc
    return start, end


def _codec_for(name: str, extension: str) -> str:
    if extension in ('.webm', '.mkv') or '.av1' in name.lower():
        return 'AV1'
    return 'H.264'


def list_recordings(records_dir: Path, now: float | None = None,
                    min_age_seconds: float = 20.0) -> list[dict]:
    """List finalized recording segments, newest first."""
    current = time.time() if now is None else now
    entries: list[dict] = []
    paths: list[Path] = []
    try:
        for extension in RECORDING_EXTENSIONS:
            paths.extend(records_dir.rglob('*' + extension))
    except OSError:
        paths = []
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        if current - stat.st_mtime < max(0.0, min_age_seconds):
            continue
        name = path.name
        match = RECORDING_NAME_RE.match(path.stem)
        start = None
        if match:
            try:
                start = datetime.datetime(*(int(x) for x in match.groups()))
            except ValueError:
                start = None
        if start:
            display_date = start.strftime('%d.%m.%Y')
            display_time = start.strftime('%H:%M:%S')
            date_iso = start.strftime('%Y-%m-%d')
            time_iso = start.strftime('%H:%M:%S')
        else:
            local = datetime.datetime.fromtimestamp(stat.st_mtime)
            display_date = local.strftime('%d.%m.%Y')
            display_time = local.strftime('%H:%M:%S')
            date_iso = local.strftime('%Y-%m-%d')
            time_iso = local.strftime('%H:%M:%S')
        extension = path.suffix.lower()
        try:
            rel = path.relative_to(records_dir).as_posix()
        except ValueError:
            rel = name
        entries.append({
            'name': name,
            'rel': rel,
            'size': stat.st_size,
            'size_human': format_size(stat.st_size),
            'mtime': stat.st_mtime,
            'display_date': display_date,
            'display_time': display_time,
            'date': date_iso,
            'time': time_iso,
            'label': '%s %s' % (display_date, display_time),
            'ext': extension.lstrip('.'),
            'codec': _codec_for(name, extension),
        })
    entries.sort(key=lambda entry: entry['mtime'], reverse=True)
    return entries


class StatusHandler(BaseHTTPRequestHandler):
    server_version = 'BoschRecorder/1.4'
    app = None  # type: ignore[assignment]

    def log_message(self, fmt: str, *args: object) -> None:
        log.debug('%s - %s', self.address_string(), fmt % args)

    # ---------- helpers ----------
    def _send(self, code: int, body: bytes, content_type: str = 'text/html; charset=utf-8',
              extra_headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, page: str, extra_headers: dict | None = None) -> None:
        headers = {'Cache-Control': 'no-store'}
        headers.update(extra_headers or {})
        self._send(code, page.encode('utf-8'), 'text/html; charset=utf-8', headers)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'),
                   'application/json; charset=utf-8', {'Cache-Control': 'no-store'})

    def _redirect(self, location: str, extra_headers: dict | None = None) -> None:
        headers = {'Location': location, **(extra_headers or {})}
        self._send(302, b'', 'text/plain; charset=utf-8', headers)

    def _cookie(self, name: str) -> str | None:
        raw = self.headers.get('Cookie') or ''
        for part in raw.split(';'):
            key, _, value = part.strip().partition('=')
            if key == name:
                return value
        return None

    def _auth_ok(self) -> bool:
        if not self.app.cfg.web_password:
            return True
        header = self.headers.get('Authorization') or ''
        if not header.startswith('Basic '):
            return False
        try:
            user, _, password = base64.b64decode(header[6:]).decode('utf-8').partition(':')
        except Exception:
            return False
        return user == 'admin' and password == self.app.cfg.web_password

    def _require_auth(self) -> bool:
        if self._auth_ok():
            return True
        self.send_response(401)
        self.send_header('WWW-Authenticate', 'Basic realm="Bosch Recorder"')
        self.send_header('Content-Length', '0')
        self.end_headers()
        return False

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(min(length, 128 * 1024)).decode('utf-8', 'replace')
        return {key: values[0] for key, values in parse_qs(body, keep_blank_values=True).items()}

    # ---------- GET ----------
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == '/healthz':
            self._json(200, {'status': 'ok', 'phase': self.app.status()['phase']})
            return
        if path == '/favicon.ico':
            self._send(204, b'')
            return
        if path.startswith('/static/'):
            self._static(path)
            return
        match = HLS_PATH_RE.match(path)
        if match:
            self._proxy_hls(match.group(1), match.group(2), urlparse(self.path).query)
            return
        media_match = MEDIA_PATH_RE.match(path)
        if media_match:
            self._serve_recording(media_match.group(1), media_match.group(2))
            return
        if not self._require_auth():
            return
        query = parse_qs(urlparse(self.path).query)
        if path == '/status':
            self._json(200, self.app.status())
            return
        if path == '/api/recordings':
            self._json(200, self._recordings_payload())
            return
        if path == '/recordings':
            self._html(200, self._recordings_page())
            return
        if path == '/system':
            self._html(200, self._system_page())
            return
        if path == '/oauth/start':
            self._oauth_start()
            return
        if path == '/login':
            self._html(200, self._login_page(query.get('error', [''])[0]))
            return
        if path == '/':
            self._html(200, self._dashboard_page(query.get('login', [''])[0]))
            return
        self._html(404, self._render('live', 'Nicht gefunden',
                                    '<div class="alert danger">Diese Seite existiert nicht.</div>'
                                    '<p><a class="btn" href="/">Zur Startseite</a></p>'))
        return

    def do_HEAD(self) -> None:  # noqa: N802
        match = MEDIA_PATH_RE.match(urlparse(self.path).path)
        if match:
            self._serve_recording(match.group(1), match.group(2), head_only=True)
            return
        self.send_response(405)
        self.send_header('Allow', 'GET, HEAD, POST')
        self.send_header('Content-Length', '0')
        self.end_headers()

    # ---------- POST ----------
    def do_POST(self) -> None:  # noqa: N802
        if not self._require_auth():
            return
        path = urlparse(self.path).path
        form = self._form()
        if path == '/oauth/finish':
            login_id = form.get('login') or self._cookie('pending_login')
            try:
                payload = self.app.oauth.finish(login_id, form.get('callback', ''))
                self.app.auth.store_token_response(payload)
                self.app.login_finished()
            except Exception as exc:
                self._html(400, self._login_page(str(exc)))
                return
            self._redirect('/?login=ok')
            return
        if path == '/setup':
            try:
                self.app.configure_token(form.get('token', ''))
            except Exception as exc:
                self._html(400, self._login_page(str(exc)))
                return
            self._redirect('/?login=ok')
            return
        self._json(404, {'error': 'not found'})

    # ---------- static / proxy ----------
    def _static(self, path: str) -> None:
        name = path[len('/static/'):]
        if not re.fullmatch(r'[A-Za-z0-9._-]+', name):
            self._json(404, {'error': 'not found'})
            return
        target = STATIC_DIR / name
        if not target.is_file():
            self._json(404, {'error': 'not found'})
            return
        content_type = MIME_TYPES.get(target.suffix.lower(), 'application/octet-stream')
        self._send(200, target.read_bytes(), content_type,
                   {'Cache-Control': 'public, max-age=300'})

    def _proxy_hls(self, token: str, rest: str, query: str) -> None:
        if token != self.app.hls_token:
            self._json(404, {'error': 'not found'})
            return
        upstream = 'http://' + self.app.cfg.mediamtx_hls_addr + '/' + rest
        if query:
            upstream += '?' + query
        try:
            request = urllib.request.Request(upstream, headers={'Accept': '*/*'})
            with urllib.request.urlopen(request, timeout=10) as response:
                data = response.read(4 * 1024 * 1024)
                content_type = response.headers.get('Content-Type', 'application/octet-stream')
            self._send(200, data, content_type, {'Cache-Control': 'no-store'})
        except urllib.error.HTTPError as exc:
            self._send(exc.code, b'', 'text/plain; charset=utf-8')
        except Exception:
            self._send(502, b'', 'text/plain; charset=utf-8')

    def _serve_recording(self, token: str, rel: str, head_only: bool = False) -> None:
        if token != self.app.media_token:
            self._json(404, {'error': 'not found'})
            return
        relative = unquote(rel)
        try:
            base = self.app.records_dir.resolve()
            target = (base / relative).resolve()
        except OSError:
            self._json(404, {'error': 'not found'})
            return
        if target != base and base not in target.parents:
            self._json(404, {'error': 'not found'})
            return
        if target.suffix.lower() not in RECORDING_EXTENSIONS or not target.is_file():
            self._json(404, {'error': 'not found'})
            return
        try:
            size = target.stat().st_size
        except OSError:
            self._json(404, {'error': 'not found'})
            return
        range_header = self.headers.get('Range')
        start, end, status = 0, size - 1, 200
        if range_header:
            try:
                parsed = parse_byte_range(range_header, size)
            except ValueError:
                self.send_response(416)
                self.send_header('Content-Range', 'bytes */%d' % size)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if parsed is not None:
                start, end = parsed
                status = 206
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header('Content-Type', MIME_TYPES.get(target.suffix.lower(), 'application/octet-stream'))
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(length))
        self.send_header('Content-Disposition', 'inline; filename="%s"' % target.name.replace('"', ''))
        if status == 206:
            self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.end_headers()
        if head_only:
            return
        remaining = length
        try:
            with target.open('rb') as handle:
                handle.seek(start)
                while remaining > 0:
                    chunk = handle.read(min(256 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------- data ----------
    def _recordings_payload(self) -> dict:
        entries = list_recordings(self.app.records_dir)
        items = []
        for entry in entries:
            items.append({
                'name': entry['name'],
                'path': entry['rel'],
                'label': entry['label'],
                'date': entry['date'],
                'time': entry['time'],
                'size': entry['size'],
                'size_human': entry['size_human'],
                'codec': entry['codec'],
                'ext': entry['ext'],
                'url': '/media/%s/%s' % (self.app.media_token, quote(entry['rel'], safe='/')),
            })
        total = sum(item['size'] for item in items)
        days = {}
        for item in items:
            day = days.setdefault(item['date'], {'date': item['date'], 'count': 0, 'bytes': 0})
            day['count'] += 1
            day['bytes'] += item['size']
        day_list = [{'date': d['date'], 'count': d['count'], 'size_human': format_size(d['bytes'])}
                    for d in sorted(days.values(), key=lambda entry: entry['date'], reverse=True)]
        return {'count': len(items), 'total_bytes': total, 'total_human': format_size(total),
                'items': items, 'days': day_list}

    # ---------- pages ----------
    def _dashboard_page(self, message: str = '') -> str:
        status = self.app.status()
        media = status.get('media') or {}
        camera = status.get('camera') or {}
        storage = status.get('storage') or {}
        retention = status.get('retention') or {}
        transcode = status.get('transcode') or {}
        phase = html.escape(str(status.get('phase')))
        auth_ok = self.app.auth.has_token()
        warning = ''
        if not self.app.cfg.web_password:
            warning = ('<div class="alert warn"><strong>Kein Passwort gesetzt.</strong> '
                       'Setze <code>WEB_PASSWORD</code> oder CapRover-HTTP-Basic-Auth, sonst kann '
                       'jeder mit der URL Livebild und Aufnahmen sehen.</div>')
        if message == 'ok':
            warning += '<div class="alert ok">Anmeldung gespeichert. Die Aufnahme startet automatisch.</div>'
        if not auth_ok:
            body = f'''
{warning}
<div class="page-head"><div><h1>Willkommen</h1><p class="lead">Der Recorder ist bereit, aber noch nicht mit deinem Bosch-Konto verbunden.</p></div></div>
<div class="grid stats">
  <div class="card"><div class="stat-label">Status</div><div class="stat-row"><span class="pill warn"><span class="dot"></span>{phase}</span></div>
    <div class="stat-sub">Einmalig mit SingleKey ID anmelden, danach laeuft alles automatisch.</div></div>
</div>
<div class="card">
  <h2>Anmeldung</h2>
  <p>Bosch verlangt den Browser-Login (SingleKey ID). Du wirst nach dem Login auf eine Fehlerseite
  umgeleitet – das ist normal. Kopiere die Callback-URL und fuege sie wieder ein.</p>
  <div class="actions"><a class="btn btn-primary" href="/login">Jetzt anmelden</a>
  <a class="btn" href="/recordings">Aufnahmen ansehen</a></div>
</div>'''
            return self._render('live', 'Bosch Eyes Recorder', body)

        ready = bool(media.get('path_ready'))
        if ready:
            player = '''
<div class="video-shell">
  <video id="live" data-hls-url="%s" controls autoplay muted playsinline></video>
  <span class="live-badge">LIVE</span>
</div>''' % html.escape(self.app.hls_url(), quote=True)
        else:
            player = '''
<div class="video-placeholder" id="live-pending" data-refresh="1">
  <div>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M3 8.5A2.5 2.5 0 0 1 5.5 6h3l1.2-2h4.6L15.5 6h3A2.5 2.5 0 0 1 21 8.5v9A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5v-9Z"/><circle cx="12" cy="13" r="3.6"/></svg>
    <h2>Stream wird vorbereitet</h2>
    <p>Die Kamera-Quelle ist noch nicht bereit. Diese Seite aktualisiert sich automatisch.</p>
    <p><span class="pill warn"><span class="dot"></span>%s</span></p>
  </div>
</div>''' % phase
        error = status.get('error')
        error_html = ''
        if error:
            error_html = '<div class="alert danger"><strong>Hinweis:</strong> <code>%s</code></div>' % html.escape(str(error))

        if transcode.get('enabled'):
            encoding_value = 'AV1 + Opus (QSV)'
            if transcode.get('available'):
                encoding_sub = '%d verarbeitet · %d ausstehend · %s gespart' % (
                    transcode.get('processed', 0), transcode.get('pending', 0),
                    transcode.get('bytes_saved_human', '0 B'))
                encoding_pill = '<span class="pill ok"><span class="dot"></span>aktiv</span>'
            else:
                encoding_sub = html.escape(str(transcode.get('error') or 'Hardware nicht verfuegbar'))
                encoding_pill = '<span class="pill danger"><span class="dot"></span>inaktiv</span>'
        else:
            encoding_value = 'H.264 + AAC (Original)'
            encoding_sub = 'Kein Re-Encoding – Aufnahme direkt von der Kamera.'
            encoding_pill = '<span class="pill">aus</span>'

        camera_pill = ('<span class="pill ok"><span class="dot"></span>online</span>'
                       if str(camera.get('connection_status', '')).upper() == 'ONLINE'
                       else '<span class="pill warn"><span class="dot"></span>%s</span>'
                       % html.escape(str(camera.get('connection_status') or 'unbekannt')))
        stream_pill = ('<span class="pill ok"><span class="dot"></span>bereit</span>' if ready
                       else '<span class="pill warn"><span class="dot"></span>wartet</span>')
        body = f'''
{warning}{error_html}
<div class="page-head">
  <div><h1>Live</h1><p class="lead">{html.escape(str(camera.get('title') or 'Kamera'))} · Firmware {html.escape(str(camera.get('firmware') or '–'))}</p></div>
  <div class="actions">
    <a class="btn btn-primary" href="/recordings">Aufnahmen</a>
    <a class="btn" href="/login">Login erneuern</a>
    <a class="btn btn-ghost" href="/system">System</a>
  </div>
</div>
{player}
<div class="grid stats">
  <div class="card"><div class="stat-label">Kamera</div><div class="stat-value">{html.escape(str(camera.get('title') or '–'))}</div>
    <div class="stat-sub">{camera_pill} · Privacy {html.escape(str(camera.get('privacy_mode') or '–'))}</div></div>
  <div class="card"><div class="stat-label">Stream</div><div class="stat-value">{stream_pill}</div>
    <div class="stat-sub">{html.escape(str(media.get('source_host') or '–'))} · {int(media.get('readers') or 0)} Zuschauer</div></div>
  <div class="card"><div class="stat-label">Aufnahme</div><div class="stat-value">{int(media.get('recordings') or 0)} Segmente</div>
    <div class="stat-sub">{format_size(int((storage.get('mb') or 0) * 1024 * 1024))} · {self._days(retention.get('retention_days', self.app.cfg.retention_days))} Tage Aufbewahrung</div></div>
  <div class="card"><div class="stat-label">Speicher</div><div class="stat-value">{html.escape(str(storage.get('free_gb', '–')))} GB frei</div>
    <div class="stat-sub">MAX_STORAGE_GB {html.escape(str(self.app.cfg.max_storage_gb))} · MIN_FREE_GB {html.escape(str(self.app.cfg.min_free_gb))}</div></div>
  <div class="card"><div class="stat-label">Kodierung</div><div class="stat-value">{html.escape(encoding_value)}</div>
    <div class="stat-sub">{encoding_pill} {encoding_sub}</div></div>
</div>
<p class="muted small">Segmentlaenge {html.escape(str(self.app.cfg.segment_minutes))} Minuten · Original ohne Neu-Kodierung.
Aufnahmen werden nach Ablauf der Aufbewahrung automatisch geloescht.</p>
'''
        return self._render('live', 'Live · Bosch Eyes', body)

    @staticmethod
    def _days(value) -> str:
        try:
            return '%g' % float(value)
        except (TypeError, ValueError):
            return html.escape(str(value))

    def _recordings_page(self) -> str:
        payload = self._recordings_payload()
        mode_badge = ''
        transcode = self.app.status().get('transcode') or {}
        if transcode.get('enabled') and transcode.get('available'):
            mode_badge = '<span class="pill ok"><span class="dot"></span>AV1 + Opus (QSV)</span>'
        summary = '%d Segmente · %s' % (payload['count'], payload['total_human'])
        if not payload['items']:
            body = f'''
<div class="page-head"><div><h1>Aufnahmen</h1><p class="lead">Noch keine fertigen Segmente.</p></div>{mode_badge}</div>
<div class="empty"><h2>Noch keine Aufnahmen</h2>
<p>Segmente erscheinen nach dem Abschluss des aktuellen Intervalls
(<code>SEGMENT_MINUTES</code>, Standard 10 Minuten).</p>
<a class="btn" href="/">Zum Livebild</a></div>'''
            return self._render('recordings', 'Aufnahmen · Bosch Eyes', body)
        data_json = json.dumps(payload, ensure_ascii=False).replace('</', '<\\/')
        body = f'''
<div class="page-head">
  <div><h1>Aufnahmen</h1><p class="lead">Letzte {self._days(self.app.cfg.retention_days)} Tage · {summary}</p></div>
  <div class="actions">{mode_badge}<a class="btn" href="/">Live</a></div>
</div>
<section class="player-panel" aria-label="Wiedergabe">
  <div class="video-shell">
    <video id="recording" playsinline preload="metadata"></video>
    <div class="player-hint" id="player-hint">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="3" y="5" width="18" height="14" rx="3"/><path d="m10 9 5 3-5 3V9Z"/></svg>
      <span>Segment ausw&auml;hlen und abspielen</span>
    </div>
    <div class="player-buffer" id="player-buffer" hidden><span class="spinner"></span> Segment wird geladen&hellip;</div>
  </div>
  <div class="now-playing">
    <span id="now-playing" class="muted" aria-live="polite">Kein Segment ausgewaehlt – waehle unten ein Segment.</span>
    <span class="muted small">Tipp: „Ab hier“ spielt fortlaufend weiter.</span>
  </div>
</section>
<section class="card" aria-labelledby="filter-title">
  <h2 id="filter-title">Tag &amp; Uhrzeit</h2>
  <div class="filters">
    <div class="field"><label for="rec-date">Tag</label><input id="rec-date" type="date"></div>
    <div class="field"><label for="rec-from">Von</label><input id="rec-from" type="time"></div>
    <div class="field"><label for="rec-to">Bis</label><input id="rec-to" type="time"></div>
    <div class="field"><label>&nbsp;</label><button id="filter-reset" class="btn" type="button">Filter zuruecksetzen</button></div>
  </div>
  <div class="chips" id="day-chips" aria-label="Verfuegbare Tage"></div>
  <p class="muted small" id="filter-summary" style="margin-top:12px"></p>
</section>
<section class="card" aria-labelledby="timeline-title" id="timeline">
  <div class="timeline-head"><h2 id="timeline-title">Tagesverlauf</h2><span id="timeline-note" class="muted small"></span></div>
  <div class="timeline-track" id="timeline-track"></div>
  <div class="timeline-legend" id="timeline-legend"></div>
</section>
<section aria-label="Segmente">
  <div id="segment-list"></div>
  <div id="segments-empty" class="empty" hidden><h2>Keine Segmente fuer diese Auswahl</h2>
    <p>Passe Tag oder Uhrzeit an.</p><button class="btn" type="button" onclick="document.getElementById('filter-reset').click()">Filter zuruecksetzen</button></div>
</section>
<script type="application/json" id="recordings-data">{data_json}</script>
'''
        return self._render('recordings', 'Aufnahmen · Bosch Eyes', body)

    def _system_page(self) -> str:
        status = self.app.status()
        rows = []
        for key in ('version', 'phase', 'error', 'uptime_seconds'):
            rows.append('<tr><th>%s</th><td><code>%s</code></td></tr>' % (html.escape(key), html.escape(str(status.get(key)))))
        for group in ('camera', 'media', 'transcode', 'storage', 'retention'):
            value = status.get(group) or {}
            for key, item in value.items():
                rows.append('<tr><th>%s.%s</th><td><code>%s</code></td></tr>' % (
                    html.escape(group), html.escape(str(key)), html.escape(str(item))))
        raw = html.escape(json.dumps(status, ensure_ascii=False, indent=2))
        body = f'''
<div class="page-head"><div><h1>System</h1><p class="lead">Zustand, Kamera, Stream und Speicher.</p></div>
  <div class="actions"><a class="btn" href="/">Live</a><a class="btn" href="/recordings">Aufnahmen</a><a class="btn" href="/status" target="_blank" rel="noopener">JSON</a></div></div>
<div class="card"><div class="table-wrap"><table>{''.join(rows)}</table></div></div>
<details><summary>Rohdaten (JSON)</summary><pre>{raw}</pre></details>
'''
        return self._render('system', 'System · Bosch Eyes', body)

    def _login_page(self, error: str = '') -> str:
        pending = self._cookie('pending_login')
        error_html = '<div class="alert danger">%s</div>' % html.escape(error) if error else ''
        if self.app.oauth.known(pending):
            step = f'''
<div class="alert ok">Der Bosch-Login wurde geoeffnet. Kopiere die Callback-URL aus der Fehlerseite und fuege sie hier ein.</div>
<form method="post" action="/oauth/finish" class="card">
  <h2>Schritt 2: Callback-URL einfuegen</h2>
  <input type="hidden" name="login" value="{html.escape(pending or '')}">
  <div class="field"><label for="callback">Callback-URL</label>
  <textarea id="callback" name="callback" rows="3" required placeholder="http://localhost:8321/callback?state=...&code=..."></textarea></div>
  <div class="actions"><button class="btn btn-primary" type="submit">Login abschliessen</button>
  <a class="btn" href="/oauth/start" target="_blank" rel="noopener">Login erneut starten</a></div>
</form>'''
        else:
            step = '''
<div class="card">
  <h2>Schritt 1: Mit Bosch anmelden</h2>
  <p>Bosch nutzt <strong>SingleKey ID</strong> im Browser (Captcha/2FA moeglich).
  Ein direktes Passwort-Login aus dem Container ist nicht moeglich.</p>
  <ol class="steps">
    <li>Auf „Mit Bosch-Konto anmelden“ klicken – der Login oeffnet sich in einem neuen Tab.</li>
    <li>Dort mit deinem Bosch-Konto anmelden.</li>
    <li>Der Browser landet auf einer Fehlerseite (<code>http://localhost:8321/callback?...</code>). Das ist normal.</li>
    <li>Die komplette URL aus der Adresszeile kopieren, hierher zurueckkehren und diese Seite neu laden.</li>
  </ol>
  <div class="actions"><a class="btn btn-primary" href="/oauth/start" target="_blank" rel="noopener">Mit Bosch-Konto anmelden</a></div>
</div>'''
        return self._render('login', 'Login · Bosch Eyes', f'''
{error_html}
<div class="page-head"><div><h1>Anmeldung</h1><p class="lead">Einmalig anmelden, danach laeuft der Recorder dauerhaft.</p></div>
<div class="actions"><a class="btn" href="/">Zurueck</a></div></div>
<div class="login-grid">
  <div>{step}</div>
  <div class="card">
    <h2>Alternative</h2>
    <p class="muted">Du kannst auch einen vorhandenen Refresh-Token einfuegen.</p>
    <form method="post" action="/setup">
      <div class="field"><label for="token">Refresh-Token</label>
      <textarea id="token" name="token" rows="4" required placeholder='{{"refresh_token":"..."}} oder nur der Token'></textarea></div>
      <div class="actions"><button class="btn" type="submit">Token speichern</button></div>
    </form>
    <hr style="border:0;border-top:1px solid var(--border);margin:18px 0">
    <p class="muted small">Der Token wird im Volume gespeichert und rotiert automatisch.
    Nie in Logs oder Chats veroeffentlichen.</p>
  </div>
</div>
''')

    def _render(self, page: str, title: str, body: str) -> str:
        return f'''<!doctype html>
<html lang="de" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0b1220">
<title>{html.escape(title)}</title>
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/app.css">
<script>try{{var t=localStorage.getItem('theme');if(!t)t=matchMedia('(prefers-color-scheme: light)').matches?'light':'dark';document.documentElement.setAttribute('data-theme',t);}}catch(e){{}}</script>
<script defer src="/static/app.js"></script>
</head>
<body data-page="{html.escape(page)}">
<a class="skip-link" href="#main">Zum Inhalt springen</a>
<header class="topbar"><div class="wrap">
  <a class="brand" href="/">
    <span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 8.5A2.5 2.5 0 0 1 5.5 6h3l1.2-2h4.6L15.5 6h3A2.5 2.5 0 0 1 21 8.5v9A2.5 2.5 0 0 1 18.5 20h-13A2.5 2.5 0 0 1 3 17.5v-9Z"/><circle cx="12" cy="13" r="3.2"/></svg></span>
    <span>Bosch Eyes<small>Recorder</small></span>
  </a>
  <nav class="nav" aria-label="Hauptnavigation">
    <a href="/" data-nav="live">Live</a>
    <a href="/recordings" data-nav="recordings">Aufnahmen</a>
    <a href="/system" data-nav="system">System</a>
    <a href="/login" data-nav="login">Login</a>
    <button class="icon-btn" id="theme-toggle" type="button" aria-label="Design wechseln">
      <svg class="theme-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 12.8A8.5 8.5 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"/></svg>
      <svg class="theme-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
    </button>
  </nav>
</div></header>
<main id="main" class="wrap">{body}</main>
<footer><div class="wrap"><span>Bosch Eyes Recorder · inoffizielles Open-Source-Projekt</span>
<span>Keine Verbindung zu Bosch · Nutzung auf eigene Verantwortung</span></div></footer>
</body></html>'''

    def _oauth_start(self) -> None:
        login_id, url = self.app.oauth.start()
        cookie = ('pending_login=%s; Path=/; Max-Age=900; HttpOnly; SameSite=Lax' % login_id)
        self._redirect(url, {'Set-Cookie': cookie})


def start_http_server(app, port: int) -> ThreadingHTTPServer:
    handler = type('BoundStatusHandler', (StatusHandler,), {'app': app})
    server = ThreadingHTTPServer(('0.0.0.0', port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name='http', daemon=True).start()
    log.info('Weboberflaeche auf Port %d gestartet.', port)
    return server
