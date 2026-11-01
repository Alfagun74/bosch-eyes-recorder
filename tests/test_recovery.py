"""Unit tests: Wiederverbinden nach abgelaufenen Kamera-Zugangsdaten."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from app.config import Config
from app.main import App


def _config(data_dir: Path) -> Config:
    return Config(
        data_dir=data_dir,
        bootstrap_token=None,
        camera_id=None,
        camera_name=None,
        camera_host=None,
        connection_type='LOCAL',
        high_quality=False,
        segment_minutes=10,
        retention_days=7,
        credential_refresh_seconds=1200,
        stall_seconds=90,
        min_free_gb=5,
        max_storage_gb=0,
        health_port=80,
        log_level='INFO',
        mediamtx_binary='mediamtx',
        web_password=None,
        mediamtx_api_addr='127.0.0.1:9997',
        mediamtx_hls_addr='127.0.0.1:8888',
        mediamtx_rtsp_addr='127.0.0.1:8554',
    )


class FakeMediaMTX:
    def __init__(self, running: bool = True, path_ready: bool = False,
                 path_state_at: float | None = None) -> None:
        self.running = running
        self.path_ready = path_ready
        self.path_state_at = time.time() if path_state_at is None else path_state_at
        self.restart_calls: list[tuple[str, str, str | None]] = []
        self.apply_calls: list[tuple[str, str, str | None]] = []

    def status(self) -> dict:
        return {'running': self.running, 'path_ready': self.path_ready,
                'path_state_at': self.path_state_at}

    def restart_with(self, url: str, fingerprint: str, host: str | None) -> None:
        self.restart_calls.append((url, fingerprint, host))

    def apply_source(self, url: str, fingerprint: str, host: str | None) -> None:
        self.apply_calls.append((url, fingerprint, host))


class FakeCloud:
    def __init__(self) -> None:
        self.calls = 0

    def create_connection(self) -> tuple[str, str, dict]:
        self.calls += 1
        return ('rtsps+http://user:pass@camera:443/rtsp_tunnel', 'AABBCC',
                {'stream_host': 'camera'})


class ApplyTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.app = App(_config(Path(self._tmp.name)))
        self.mtx = FakeMediaMTX()
        self.cloud = FakeCloud()
        self.app.mtx = self.mtx  # type: ignore[assignment]
        self.app.cloud = self.cloud  # type: ignore[assignment]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_restarts_mediamtx_with_fresh_credentials(self) -> None:
        self.assertTrue(self.app._apply_target())
        self.assertEqual(len(self.mtx.restart_calls), 1)
        url, fingerprint, host = self.mtx.restart_calls[0]
        self.assertIn('rtsp_tunnel', url)
        self.assertEqual(fingerprint, 'AABBCC')
        self.assertEqual(host, 'camera')
        self.assertEqual(self.cloud.calls, 1)

    def test_does_nothing_when_stream_is_ready(self) -> None:
        self.mtx.path_ready = True
        self.assertFalse(self.app._apply_target())
        self.assertEqual(self.mtx.restart_calls, [])
        self.assertEqual(self.cloud.calls, 0)

    def test_stale_ready_state_triggers_recovery(self) -> None:
        # z. B. Status-API liefert nicht mehr: alter Wert darf nicht blockieren
        self.mtx.path_ready = True
        self.mtx.path_state_at = time.time() - 120
        self.assertTrue(self.app._apply_target())
        self.assertEqual(len(self.mtx.restart_calls), 1)

    def test_writes_config_only_when_mediamtx_is_down(self) -> None:
        self.mtx.running = False
        self.assertFalse(self.app._apply_target())
        self.assertEqual(self.mtx.restart_calls, [])
        self.assertEqual(len(self.mtx.apply_calls), 1)

    def test_relogin_reconnects_stalled_stream(self) -> None:
        self.app._resume_after_login()
        self.assertEqual(len(self.mtx.restart_calls), 1)

    def test_relogin_forces_reconnect_even_if_state_looks_ready(self) -> None:
        # Bewusster Login ist ein Reset; ein evtl. veralteter path_ready-Wert
        # darf ihn nicht stillschweigend zu einem No-op machen.
        self.mtx.path_ready = True
        self.app._resume_after_login()
        self.assertEqual(len(self.mtx.restart_calls), 1)
        self.assertEqual(self.cloud.calls, 1)

    def test_login_finished_triggers_reconnect_for_running_worker(self) -> None:
        self.app._worker_started = True
        self.app.login_finished()
        deadline = time.time() + 2
        while time.time() < deadline and not self.mtx.restart_calls:
            time.sleep(0.01)
        self.assertEqual(len(self.mtx.restart_calls), 1)


if __name__ == '__main__':
    unittest.main()
