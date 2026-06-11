"""CypCut penceresi ekran görüntüsü alma modülü.

PIL (Pillow) ve pywin32 kullanarak CypCut laser切割控制系统 penceresini bulur
ve ekran görüntüsünü JPEG olarak kaydeder.

Pencere bulunamazsa tüm ekranın görüntüsünü alır.
"""
from __future__ import annotations

import ctypes
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..logging_setup import get_logger

logger = get_logger(__name__)


def _find_cypcut_window() -> Optional[int]:
    """CypCut penceresinin HWND değerini döner.

    Pencere başlığında 'CypCut' geçen ilk pencereyi arar.
    """
    try:
        import win32gui
    except ImportError:
        logger.warning("pywin32 kurulu değil, ekran görüntüsü alınamaz.")
        return None

    found_hwnd: Optional[int] = None

    def _enum_callback(hwnd: int, _: object) -> bool:
        nonlocal found_hwnd
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if "CypCut" in title:
            found_hwnd = hwnd
            return False
        return True

    try:
        win32gui.EnumWindows(_enum_callback, None)
    except Exception as exc:
        logger.warning("EnumWindows hatası: %s", exc)

    return found_hwnd


def _capture_window_pil(hwnd: int) -> Optional[Path]:
    """PIL.ImageGrab ile pencere görüntüsü alır."""
    from PIL import ImageGrab

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    height = bottom - top

    if width <= 0 or height <= 0:
        logger.warning("Pencere boyutları geçersiz: %dx%d", width, height)
        return None

    img = ImageGrab.grab(bbox=(left, top, right, bottom))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = Path(tempfile.gettempdir()) / f"cypcut_screen_{ts}.jpg"
    img.save(str(tmp_path), "JPEG", quality=95)

    if tmp_path.exists() and tmp_path.stat().st_size > 0:
        logger.info("Pencere görüntüsü alındı: %s (%dx%d)", tmp_path, width, height)
        return tmp_path
    return None


def _capture_window_win32(hwnd: int) -> Optional[Path]:
    """pywin32 + GDI ile pencere görüntüsü alır (Pillow yoksa fallback)."""
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

    result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)
    if result == 0:
        save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)

    bmp_info = bmp.GetInfo()
    bmp_bits = bmp.GetBitmapBits(True)

    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    stride = bmp_info["bmWidthBytes"]
    if stride % 4 != 0:
        stride += 4 - (stride % 4)

    from PIL import Image
    img = Image.frombuffer(
        "RGB",
        (width, height),
        bmp_bits,
        "raw",
        "BGRX",
        stride,
        -1,
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = Path(tempfile.gettempdir()) / f"cypcut_screen_{ts}.jpg"
    img.save(str(tmp_path), "JPEG", quality=95)

    if tmp_path.exists() and tmp_path.stat().st_size > 0:
        logger.info("Pencere görüntüsü alındı: %s (%dx%d)", tmp_path, width, height)
        return tmp_path
    return None


def _capture_window(hwnd: int) -> Optional[Path]:
    """Belirtilen pencerenin ekran görüntüsünü alır, JPEG olarak kaydeder."""
    try:
        return _capture_window_pil(hwnd)
    except Exception as exc:
        logger.warning("PIL pencere görüntüsü hatası: %s, win32 deneniyor.", exc)

    try:
        return _capture_window_win32(hwnd)
    except Exception as exc:
        logger.exception("Win32 pencere görüntüsü hatası: %s", exc)
        return None


def _capture_full_screen_pil() -> Optional[Path]:
    """PIL.ImageGrab ile tam ekran görüntüsü alır."""
    from PIL import ImageGrab

    img = ImageGrab.grab()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = Path(tempfile.gettempdir()) / f"cypcut_screen_{ts}.jpg"
    img.save(str(tmp_path), "JPEG", quality=95)

    if tmp_path.exists() and tmp_path.stat().st_size > 0:
        logger.info("Tam ekran görüntüsü alındı: %s", tmp_path)
        return tmp_path
    return None


def _capture_full_screen_win32() -> Optional[Path]:
    """pywin32 + GDI ile tam ekran görüntüsü alır (Pillow yoksa fallback)."""
    import win32gui
    import win32ui
    import win32con
    import win32api

    screen_width = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
    screen_height = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)

    hwnd_dc = win32gui.GetDC(0)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()

    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(mfc_dc, screen_width, screen_height)
    save_dc.SelectObject(bmp)

    save_dc.BitBlt(
        (0, 0), (screen_width, screen_height),
        mfc_dc, (0, 0), win32con.SRCCOPY,
    )

    bmp_info = bmp.GetInfo()
    bmp_bits = bmp.GetBitmapBits(True)

    win32gui.DeleteObject(bmp.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(0, hwnd_dc)

    stride = bmp_info["bmWidthBytes"]
    if stride % 4 != 0:
        stride += 4 - (stride % 4)

    from PIL import Image
    img = Image.frombuffer(
        "RGB",
        (screen_width, screen_height),
        bmp_bits,
        "raw",
        "BGRX",
        stride,
        -1,
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = Path(tempfile.gettempdir()) / f"cypcut_screen_{ts}.jpg"
    img.save(str(tmp_path), "JPEG", quality=95)

    if tmp_path.exists() and tmp_path.stat().st_size > 0:
        logger.info("Tam ekran görüntüsü alındı: %s (%dx%d)",
                    tmp_path, screen_width, screen_height)
        return tmp_path
    return None


def _capture_full_screen() -> Optional[Path]:
    """Tüm ekranın görüntüsünü alır, JPEG olarak kaydeder."""
    try:
        return _capture_full_screen_pil()
    except Exception as exc:
        logger.warning("PIL ekran görüntüsü hatası: %s, win32 deneniyor.", exc)

    try:
        return _capture_full_screen_win32()
    except Exception as exc:
        logger.exception("Win32 ekran görüntüsü hatası: %s", exc)
        return None


class ScreenCapture:
    """CypCut ekran görüntüsü alma servisi.

    Thread-safe: çoklu thread erişiminde lock kullanır.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._available = self._check_availability()

    def _check_availability(self) -> bool:
        """Gerekli kütüphanelerin yüklü olup olmadığını kontrol eder."""
        try:
            from PIL import ImageGrab
            return True
        except ImportError:
            pass
        try:
            import win32gui
            import win32ui
            return True
        except ImportError:
            logger.warning(
                "Pillow veya pywin32 kurulu değil. "
                "Ekran görüntüsü özelliği çalışmayacak. "
                "Kurulum: pip install Pillow pywin32"
            )
            return False

    @property
    def is_available(self) -> bool:
        return self._available

    def capture(self) -> Optional[Path]:
        """CypCut penceresinin veya tam ekranın görüntüsünü alır.

        Akış:
        1. CypCut penceresini ara
        2. Bulursa: sadece o pencerenin görüntüsünü al
        3. Bulamazsa: tüm ekranın görüntüsünü al

        Returns:
            JPEG dosya yolu veya None (hata durumunda)
        """
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
                logger.warning("Pencere görüntüsü alınamadı, tam ekran deneniyor.")

            return _capture_full_screen()


__all__ = ["ScreenCapture"]
