"""MediaMTX-Supervisor: zieht den Kamera-Stream und nimmt ihn mit interner
Aufnahmefunktion (fmp4-Segmente) auf. Kein ffmpeg noetig.
"""
from __future__ import annotations

import json
import logging
import secrets
import subprocess
import threading
import time
from pathlib import Path

import requests

from .config import Config

log = logging.getLogger('bosch-recorder.mediamtx')


def _duration(minutes: int) -> str:
    return '%dm' % max(1, int(minutes))


class MediaMTX:
    def __init__(self, cfg: Config, data_dir: Path, binary: str = 'mediamtx'):
        self.cfg = cfg
        self.binary = binary
        self.data_dir = data_dir
        self.config_path = data_dir / 'state' / 'mediamtx.yml'
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.recordings_dir = data_dir / 'recordings'
        self.recordings_dir.mkdir(parents=True, exist_ok=True)
        self.admin_password = secrets.token_urlsafe(24)
        self.api_address = cfg.mediamtx_api_addr
        self._proc: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._state = {'running': False, 'path_ready': False, 'readers': 0,
                       'restarts': 0, 'last_error': None, 'source_host': None,
                       'last_config_write': None, 'recordings': 0, 'recordings_mb': 0.0}

    # ---------- Konfiguration ----------
    def _config(self, source_url: str, fingerprint: str) -> dict:
        record_path = str(self.recordings_dir / '%path' / '%Y-%m-%d_%H-%M-%S-%f')
        return {
            'logLevel': 'info', 'logDestinations': ['stdout'],
            'api': True, 'apiAddress': self.api_address, 'apiAllowOrigins': [],
            'hls': True, 'hlsAddress': self.cfg.mediamtx_hls_addr,
            'hlsVariant': 'fmp4', 'hlsSegmentCount': 7,
            'hlsSegmentDuration': '1s', 'hlsPartDuration': '200ms', 'hlsAlwaysRemux': True,
            'metrics': False, 'pprof': False, 'playback': False,
            'rtsp': True, 'rtspAddress': self.cfg.mediamtx_rtsp_addr, 'rtspTransports': ['tcp'],
            'rtmp': False, 'webrtc': False, 'srt': False, 'moq': False,
            'authMethod': 'internal',
            'authInternalUsers': [
                {'user': 'admin', 'pass': self.admin_password, 'ips': ['127.0.0.1'],
                 'permissions': [{'action': 'api'}]},
                {'user': 'any', 'pass': '', 'ips': ['127.0.0.1'],
                 'permissions': [{'action': 'read', 'path': 'eyes'}]},
            ],
            'paths': {'eyes': {
                'source': source_url,
                'sourceFingerprint': fingerprint.lower(),
                'sourceOnDemand': False,
                'rtspTransport': 'tcp',
                'record': True,
                'recordPath': record_path,
                'recordFormat': 'fmp4',
                'recordSegmentDuration': _duration(self.cfg.segment_minutes),
                'recordDeleteAfter': '%dh' % max(1, round(self.cfg.retention_days * 24)),
            }},
        }

    def apply_source(self, source_url: str, fingerprint: str, host: str | None) -> None:
        config = self._config(source_url, fingerprint)
        self.config_path.write_text(json.dumps(config, indent=2), encoding='utf-8')
        with self._lock:
            self._state['source_host'] = host
            self._state['last_config_write'] = time.time()
        log.info('MediaMTX-Konfiguration aktualisiert (Quelle %s).', host)

    # ---------- Prozess ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='mediamtx', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kill()
        if self._thread:
            self._thread.join(timeout=20)

    def _run(self) -> None:
        backoff = 3
        while not self._stop.is_set():
            log.info('Starte MediaMTX.')
            self._proc = subprocess.Popen([self.binary, str(self.config_path)],
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          text=True, encoding='utf-8', errors='replace')
            with self._lock:
                self._state['running'] = True
                self._state['last_error'] = None
            threading.Thread(target=self._collect, args=(self._proc,), daemon=True).start()
            self._state_loop()
            with self._lock:
                self._state['running'] = False
            self._kill()
            if self._stop.wait(backoff):
                return
            backoff = min(30, backoff * 2)
            with self._lock:
                self._state['restarts'] += 1

    def _collect(self, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            log.info('mediamtx: %s', line.rstrip())
            if 'recording' in line:
                log.info('MediaMTX-Recorder aktiv.')

    def _state_loop(self) -> None:
        while not self._stop.wait(5):
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return
            self._refresh_path_state()

    def _refresh_path_state(self) -> None:
        try:
            response = requests.get('http://' + self.api_address + '/v3/paths/get/eyes',
                                    auth=('admin', self.admin_password), timeout=3)
            if response.status_code != 200:
                return
            payload = response.json()
            with self._lock:
                self._state['path_ready'] = bool(payload.get('ready'))
                self._state['readers'] = len(payload.get('readers') or [])
        except Exception:
            pass

    def _kill(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None

    # ---------- Status ----------
    def status(self) -> dict:
        with self._lock:
            state = dict(self._state)
        files = list(self.recordings_dir.rglob('*.mp4'))
        state['recordings'] = len(files)
        state['recordings_mb'] = round(sum(f.stat().st_size for f in files if f.is_file()) / (1024 ** 2), 1)
        state['latest_recording'] = max(files, key=lambda p: p.stat().st_mtime).name if files else None
        return state

    def restart_with(self, source_url: str, fingerprint: str, host: str | None) -> None:
        """Schreibt neue Quelle und startet MediaMTX neu (nach Quellenfehler)."""
        self.apply_source(source_url, fingerprint, host)
        self._kill()
