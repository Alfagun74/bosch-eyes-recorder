"""Optional AV1/Opus transcoding with Intel QSV hardware acceleration.

MediaMTX keeps recording the camera's original H.264/AAC segments. This worker
watches for finalized ``.mp4`` segments and re-encodes them to AV1 (Intel QSV)
with Opus audio, replacing the original file after a successful transcode.
The default output container is WebM because Chrome/Edge/Firefox can play
AV1+Opus WebM natively in the recordings browser.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .config import Config

log = logging.getLogger('bosch-recorder.transcode')

CONTAINERS = {
    'webm': ('webm', '.webm', 'video/webm'),
    'mkv': ('matroska', '.mkv', 'video/x-matroska'),
    'mp4': ('mp4', '.mp4', 'video/mp4'),
}


def output_for(source: Path, container: str) -> Path:
    _, extension, _ = CONTAINERS.get(container, CONTAINERS['webm'])
    if source.suffix.lower() == extension:
        # Never use the source file itself as the destination.
        return source.with_name(source.stem + '.av1' + extension)
    return source.with_suffix(extension)


def build_ffmpeg_command(cfg: Config, source: Path, destination: Path,
                         hardware_decode: bool = True) -> list[str]:
    """Build the ffmpeg command for one finalized segment."""
    fmt, _, _ = CONTAINERS.get(cfg.transcode_container, CONTAINERS['webm'])
    command = [cfg.ffmpeg_binary, '-hide_banner', '-nostdin', '-y', '-loglevel', 'warning']
    if hardware_decode:
        command += ['-hwaccel', 'qsv', '-hwaccel_device', cfg.transcode_device,
                    '-hwaccel_output_format', 'qsv']
    command += ['-i', str(source), '-map', '0:v:0', '-map', '0:a:0?']
    command += ['-c:v', cfg.transcode_encoder, '-preset', cfg.transcode_preset]
    if cfg.transcode_bitrate:
        command += ['-b:v', cfg.transcode_bitrate]
    else:
        command += ['-global_quality', str(cfg.transcode_quality)]
    command += ['-c:a', 'libopus', '-b:a', cfg.transcode_audio_bitrate]
    command += ['-map_metadata', '-1']
    if fmt == 'mp4':
        command += ['-movflags', '+faststart']
    command += ['-f', fmt, str(destination)]
    return command


class TranscodeWorker:
    """Supervises batch transcoding of finalized MediaMTX segments."""

    def __init__(self, cfg: Config, records_dir: Path):
        self.cfg = cfg
        self.records_dir = records_dir
        self.stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._available = False
        self._availability_error = 'noch nicht geprueft'
        self._proc: subprocess.Popen | None = None
        self._processed = 0
        self._failed = 0
        self._bytes_saved = 0
        self._current: str | None = None
        self._last_error: str | None = None
        self._failed_until: dict[Path, float] = {}
        self._start_timestamp = time.time()

    # ---------- Lifecycle ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.stop_event.clear()
        self._thread = threading.Thread(target=self._run, name='transcode', daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._thread:
            self._thread.join(timeout=30)

    # ---------- status ----------
    def status(self) -> dict:
        with self._lock:
            return {
                'enabled': True,
                'available': self._available,
                'encoder': self.cfg.transcode_encoder,
                'container': self.cfg.transcode_container,
                'quality': self.cfg.transcode_quality,
                'preset': self.cfg.transcode_preset,
                'processed': self._processed,
                'failed': self._failed,
                'bytes_saved': self._bytes_saved,
                'bytes_saved_human': _human(self._bytes_saved),
                'pending': self._pending_count(),
                'current': self._current,
                'error': self._availability_error if not self._available else self._last_error,
            }

    # ---------- loop ----------
    def _run(self) -> None:
        self._check_encoder()
        while not self.stop_event.is_set():
            if not self._available:
                self._check_encoder()
                if not self._available:
                    self.stop_event.wait(120)
                    continue
            try:
                pending = self._pending_files()
            except OSError as exc:
                self._last_error = str(exc)
                pending = []
            for source in pending:
                if self.stop_event.is_set():
                    return
                with self._lock:
                    self._current = source.name
                try:
                    self._transcode(source)
                except Exception as exc:  # keep the source, retry later
                    self._failed += 1
                    self._last_error = '%s: %s' % (source.name, exc)
                    self._failed_until[source] = time.time() + 600
                    log.warning('Transcode fehlgeschlagen fuer %s: %s', source.name, exc)
                finally:
                    with self._lock:
                        self._current = None
            self.stop_event.wait(10)

    def _check_encoder(self) -> None:
        binary = self.cfg.ffmpeg_binary
        if not (Path(binary).is_file() or shutil.which(binary)):
            self._available = False
            self._availability_error = 'ffmpeg nicht gefunden: %s' % binary
            return
        try:
            result = subprocess.run([binary, '-hide_banner', '-encoders'],
                                    capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._available = False
            self._availability_error = 'ffmpeg-Encoder-Pruefung: %s' % exc
            return
        encoders = (result.stdout + result.stderr).lower()
        missing = []
        if self.cfg.transcode_encoder.lower() not in encoders:
            missing.append(self.cfg.transcode_encoder)
        if 'libopus' not in encoders:
            missing.append('libopus')
        if missing:
            self._available = False
            self._availability_error = 'Encoder fehlen: %s' % ', '.join(missing)
            return
        self._available = True
        self._availability_error = ''

    def _pending_files(self, cleanup: bool = True) -> list[Path]:
        now = time.time()
        files = []
        for path in self.records_dir.rglob('*.mp4'):
            try:
                stat = path.stat()
            except OSError:
                continue
            if now - stat.st_mtime < max(1, self.cfg.transcode_min_age_seconds):
                continue
            destination = output_for(path, self.cfg.transcode_container)
            if destination.exists():
                # A previous run may have crashed after writing the output.
                if cleanup and destination.stat().st_size > 0:
                    _unlink(path)
                continue
            if not self.cfg.transcode_existing and stat.st_mtime < self._start_timestamp:
                continue
            if self._failed_until.get(path, 0) > now:
                continue
            files.append(path)
        files.sort(key=lambda item: item.stat().st_mtime)
        return files

    def _pending_count(self) -> int:
        try:
            return len(self._pending_files(cleanup=False))
        except OSError:
            return 0

    def _transcode(self, source: Path) -> None:
        destination = output_for(source, self.cfg.transcode_container)
        temporary = destination.with_name(destination.name + '.part')
        _unlink(temporary)
        source_size = source.stat().st_size
        errors: list[str] = []
        for hardware_decode in (True, False):
            command = build_ffmpeg_command(self.cfg, source, temporary, hardware_decode)
            log.info('Transcode %s -> %s (%s, HW-Decode=%s)',
                     source.name, destination.name, self.cfg.transcode_encoder, hardware_decode)
            try:
                proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, encoding='utf-8', errors='replace')
                self._proc = proc
                try:
                    _, stderr = proc.communicate(timeout=1800)
                finally:
                    self._proc = None
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(str(exc))
                _unlink(temporary)
                continue
            if proc.returncode == 0 and temporary.exists() and temporary.stat().st_size > 0:
                output_size = temporary.stat().st_size
                os.replace(temporary, destination)
                _unlink(source)
                with self._lock:
                    self._processed += 1
                    self._bytes_saved += max(0, source_size - output_size)
                    self._last_error = None
                log.info('Transcode fertig: %s (%s -> %s)', source.name,
                         _human(source_size), _human(output_size))
                return
            errors.append((stderr or '').strip()[-500:])
            _unlink(temporary)
            if not hardware_decode:
                break
        raise RuntimeError('; '.join(error for error in errors if error) or 'ffmpeg exit != 0')


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _human(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return '%.2f GB' % (num_bytes / (1024 ** 3))
    if num_bytes >= 1024 ** 2:
        return '%.1f MB' % (num_bytes / (1024 ** 2))
    if num_bytes >= 1024:
        return '%.1f KB' % (num_bytes / 1024)
    return '%d B' % num_bytes
