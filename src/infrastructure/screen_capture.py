"""CypCut penceresi ekran görüntüsü alma modülü.

PIL (Pillow) ImageGrab kullanarak CypCut laser切割控制系统 penceresini bulur
ve ekran görüntüsünü JPEG olarak kaydeder.

Pencere bulunamazsa tüm ekranın görüntüsünü alır.
"""
from __future__ import annotations

import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..logging_setup import get_logger

logger = get_logger(__name__)


def _find_cypcut_window() -> Optional[int]:
    """CypCut penceresinin HWND değerini döner.

    Başlıkta 'CypCut激光' veya 'CypCut' ile başlayıp '激光' içeren pencereleri eşleştirir.
    Tarayıcı, editör, dosya gezgini pencerelerini atlar.
    """
    try:
        import win32gui
    except ImportError:
        logger.warning("pywin32 kurulu değil.")
        return None

    found_hwnd: Optional[int] = None
    all_windows: list[str] = []

    def _enum_callback(hwnd: int, _: object) -> bool:
        nonlocal found_hwnd
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True

        # Tüm pencereleri logla (debug)
        try:
            cls = win32gui.GetClassName(hwnd)
        except Exception:
            cls = "?"
        all_windows.append(f"  [{hwnd}] cls={cls} | {title}")

        # CypCut pencere başlığını kontrol et
        if "激光" in title:
            skip_words = ["Edge", "Chrome", "Firefox", "Visual Studio", "Notepad",
                          "VS Code", "Sublime", "GitHub", "Explorer", "cmd",
                          "Terminal", "PowerShell", "python", "Stack"]
            for sw in skip_words:
                if sw.lower() in title.lower():
                    return True
            found_hwnd = hwnd
            return False

        return True

    try:
        win32gui.EnumWindows(_enum_callback, None)
    except Exception as exc:
        logger.warning("EnumWindows hatası: %s", exc)

    # Tüm pencereleri logla
    logger.info("=== Tüm Görünür Pencereler (%d) ===", len(all_windows))
    for w in all_windows:
        logger.info(w)
    logger.info("=== Pencere Listesi Sonu ===")

    if found_hwnd is not None:
        logger.info("Eşleşen CypCut penceresi: HWND=%s", found_hwnd)
    else:
        logger.info("CypCut penceresi bulunamadı.")

    return found_hwnd


def _get_window_rect(hwnd: int) -> Optional[tuple[int, int, int, int]]:
    """Pencere koordinatlarını alır, DPI aware olacak şekilde düzeltir."""
    import win32gui

    try:
        # DPI-aware koordinatlar için
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        pass

    rect = win32gui.GetWindowRect(hwnd)
    return rect


def _capture_with_pil(hwnd: Optional[int]) -> Optional[Path]:
    """PIL.ImageGrab ile ekran görüntüsü alır."""
    from PIL import ImageGrab

    if hwnd is not None:
        # Pencere koordinatlarını al
        left, top, right, bottom = _get_window_rect(hwnd)
        width = right - left
        height = bottom - top

        if width <= 0 or height <= 0:
            logger.warning("Pencere boyutları geçersiz: %dx%d", width, height)
            return None

        img = ImageGrab.grab(bbox=(left, top, right, bottom))
    else:
        img = ImageGrab.grab()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = Path(tempfile.gettempdir()) / f"cypcut_screen_{ts}.jpg"
    img.save(str(tmp_path), "JPEG", quality=95)

    if tmp_path.exists() and tmp_path.stat().st_size > 0:
        w, h = img.size
        logger.info("Ekran görüntüsü alındı: %s (%dx%d)", tmp_path, w, h)
        return tmp_path

    return None


class ScreenCapture:
    """CypCut ekran görüntüsü alma servisi."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._available = self._check_availability()

    def _check_availability(self) -> bool:
        try:
            from PIL import ImageGrab
            import win32gui
            return True
        except ImportError:
            logger.warning(
                "Pillow veya pywin32 kurulu değil. "
                "Ekran görüntüsü çalışmayacak. "
                "Kurulum: pip install Pillow pywin32"
            )
            return False

    @property
    def is_available(self) -> bool:
        return self._available

    def capture(self) -> Optional[Path]:
        if not self._available:
            return None

        with self._lock:
            hwnd = _find_cypcut_window()
            if hwnd is not None:
                title = ""
                try:
                    import win32gui
                    title = win32gui.GetWindowText(hwnd)
                except Exception:
                    pass
                logger.info("CypCut penceresi bulundu (HWND: %s, başlık: %s)", hwnd, title)
            else:
                logger.info("CypCut penceresi bulunamadı, tam ekran alınıyor.")

            try:
                return _capture_with_pil(hwnd)
            except Exception as exc:
                logger.exception("Ekran görüntüsü alma hatası: %s", exc)
                return None


__all__ = ["ScreenCapture"]
