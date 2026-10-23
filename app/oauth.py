"""SingleKey-ID/OAuth-Login fuer die Weboberflaeche.

Der Bosch-OAuth-Client erlaubt nur die registrierte Redirect-URI
`http://localhost:8321/callback`. Der Server kann die Weiterleitung nicht
selbst empfangen, wenn der Browser auf einem anderen Rechner laeuft. Deshalb:
1. Web-UI erzeugt PKCE-Verifier + state und leitet den Browser zu Bosch.
2. Nach dem Login bleibt der Browser auf `http://localhost:8321/callback?...`
   stehen (Fehlerseite). Die komplette URL aus der Adresszeile wird in die
   Weboberflaeche kopiert und dort eingereicht.
3. Der Server tauscht den Code mit dem Verifier gegen Tokens (PKCE schuetzt
   den Code, auch wenn er die Weboberflaeche erreicht).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse

import requests

KEYCLOAK_BASE = ('https://smarthome.authz.bosch.com/auth/realms/home_auth_provider'
                 '/protocol/openid-connect')
CLIENT_ID = 'oss_residential_app'
CLIENT_SECRET = 'F1jZzsG5Ntw7x2VVc8J6qgsnisMOfaZg'  # oeffentliche App-Konstante
SCOPES = 'email offline_access profile openid'
REDIRECT_URI = 'http://localhost:8321/callback'

log = logging.getLogger('bosch-recorder.oauth')


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


class OAuthFlow:
    def __init__(self) -> None:
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _cleanup(self) -> None:
        cutoff = time.time() - 1800
        for key in [k for k, v in self._pending.items() if v['created'] < cutoff]:
            self._pending.pop(key, None)

    def start(self) -> tuple[str, str]:
        self._cleanup()
        login_id = secrets.token_urlsafe(18)
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)
        with self._lock:
            self._pending[login_id] = {'verifier': verifier, 'state': state, 'created': time.time()}
        url = KEYCLOAK_BASE + '/auth?' + urlencode({
            'client_id': CLIENT_ID,
            'response_type': 'code',
            'scope': SCOPES,
            'redirect_uri': REDIRECT_URI,
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
            'state': state,
        })
        return login_id, url

    def known(self, login_id: str | None) -> bool:
        if not login_id:
            return False
        with self._lock:
            return login_id in self._pending

    def finish(self, login_id: str | None, raw: str) -> dict:
        """Nimmt Callback-URL oder Code entgegen und liefert die Token-Antwort."""
        if not login_id:
            raise RuntimeError('Kein laufender Login. Bitte erneut "Mit Bosch anmelden" klicken.')
        with self._lock:
            pending = self._pending.pop(login_id, None)
        if not pending:
            raise RuntimeError('Login-Sitzung abgelaufen. Bitte erneut anmelden.')
        raw = (raw or '').strip()
        if not raw:
            raise RuntimeError('Bitte die Callback-URL oder den Code einfuegen.')
        if 'code=' in raw or 'error=' in raw:
            query = parse_qs(urlparse(raw).query)
            if query.get('error'):
                description = query.get('error_description', query['error'])[0]
                raise RuntimeError('Bosch-Login abgebrochen: ' + description)
            state = (query.get('state') or [''])[0]
            if state and state != pending['state']:
                raise RuntimeError('Ungueltiger Login-Status (state). Bitte erneut anmelden.')
            code = (query.get('code') or [''])[0]
            if not code:
                raise RuntimeError('In der URL wurde kein code-Parameter gefunden.')
        else:
            code = raw
        response = requests.post(KEYCLOAK_BASE + '/token', data={
            'grant_type': 'authorization_code',
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'redirect_uri': REDIRECT_URI,
            'code': code,
            'code_verifier': pending['verifier'],
        }, timeout=25)
        if response.status_code != 200:
            detail = ''
            try:
                payload = response.json()
                detail = str(payload.get('error_description') or payload.get('error') or '')
            except ValueError:
                pass
            raise RuntimeError('Token-Austausch fehlgeschlagen (HTTP %d). %s'
                               % (response.status_code, detail.strip()))
        log.info('Bosch-Login ueber Weboberflaeche abgeschlossen.')
        return response.json()
