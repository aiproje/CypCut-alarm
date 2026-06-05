"""Uygulama loglama kurulumu.

Tüm loglar hem konsola hem döngüsel dosyaya yazılır. Renkli konsol çıktısı
Windows cmd uyumluluğu için sade tutulmuştur.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_path: Path, level: str = "INFO") -> None:
    """Root logger'ı yapılandırır.

    - Konsola (INFO+)
    - Döngüsel dosyaya (5 dosya x 2 MB)
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("watchdog").setLevel(logging.WARNING)
    logging.getLogger("cv2").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Modül için isimlendirilmiş logger döner."""
    return logging.getLogger(name)


__all__ = ["setup_logging", "get_logger"]
