"""Einstiegspunkt des Bosch-Eyes-Dauerrecorders (MediaMTX-basiert)."""
from __future__ import annotations

import json
import logging
import secrets
import shutil
import signal
import threading
import time

from .auth import AuthError, BoschAuth
from .cloud import BoschCloud, CloudError
from .config import Config
from .mediamtx import MediaMTX
from .oauth import OAuthFlow
from .retention import prune, retention_loop
from .status_http import start_http_server
from .transcode import TranscodeWorker

VERSION = '1.4.1'
# Ab diesem Alter gilt der zuletzt gemeldete path_ready-Wert als veraltet
# (MediaMTX wird alle 5 s abgefragt; 30 s = 6 verpasste Abfragen).
PATH_STATE_MAX_AGE = 30.0
log = logging.getLogger('bosch-recorder')


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.data_dir = cfg.data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / 'state').mkdir(parents=True, exist_ok=True)
        self.records_dir = self.data_dir / 'recordings'
        self.records_dir.mkdir(parents=True, exist_ok=True)

        self.auth = BoschAuth(self.data_dir, cfg.bootstrap_token)
        self.cloud = BoschCloud(self.auth, cfg)
        self.mtx = MediaMTX(cfg, self.data_dir, cfg.mediamtx_binary)
        self.transcode = TranscodeWorker(cfg, self.records_dir) if cfg.transcode_enabled else None
        self.oauth = OAuthFlow()
        self.hls_token = secrets.token_urlsafe(18)
        self.media_token = secrets.token_urlsafe(18)

        self.stop_event = threading.Event()
        self.setup_event = threading.Event()
        self._status_lock = threading.Lock()
        self._target_lock = threading.Lock()
        self._recover_lock = threading.Lock()
        self._last_recover_at = 0.0
        self._target: tuple[str, str, str, float] | None = None
        self._storage_lock = threading.Lock()
        self._storage_cache = {'computed_at': 0.0, 'files': 0, 'mb': 0.0, 'free_gb': 0.0}
        self._retention_stats: dict = {}
        self._started_at = time.time()
        self._status: dict = {
            'version': VERSION,
            'phase': 'starting',
            'error': None,
            'started_at': self._started_at,
            'camera': {},
            'auth_has_token': self.auth.has_token(),
            'last_target_at': None,
            'target_host': None,
        }
        self._worker_started = False
        self._http_server = None
        self._threads: list[threading.Thread] = []

    # ---------- Status ----------
    def status(self) -> dict:
        with self._status_lock:
            status = dict(self._status)
        status['auth_has_token'] = self.auth.has_token()
        status['media'] = self.mtx.status()
        status['transcode'] = self.transcode.status() if self.transcode else {'enabled': False}
        status['retention'] = dict(self._retention_stats)
        status['storage'] = self._storage_stats()
        status['uptime_seconds'] = round(time.time() - self._started_at)
        return status

    def _update(self, **kwargs: object) -> None:
        with self._status_lock:
            self._status.update(kwargs)

    def _storage_stats(self) -> dict:
        now = time.time()
        with self._storage_lock:
            if now - self._storage_cache['computed_at'] < 30:
                return dict(self._storage_cache)
            files = 0
            total = 0
            try:
                for path in (list(self.records_dir.rglob('*.mp4'))
                             + list(self.records_dir.rglob('*.webm'))
                             + list(self.records_dir.rglob('*.mkv'))):
                    try:
                        total += path.stat().st_size
                        files += 1
                    except OSError:
                        continue
            except OSError:
                pass
            try:
                free = shutil.disk_usage(self.data_dir).free / (1024 ** 3)
            except OSError:
                free = 0.0
            self._storage_cache = {'computed_at': now, 'files': files,
                                   'mb': round(total / (1024 ** 2), 1), 'free_gb': round(free, 2)}
            return dict(self._storage_cache)

    # ---------- Web/Login ----------
    def hls_url(self) -> str:
        return '/hls/%s/eyes/index.m3u8' % self.hls_token

    def login_finished(self) -> None:
        self._update(error=None)
        self.setup_event.set()
        log.info('Bosch-Login abgeschlossen; Aufnahme startet.')
        if not self._worker_started:
            self.start_worker()
            return
        try:
            if not self.mtx.status().get('running'):
                # Der Worker holt beim Start selbst eine frische Quelle.
                return
        except Exception:
            pass
        threading.Thread(target=self._resume_after_login, name='relogin', daemon=True).start()

    def _resume_after_login(self) -> None:
        """Erneuert nach einem Login die Kamera-Quelle von MediaMTX.

        Ein erneuter OAuth-Login allein hilft nicht: die 401-Fehler im
        MediaMTX-Log betreffen die separaten, kurzlebigen Kamera-Zugangsdaten
        im generierten mediamtx.yml, nicht das Bosch-Token. Ohne diesen
        Schritt laeuft MediaMTX weiter mit der alten, abgelaufenen Quelle.
        """
        try:
            # Ein bewusster Login ist ein Reset: Bereitschafts-/Cooldown-Gates
            # werden umgangen, damit ein haengender Zustand sicher behoben wird.
            if self._apply_target(force=True):
                log.info('MediaMTX mit frischen Stream-Zugangsdaten neu gestartet.')
        except Exception as exc:
            log.warning('Quelle nach Login konnte nicht erneuert werden: %s', exc)
            self._update(error='Quelle nach Login: ' + type(exc).__name__)

    # ---------- Setup ----------
    def configure_token(self, text: str) -> None:
        self.auth.bootstrap(text)
        self.login_finished()
        log.info('Refresh-Token per Setup gespeichert und validiert.')

    # ---------- Stream-Zugangsdaten ----------
    def _get_target(self, force: bool = False) -> tuple[str, str, dict]:
        with self._target_lock:
            now = time.time()
            if (self._target and not force
                    and now - self._target[3] < self.cfg.credential_refresh_seconds):
                _, fingerprint, host, _ = self._target
                return self._target[0], fingerprint, {'stream_host': host}
            url, fingerprint, diagnostics = self.cloud.create_connection()
            host = diagnostics.get('stream_host')
            self._target = (url, fingerprint, host or '', now)
        self._update(last_target_at=now, target_host=host, error=None)
        log.info('Neue Stream-Zugangsdaten (Host %s, Pin %s).', host, fingerprint[:16])
        return url, fingerprint, diagnostics

    def _path_ready(self, state: dict) -> bool:
        """True nur bei frisch bestaetigter, bereiter MediaMTX-Quelle.

        Ein einmal gesetztes path_ready darf die Erholung nicht dauerhaft
        blockieren: bleibt die Statusabfrage aus (Prozessneustart, API-Fehler),
        gilt der Wert als unbekannt und die Quelle wird erneuert.
        """
        if not state.get('path_ready'):
            return False
        since = state.get('path_state_at')
        if not since:
            return False
        return time.time() - float(since) < PATH_STATE_MAX_AGE

    def _apply_target(self, force: bool = False) -> bool:
        """Holt frische Kamera-Zugangsdaten und gibt sie an MediaMTX weiter.

        MediaMTX liest mediamtx.yml im laufenden Betrieb normalerweise neu ein,
        aber das haengt vom Dateiwatcher des Hosts/Volumes ab. Um die neuen
        Zugangsdaten garantiert zu verwenden und eine festgefahrene Quelle
        zurueckzusetzen, wird der Prozess nach dem Schreiben kontrolliert neu
        gestartet; der Supervisor liest dann die aktualisierte Datei.

        force=True umgeht Bereitschafts- und Cooldown-Pruefung (manueller
        Login = bewusster Reset).

        Rueckgabe: True, wenn MediaMTX mit neuer Quelle neu gestartet wurde.
        """
        with self._recover_lock:
            state = self.mtx.status()
            now = time.time()
            if not force:
                if self._path_ready(state):
                    return False
                if now - self._last_recover_at < 60:
                    return False
            # Cooldown vor dem Request setzen: auch fehlgeschlagene Versuche
            # sollen nicht alle 30 s neue Bosch-Sessions erzeugen.
            self._last_recover_at = now
            url, fingerprint, diagnostics = self._get_target(force=True)
            if not self.mtx.status().get('running'):
                # MediaMTX ist gerade aus (z. B. waehrend eines Neustarts);
                # der Supervisor liest die Datei beim naechsten Start.
                self.mtx.apply_source(url, fingerprint, diagnostics.get('stream_host'))
                return False
            self.mtx.restart_with(url, fingerprint, diagnostics.get('stream_host'))
            return True

    # ---------- Worker ----------
    def start_worker(self) -> None:
        if self._worker_started:
            self.setup_event.set()
            return
        self._worker_started = True
        thread = threading.Thread(target=self._worker, name='worker', daemon=True)
        self._threads.append(thread)
        thread.start()

    def _worker(self) -> None:
        hinted = False
        while not self.stop_event.is_set():
            if not self.auth.has_token():
                self._update(phase='need_setup',
                             error='Kein Refresh-Token. Bitte Setup-Seite oeffnen.')
                if not hinted:
                    log.warning('Kein Refresh-Token: Setup unter http://<app>:8080/setup noetig.')
                    hinted = True
                self.setup_event.wait(10)
                self.setup_event.clear()
                continue
            try:
                camera = self.cloud.resolve_camera()
                self._update(camera=self.cloud.camera_summary(), phase='starting', error=None)
                url, fingerprint, diagnostics = self._get_target(force=True)
                self.mtx.apply_source(url, fingerprint, diagnostics.get('stream_host'))
                stats = prune(self.records_dir, self.cfg.retention_days,
                              self.cfg.max_storage_gb, self.cfg.min_free_gb)
                self._retention_stats.update(stats)
                self.mtx.start()
                if self.transcode:
                    self.transcode.start()
                    log.info('Transcoding aktiv: %s -> %s (%s)', self.cfg.transcode_encoder,
                             self.cfg.transcode_container, self.cfg.transcode_preset)
                self._update(phase='running')
                log.info('Aufnahme gestartet fuer Kamera %s (id=%s).',
                         camera.get('title'), camera.get('id'))
                cred_thread = threading.Thread(target=self._credential_loop,
                                               name='credentials', daemon=True)
                monitor_thread = threading.Thread(target=self._monitor_loop,
                                                  name='monitor', daemon=True)
                retention_thread = threading.Thread(
                    target=retention_loop,
                    args=(self.cfg, self.records_dir, self._retention_stats,
                          self.stop_event, 300),
                    name='retention', daemon=True)
                self._threads.extend([cred_thread, monitor_thread, retention_thread])
                cred_thread.start()
                monitor_thread.start()
                retention_thread.start()
                return
            except AuthError as exc:
                self._update(phase='need_setup', error=str(exc))
                self.setup_event.wait(10)
                self.setup_event.clear()
            except Exception as exc:
                message = str(exc) if isinstance(exc, CloudError) else type(exc).__name__
                self._update(phase='starting', error=message)
                log.warning('Start noch nicht moeglich: %s', message)
                self.stop_event.wait(25)

    def _credential_loop(self) -> None:
        """Sicherheitsnetz, falls der Watchdog nicht mehr greift.

        Frueher wurden hier nur neue Zugangsdaten geholt und verworfen - sie
        erreichten MediaMTX nie. Jetzt wird die Quelle nur dann erneuert,
        wenn der Stream tatsaechlich steht; im Normalbetrieb bleibt die
        laufende Kamera-Verbindung unangetastet.
        """
        while not self.stop_event.is_set():
            if self.stop_event.wait(self.cfg.credential_refresh_seconds):
                return
            try:
                state = self.mtx.status()
                if self._path_ready(state) or not state.get('running'):
                    continue
                if self._apply_target():
                    log.info('Zugangsdaten-Erneuerung: MediaMTX mit frischer Quelle neu gestartet.')
            except Exception as exc:
                log.warning('Zugangsdaten-Erneuerung fehlgeschlagen: %s', exc)
                self._update(error='Zugangsdaten-Erneuerung: ' + type(exc).__name__)
                self.stop_event.wait(60)

    def _monitor_loop(self) -> None:
        not_ready_since: float | None = None
        while not self.stop_event.is_set():
            if self.stop_event.wait(8):
                return
            try:
                state = self.mtx.status()
            except Exception as exc:  # Watchdog darf nie sterben.
                log.warning('MediaMTX-Status nicht lesbar: %s', exc)
                continue
            if self._path_ready(state):
                not_ready_since = None
                continue
            if not state.get('running'):
                self._update(error='MediaMTX laeuft nicht (Neustart laeuft).')
                continue
            now = time.time()
            if not_ready_since is None:
                not_ready_since = now
                continue
            if now - not_ready_since < max(1, self.cfg.stall_seconds):
                continue
            log.warning('Kamera-Quelle seit %.0f s nicht bereit; aktualisiere Zugangsdaten.',
                        now - not_ready_since)
            try:
                if self._apply_target():
                    log.info('MediaMTX mit frischen Zugangsdaten neu gestartet.')
                not_ready_since = None
            except Exception as exc:
                log.warning('Umschalten auf neue Zugangsdaten fehlgeschlagen: %s', exc)
                self._update(error='Umschalten fehlgeschlagen: ' + type(exc).__name__)
                self.stop_event.wait(30)

    # ---------- Run ----------
    def run(self) -> int:
        log.info('Bosch-Recorder %s startet; Daten in %s', VERSION, self.data_dir)
        self._http_server = start_http_server(self, self.cfg.health_port)
        self.start_worker()
        try:
            while not self.stop_event.wait(1):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()
        return 0

    def shutdown(self) -> None:
        log.info('Beende Recorder.')
        self.stop_event.set()
        if self.transcode:
            self.transcode.stop()
        self.mtx.stop()
        if self._http_server is not None:
            try:
                self._http_server.shutdown()
            except Exception:
                pass
        try:
            (self.data_dir / 'state' / 'last_status.json').write_text(
                json.dumps(self.status(), indent=2), encoding='utf-8')
        except OSError:
            pass


def main() -> int:
    cfg = Config.from_env()
    logging.basicConfig(level=getattr(logging, cfg.log_level, logging.INFO),
                        format='%(asctime)s %(levelname)s %(name)s %(message)s')
    app = App(cfg)

    def _signal(_signum: int, _frame: object) -> None:
        app.stop_event.set()

    for name in ('SIGTERM', 'SIGINT'):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), _signal)
            except ValueError:
                pass
    return app.run()


if __name__ == '__main__':
    raise SystemExit(main())
