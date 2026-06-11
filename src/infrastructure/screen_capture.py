"""CypCut penceresi ekran görüntüsü alma modülü.

PrintWindow API ile CypCut laser切割控制系统 penceresini bulur
ve ekran görüntüsünü JPEG olarak kaydeder.

Arka plandaki pencereleri bile yakalayabilir (minimize hariç).
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..logging_setup import get_logger

logger = get_logger(__name__)


def _find_cypcut_window() -> Optional[int]:
    """CypCut penceresinin HWND değerini döner."""
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

        try:
            cls = win32gui.GetClassName(hwnd)
        except Exception:
            cls = "?"

        all_windows.append(f"  [{hwnd}] cls={cls} | {title}")

        # CypCut pencere başlığını veya sınıf adını kontrol et
        is_cypcut = (
            "激光" in title
            or "CypCut" in title
            or "cypcut" in cls.lower()
        )
        if is_cypcut:
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

    # Debug: tüm pencereleri logla
    logger.info("=== Görünür Pencereler (%d) ===", len(all_windows))
    for w in all_windows:
        logger.info(w)
    logger.info("=== Son ===")

    return found_hwnd


def _is_minimized(hwnd: int) -> bool:
    """Pencere minimize edilmiş mi kontrol eder."""
    import win32gui
    return win32gui.IsIconic(hwnd)


def _capture_with_printwindow(hwnd: int) -> Optional[Path]:
    """PrintWindow ile pencere görüntüsü alır.

    Bu method arka plandaki pencereleri bile yakalayabilir.
    Pencere minimize edilmemiş olmalı.
    """
    import win32gui
    import win32ui
    import win32con

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        logger.warning("Pencere boyutları geçersiz: %dx%d", width, height)
        return None

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()

    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, width, height)
    save_dc.SelectObject(bmp)

    # PrintWindow: 3 = PW_RENDERFULLCONTENT (DWM composited pencereler için)
    result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)
    if result == 0:
        # Fallback: normal PrintWindow
        result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)
    if result == 0:
        # Son fallback: BitBlt (sadece görünür pencereler)
        save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)

    bmp_bits = bmp.GetBitmapBits(True)
    bmp_info = bmp.GetInfo()

    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    # Bitmap verisini PIL ile JPEG'e çevir
    from PIL import Image

    bpp = bmp_info.get("bmBitsPixel", 32)
    if bpp == 32:
        # BGRA -> RGB: numpy ile düzgün dönüşüm
        import numpy as np
        raw = np.frombuffer(bmp_bits, dtype=np.uint8).reshape((height, width, 4))
        rgb = raw[:, :, :3][:, :, ::-1]  # BGR -> RGB
        img = Image.fromarray(rgb, "RGB")
    else:
        img = Image.frombuffer(
            "RGB",
            (width, height),
            bmp_bits,
            "raw",
            "BGR",
            bmp_info.get("bmWidthBytes", width * 3),
        )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = Path(tempfile.gettempdir()) / f"cypcut_screen_{ts}.jpg"
    img.save(str(tmp_path), "JPEG", quality=95)

    if tmp_path.exists() and tmp_path.stat().st_size > 0:
        logger.info("PrintWindow ile görüntü alındı: %s (%dx%d)", tmp_path, width, height)
        return tmp_path

    return None


def _capture_with_imagegrab(hwnd: int) -> Optional[Path]:
    """PIL.ImageGrab ile pencere görüntüsü alır (sadece görünür pencereler)."""
    from PIL import ImageGrab

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        return None

    img = ImageGrab.grab(bbox=(left, top, right, bottom))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = Path(tempfile.gettempdir()) / f"cypcut_screen_{ts}.jpg"
    img.save(str(tmp_path), "JPEG", quality=95)

    if tmp_path.exists() and tmp_path.stat().st_size > 0:
        logger.info("ImageGrab ile görüntü alındı: %s (%dx%d)", tmp_path, width, height)
        return tmp_path

    return None


def _capture_window(hwnd: int) -> Optional[Path]:
    """Pencere görüntüsü alır. Önce PrintWindow dener, sonra ImageGrab."""
    import win32gui

    title = win32gui.GetWindowText(hwnd)
    minimized = _is_minimized(hwnd)

    # Pencere durumunu logla
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    logger.info("Pencere durumu: başlık='%s', minimize=%s, boyut=%dx%d, "
                "konum=(%d,%d)-(%d,%d)",
                title, minimized, right - left, bottom - top,
                left, top, right, bottom)

    if minimized:
        logger.warning("Pencere minimize edilmiş, yakalanamaz. Önce büyütmek gerekiyor.")
        return None

    # PrintWindow dene (arka plan pencereleri için)
    result = _capture_with_printwindow(hwnd)
    if result is not None:
        return result

    # ImageGrab dene (görünür pencereler için)
    try:
        result = _capture_with_imagegrab(hwnd)
        if result is not None:
            return result
    except Exception as exc:
        logger.warning("ImageGrab hatası: %s", exc)

    return None


def _capture_full_screen() -> Optional[Path]:
    """Tüm ekranın görüntüsünü alır."""
    import win32gui
    from PIL import ImageGrab

    img = ImageGrab.grab()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = Path(tempfile.gettempdir()) / f"cypcut_screen_{ts}.jpg"
    img.save(str(tmp_path), "JPEG", quality=95)

    if tmp_path.exists() and tmp_path.stat().st_size > 0:
        logger.info("Tam ekran görüntüsü alındı: %s", tmp_path)
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
                result = _capture_window(hwnd)
                if result is not None:
                    return result
                logger.warning("Pencere görüntülenemed tam ekran alınıyor.")

            return _capture_full_screen()


__all__ = ["ScreenCapture"]
