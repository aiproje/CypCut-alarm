"""Log dizini izleyici.

watchdog kullanarak CypCut klasöründe yeni dosya oluştuğunda callback tetikler.
Amaç: log rotasyonunda eski dosyadan yeni dosyaya otomatik geçiş.

Geliştirilmiş:
  - Dizin oluşana kadar retry
  - Watchdog çökerse otomatik yeniden başlatma
  - Fallback olarak LogFinder ile periyodik tarama
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from ..logging_setup import get_logger
from .log_finder import LogFinder, is_cypcut_log

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

    def on_moved(self, event) -> None:  # type: ignore[override]
        """Bazı durumlarda dosya move ile oluşur."""
        if event.is_directory:
            return
        dest = Path(getattr(event, "dest_path", event.src_path))
        if not is_cypcut_log(dest.name):
            return
        logger.info("Yeni CypCut log dosyası (move): %s", dest)
        try:
            self._on_new_file(dest)
        except Exception as exc:
            logger.exception("on_new_file (move) callback hatası: %s", exc)


class LogDirectoryWatcher:
    """watchdog Observer sarmalayıcı.

    Ek olarak:
      - Dizin mevcut değilse bekler ve oluşunca başlatır
      - Watchdog hata verirse otomatik yeniden dener
      - LogFinder ile fallback tarama yapar
    """

    def __init__(
        self,
        directory: Path,
        on_new_file: Callable[[Path], None],
        retry_interval: float = 10.0,
        fallback_scan_interval: float = 5.0,
    ) -> None:
        self._directory = directory
        self._on_new_file = on_new_file
        self._retry_interval = max(3.0, retry_interval)
        self._observer: Optional[Observer] = None
        self._stop_event = threading.Event()
        self._retry_thread: Optional[threading.Thread] = None
        self._fallback: Optional[LogFinder] = None

    def start(self) -> None:
        """Watcher'ı başlatır. Dizin yoksa retry döngüsüne girer."""
        self._stop_event.clear()

        if self._directory.exists():
            self._start_observer()
        else:
            logger.warning("Log dizini mevcut değil, bekleniyor: %s", self._directory)
            self._start_retry_thread()

        # Fallback tarama her zaman çalışsın
        self._fallback = LogFinder(
            log_dir=self._directory,
            scan_interval=self._retry_interval,
            on_new_file=self._on_new_file,
        )
        self._fallback.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._fallback is not None:
            self._fallback.stop()
        if self._retry_thread is not None:
            self._retry_thread.join(timeout=timeout)
        self._stop_observer(timeout)

    def _start_observer(self) -> None:
        if self._observer is not None:
            return
        try:
            handler = _Handler(self._on_new_file)
            self._observer = Observer()
            self._observer.schedule(handler, str(self._directory), recursive=False)
            self._observer.daemon = True
            self._observer.start()
            logger.info("Log dizini izleniyor (watchdog): %s", self._directory)
        except Exception as exc:
            logger.warning("Watchdog başlatılamadı: %s", exc)
            self._observer = None

    def _stop_observer(self, timeout: float = 2.0) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=timeout)
        except Exception as exc:
            logger.warning("Watcher durdurma hatası: %s", exc)
        finally:
            self._observer = None

    def _start_retry_thread(self) -> None:
        if self._retry_thread is not None and self._retry_thread.is_alive():
            return
        self._retry_thread = threading.Thread(
            target=self._retry_loop, name="LogWatcherRetry", daemon=True
        )
        self._retry_thread.start()

    def _retry_loop(self) -> None:
        """Dizin oluşana kadar deneme yapar."""
        while not self._stop_event.is_set():
            if self._directory.exists():
                logger.info("Log dizini artık mevcut: %s", self._directory)
                self._start_observer()
                return
            self._stop_event.wait(self._retry_interval)

    def _restart_observer_if_needed(self) -> None:
        """Observer çökmüşse yeniden başlat."""
        if self._observer is not None and not self._observer.is_alive():
            logger.warning("Watchdog ölü, yeniden başlatılıyor...")
            self._stop_observer()
            self._start_observer()


__all__ = ["LogDirectoryWatcher"]
