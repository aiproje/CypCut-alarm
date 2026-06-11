"""CypCut penceresi ekran görüntüsü alma modülü.

Windows GDI+ kullanarak CypCut laser切割控制系统 penceresini bulur
ve ekran görüntüsünü JPEG olarak kaydeder.

Pencere bulunamazsa tüm ekranın görüntüsünü alır.
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

# GDI+ token
_gdiplus_token = 0


def _init_gdiplus() -> None:
    """GDI+ kütüphanesini başlatır."""
    global _gdiplus_token
    if _gdiplus_token:
        return

    try:
        gdiplus = ctypes.WinDLL("gdiplus")
    except OSError:
        logger.warning("gdiplus.dll yüklenemedi.")
        return

    class GdiplusStartupInput(ctypes.Structure):
        _fields_ = [
            ("GdiplusVersion", wintypes.UINT),
            ("DebugEventCallback", ctypes.c_void_p),
            ("SuppressBackgroundThread", wintypes.BOOL),
            ("SuppressExternalCodecs", wintypes.BOOL),
        ]

    input_struct = GdiplusStartupInput()
    input_struct.GdiplusVersion = 1
    token = wintypes.UINT()

    status = gdiplus.GdiplusStartup(
        ctypes.byref(token), ctypes.byref(input_struct), None
    )
    if status == 0:
        _gdiplus_token = token.value
        logger.debug("GDI+ başlatıldı.")
    else:
        logger.warning("GDI+ başlatılamadı (status=%d).", status)


def _find_cypcut_window() -> Optional[int]:
    """CypCut penceresinin HWND değerini döner."""
    try:
        import win32gui
    except ImportError:
        logger.warning("pywin32 kurulu değil.")
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


def _capture_window_to_jpeg(hwnd: int) -> Optional[Path]:
    """GDI PrintWindow ile pencereyi yakalayıp GDI+ ile JPEG olarak kaydeder."""
    import win32gui
    import win32ui
    import win32con

    _init_gdiplus()

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

    # PrintWindow: 0=normal, 2=client only, 3=whole window (DWM composited)
    result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 3)
    if result == 0:
        save_dc.BitBlt((0, 0), (width, height), mfc_dc, (0, 0), win32con.SRCCOPY)

    # HBITMAP handle'ı al
    hbitmap = bmp.GetHandle()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = Path(tempfile.gettempdir()) / f"cypcut_screen_{ts}.jpg"
    saved = _save_hbitmap_as_jpeg(hbitmap, str(tmp_path))

    # Kaynakları temizle
    win32gui.DeleteObject(hbitmap)
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    if saved and tmp_path.exists() and tmp_path.stat().st_size > 0:
        logger.info("Pencere görüntüsü alındı: %s (%dx%d)", tmp_path, width, height)
        return tmp_path

    return None


def _capture_full_screen_to_jpeg() -> Optional[Path]:
    """GDI BitBlt ile tam ekranı yakalayıp GDI+ ile JPEG olarak kaydeder."""
    import win32gui
    import win32ui
    import win32con
    import win32api

    _init_gdiplus()

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

    hbitmap = bmp.GetHandle()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = Path(tempfile.gettempdir()) / f"cypcut_screen_{ts}.jpg"
    saved = _save_hbitmap_as_jpeg(hbitmap, str(tmp_path))

    win32gui.DeleteObject(hbitmap)
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(0, hwnd_dc)

    if saved and tmp_path.exists() and tmp_path.stat().st_size > 0:
        logger.info("Tam ekran görüntüsü alındı: %s (%dx%d)",
                    tmp_path, screen_width, screen_height)
        return tmp_path

    return None


def _save_hbitmap_as_jpeg(hbitmap: int, filepath: str) -> bool:
    """HBITMAP'i GDI+ ile JPEG olarak kaydeder."""
    if not _gdiplus_token:
        logger.warning("GDI+ başlatılmamış.")
        return False

    try:
        gdiplus = ctypes.WinDLL("gdiplus")

        # GDI+ bitmap oluştur
        bitmap_ptr = ctypes.c_void_p()
        status = gdiplus.GdipCreateBitmapFromHBITMAP(
            ctypes.c_void_p(hbitmap),
            None,
            ctypes.byref(bitmap_ptr),
        )
        if status != 0 or not bitmap_ptr:
            logger.warning("GdipCreateBitmapFromHBITMAP hatası: %d", status)
            return False

        # JPEG encoder bul
        clsid = ctypes.c_ubyte * 16
        encoder_clsid = clsid()
        num = wintypes.UINT(1)
        status = gdiplus.GdipGetImageEncoders(1, ctypes.byref(num), None)
        if status != 0:
            logger.warning("GdipGetImageEncoders boyut hatası: %d", status)
            return False

        encoder_size = wintypes.UINT(0)
        status = gdiplus.GdipGetImageEncoders(1, ctypes.byref(num), None)
        # Encoder listesini al
        encoder_size = num.value * 76 + 4  # tahmini boyut
        encoder_buf = ctypes.create_string_buffer(encoder_size)
        num = wintypes.UINT(1)
        status = gdiplus.GdipGetImageEncoders(
            1, ctypes.byref(num), encoder_buf
        )
        if status != 0:
            logger.warning("GdipGetImageEncoders hatası: %d", status)
            return False

        # JPEG encoder CLSID'ini bul
        # ImageCodecInfo yapısı: 16 byte CLSID + 20 byte Flags + ...
        # MIME type offset: 48 bytes into struct
        found = False
        for i in range(num.value):
            offset = i * 76  # her struct ~76 bytes
            mime_offset = offset + 48
            mime = ctypes.string_at(encoder_buf, encoder_size)[mime_offset:mime_offset + 40]
            if b"image/jpeg" in mime:
                encoder_clsid = clsid(*encoder_buf[offset:offset + 16])
                found = True
                break

        if not found:
            logger.warning("JPEG encoder bulunamadı.")
            return False

        # Encoder parametreleri: kalite = 95
        class EncoderParameter(ctypes.Structure):
            _fields_ = [
                ("Guid", ctypes.c_ubyte * 16),
                ("NumberOfValues", wintypes.ULONG),
                ("Type", wintypes.ULONG),
                ("Value", ctypes.c_void_p),
            ]

        class EncoderParameters(ctypes.Structure):
            _fields_ = [
                ("Count", wintypes.ULONG),
                ("Parameter", EncoderParameter * 1),
            ]

        quality = wintypes.UINT(95)
        params = EncoderParameters()
        params.Count = 1
        # JPEG Quality GUID: {1D5BE4B5-FA4A-452D-9CDD-5DB35105E7EB}
        quality_guid = (ctypes.c_ubyte * 16)(0xB5, 0xE4, 0x5B, 0x1D, 0x4A, 0xFA, 0x2D, 0x45, 0x9C, 0xDD, 0x5D, 0xB3, 0x51, 0x05, 0xE7, 0xEB)
        ctypes.memmove(params.Parameter[0].Guid, quality_guid, 16)
        params.Parameter[0].NumberOfValues = 1
        params.Parameter[0].Type = 4  # EncoderParameterValueTypeLong
        params.Parameter[0].Value = ctypes.addressof(ctypes.c_uint(95))

        # Unicode filepath
        filepath_w = filepath.encode("utf-16-le") + b"\x00\x00"
        filepath_ptr = ctypes.c_wchar_p(filepath)

        # JPEG olarak kaydet
        status = gdiplus.GdipSaveImageToFile(
            bitmap_ptr,
            filepath_ptr,
            ctypes.byref(encoder_clsid),
            ctypes.byref(params),
        )

        # Bitmap'i serbest bırak
        gdiplus.GdipDisposeImage(bitmap_ptr)

        if status != 0:
            logger.warning("GdipSaveImageToFile hatası: %d", status)
            return False

        return True

    except Exception as exc:
        logger.exception("JPEG kaydetme hatası: %s", exc)
        return False


def _capture_window(hwnd: int) -> Optional[Path]:
    """Belirtilen pencerenin ekran görüntüsünü alır."""
    try:
        return _capture_window_to_jpeg(hwnd)
    except Exception as exc:
        logger.exception("Pencere görüntüsü alma hatası: %s", exc)
        return None


def _capture_full_screen() -> Optional[Path]:
    """Tüm ekranın görüntüsünü alır."""
    try:
        return _capture_full_screen_to_jpeg()
    except Exception as exc:
        logger.exception("Tam ekran görüntüsü alma hatası: %s", exc)
        return None


class ScreenCapture:
    """CypCut ekran görüntüsü alma servisi."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._available = self._check_availability()

    def _check_availability(self) -> bool:
        try:
            import win32gui
            import win32ui
            return True
        except ImportError:
            logger.warning(
                "pywin32 kurulu değil. Ekran görüntüsü çalışmayacak. "
                "Kurulum: pip install pywin32"
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
                logger.warning("Pencere görüntüsü alınamadı, tam ekran deneniyor.")

            return _capture_full_screen()


__all__ = ["ScreenCapture"]
