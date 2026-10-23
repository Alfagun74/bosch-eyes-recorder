"""Bosch-OAuth (SingleKey ID): Refresh-Token-Verwaltung.

Ein direktes Login mit Benutzername/Passwort ist fuer diesen Client nicht
moeglich (Keycloak: "Client not allowed for direct access grants"). Der
Bosch-Login laeuft ueber SingleKey ID im Browser (Captcha/MFA moeglich); danach
wird ein langlebiger Refresh-Token (Scope offline_access) verwendet.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import requests

from .bosch_ca import SSLAdapter, build_ssl_context

TOKEN_URL = ('https://smarthome.authz.bosch.com/auth/realms/home_auth_provider'
             '/protocol/openid-connect/token')
# Oeffentliche Client-Konstanten der Bosch-App (kein Kunden-Secret).
CLIENT_ID = 'oss_residential_app'
CLIENT_SECRET = 'F1jZzsG5Ntw7x2VVc8J6qgsnisMOfaZg'

log = logging.getLogger('bosch-recorder.auth')


class AuthError(RuntimeError):
    """Refresh-Token fehlt, ist abgelaufen oder wurde abgelehnt."""


def _extract_refresh_token(text: str) -> str | None:
    """Akzeptiert rohen Token, unser State-JSON oder bosch_config.json."""
    text = text.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text or None
    if isinstance(data, str):
        return data.strip() or None
    if isinstance(data, dict):
        for key in ('refresh_token', 'refreshToken'):
            if isinstance(data.get(key), str) and data[key].strip():
                return data[key].strip()
        for value in data.values():
            if isinstance(value, dict):
                found = _extract_refresh_token(json.dumps(value))
                if found:
                    return found
    return None


class BoschAuth:
    def __init__(self, data_dir: Path, bootstrap_token: str | None):
        self.state_path = data_dir / 'state' / 'bosch_token.json'
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._state: dict = {'refresh_token': None, 'access_token': None,
                             'expires_at': 0.0, 'obtained_at': None}
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.mount('https://', SSLAdapter(build_ssl_context()))
        self._load(bootstrap_token)

    # ---------- Persistenz ----------
    def _load(self, bootstrap_token: str | None) -> None:
        candidates: list[str] = []
        if bootstrap_token:
            candidates.append(bootstrap_token)
        if self.state_path.exists():
            try:
                candidates.append(self.state_path.read_text(encoding='utf-8'))
            except OSError:
                pass
        # Kompatibilitaet: bosch_config.json des Community-Tools
        community = self.state_path.parent.parent / 'bosch_config.json'
        if community.exists():
            try:
                candidates.append(community.read_text(encoding='utf-8'))
            except OSError:
                pass
        for text in candidates:
            token = _extract_refresh_token(text)
            if not token:
                continue
            if self._looks_like_state_json(text):
                try:
                    parsed = json.loads(text)
                    self._state.update({k: parsed[k] for k in
                                        ('refresh_token', 'access_token', 'expires_at', 'obtained_at') if k in parsed})
                    continue
                except json.JSONDecodeError:
                    pass
            self._state['refresh_token'] = token
        if self._state.get('refresh_token'):
            log.info('Refresh-Token vorhanden (Quelle: %s).',
                     'ENV' if bootstrap_token else 'Volume/Datei')
        else:
            log.warning('Kein Refresh-Token vorhanden. Setup ueber die Weboberflaeche noetig.')

    @staticmethod
    def _looks_like_state_json(text: str) -> bool:
        try:
            data = json.loads(text)
            return isinstance(data, dict) and 'refresh_token' in data and 'expires_at' in data
        except json.JSONDecodeError:
            return False

    def _save(self) -> None:
        tmp = self.state_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self._state, indent=2), encoding='utf-8')
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self.state_path)

    # ---------- Tokens ----------
    def has_token(self) -> bool:
        with self._lock:
            return bool(self._state.get('refresh_token'))

    def bootstrap(self, text: str) -> None:
        """Validiert einen per Setup eingegebenen Refresh-Token atomar.

        Bei ungueltigem Token bleibt der bisherige Zustand unveraendert.
        """
        token = _extract_refresh_token(text)
        if not token:
            raise AuthError('Kein Refresh-Token in der Eingabe gefunden.')
        try:
            response = self._session.post(TOKEN_URL, data={
                'grant_type': 'refresh_token',
                'client_id': CLIENT_ID,
                'client_secret': CLIENT_SECRET,
                'refresh_token': token,
            }, timeout=25)
        except requests.RequestException as exc:
            raise AuthError('OAuth nicht erreichbar: ' + type(exc).__name__) from exc
        if response.status_code != 200:
            raise AuthError('Refresh-Token wurde von Bosch abgelehnt (HTTP %d).' % response.status_code)
        payload = response.json()
        access = payload.get('access_token')
        if not access:
            raise AuthError('OAuth-Antwort ohne access_token.')
        with self._lock:
            self._state['refresh_token'] = payload.get('refresh_token') or token
            self._state['access_token'] = access
            self._state['expires_at'] = time.time() + int(payload.get('expires_in', 3600))
            self._state['obtained_at'] = time.time()
            self._save()

    def force_refresh(self) -> str:
        """Erzwingt eine OAuth-Erneuerung (z. B. nach Cloud-401)."""
        with self._lock:
            self._state['expires_at'] = 0.0
        return self._refresh()

    def store_token_response(self, payload: dict) -> None:
        """Speichert eine komplette OAuth-Token-Antwort (Code-Flow) atomar."""
        access = payload.get('access_token')
        refresh = payload.get('refresh_token')
        if not access:
            raise AuthError('OAuth-Antwort ohne access_token.')
        with self._lock:
            if refresh:
                self._state['refresh_token'] = refresh
            if not self._state.get('refresh_token'):
                raise AuthError('OAuth-Antwort ohne refresh_token (Scope offline_access?).')
            self._state['access_token'] = access
            self._state['expires_at'] = time.time() + int(payload.get('expires_in', 3600))
            self._state['obtained_at'] = time.time()
            self._save()

    def access_token(self) -> str:
        with self._lock:
            if not self._state.get('refresh_token'):
                raise AuthError('Kein Refresh-Token vorhanden.')
            token = self._state.get('access_token')
            expires_at = float(self._state.get('expires_at') or 0)
            if token and expires_at - time.time() > 120:
                return token
        return self._refresh()

    def _refresh(self) -> str:
        with self._lock:
            refresh = self._state.get('refresh_token')
            if not refresh:
                raise AuthError('Kein Refresh-Token vorhanden.')
            try:
                response = self._session.post(TOKEN_URL, data={
                    'grant_type': 'refresh_token',
                    'client_id': CLIENT_ID,
                    'client_secret': CLIENT_SECRET,
                    'refresh_token': refresh,
                }, timeout=25)
            except requests.RequestException as exc:
                raise AuthError('OAuth nicht erreichbar: ' + type(exc).__name__) from exc
            if response.status_code != 200:
                detail = ''
                try:
                    payload = response.json()
                    detail = str(payload.get('error', '')) + ' ' + str(payload.get('error_description', ''))
                except ValueError:
                    pass
                if response.status_code in (400, 401):
                    raise AuthError('Refresh-Token abgelehnt (HTTP %d). Bitte neu einloggen und Token aktualisieren. %s'
                                    % (response.status_code, detail.strip()))
                raise AuthError('OAuth-Fehler HTTP %d' % response.status_code)
            payload = response.json()
            access = payload.get('access_token')
            if not access:
                raise AuthError('OAuth-Antwort ohne access_token.')
            self._state['access_token'] = access
            if payload.get('refresh_token'):
                self._state['refresh_token'] = payload['refresh_token']
            self._state['expires_at'] = time.time() + int(payload.get('expires_in', 3600))
            self._state['obtained_at'] = self._state.get('obtained_at') or time.time()
            self._save()
            log.info('OAuth-Token erneuert (Refresh-Token rotiert gespeichert).')
            return access
