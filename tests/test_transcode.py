"""Unit tests: AV1/Opus ffmpeg command construction (no GPU required)."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from app.transcode import build_ffmpeg_command, output_for, parse_audio_packets


def _cfg(**overrides):
    values = {
        'ffmpeg_binary': '/usr/lib/jellyfin-ffmpeg/ffmpeg',
        'transcode_encoder': 'av1_qsv',
        'transcode_container': 'webm',
        'transcode_quality': 32,
        'transcode_preset': 'slow',
        'transcode_audio_bitrate': '48k',
        'transcode_bitrate': '',
        'transcode_device': '/dev/dri/renderD128',
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class OutputNamingTests(unittest.TestCase):
    def test_webm_extension(self) -> None:
        source = Path('/data/recordings/eyes/2026-01-01_00-00-00-000000.mp4')
        self.assertEqual(output_for(source, 'webm'), source.with_suffix('.webm'))

    def test_mkv_extension(self) -> None:
        source = Path('/data/rec/a.mp4')
        self.assertEqual(output_for(source, 'mkv'), source.with_suffix('.mkv'))

    def test_mp4_does_not_overwrite_source(self) -> None:
        source = Path('/data/rec/a.mp4')
        destination = output_for(source, 'mp4')
        self.assertNotEqual(destination, source)
        self.assertEqual(destination.name, 'a.av1.mp4')

    def test_unknown_container_defaults_to_webm(self) -> None:
        source = Path('/data/rec/a.mp4')
        self.assertEqual(output_for(source, 'nope'), source.with_suffix('.webm'))


class AudioDetectionTests(unittest.TestCase):
    def test_audio_packets_present(self) -> None:
        self.assertIs(parse_audio_packets('1\n'), True)

    def test_no_audio_packets(self) -> None:
        self.assertIs(parse_audio_packets('0\n'), False)

    def test_no_audio_stream(self) -> None:
        self.assertIs(parse_audio_packets(''), None)


class CommandTests(unittest.TestCase):
    def test_qsv_av1_opus_command(self) -> None:
        source = Path('/data/rec/in.mp4')
        destination = Path('/data/rec/out.webm.part')
        command = build_ffmpeg_command(_cfg(), source, destination)
        joined = ' '.join(command)
        self.assertIn('av1_qsv', joined)
        self.assertIn('libopus', joined)
        self.assertIn('48k', joined)
        self.assertIn('-global_quality 32', joined)
        self.assertIn('av1_qsv -preset slow', joined)
        self.assertIn('-hwaccel qsv', joined)
        self.assertIn('renderD128', joined)
        self.assertIn('-f webm', joined)
        self.assertEqual(command[-1], str(destination))

    def test_video_only_command_omits_audio(self) -> None:
        command = build_ffmpeg_command(_cfg(), Path('/data/in.mp4'),
                                       Path('/data/out.webm'), include_audio=False)
        joined = ' '.join(command)
        self.assertNotIn('0:a:0', joined)
        self.assertNotIn('libopus', joined)
        self.assertNotIn('-c:a', command)

    def test_software_decode_variant_has_no_hwaccel(self) -> None:
        command = build_ffmpeg_command(_cfg(), Path('/data/in.mp4'), Path('/data/out.webm'), False)
        self.assertNotIn('-hwaccel', command)

    def test_bitrate_mode(self) -> None:
        command = build_ffmpeg_command(_cfg(transcode_bitrate='1200k'), Path('a.mp4'), Path('a.webm'))
        self.assertIn('-b:v 1200k', ' '.join(command))
        self.assertNotIn('-global_quality', command)

    def test_matroska_format(self) -> None:
        command = build_ffmpeg_command(_cfg(transcode_container='mkv'), Path('a.mp4'), Path('a.mkv'))
        self.assertIn('-f matroska', ' '.join(command))

    def test_mp4_faststart(self) -> None:
        command = build_ffmpeg_command(_cfg(transcode_container='mp4'), Path('a.mp4'), Path('a.av1.mp4'))
        self.assertIn('-movflags +faststart', ' '.join(command))


if __name__ == '__main__':
    unittest.main()
