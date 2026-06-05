"""Log dizini izleyici.

watchdog kullanarak CypCut klasöründe yeni dosya oluştuğunda callback tetikler.
Amaç: log rotasyonunda eski dosyadan yeni dosyaya otomatik geçiş.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ..logging_setup import get_logger
from .log_finder import is_cypcut_log

logger = get_logger(__name__)


class _Handler(FileSystemEventHandler):
    def __init__(self, on_new_file: Callable[[Path], None]) -> None:
        self._on_new_file = on_new_file

    def on_created(self, event) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(getattr(event, "dest_path", None) or event.src_path)
        if not is_cypcut_log(path.name):
            return
        logger.info("Yeni CypCut log dosyası tespit edildi: %s", path)
        try:
            self._on_new_file(path)
        except Exception as exc:
            logger.exception("on_new_file callback hatası: %s", exc)


class LogDirectoryWatcher:
    """watchdog Observer sarmalayıcı."""

    def __init__(self, directory: Path, on_new_file: Callable[[Path], None]) -> None:
        self._directory = directory
        self._on_new_file = on_new_file
        self._observer: Optional[Observer] = None

    def start(self) -> None:
        if self._observer is not None:
            return
        if not self._directory.exists():
            logger.warning("Log dizini mevcut değil, izleme başlatılamadı: %s", self._directory)
            return
        handler = _Handler(self._on_new_file)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._directory), recursive=False)
        self._observer.daemon = True
        self._observer.start()
        logger.info("Log dizini izleniyor: %s", self._directory)

    def stop(self, timeout: float = 2.0) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=timeout)
        except Exception as exc:
            logger.warning("Watcher durdurma hatası: %s", exc)
        finally:
            self._observer = None


__all__ = ["LogDirectoryWatcher"]
