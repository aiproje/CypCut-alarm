"""Uygulama giriş noktası."""
from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import AppConfig
from src.logging_setup import get_logger, setup_logging
from src.services.monitor_service import MonitorLoop, MonitorService


def main() -> int:
    config = AppConfig.load()
    setup_logging(config.app_log_path, level=config.app_log_level)
    logger = get_logger("main")

    errors = config.validate()
    if errors:
        for err in errors:
            logger.error("Konfigürasyon hatası: %s", err)
        return 2

    service = MonitorService(config)
    loop = MonitorLoop(service, poll_interval=config.tail_poll_interval)
    loop.start()

    try:
        service.run()
    except Exception as exc:
        logger.exception("Beklenmeyen hata: %s", exc)
        return 1
    finally:
        loop.stop()
        loop.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
