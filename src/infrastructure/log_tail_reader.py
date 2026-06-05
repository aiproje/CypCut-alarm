"""Tail modunda log okuyucu.

Davranış:
- Dosyayı son byte'ından itibaren takip eder (tail -F).
- Yeni satırlar callback'e verilir.
- Dosya boyutu küçülürse (rotation) sıfırdan başlar.
- Callback thread-safe olmalıdır; okuyucu kendi thread'inde çalışır.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from ..logging_setup import get_logger

logger = get_logger(__name__)


class LogTailReader:
    """Belirli bir log dosyasını tail modunda izler."""

    def __init__(
        self,
        log_path: Path,
        on_line: Callable[[str], None],
        poll_interval: float = 0.2,
    ) -> None:
        self._log_path = log_path
        self._on_line = on_line
        self._poll_interval = max(0.05, poll_interval)

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._current_path: Optional[Path] = None
        self._fh = None
        self._position: int = 0
        self._partial: str = ""

    @property
    def log_path(self) -> Path:
        return self._log_path

    def switch_file(self, new_path: Path) -> None:
        """Aktif dosyayı değiştirir. Mevcut tampon kapatılır, yeni dosya baştan açılır."""
        logger.info("Log dosyası değiştiriliyor: %s -> %s", self._log_path, new_path)
        self._log_path = new_path
        self._close_handle()
        self._current_path = None
        self._position = 0
        self._partial = ""

    def stop(self) -> None:
        """Okuma döngüsünü durdurur."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._close_handle()

    def start(self) -> None:
        """Arka plan thread'ini başlatır."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="LogTailReader", daemon=True
        )
        self._thread.start()

    def _close_handle(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def _ensure_handle(self) -> bool:
        """Dosya handle'ı gerekirse açar. Dosya henüz yoksa False döner."""
        if self._current_path == self._log_path and self._fh is not None:
            return True
        self._close_handle()
        if not self._log_path.exists():
            return False
        try:
            self._fh = self._log_path.open("rb")
            self._current_path = self._log_path
            if self._position > 0:
                self._fh.seek(self._position)
            return True
        except OSError as exc:
            logger.warning("Log dosyası açılamadı (%s): %s", self._log_path, exc)
            return False

    def _run(self) -> None:
        logger.info("Tail okuyucu başlatıldı: %s", self._log_path)
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                logger.exception("Tail tick hatası: %s", exc)
            self._stop_event.wait(self._poll_interval)
        logger.info("Tail okuyucu durduruldu.")

    def _tick(self) -> None:
        if not self._ensure_handle():
            return

        try:
            current_size = self._log_path.stat().st_size
        except OSError:
            return

        if current_size < self._position:
            logger.info("Log dosyası truncate/rotasyon algılandı, sıfırdan başlanıyor.")
            self._close_handle()
            self._position = 0
            self._partial = ""
            if not self._ensure_handle():
                return
            current_size = self._log_path.stat().st_size

        if current_size == self._position:
            return

        chunk_size = max(1024, min(8192, current_size - self._position))
        try:
            raw = self._fh.read(chunk_size) if self._fh else b""
        except OSError as exc:
            logger.warning("Log okuma hatası: %s", exc)
            self._close_handle()
            return

        if not raw:
            return

        self._position += len(raw)
        try:
            data = raw.decode("utf-8", errors="replace")
        except Exception:
            data = raw.decode("cp1254", errors="replace")

        data = data.replace("\\par", "\n").replace("\\\\par", "\n")

        buffer = self._partial + data

        if "\n" in buffer:
            lines = buffer.split("\n")
            self._partial = lines[-1]
            for line in lines[:-1]:
                cleaned = line.rstrip("\r")
                if cleaned.strip():
                    try:
                        self._on_line(cleaned)
                    except Exception as exc:
                        logger.exception("on_line callback hatası: %s", exc)
        else:
            self._partial = buffer


__all__ = ["LogTailReader"]
