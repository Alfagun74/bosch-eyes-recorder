"""Unit tests: configuration parsing."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.config import Config


class ConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = Config.from_env()
        self.assertEqual(cfg.health_port, 80)
        self.assertEqual(cfg.segment_minutes, 10)
        self.assertAlmostEqual(cfg.retention_days, 7.0)
        self.assertFalse(cfg.high_quality)
        self.assertEqual(cfg.connection_type, 'LOCAL')
        self.assertIsNone(cfg.bootstrap_token)
        self.assertIsNone(cfg.web_password)
        self.assertEqual(cfg.data_dir, Path('/data'))

    def test_overrides_and_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                'DATA_DIR': tmp,
                'HEALTH_PORT': '8080',
                'RETENTION_DAYS': '3.5',
                'SEGMENT_MINUTES': '5',
                'HIGH_QUALITY': 'true',
                'CONNECTION_TYPE': 'remote',
                'BOSCH_REFRESH_TOKEN': 'token-value',
                'WEB_PASSWORD': 'secret',
                'MIN_FREE_GB': '2',
                'MAX_STORAGE_GB': '10',
            }
            with mock.patch.dict(os.environ, env, clear=True):
                cfg = Config.from_env()
        self.assertEqual(cfg.health_port, 8080)
        self.assertAlmostEqual(cfg.retention_days, 3.5)
        self.assertEqual(cfg.segment_minutes, 5)
        self.assertTrue(cfg.high_quality)
        self.assertEqual(cfg.connection_type, 'REMOTE')
        self.assertEqual(cfg.bootstrap_token, 'token-value')
        self.assertEqual(cfg.web_password, 'secret')
        self.assertAlmostEqual(cfg.min_free_gb, 2.0)
        self.assertAlmostEqual(cfg.max_storage_gb, 10.0)

    def test_invalid_values_fall_back(self) -> None:
        env = {
            'HEALTH_PORT': 'not-a-number',
            'RETENTION_DAYS': '-1',
            'SEGMENT_MINUTES': '0',
            'CONNECTION_TYPE': 'invalid',
            'HIGH_QUALITY': 'maybe',
        }
        with mock.patch.dict(os.environ, env, clear=True):
            cfg = Config.from_env()
        self.assertEqual(cfg.health_port, 80)
        self.assertEqual(cfg.segment_minutes, 1)  # clamped to minimum 1
        self.assertAlmostEqual(cfg.retention_days, 0.05)  # clamped minimum
        self.assertEqual(cfg.connection_type, 'LOCAL')
        self.assertFalse(cfg.high_quality)


if __name__ == '__main__':
    unittest.main()
