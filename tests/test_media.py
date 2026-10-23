"""Unit tests: recording list and HTTP Range parsing (no camera)."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from app.status_http import format_size, list_recordings, parse_byte_range


class ByteRangeTests(unittest.TestCase):
    def test_none_or_other_unit_is_ignored(self) -> None:
        self.assertIsNone(parse_byte_range(None, 1000))
        self.assertIsNone(parse_byte_range('', 1000))
        self.assertIsNone(parse_byte_range('items=0-10', 1000))
        self.assertIsNone(parse_byte_range('bytes=abc', 1000))

    def test_closed_range(self) -> None:
        self.assertEqual(parse_byte_range('bytes=0-99', 1000), (0, 99))
        self.assertEqual(parse_byte_range('bytes=100-999', 1000), (100, 999))

    def test_open_ended_range(self) -> None:
        self.assertEqual(parse_byte_range('bytes=100-', 1000), (100, 999))
        self.assertEqual(parse_byte_range('bytes=0-', 1000), (0, 999))

    def test_suffix_range(self) -> None:
        self.assertEqual(parse_byte_range('bytes=-100', 1000), (900, 999))
        self.assertEqual(parse_byte_range('bytes=-5000', 1000), (0, 999))

    def test_end_beyond_size_is_clamped(self) -> None:
        self.assertEqual(parse_byte_range('bytes=900-5000', 1000), (900, 999))

    def test_multiple_ranges_use_first(self) -> None:
        self.assertEqual(parse_byte_range('bytes=0-9,20-29', 1000), (0, 9))

    def test_invalid_or_unsatisfiable(self) -> None:
        for header in ('bytes=1000-', 'bytes=1000-2000', 'bytes=5-4', 'bytes=-0', 'bytes=abc-def'):
            with self.assertRaises(ValueError):
                parse_byte_range(header, 1000)

    def test_empty_file(self) -> None:
        with self.assertRaises(ValueError):
            parse_byte_range('bytes=0-', 0)


class RecordingListTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.records = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _file(self, rel: str, age_seconds: float, size: int = 1024) -> Path:
        path = self.records / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * size)
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_parses_timestamp_and_relative_path(self) -> None:
        path = self._file('eyes/2026-01-31_23-10-02-283092.mp4', age_seconds=120)
        entries = list_recordings(self.records)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry['name'], path.name)
        self.assertEqual(entry['rel'], 'eyes/2026-01-31_23-10-02-283092.mp4')
        self.assertEqual(entry['display_date'], '31.01.2026')
        self.assertEqual(entry['display_time'], '23:10:02')
        self.assertEqual(entry['size'], 1024)

    def test_skips_currently_written_segment(self) -> None:
        self._file('eyes/2026-01-31_23-00-00-000000.mp4', age_seconds=120)
        self._file('eyes/2026-01-31_23-10-00-000000.mp4', age_seconds=1)
        entries = list_recordings(self.records, min_age_seconds=20)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['display_time'], '23:00:00')

    def test_newest_first(self) -> None:
        self._file('eyes/2026-01-31_22-00-00-000000.mp4', age_seconds=300)
        self._file('eyes/2026-01-31_23-00-00-000000.mp4', age_seconds=120)
        self._file('eyes/2026-02-01_00-00-00-000000.mp4', age_seconds=60)
        entries = list_recordings(self.records, min_age_seconds=10)
        self.assertEqual([e['display_time'] for e in entries], ['00:00:00', '23:00:00', '22:00:00'])

    def test_unknown_filename_is_listed(self) -> None:
        self._file('eyes/legacy-recording.mp4', age_seconds=120)
        entries = list_recordings(self.records)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['display_date'], 'Unbekannt')

    def test_missing_directory(self) -> None:
        self.assertEqual(list_recordings(self.records / 'missing'), [])


class FormatSizeTests(unittest.TestCase):
    def test_units(self) -> None:
        self.assertEqual(format_size(0), '0 B')
        self.assertEqual(format_size(999), '999 B')
        self.assertEqual(format_size(1024), '1.0 KB')
        self.assertEqual(format_size(1024 ** 2), '1.0 MB')
        self.assertEqual(format_size(1024 ** 3), '1.00 GB')


if __name__ == '__main__':
    unittest.main()
