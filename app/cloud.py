"""Bosch-Cloud-Client (Residential CBS API) fuer MediaMTX.

Nutzt die Bosch-Video-CA zusaetzlich zu den Systemwurzeln (kein verify=False).
Liefert eine MediaMTX-taugliche Quell-URL (RTSP-over-HTTPS-Tunnel) plus den
SHA-256-Fingerprint des Kamera-Zertifikats fuer die Pin-Pruefung.
"""
from __future__ import annotations

import hashlib
import logging
import socket
import ssl
from typing import Any
from urllib.parse import quote, urlparse

import requests
from requests.adapters import HTTPAdapter

from .auth import BoschAuth
from .bosch_ca import BOSCH_CLOUD_CA_PEM
from .config import Config

CLOUD_BASE = 'https://residential.cbs.boschsecurity.com'
PRIVATE_PREFIXES = ('10.', '192.168.', '172.16.', '172.17.', '172.18.', '172.19.',
                    '172.20.', '172.21.', '172.22.', '172.23.', '172.24.', '172.25.',
                    '172.26.', '172.27.', '172.28.', '172.29.', '172.30.', '172.31.')

log = logging.getLogger('bosch-recorder.cloud')


class CloudError(RuntimeError):
    pass


def _ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.load_verify_locations(cadata=BOSCH_CLOUD_CA_PEM)
    context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    return context


class _SSLAdapter(HTTPAdapter):
    def __init__(self, ssl_context: ssl.SSLContext, **kwargs: Any) -> None:
        self._ssl_context = ssl_context
        super().__init__(**kwargs)

    def init_poolmanager(self, *args: Any, **kwargs: Any) -> Any:
        kwargs['ssl_context'] = self._ssl_context
        return super().init_poolmanager(*args, **kwargs)


def _private_host(host: str | None) -> bool:
    return bool(host) and host.startswith(PRIVATE_PREFIXES)


def camera_fingerprint(host: str) -> str:
    """SHA-256 des aktuellen Kamera-TLS-Zertifikats (TOFU-Pin)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, 443), timeout=8) as sock:
        with context.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    return hashlib.sha256(der).hexdigest().upper()


def _normalize_url(raw: str) -> str:
    return raw if '://' in raw else 'https://' + raw


class BoschCloud:
    def __init__(self, auth: BoschAuth, cfg: Config):
        self.auth = auth
        self.cfg = cfg
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.mount('https://', _SSLAdapter(_ssl_context()))
        self._camera: dict | None = None

    # ---------- HTTP ----------
    def _request(self, method: str, path: str, *, retry_auth: bool = True, **kwargs: Any) -> requests.Response:
        token = self.auth.access_token()
        headers = kwargs.pop('headers', {})
        headers['Authorization'] = 'Bearer ' + token
        response = self.session.request(method, CLOUD_BASE + path, headers=headers, timeout=30, **kwargs)
        if response.status_code == 401 and retry_auth:
            log.info('Cloud-401; OAuth-Token wird erzwungen erneuert.')
            self.auth.force_refresh()
            headers['Authorization'] = 'Bearer ' + self.auth.access_token()
            response = self.session.request(method, CLOUD_BASE + path, headers=headers, timeout=30, **kwargs)
        return response

    def _json(self, response: requests.Response, action: str) -> Any:
        if response.status_code in (401, 403):
            raise CloudError('%s: Cloud-Zugriff verweigert (HTTP %d).' % (action, response.status_code))
        if response.status_code >= 400:
            raise CloudError('%s: Cloud HTTP %d.' % (action, response.status_code))
        try:
            return response.json()
        except ValueError as exc:
            raise CloudError('%s: Cloud-Antwort ist kein JSON.' % action) from exc

    # ---------- Kamera ----------
    def list_cameras(self) -> list[dict]:
        payload = self._json(self._request('GET', '/v11/video_inputs'), 'Kamera-Liste')
        if isinstance(payload, dict):
            payload = payload.get('items') or payload.get('cameras') or []
        if not isinstance(payload, list):
            raise CloudError('Kamera-Liste: unerwartetes Format.')
        return [c for c in payload if isinstance(c, dict)]

    def resolve_camera(self) -> dict:
        cameras = self.list_cameras()
        if not cameras:
            raise CloudError('Keine Kamera im Bosch-Konto gefunden.')
        if self.cfg.camera_id:
            for camera in cameras:
                if str(camera.get('id', '')).upper() == self.cfg.camera_id.upper():
                    self._camera = camera
                    return camera
            raise CloudError('BOSCH_CAMERA_ID nicht im Konto gefunden. Verfuegbar: %s'
                             % ', '.join(self._describe(c) for c in cameras))
        if self.cfg.camera_name:
            needle = self.cfg.camera_name.casefold()
            matches = [c for c in cameras if needle in str(c.get('title', '')).casefold()]
            if len(matches) == 1:
                self._camera = matches[0]
                return matches[0]
            raise CloudError('BOSCH_CAMERA_NAME ist nicht eindeutig. Verfuegbar: %s'
                             % ', '.join(self._describe(c) for c in cameras))
        if len(cameras) > 1:
            raise CloudError('Mehrere Kameras im Konto; bitte BOSCH_CAMERA_ID oder BOSCH_CAMERA_NAME setzen. Verfuegbar: %s'
                             % ', '.join(self._describe(c) for c in cameras))
        self._camera = cameras[0]
        return cameras[0]

    @staticmethod
    def _describe(camera: dict) -> str:
        return '%s (id=%s)' % (camera.get('title') or 'ohne Titel', camera.get('id'))

    def camera_summary(self) -> dict:
        camera = self._camera or {}
        return {'id': camera.get('id'), 'title': camera.get('title'),
                'firmware': camera.get('firmwareVersion'),
                'connection_status': camera.get('connectionStatus'),
                'privacy_mode': camera.get('privacyMode')}

    def create_connection(self) -> tuple[str, str, dict]:
        """Erzeugt temporaere Zugangsdaten; Rueckgabe: (MediaMTX-URL, Fingerprint, Diagnose)."""
        if self._camera is None:
            self.resolve_camera()
        camera_id = str(self._camera['id'])
        response = self._request('PUT', '/v11/video_inputs/%s/connection' % quote(camera_id, safe=''),
                                 json={'type': self.cfg.connection_type,
                                       'highQualityVideo': self.cfg.high_quality})
        connection = self._json(response, 'Verbindungsaufbau')
        if not isinstance(connection, dict) or not connection.get('user') or not connection.get('password'):
            raise CloudError('Verbindungsantwort ohne Zugangsdaten.')
        urls = connection.get('urls') or []
        if isinstance(urls, str):
            urls = [urls]
        host, port, scheme = self._select_host(urls)
        user = quote(str(connection['user']), safe='')
        password = quote(str(connection['password']), safe='')
        query = 'line=1&inst=1&enableaudio=1&maxSessionDuration=3600'
        if self.cfg.connection_type == 'REMOTE' and scheme == 'rtsps':
            url = 'rtsps://%s:%s@%s:%d/rtsp_tunnel?%s' % (user, password, host, port, query)
        else:
            url = 'rtsps+http://%s:%s@%s:%d/rtsp_tunnel?%s' % (user, password, host, port, query)
        fingerprint = camera_fingerprint(host)
        diagnostics = {'connection_type': self.cfg.connection_type,
                       'url_schemes': sorted({urlparse(_normalize_url(u)).scheme for u in urls if isinstance(u, str)}),
                       'stream_host': host, 'media_scheme': url.split('://', 1)[0],
                       'fingerprint_prefix': fingerprint[:16]}
        return url, fingerprint, diagnostics

    def _select_host(self, urls: list) -> tuple[str, int, str]:
        candidates: list[tuple[str, int, str]] = []
        for raw in urls:
            if not isinstance(raw, str):
                continue
            parsed = urlparse(_normalize_url(raw))
            if not parsed.hostname:
                continue
            candidates.append((parsed.hostname, parsed.port or 443, parsed.scheme.lower()))
        if not candidates:
            raise CloudError('Verbindungsantwort enthaelt keine nutzbare Kameraadresse.')
        if self.cfg.camera_host:
            for host, port, scheme in candidates:
                if host == self.cfg.camera_host:
                    return host, port, scheme
            return self.cfg.camera_host, 443, 'https'
        if self.cfg.connection_type == 'LOCAL':
            for host, port, scheme in candidates:
                if _private_host(host):
                    return host, port, scheme
        return candidates[0]
