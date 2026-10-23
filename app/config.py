"""Konfiguration aus Umgebungsvariablen (CapRover / docker-compose)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == '':
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.environ.get(name, '').strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.environ.get(name, '').strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


@dataclass(frozen=True)
class Config:
    data_dir: Path
    bootstrap_token: str | None
    camera_id: str | None
    camera_name: str | None
    camera_host: str | None
    connection_type: str
    high_quality: bool
    segment_minutes: int
    retention_days: float
    credential_refresh_seconds: int
    stall_seconds: int
    min_free_gb: float
    max_storage_gb: float
    health_port: int
    log_level: str
    mediamtx_binary: str
    web_password: str | None
    mediamtx_api_addr: str
    mediamtx_hls_addr: str
    mediamtx_rtsp_addr: str

    @classmethod
    def from_env(cls) -> 'Config':
        data_dir = Path(os.environ.get('DATA_DIR', '/data')).expanduser()
        connection_type = os.environ.get('CONNECTION_TYPE', 'LOCAL').strip().upper()
        if connection_type not in ('LOCAL', 'REMOTE'):
            connection_type = 'LOCAL'
        camera_id = (os.environ.get('BOSCH_CAMERA_ID') or '').strip() or None
        camera_name = (os.environ.get('BOSCH_CAMERA_NAME') or '').strip() or None
        camera_host = (os.environ.get('CAMERA_HOST') or '').strip() or None
        token = (os.environ.get('BOSCH_REFRESH_TOKEN') or '').strip() or None
        return cls(
            data_dir=data_dir,
            bootstrap_token=token,
            camera_id=camera_id,
            camera_name=camera_name,
            camera_host=camera_host,
            connection_type=connection_type,
            high_quality=_bool('HIGH_QUALITY', False),
            segment_minutes=_int('SEGMENT_MINUTES', 10),
            retention_days=_float('RETENTION_DAYS', 7.0, minimum=0.05),
            credential_refresh_seconds=_int('CREDENTIAL_REFRESH_SECONDS', 1200),
            stall_seconds=_int('STALL_SECONDS', 90),
            min_free_gb=_float('MIN_FREE_GB', 5.0),
            max_storage_gb=_float('MAX_STORAGE_GB', 0.0),
            health_port=_int('HEALTH_PORT', 80),
            log_level=os.environ.get('LOG_LEVEL', 'INFO').strip().upper() or 'INFO',
            mediamtx_binary=os.environ.get('MEDIAMTX_BINARY', 'mediamtx').strip() or 'mediamtx',
            web_password=(os.environ.get('WEB_PASSWORD') or '').strip() or None,
            mediamtx_api_addr=os.environ.get('MEDIAMTX_API_ADDR', '127.0.0.1:9997').strip(),
            mediamtx_hls_addr=os.environ.get('MEDIAMTX_HLS_ADDR', '127.0.0.1:8888').strip(),
            mediamtx_rtsp_addr=os.environ.get('MEDIAMTX_RTSP_ADDR', '127.0.0.1:8554').strip(),
        )
