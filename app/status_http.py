"""Weboberflaeche: einfacher Bosch-Login, Livebild (HLS) und Token-Fallback."""
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
    r'^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})-(\d{6})\.mp4$')


def format_size(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return '%.2f GB' % (num_bytes / (1024 ** 3))
    if num_bytes >= 1024 ** 2:
        return '%.1f MB' % (num_bytes / (1024 ** 2))
    if num_bytes >= 1024:
        return '%.1f KB' % (num_bytes / 1024)
    return '%d B' % num_bytes


def parse_byte_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Parse an HTTP Range header. None means "serve the full file".

    Raises ValueError for unsatisfiable/invalid byte ranges.
    """
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


def list_recordings(records_dir: Path, now: float | None = None,
                    min_age_seconds: float = 20.0) -> list[dict]:
    """List finalized fMP4 segments, newest first.

    Files modified less than ``min_age_seconds`` ago are skipped because the
    segment MediaMTX is currently writing is not necessarily playable.
    """
    current = time.time() if now is None else now
    entries: list[dict] = []
    try:
        paths = list(records_dir.rglob('*.mp4'))
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
        match = RECORDING_NAME_RE.match(name)
        if match:
            year, month, day, hour, minute, second, micros = (int(x) for x in match.groups())
            start = datetime.datetime(year, month, day, hour, minute, second, micros)
            display_date = start.strftime('%d.%m.%Y')
            display_time = start.strftime('%H:%M:%S')
        else:
            display_date = 'Unbekannt'
            display_time = name
        try:
            rel = path.relative_to(records_dir).as_posix()
        except ValueError:
            rel = name
        entries.append({
            'name': name,
            'rel': rel,
            'size': stat.st_size,
            'mtime': stat.st_mtime,
            'display_date': display_date,
            'display_time': display_time,
            'label': '%s %s' % (display_date, display_time),
        })
    entries.sort(key=lambda entry: entry['mtime'], reverse=True)
    return entries


class StatusHandler(BaseHTTPRequestHandler):
    server_version = 'BoschRecorder/1.2'
    app = None  # type: ignore[assignment]

    def log_message(self, fmt: str, *args: object) -> None:
        log.debug('%s - %s', self.address_string(), fmt % args)

    # ---------- Helfer ----------
    def _send(self, code: int, body: bytes, content_type: str = 'text/html; charset=utf-8',
              extra_headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _html(self, code: int, page: str) -> None:
        self._send(code, page.encode('utf-8'))

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8'),
                   'application/json; charset=utf-8')

    def _redirect(self, location: str, extra_headers: dict | None = None) -> None:
        self._send(302, b'', 'text/plain; charset=utf-8', {'Location': location, **(extra_headers or {})})

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
        if path == '/oauth/start':
            self._oauth_start()
            return
        if path == '/login':
            self._html(200, self._login_page(query.get('error', [''])[0]))
            return
        if path == '/':
            self._html(200, self._dashboard_page(query.get('login', [''])[0]))
            return
        self._json(404, {'error': 'not found'})

    def do_HEAD(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        match = MEDIA_PATH_RE.match(path)
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

    # ---------- Login ----------
    def _oauth_start(self) -> None:
        login_id, url = self.app.oauth.start()
        cookie = ('pending_login=%s; Path=/; Max-Age=900; HttpOnly; SameSite=Lax' % login_id)
        self._redirect(url, {'Set-Cookie': cookie})

    # ---------- HLS-Proxy ----------
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
            self._send(200, data, content_type)
        except urllib.error.HTTPError as exc:
            self._send(exc.code, b'', 'text/plain; charset=utf-8')
        except Exception:
            self._send(502, b'', 'text/plain; charset=utf-8')

    def _static(self, path: str) -> None:
        name = path[len('/static/'):]
        if not re.fullmatch(r'[A-Za-z0-9._-]+', name):
            self._json(404, {'error': 'not found'})
            return
        target = STATIC_DIR / name
        if not target.is_file():
            self._json(404, {'error': 'not found'})
            return
        content_type = 'application/javascript; charset=utf-8' if name.endswith('.js') else 'text/plain; charset=utf-8'
        self._send(200, target.read_bytes(), content_type)

    # ---------- Seiten ----------
    def _dashboard_page(self, message: str) -> str:
        status = self.app.status()
        media = status.get('media') or {}
        storage = status.get('storage') or {}
        camera = status.get('camera') or {}
        auth_ok = self.app.auth.has_token()
        phase = html.escape(str(status.get('phase')))
        warning = ''
        if self.app.cfg.web_password:
            warning = ''
        else:
            warning = ('<p class="warn">Diese Seite ist ohne Passwort erreichbar. '
                       'Fuer Internetzugriff <code>WEB_PASSWORD</code> setzen oder CapRover HTTP Basic Auth aktivieren.</p>')
        if message == 'ok':
            warning += '<p class="ok">Anmeldung gespeichert. Die Aufnahme startet automatisch.</p>'
        if not auth_ok:
            return self._render('Bosch Eyes Recorder', f'''
{warning}
<h1>Bosch Eyes Recorder</h1>
<p>Noch nicht angemeldet.</p>
<p><a class="button" href="/login">Jetzt mit Bosch-Konto anmelden</a></p>
<p class="muted">Phase: <code>{phase}</code></p>
''')
        ready = bool(media.get('path_ready'))
        if ready:
            player = f'''
<div class="player">
  <video id="live" controls autoplay muted playsinline></video>
  <div class="live">LIVE</div>
</div>
<script src="/static/hls.min.js"></script>
<script>
(function() {{
  var url = {json.dumps(self.app.hls_url())};
  var video = document.getElementById('live');
  if (window.Hls && Hls.isSupported()) {{
    var hls = new Hls({{ lowLatencyMode: true, liveSyncDurationCount: 3, enableWorker: true }});
    hls.loadSource(url);
    hls.attachMedia(video);
  }} else {{
    video.src = url;
  }}
}})();
</script>
'''
        else:
            player = (f'<p class="warn">Der Stream ist noch nicht bereit (Phase <code>{phase}</code>). '
                      'Die Seite aktualisiert sich automatisch, sobald die Kamera verbunden ist.</p>')
        rows = {
            'Kamera': str(camera.get('title') or '-'),
            'Firmware': str(camera.get('firmware') or '-'),
            'Status': phase,
            'Quelle bereit': str(ready),
            'Segmente': str(media.get('recordings')),
            'Aufnahmevolumen': f"{media.get('recordings_mb')} MB",
            'Freier Speicher': f"{storage.get('free_gb')} GB",
            'Letzte Datei': str(media.get('latest_recording') or '-'),
        }
        table = ''.join('<tr><th>%s</th><td><code>%s</code></td></tr>' % (html.escape(k), html.escape(v))
                        for k, v in rows.items())
        refresh = '' if ready else '<meta http-equiv="refresh" content="10">'
        error = status.get('error')
        error_html = f'<p class="warn">Letzter Fehler: <code>{html.escape(str(error))}</code></p>' if error else ''
        return self._render('Bosch Eyes Recorder', f'''{refresh}
{warning}
<h1>Bosch Eyes Recorder</h1>
{error_html}
{player}
<h2>Status</h2><table>{table}</table>
<p><a href="/recordings">Aufnahmen ansehen</a> · <a href="/login">Bosch-Login erneuern</a> · <a href="/status">JSON-Status</a> · <a href="/">Aktualisieren</a></p>
''')

    def _recordings_payload(self) -> dict:
        entries = list_recordings(self.app.records_dir)
        items = []
        for entry in entries:
            items.append({
                'name': entry['name'],
                'path': entry['rel'],
                'label': entry['label'],
                'date': entry['display_date'],
                'size': entry['size'],
                'size_human': format_size(entry['size']),
                'url': '/media/%s/%s' % (self.app.media_token, quote(entry['rel'], safe='/')),
            })
        total = sum(item['size'] for item in items)
        return {'count': len(items), 'total_bytes': total, 'total_human': format_size(total),
                'items': items}

    def _recordings_page(self) -> str:
        payload = self._recordings_payload()
        items = payload['items']
        if not items:
            body = ('<h1>Aufnahmen</h1><p class="muted">Noch keine abgeschlossenen '
                    'Aufnahmen vorhanden. Segmente erscheinen nach dem Abschluss '
                    '(Segmentlaenge: siehe <code>SEGMENT_MINUTES</code>).</p>'
                    '<p><a href="/">Zurueck</a> · <a href="/recordings">Aktualisieren</a></p>')
            return self._render('Aufnahmen', body)

        # Ascending queue for "play from here" (chronological playback).
        ascending = list(reversed(items))
        index_by_name = {item['name']: index for index, item in enumerate(ascending)}
        groups: dict[str, list[dict]] = {}
        for item in items:
            groups.setdefault(item['date'], []).append(item)

        rows = []
        for date, entries in groups.items():
            rows.append('<h2>%s</h2><table class="recordings"><tr>'
                        '<th>Uhrzeit</th><th>Groesse</th><th>Wiedergabe</th></tr>' % html.escape(date))
            for entry in entries:
                index = index_by_name[entry['name']]
                rows.append(
                    '<tr><td>%s</td><td>%s</td><td>'
                    '<button type="button" data-play="%d">&#9654;</button> '
                    '<button type="button" data-chain="%d">Ab hier</button> '
                    '<a href="%s" download>Download</a></td></tr>'
                    % (html.escape(entry['label']), html.escape(entry['size_human']), index, index,
                       html.escape(entry['url'])))
            rows.append('</table>')
        player_js = '''
<div class="player"><video id="recording" controls preload="metadata" playsinline></video></div>
<p id="now" class="muted">Kein Segment ausgewaehlt.</p>
<script>
(function() {
  var items = %s;
  var player = document.getElementById('recording');
  var current = -1;
  var chain = false;
  var now = document.getElementById('now');
  function playAt(index, continueChain) {
    if (index < 0 || index >= items.length) { return; }
    current = index;
    chain = continueChain;
    player.src = items[index].url;
    player.play();
    now.textContent = 'Wiedergabe: ' + items[index].label;
  }
  player.addEventListener('ended', function() {
    if (chain && current + 1 < items.length) { playAt(current + 1, true); }
    else { chain = false; }
  });
  document.querySelectorAll('button[data-play]').forEach(function(button) {
    button.addEventListener('click', function() { playAt(parseInt(button.dataset.play, 10), false); });
  });
  document.querySelectorAll('button[data-chain]').forEach(function(button) {
    button.addEventListener('click', function() { playAt(parseInt(button.dataset.chain, 10), true); });
  });
})();
</script>''' % json.dumps([{'url': item['url'], 'label': item['label']} for item in ascending], ensure_ascii=False)
        body = ('<h1>Aufnahmen</h1>'
                '<p class="muted">%d Segmente, %s gesamt. Das aktuell laufende Segment '
                'erscheint nach dem Abschluss. Wiedergabe: Original-H.264/AAC.</p>'
                '<p><a href="/">Zurueck</a> · <a href="/recordings">Aktualisieren</a></p>'
                '%s%s' % (payload['count'], html.escape(payload['total_human']), player_js, ''.join(rows)))
        return self._render('Aufnahmen', body)

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
        if target.suffix.lower() != '.mp4' or not target.is_file():
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
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(length))
        self.send_header('Content-Disposition',
                         'inline; filename="%s"' % target.name.replace('"', ''))
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

    def _login_page(self, error: str = '') -> str:
        pending = self._cookie('pending_login')
        if self.app.oauth.known(pending):
            step = f'''
<h2>Schritt 2: Callback-URL einfuegen</h2>
<p>Nach dem Bosch-Login bleibt der Browser auf einer Fehlerseite
(<code>http://localhost:8321/callback?...</code>) stehen. Kopiere die
<b>komplette Adresszeile</b> aus dem Browser und fuege sie hier ein:</p>
<form method="post" action="/oauth/finish">
  <input type="hidden" name="login" value="{html.escape(pending or '')}">
  <textarea name="callback" rows="3" required placeholder="http://localhost:8321/callback?state=...&code=..."></textarea>
  <button type="submit">Login abschliessen</button>
</form>
<p class="muted">Falls die Seite bereits geschlossen wurde:
<a href="/oauth/start">Login erneut starten</a>.</p>
'''
        else:
            step = '''
<h2>Schritt 1: Mit Bosch-Konto anmelden</h2>
<p>Bosch verwendet <b>SingleKey ID</b> (Browser-Login, ggf. 2FA). Ein direktes
Passwort-Login aus dem Container ist nicht moeglich; der Login laeuft im
Browser und wird danach einmalig hierher uebertragen.</p>
<p><a class="button" href="/oauth/start" target="_blank" rel="noopener">Mit Bosch-Konto anmelden</a></p>
<p class="muted">Der Login oeffnet sich in einem neuen Tab. Nach dem Bosch-Login die
Callback-URL kopieren, hierher zurueckkehren und diese Seite neu laden.</p>
'''
        return self._render('Bosch Login', f'''{error}
<h1>Bosch Eyes Recorder – Anmeldung</h1>
{step}
<h2>Alternative: Refresh-Token einfuegen</h2>
<p>Falls du bereits einen Refresh-Token hast (z.&nbsp;B. aus
<code>tools/export_refresh_token.py</code>), kannst du ihn hier direkt
hinterlegen. Er wird beim Speichern validiert.</p>
<form method="post" action="/setup">
  <textarea name="token" rows="3" required placeholder='{{"refresh_token":"..."}} oder nur der Token'></textarea>
  <button type="submit">Token speichern</button>
</form>
<p><a href="/">Zurueck</a></p>
''')

    @staticmethod
    def _render(title: str, body: str) -> str:
        return f'''<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#111;color:#eee}}
main{{max-width:64rem;margin:0 auto;padding:1.5rem}}
h1{{font-size:1.6rem}} h2{{font-size:1.15rem;margin-top:2rem}}
a{{color:#7cc4ff}} code{{background:#222;padding:.1rem .3rem;border-radius:4px;word-break:break-all}}
table{{border-collapse:collapse;margin-top:.5rem}} th,td{{border:1px solid #333;padding:.35rem .6rem;text-align:left}}
th{{background:#1c1c1c;white-space:nowrap}}
.player{{position:relative;background:#000;border-radius:8px;overflow:hidden;margin:1rem 0}}
video{{display:block;width:100%;max-height:70vh;background:#000}}
.live{{position:absolute;top:.6rem;left:.6rem;background:#c00;color:#fff;font-weight:700;font-size:.8rem;padding:.2rem .5rem;border-radius:4px}}
.button{{display:inline-block;background:#0a6cff;color:#fff;padding:.7rem 1.1rem;border-radius:6px;text-decoration:none;font-weight:600}}
button{{background:#0a6cff;color:#fff;border:0;padding:.6rem 1rem;border-radius:6px;cursor:pointer;margin-top:.5rem}}
textarea{{width:100%;box-sizing:border-box;background:#1a1a1a;color:#eee;border:1px solid #444;border-radius:6px;padding:.6rem;font-family:ui-monospace,Consolas,monospace}}
.warn{{background:#3a2a00;border:1px solid #7a5a00;padding:.7rem;border-radius:6px}}
.ok{{background:#0c2f12;border:1px solid #1d6b2f;padding:.7rem;border-radius:6px}}
.muted{{color:#999}}
</style></head><body><main>
{body}
</main></body></html>'''


def start_http_server(app, port: int) -> ThreadingHTTPServer:
    handler = type('BoundStatusHandler', (StatusHandler,), {'app': app})
    server = ThreadingHTTPServer(('0.0.0.0', port), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name='http', daemon=True).start()
    log.info('Weboberflaeche auf Port %d gestartet.', port)
    return server
