"""Rollierende Loeschung: Alter, Maximalgroesse und freier Speicher."""
from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from .config import Config

log = logging.getLogger('bosch-recorder.retention')


def _disk_free_gb(path: Path) -> float:
    try:
        usage = shutil.disk_usage(path)
        return usage.free / (1024 ** 3)
    except OSError:
        return float('inf')


def _scan(records_dir: Path) -> list[tuple[float, int, Path]]:
    files: list[tuple[float, int, Path]] = []
    if not records_dir.exists():
        return files
    for path in list(records_dir.rglob('*.mp4')) + list(records_dir.rglob('*.mkv')):
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append((stat.st_mtime, stat.st_size, path))
    files.sort(key=lambda item: item[0])
    return files


def prune(records_dir: Path, retention_days: float, max_storage_gb: float,
          min_free_gb: float) -> dict:
    """Loescht aelteste Segmente, bis Alter/Groesse/Freispabe erfuellt sind."""
    deleted = 0
    deleted_bytes = 0
    now = time.time()
    cutoff = now - retention_days * 86400

    def remove(item: tuple[float, int, Path]) -> None:
        nonlocal deleted, deleted_bytes
        mtime, size, path = item
        try:
            path.unlink()
            deleted += 1
            deleted_bytes += size
        except OSError as exc:
            log.warning('Segment konnte nicht geloescht werden: %s (%s)', path.name, exc)

    files = _scan(records_dir)
    files_before = len(files)
    total_bytes = sum(size for _, size, _ in files)

    for item in list(files):
        if item[0] < cutoff:
            remove(item)
            files.remove(item)
            total_bytes -= item[1]

    if max_storage_gb > 0:
        limit = max_storage_gb * (1024 ** 3)
        while files and total_bytes > limit:
            item = files.pop(0)
            remove(item)
            total_bytes -= item[1]

    min_free_gb = max(0.0, min_free_gb)
    while files and _disk_free_gb(records_dir) < min_free_gb:
        item = files.pop(0)
        remove(item)
        total_bytes -= item[1]

    # Leere Kamera-Unterordner aufraeumen
    try:
        for directory in sorted(records_dir.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            if directory.is_dir():
                try:
                    directory.rmdir()
                except OSError:
                    pass
    except OSError:
        pass

    stats = {
        'files_before': files_before,
        'files_after': len(files),
        'deleted': deleted,
        'deleted_mb': round(deleted_bytes / (1024 ** 2), 2),
        'retention_days': retention_days,
        'free_gb': round(_disk_free_gb(records_dir), 2),
        'timestamp': now,
    }
    if deleted:
        log.info('Rollierung: %d alte Segmente (%s MB) geloescht; %d Dateien verbleiben.',
                 deleted, stats['deleted_mb'], len(files))
    return stats


def retention_loop(cfg: Config, records_dir: Path, stats_sink: dict,
                   stop_event, interval_seconds: int = 300) -> None:
    while not stop_event.wait(interval_seconds):
        try:
            stats_sink.update(prune(records_dir, cfg.retention_days,
                                    cfg.max_storage_gb, cfg.min_free_gb))
        except Exception as exc:  # nie den Recorder stoeren
            log.warning('Rollierung fehlgeschlagen: %s', exc)
