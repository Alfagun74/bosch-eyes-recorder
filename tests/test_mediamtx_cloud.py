"""Unit tests: MediaMTX configuration and Bosch cloud URL helpers (no network)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.cloud import BoschCloud, _normalize_url, _private_host
from app.config import Config
from app.mediamtx import MediaMTX, _duration


class MediaMTXConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name)
        self.cfg = Config(
            data_dir=self.data,
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
        self.mtx = MediaMTX(self.cfg, self.data, 'mediamtx')

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_duration_format(self) -> None:
        self.assertEqual(_duration(10), '10m')
        self.assertEqual(_duration(0), '1m')

    def test_config_contains_recording_and_hls(self) -> None:
        config = self.mtx._config('rtsps+http://user:pass@example:443/rtsp_tunnel', 'AABBCC')
        self.assertTrue(config['hls'])
        self.assertEqual(config['hlsAddress'], '127.0.0.1:8888')
        self.assertEqual(config['hlsVariant'], 'fmp4')
        path = config['paths']['eyes']
        self.assertTrue(path['record'])
        self.assertEqual(path['recordFormat'], 'fmp4')
        self.assertEqual(path['recordSegmentDuration'], '10m')
        self.assertEqual(path['recordDeleteAfter'], '168h')
        self.assertIn('%path', path['recordPath'])
        self.assertEqual(path['sourceFingerprint'], 'aabbcc')
        self.assertTrue(path['source'].startswith('rtsps+http://'))
        any_user = [u for u in config['authInternalUsers'] if u['user'] == 'any']
        self.assertTrue(any_user)
        self.assertEqual(any_user[0]['permissions'][0]['action'], 'read')

    def test_apply_source_writes_json_and_updates_status(self) -> None:
        self.mtx.apply_source('rtsps+http://example', 'AABB', 'example')
        config = json.loads(self.mtx.config_path.read_text(encoding='utf-8'))
        self.assertEqual(config['paths']['eyes']['sourceFingerprint'], 'aabb')
        status = self.mtx.status()
        self.assertEqual(status['source_host'], 'example')
        self.assertEqual(status['recordings'], 0)


class CloudUrlTests(unittest.TestCase):
    def test_normalize_url_adds_scheme(self) -> None:
        self.assertEqual(_normalize_url('10.0.0.5:443'), 'https://10.0.0.5:443')
        self.assertEqual(_normalize_url('rtsps://x:443'), 'rtsps://x:443')

    def test_private_host(self) -> None:
        self.assertTrue(_private_host('10.1.2.3'))
        self.assertTrue(_private_host('192.168.1.2'))
        self.assertTrue(_private_host('172.20.0.1'))
        self.assertFalse(_private_host('8.8.8.8'))

    def _cloud(self, connection_type: str, camera_host: str | None = None) -> BoschCloud:
        cloud = object.__new__(BoschCloud)
        cloud.cfg = SimpleNamespace(connection_type=connection_type, camera_host=camera_host)
        return cloud

    def test_local_prefers_private_url(self) -> None:
        host, port, scheme = self._cloud('LOCAL')._select_host(
            ['proxy.example.com:443', '10.0.0.32:443'])
        self.assertEqual((host, port, scheme), ('10.0.0.32', 443, 'https'))

    def test_connection_type_remote_uses_first_candidate(self) -> None:
        host, _, _ = self._cloud('REMOTE')._select_host(
            ['proxy.example.com:443', '10.0.0.32:443'])
        self.assertEqual(host, 'proxy.example.com')

    def test_camera_host_override(self) -> None:
        host, port, _ = self._cloud('LOCAL', camera_host='cam.local')._select_host(
            ['10.0.0.32:443'])
        self.assertEqual((host, port), ('cam.local', 443))

    def test_no_urls_raises(self) -> None:
        with self.assertRaises(Exception):
            self._cloud('LOCAL')._select_host([])


if __name__ == '__main__':
    unittest.main()
