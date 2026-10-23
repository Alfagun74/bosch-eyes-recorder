"""Einmaliger Bosch-SingleKey-ID-Login am PC, um einen Refresh-Token zu erzeugen.

Der Container kann sich NICHT mit Benutzername/Passwort direkt anmelden:
Keycloak lehnt "direct access grants" fuer die Bosch-App ab, und die
SingleKey-ID-Anmeldung laeuft im Browser (Captcha/MFA moeglich). Deshalb wird
der Refresh-Token einmalig hier erzeugt und dann im Container hinterlegt.

In der Regel wird dieses Skript NICHT benoetigt: Die Weboberflaeche des
Containers (/login) fuehrt den Browser-Login direkt aus. Dieses Skript ist
fuer Workstations gedacht, auf denen man den Login lieber lokal durchfuehren
und den Token anschliessend uebertragen moechte.

Aufruf:
    python export_refresh_token.py
    python export_refresh_token.py --manual   # Browser nicht automatisch oeffnen

Ergebnis: Datei bosch-refresh-token.json (Berechtigung 600) im aktuellen Ordner.
Den Inhalt des Feldes "refresh_token" oder die ganze JSON-Datei auf der
Login-Seite des Containers einfuegen bzw. als BOSCH_REFRESH_TOKEN setzen.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

KEYCLOAK_BASE = ('https://smarthome.authz.bosch.com/auth/realms/home_auth_provider'
                 '/protocol/openid-connect')
CLIENT_ID = 'oss_residential_app'
CLIENT_SECRET = 'F1jZzsG5Ntw7x2VVc8J6qgsnisMOfaZg'  # oeffentliche App-Konstante
SCOPES = 'email offline_access profile openid'
REDIRECT_PORT = 8321
REDIRECT_URI = 'http://localhost:%d/callback' % REDIRECT_PORT
OUTPUT = Path('bosch-refresh-token.json')


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


def auth_url(challenge: str, state: str) -> str:
    return KEYCLOAK_BASE + '/auth?' + urlencode({
        'client_id': CLIENT_ID,
        'response_type': 'code',
        'scope': SCOPES,
        'redirect_uri': REDIRECT_URI,
        'code_challenge': challenge,
        'code_challenge_method': 'S256',
        'state': state,
    })


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None
    error: str | None = None
    received = threading.Event()

    def do_GET(self) -> None:  # noqa: N802
        query = parse_qs(urlparse(self.path).query)
        if 'code' in query:
            _CallbackHandler.code = query['code'][0]
            body = 'Login erfolgreich. Sie koennen dieses Fenster schliessen.'
        else:
            _CallbackHandler.error = (query.get('error_description') or query.get('error') or ['unbekannt'])[0]
            body = 'Login fehlgeschlagen: ' + _CallbackHandler.error
        payload = body.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        _CallbackHandler.received.set()

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def wait_for_code(timeout: int) -> str:
    server = HTTPServer(('127.0.0.1', REDIRECT_PORT), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        if not _CallbackHandler.received.wait(timeout):
            raise TimeoutError('Kein Login-Callback innerhalb von %d s.' % timeout)
    finally:
        server.shutdown()
    if _CallbackHandler.error:
        raise RuntimeError('Login abgebrochen: ' + _CallbackHandler.error)
    if not _CallbackHandler.code:
        raise RuntimeError('Kein Code im Callback.')
    return _CallbackHandler.code


def wait_for_manual_code() -> str:
    print('Oeffnen Sie diese URL im Browser und melden Sie sich an:')
    print(_AUTH_URL)
    print('Die Weiterleitung auf localhost schlaegt ggf. fehl; kopieren Sie die komplette')
    print('Adresszeile der Weiterleitung hierher (oder nur den code-Parameter).')
    raw = input('Callback-URL/Code: ').strip()
    if 'code=' in raw:
        return parse_qs(urlparse(raw).query)['code'][0]
    return raw


def exchange(code: str, verifier: str) -> dict:
    response = requests.post(KEYCLOAK_BASE + '/token', data={
        'grant_type': 'authorization_code',
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'redirect_uri': REDIRECT_URI,
        'code': code,
        'code_verifier': verifier,
    }, timeout=30)
    if response.status_code != 200:
        raise RuntimeError('Token-Austausch fehlgeschlagen (HTTP %d): %s'
                           % (response.status_code, response.text[:300]))
    return response.json()


def main() -> int:
    global _AUTH_URL
    parser = argparse.ArgumentParser(description='Bosch-Refresh-Token einmalig per Browser erzeugen')
    parser.add_argument('--manual', action='store_true', help='Browser nicht automatisch oeffnen')
    parser.add_argument('--timeout', type=int, default=300)
    parser.add_argument('--output', default=str(OUTPUT))
    args = parser.parse_args()

    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    _AUTH_URL = auth_url(challenge, state)
    print('Bosch-Login (SingleKey ID) wird geoeffnet. Bitte mit dem Konto anmelden,')
    print('dem die Kamera gehoert; bei 2FA den Code eingeben.')
    if args.manual:
        code = wait_for_manual_code()
    else:
        try:
            webbrowser.open(_AUTH_URL)
        except Exception:
            print('Browser konnte nicht geoeffnet werden.')
        print('Falls sich kein Browser oeffnet, diese URL verwenden:')
        print(_AUTH_URL)
        code = wait_for_code(args.timeout)
    tokens = exchange(code, verifier)
    refresh = tokens.get('refresh_token')
    if not refresh:
        print('FEHLER: Antwort enthaelt keinen refresh_token (Scope offline_access fehlt?).')
        return 2
    out = Path(args.output)
    out.write_text(json.dumps({
        'refresh_token': refresh,
        'access_token': tokens.get('access_token'),
        'expires_in': tokens.get('expires_in'),
        'obtained_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'note': 'Nur auf dem Server/Container hinterlegen; nicht weitergeben.',
    }, indent=2), encoding='utf-8')
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass
    print('')
    print('Refresh-Token gespeichert: ' + str(out.resolve()))
    print('Im Container: Setup-Seite oeffnen und den Token (oder die ganze JSON-Datei) einfuegen,')
    print('alternativ BOSCH_REFRESH_TOKEN in CapRover setzen und Container neu starten.')
    print('Den Token niemals in Chat/Logs veroeffentlichen.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
