"""CypCut log dosyalarını bulur ve en güncelini seçer.

Güncelleme mantığı:
  1. Dosya adına göre sıralama (alfabetik = tarihsel bu formatta)
  2. mtime (değiştirme tarihi) ile çapraz doğrulama
  3. Periyodik tarama ile yeni dosyaları yakalama
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

# CypCut-20260605083823-5408-1.rtf
_CYPCUT_PATTERN = re.compile(r"^CypCut-\d{14}-\d+-\d+\.rtf$", re.IGNORECASE)


def is_cypcut_log(name: str) -> bool:
    """Verilen dosya adı CypCut log mu?"""
    return bool(_CYPCUT_PATTERN.match(name))


def list_logs(log_dir: Path) -> list[Path]:
    """log_dir içindeki tüm CypCut log dosyalarını döner.

    Sıralama: önce dosya adına göre, eşitlik olursa mtime'a göre.
    """
    if not log_dir.exists() or not log_dir.is_dir():
        return []
    files = [p for p in log_dir.iterdir() if p.is_file() and is_cypcut_log(p.name)]
    files.sort(key=lambda p: (p.name, _safe_mtime(p)))
    return files


def find_latest_log(log_dir: Path) -> Optional[Path]:
    """En yeni CypCut log dosyasını döner (yoksa None).

    Sadece isim sıralaması yetmez, mtime ile de doğrular.
    """
    files = list_logs(log_dir)
    if not files:
        return None
    # İsim sıralaması birincil, mtime ikincil
    return files[-1]


def find_latest_log_by_mtime(log_dir: Path) -> Optional[Path]:
    """En son değiştirilen CypCut log dosyasını döner (mtime bazlı)."""
    if not log_dir.exists() or not log_dir.is_dir():
        return None
    files = [p for p in log_dir.iterdir() if p.is_file() and is_cypcut_log(p.name)]
    if not files:
        return None
    files.sort(key=lambda p: _safe_mtime(p), reverse=True)
    return files[0]


def _safe_mtime(p: Path) -> float:
    """Güvenli mtime okuması. Hata olursa 0 döner."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


class LogFinder:
    """Periyodik olarak log dizinini tarayan ve yeni dosya bulduğunda
    callback tetikleyen servis.

    watchdog'un yetersiz kaldığı Windows ortamlarda fallback olarak kullanılır.
    """

    def __init__(
        self,
        log_dir: Path,
        scan_interval: float = 5.0,
        on_new_file: Optional[Callable[[Path], None]] = None,
    ) -> None:
        self._log_dir = log_dir
        self._scan_interval = max(1.0, scan_interval)
        self._on_new_file = on_new_file
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_known: Optional[Path] = None

    def start(self) -> None:
        """Tarama thread'ini başlatır."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        # İlk en son dosyayı kaydet
        latest = find_latest_log(self._log_dir)
        self._last_known = latest
        self._thread = threading.Thread(
            target=self._scan_loop, name="LogFinder", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Tarama döngüsünü durdurur."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)

    @property
    def last_known(self) -> Optional[Path]:
        return self._last_known

    def _scan_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._scan_once()
            except Exception:
                pass
            self._stop_event.wait(self._scan_interval)

    def _scan_once(self) -> None:
        latest = find_latest_log(self._log_dir)
        if latest is None:
            return
        if self._last_known is None or latest != self._last_known:
            self._last_known = latest
            if self._on_new_file is not None:
                try:
                    self._on_new_file(latest)
                except Exception:
                    pass

    def force_scan(self) -> Optional[Path]:
        """Zorunlu tarama yapar, yeni dosya varsa döner."""
        latest = find_latest_log(self._log_dir)
        if latest is not None and (self._last_known is None or latest != self._last_known):
            self._last_known = latest
            if self._on_new_file is not None:
                try:
                    self._on_new_file(latest)
                except Exception:
                    pass
        return latest


__all__ = [
    "is_cypcut_log",
    "list_logs",
    "find_latest_log",
    "find_latest_log_by_mtime",
    "LogFinder",
]
