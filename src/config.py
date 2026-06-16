"""Uygulama konfigürasyonu.

Tüm yapılandırma değerleri .env dosyasından okunur ve AppConfig dataclass'ında toplanır.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


def _get_env(key: str, default: str | None = None) -> str | None:
    value = os.getenv(key)
    if value is None or value == "":
        return default
    return value


def _get_env_int(key: str, default: int) -> int:
    raw = _get_env(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _get_env_float(key: str, default: float) -> float:
    raw = _get_env(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _get_env_bool(key: str, default: bool) -> bool:
    raw = _get_env(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path


@dataclass(frozen=True)
class AppConfig:
    """Tüm uygulama konfigürasyon değerleri."""

    machine_name: str
    log_dir: Path
    tail_poll_interval: float

    telegram_bot_token: str
    telegram_chat_id: str
    telegram_poll_interval: float
    dry_run: bool

    alarm_cooldown_seconds: int

    camera_index: int | None
    camera_scan_max_index: int
    video_duration: float

    db_path: Path
    app_log_path: Path
    app_log_level: str

    # Retry ayarları
    telegram_retry_check_interval: float
    log_watcher_retry_interval: float
    log_finder_scan_interval: float

    # OCR izleme ayarları
    ocr_monitor_interval: float
    ocr_log_path: Path

    # Stream server ayarları
    stream_enabled: bool
    stream_host: str
    stream_port: int
    stream_width: int
    stream_height: int
    stream_fps: int
    stream_quality: int

    # CypCut arkaplan tespit ayarları
    background_check_enabled: bool
    background_long_threshold_seconds: int

    @classmethod
    def load(cls, env_file: Path | None = None) -> "AppConfig":
        """`.env` dosyasını yükleyerek yapılandırmayı oluşturur."""
        env_path = env_file if env_file is not None else PROJECT_ROOT / ".env"
        if env_path.exists():
            load_dotenv(env_path, override=False)

        base = PROJECT_ROOT

        return cls(
            machine_name=_get_env("MACHINE_NAME", "Lazer-1") or "Lazer-1",
            log_dir=Path(
                _get_env(
                    "LOG_DIR",
                    r"C:\Program Files (x86)\Friendess\Share\fsdc\log\LogFiles",
                )
                or r"C:\Program Files (x86)\Friendess\Share\fsdc\log\LogFiles"
            ),
            tail_poll_interval=_get_env_float("TAIL_POLL_INTERVAL", 0.2),

            telegram_bot_token=_get_env("TELEGRAM_BOT_TOKEN", "") or "",
            telegram_chat_id=_get_env("TELEGRAM_CHAT_ID", "") or "",
            telegram_poll_interval=_get_env_float("TELEGRAM_POLL_INTERVAL", 2.0),
            dry_run=_get_env_bool("DRY_RUN", False),

            alarm_cooldown_seconds=_get_env_int("ALARM_COOLDOWN_SECONDS", 300),

            camera_index=(
                _get_env_int("CAMERA_INDEX", -1)
                if _get_env("CAMERA_INDEX") not in (None, "")
                else None
            ),
            camera_scan_max_index=_get_env_int("CAMERA_SCAN_MAX_INDEX", 4),
            video_duration=_get_env_float("VIDEO_DURATION", 5.0),

            db_path=_resolve_path(
                _get_env("DB_PATH", "data/cypcut_monitor.db") or "data/cypcut_monitor.db",
                base,
            ),
            app_log_path=_resolve_path(
                _get_env("APP_LOG_PATH", "logs/application.log") or "logs/application.log",
                base,
            ),
            app_log_level=(_get_env("APP_LOG_LEVEL", "INFO") or "INFO").upper(),

            telegram_retry_check_interval=_get_env_float("TELEGRAM_RETRY_CHECK_INTERVAL", 5.0),
            log_watcher_retry_interval=_get_env_float("LOG_WATCHER_RETRY_INTERVAL", 10.0),
            log_finder_scan_interval=_get_env_float("LOG_FINDER_SCAN_INTERVAL", 5.0),

            ocr_monitor_interval=_get_env_float("OCR_MONITOR_INTERVAL", 5.0),
            ocr_log_path=_resolve_path(
                _get_env("OCR_LOG_PATH", "logs/ocr_data.txt") or "logs/ocr_data.txt",
                base,
            ),
            stream_enabled=_get_env_bool("STREAM_ENABLED", True),
            stream_host=_get_env("STREAM_HOST", "") or "",
            stream_port=_get_env_int("STREAM_PORT", 2373),
            stream_width=_get_env_int("STREAM_WIDTH", 640),
            stream_height=_get_env_int("STREAM_HEIGHT", 480),
            stream_fps=_get_env_int("STREAM_FPS", 10),
            stream_quality=_get_env_int("STREAM_QUALITY", 70),
            background_check_enabled=_get_env_bool("BACKGROUND_CHECK_ENABLED", True),
            background_long_threshold_seconds=_get_env_int("BACKGROUND_LONG_THRESHOLD_SECONDS", 300),
        )

    def validate(self) -> list[str]:
        """Minimum doğrulama. Eksik/hatalı alanları liste olarak döner."""
        errors: list[str] = []
        if not self.telegram_bot_token or self.telegram_bot_token == "PUT_YOUR_BOT_TOKEN_HERE":
            errors.append("TELEGRAM_BOT_TOKEN tanımlı değil.")
        if not self.telegram_chat_id or self.telegram_chat_id == "PUT_YOUR_CHAT_ID_HERE":
            errors.append("TELEGRAM_CHAT_ID tanımlı değil.")
        if self.alarm_cooldown_seconds < 0:
            errors.append("ALARM_COOLDOWN_SECONDS negatif olamaz.")
        if not self.log_dir:
            errors.append("LOG_DIR tanımlı değil.")
        if self.video_duration < 1.0:
            errors.append("VIDEO_DURATION 1 saniyeden az olamaz.")
        if self.stream_port < 1 or self.stream_port > 65535:
            errors.append("STREAM_PORT 1-65535 aralığında olmalı.")
        if self.stream_width < 160 or self.stream_width > 1920:
            errors.append("STREAM_WIDTH 160-1920 aralığında olmalı.")
        if self.stream_height < 120 or self.stream_height > 1080:
            errors.append("STREAM_HEIGHT 120-1080 aralığında olmalı.")
        if self.stream_fps < 1 or self.stream_fps > 30:
            errors.append("STREAM_FPS 1-30 aralığında olmalı.")
        if self.stream_quality < 10 or self.stream_quality > 100:
            errors.append("STREAM_QUALITY 10-100 aralığında olmalı.")
        if self.background_long_threshold_seconds < 60:
            errors.append("BACKGROUND_LONG_THRESHOLD_SECONDS 60 saniyeden az olamaz.")
        return errors


__all__ = ["AppConfig", "PROJECT_ROOT"]
