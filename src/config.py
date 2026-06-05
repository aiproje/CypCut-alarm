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

    db_path: Path
    app_log_path: Path
    app_log_level: str

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

            db_path=_resolve_path(
                _get_env("DB_PATH", "data/cypcut_monitor.db") or "data/cypcut_monitor.db",
                base,
            ),
            app_log_path=_resolve_path(
                _get_env("APP_LOG_PATH", "logs/application.log") or "logs/application.log",
                base,
            ),
            app_log_level=(_get_env("APP_LOG_LEVEL", "INFO") or "INFO").upper(),
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
        return errors


__all__ = ["AppConfig", "PROJECT_ROOT"]
