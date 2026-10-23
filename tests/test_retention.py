"""Unit tests: rolling retention (age, maximum storage, free space)."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from app.retention import prune


class RetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.records = Path(self._tmp.name) / 'recordings'
        self.records.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make(self, name: str, size: int, age_days: float = 0.0) -> Path:
        path = self.records / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * size)
        stamp = time.time() - age_days * 86400
        os.utime(path, (stamp, stamp))
        return path

    def test_deletes_files_older_than_retention(self) -> None:
        old = self._make('eyes/old.mp4', 1000, age_days=8)
        new = self._make('eyes/new.mp4', 1000, age_days=1)
        stats = prune(self.records, retention_days=7, max_storage_gb=0, min_free_gb=0)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())
        self.assertEqual(stats['deleted'], 1)
        self.assertEqual(stats['files_before'], 2)
        self.assertEqual(stats['files_after'], 1)

    def test_keeps_legacy_mkv_files(self) -> None:
        old = self._make('eyes/old.mkv', 1000, age_days=9)
        prune(self.records, retention_days=7, max_storage_gb=0, min_free_gb=0)
        self.assertFalse(old.exists())

    def test_max_storage_deletes_oldest_first(self) -> None:
        oldest = self._make('eyes/a.mp4', 1024 * 1024, age_days=3)
        middle = self._make('eyes/b.mp4', 1024 * 1024, age_days=2)
        newest = self._make('eyes/c.mp4', 1024 * 1024, age_days=1)
        max_gb = (1.5 * 1024 * 1024) / (1024 ** 3)
        stats = prune(self.records, retention_days=7, max_storage_gb=max_gb, min_free_gb=0)
        self.assertFalse(oldest.exists())
        self.assertFalse(middle.exists())
        self.assertTrue(newest.exists())
        self.assertEqual(stats['deleted'], 2)

    def test_removes_empty_camera_directories(self) -> None:
        old = self._make('eyes/sub/old.mp4', 1000, age_days=10)
        prune(self.records, retention_days=7, max_storage_gb=0, min_free_gb=0)
        self.assertFalse(old.exists())
        self.assertFalse((self.records / 'eyes').exists())

    def test_missing_directory_is_tolerated(self) -> None:
        stats = prune(self.records / 'does-not-exist', retention_days=7,
                      max_storage_gb=0, min_free_gb=0)
        self.assertEqual(stats['files_before'], 0)
        self.assertEqual(stats['deleted'], 0)


if __name__ == '__main__':
    unittest.main()
