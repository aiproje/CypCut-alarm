"""CypCut penceresi ekran görüntüsü alma modülü.

Windows GDI+ kullanarak CypCut laser切割控制系统 penceresini bulur
ve ekran görüntüsünü JPEG olarak kaydeder.

Pencere bulunamazsa tüm ekranın görüntüsünü alır.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import struct
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..logging_setup import get_logger

logger = get_logger(__name__)

# GDI+ token
_gdiplus_token = 0
_gdiplus_lib = None
_gdiplus_jpeg_clsid: Optional[bytes] = None


def _init_gdiplus() -> bool:
    """GDI+ kütüphanesini başlatır ve JPEG encoder CLSID'ini bulur."""
    global _gdiplus_token, _gdiplus_lib, _gdiplus_jpeg_clsid

    if _gdiplus_token:
        return True

    try:
        _gdiplus_lib = ctypes.WinDLL("gdiplus")
    except OSError:
        logger.warning("gdiplus.dll yüklenemedi.")
        return False

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

    status = _gdiplus_lib.GdiplusStartup(
        ctypes.byref(token), ctypes.byref(input_struct), None
    )
    if status != 0:
        logger.warning("GDI+ başlatılamadı (status=%d).", status)
        return False

    _gdiplus_token = token.value

    # JPEG encoder CLSID'ini bul
    _find_jpeg_encoder()
    return True


def _find_jpeg_encoder() -> None:
    """JPEG encoder CLSID'ini GDI+ API ile bulur."""
    global _gdiplus_jpeg_clsid

    num = wintypes.UINT(0)
    # İlk çağrı: boyutu öğren
    status = _gdiplus_lib.GdipGetImageEncoders(
        ctypes.byref(num), ctypes.byref(wintypes.UINT(0)), None
    )
    if status != 0 or num.value == 0:
        # GDI+ 1.1+ gerektirir, alternatif: bilinen CLSID kullan
        # JPEG Encoder CLSID: {557CF401-1A04-11D3-9A73-0090273FC1FD}
        _gdiplus_jpeg_clsid = bytes([
            0x01, 0xF4, 0x7C, 0x55, 0x04, 0x1A, 0xD3, 0x11,
            0x9A, 0x73, 0x00, 0x90, 0x27, 0x3F, 0xC1, 0xFD,
        ])
        logger.debug("JPEG encoder CLSID sabit değer kullanıldı.")
        return

    # Struct boyutunu hesapla (WIN32 + MIME string + ... = ~120 bytes)
    struct_size = 120
    buf_size = num.value * struct_size
    buf = ctypes.create_string_buffer(buf_size)

    actual_num = wintypes.UINT(num.value)
    actual_size = wintypes.UINT(buf_size)
    status = _gdiplus_lib.GdipGetImageEncoders(
        ctypes.byref(actual_num), ctypes.byref(actual_size), buf
    )
    if status != 0:
        _gdiplus_jpeg_clsid = bytes([
            0x01, 0xF4, 0x7C, 0x55, 0x04, 0x1A, 0xD3, 0x11,
            0x9A, 0x73, 0x00, 0x90, 0x27, 0x3F, 0xC1, 0xFD,
        ])
        logger.debug("JPEG encoder CLSID sabit değer kullanıldı (fallback).")
        return

    raw = buf.raw
    for i in range(actual_num.value):
        offset = i * struct_size
        # Her ImageCodecInfo yapısında:
        # offset+0: Clsid (16 bytes)
        # offset+48: MimeType ptr (wchar_t*)
        # 64-bit sistemde ptr 8 byte
        mime_ptr = struct.unpack_from("<Q", raw, offset + 48)[0]
        if mime_ptr:
            try:
                mime_str = ctypes.c_wchar_p(mime_ptr).value or ""
            except Exception:
                continue
            if "image/jpeg" in mime_str.lower():
                _gdiplus_jpeg_clsid = raw[offset:offset + 16]
                logger.debug("JPEG encoder bulundu (index=%d).", i)
                return

    # Bulunamadıysa sabit CLSID kullan
    _gdiplus_jpeg_clsid = bytes([
        0x01, 0xF4, 0x7C, 0x55, 0x04, 0x1A, 0xD3, 0x11,
        0x9A, 0x73, 0x00, 0x90, 0x27, 0x3F, 0xC1, 0xFD,
    ])
    logger.debug("JPEG encoder CLSID sabit değer kullanıldı (fallback).")


def _find_cypcut_window() -> Optional[int]:
    """CypCut penceresinin HWND değerini döner.

    Sadece başlığı tam olarak 'CypCut' ile başlayan veya
    'CypCut激光切割控制系统' içeren pencereleri eşleştirir.
    Tarayıcı ve editör pencerelerini atlar.
    """
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
        if not title:
            return True

        # Tarayıcı/edithor pencerelerini atla
        skip_words = ["Edge", "Chrome", "Firefox", "Visual Studio", "Notepad++",
                       "VS Code", "Sublime", "GitHub", "Stack", "python",
                       "Terminal", "PowerShell", "cmd"]
        for sw in skip_words:
            if sw.lower() in title.lower():
                return True

        # CypCut ile başlayan veya CypCut激光切割控制系统 içeren pencere
        if title.startswith("CypCut") or "CypCut激光" in title:
            found_hwnd = hwnd
            return False
        return True

    try:
        win32gui.EnumWindows(_enum_callback, None)
    except Exception as exc:
        logger.warning("EnumWindows hatası: %s", exc)

    return found_hwnd


def _save_hbitmap_as_jpeg(hbitmap: int, filepath: str) -> bool:
    """HBITMAP'i GDI+ ile JPEG olarak kaydeder."""
    if not _gdiplus_lib or not _gdiplus_token:
        logger.warning("GDI+ başlatılmamış.")
        return False

    if not _gdiplus_jpeg_clsid:
        _find_jpeg_encoder()
    if not _gdiplus_jpeg_clsid:
        logger.warning("JPEG encoder bulunamadı.")
        return False

    try:
        # GDI+ bitmap oluştur
        bitmap_ptr = ctypes.c_void_p()
        status = _gdiplus_lib.GdipCreateBitmapFromHBITMAP(
            ctypes.c_void_p(hbitmap),
            None,
            ctypes.byref(bitmap_ptr),
        )
        if status != 0 or not bitmap_ptr:
            logger.warning("GdipCreateBitmapFromHBITMAP hatası: %d", status)
            return False

        # CLSID kopyala
        clsid_buf = (ctypes.c_ubyte * 16)(*list(_gdiplus_jpeg_clsid))

        # Encoder parametreleri: kalite = 95
        quality_value = wintypes.UINT(95)

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

        # JPEG Quality GUID: {1D5BE4B5-FA4A-452D-9CDD-5DB35105E7EB}
        quality_guid = (ctypes.c_ubyte * 16)(
            0xB5, 0xE4, 0x5B, 0x1D, 0x4A, 0xFA, 0x2D, 0x45,
            0x9C, 0xDD, 0x5D, 0xB3, 0x51, 0x05, 0xE7, 0xEB,
        )

        params = EncoderParameters()
        params.Count = 1
        ctypes.memmove(params.Parameter[0].Guid, quality_guid, 16)
        params.Parameter[0].NumberOfValues = 1
        params.Parameter[0].Type = 4  # EncoderParameterValueTypeLong
        params.Parameter[0].Value = ctypes.addressof(quality_value)

        filepath_ptr = ctypes.c_wchar_p(filepath)

        status = _gdiplus_lib.GdipSaveImageToFile(
            bitmap_ptr,
            filepath_ptr,
            ctypes.byref(clsid_buf),
            ctypes.byref(params),
        )

        _gdiplus_lib.GdipDisposeImage(bitmap_ptr)

        if status != 0:
            logger.warning("GdipSaveImageToFile hatası: %d", status)
            return False

        return True

    except Exception as exc:
        logger.exception("JPEG kaydetme hatası: %s", exc)
        return False


def _capture_window(hwnd: int) -> Optional[Path]:
    """Belirtilen pencerenin ekran görüntüsünü alır."""
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

    hbitmap = bmp.GetHandle()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp_path = Path(tempfile.gettempdir()) / f"cypcut_screen_{ts}.jpg"
    saved = _save_hbitmap_as_jpeg(hbitmap, str(tmp_path))

    win32gui.DeleteObject(hbitmap)
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    if saved and tmp_path.exists() and tmp_path.stat().st_size > 0:
        logger.info("Pencere görüntüsü alındı: %s (%dx%d)", tmp_path, width, height)
        return tmp_path
    return None


def _capture_full_screen() -> Optional[Path]:
    """Tüm ekranın görüntüsünü alır."""
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
            _init_gdiplus()

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
