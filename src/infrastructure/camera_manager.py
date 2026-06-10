"""Kamera yönetimi.

Sistem başlangıcında kullanılabilir kameraları tarar; ilk açılan index'i
seçer. Env'de CAMERA_INDEX tanımlıysa önce o denenir. Kamera bulunamazsa
``camera`` None döner, sistem çalışmaya devam eder.

Geliştirilmiş:
  - Video çekme desteği (5 saniyelik MP4)
  - Kamera bağlantı kopmasında otomatik yeniden deneme
"""
from __future__ import annotations

import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2

from ..logging_setup import get_logger

logger = get_logger(__name__)


class CameraManager:
    """Thread-safe kamera yöneticisi."""

    def __init__(self, preferred_index: Optional[int], max_index: int = 4) -> None:
        self._preferred_index = preferred_index
        self._max_index = max_index
        self._capture: Optional[cv2.VideoCapture] = None
        self._active_index: Optional[int] = None
        self._lock = threading.Lock()
        self._initialized = False
        self._last_read_ok: bool = True

    def initialize(self) -> bool:
        """Kamerayı başlatır. Bulunursa True, yoksa False."""
        with self._lock:
            if self._initialized:
                return self._capture is not None

            indices_to_try: list[int] = []
            if self._preferred_index is not None and self._preferred_index >= 0:
                indices_to_try.append(self._preferred_index)
            for i in range(self._max_index):
                if i not in indices_to_try:
                    indices_to_try.append(i)

            for index in indices_to_try:
                cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)
                if not cap.isOpened():
                    cap.release()
                    continue
                ok, _ = cap.read()
                if not ok:
                    cap.release()
                    continue
                self._capture = cap
                self._active_index = index
                self._initialized = True
                self._last_read_ok = True
                logger.info("Kamera bulundu (Index: %d)", index)
                return True

            self._capture = None
            self._active_index = None
            self._initialized = True
            logger.warning("Kamera bulunamadı (0-%d aralığı tarandı)", self._max_index - 1)
            return False

    @property
    def is_available(self) -> bool:
        return self._capture is not None

    @property
    def active_index(self) -> Optional[int]:
        return self._active_index

    def read(self) -> Optional[cv2.Mat]:
        """Tek bir kare okur. Hata/bağlantı yoksa None."""
        if self._capture is None:
            return None
        with self._lock:
            ok, frame = self._capture.read()
        if not ok:
            self._last_read_ok = False
            return None
        self._last_read_ok = True
        return frame

    def capture_video(self, duration: float = 5.0, fps: float = 20.0) -> Optional[Path]:
        """Kameradan belirtilen süre kadar video çeker, MP4 olarak kaydeder.

        Args:
            duration: Video süresi (saniye). Varsayılan 5.
            fps: Kare hızı. Varsayılan 20.

        Returns:
            Geçici MP4 dosyasının yolu, hata olursa None.
        """
        if self._capture is None:
            return None

        with self._lock:
            # Videonun boyutlarını al
            width = int(self._capture.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(self._capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            tmp_path = Path(tempfile.gettempdir()) / f"cypcut_video_{ts}.mp4"

            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(tmp_path), fourcc, fps, (width, height))

            if not writer.isOpened():
                logger.warning("VideoWriter açılamadı.")
                return None

            frames_needed = int(duration * fps)
            frames_written = 0
            start_time = time.monotonic()

            try:
                while frames_written < frames_needed:
                    elapsed = time.monotonic() - start_time
                    if elapsed >= duration:
                        break
                    ok, frame = self._capture.read()
                    if not ok:
                        logger.warning("Video kare okunamadı (%d/%d)",
                                       frames_written, frames_needed)
                        break
                    writer.write(frame)
                    frames_written += 1
            finally:
                writer.release()

            if frames_written == 0:
                logger.warning("Hiç kare yazılamadı, video iptal.")
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return None

            logger.info("Video çekildi: %s (%d kare, %.1fs)",
                        tmp_path, frames_written, duration)
            return tmp_path

    def check_connection(self) -> bool:
        """Kamera bağlantısını kontrol eder. Sorun varsa yeniden dener."""
        if self._capture is None:
            return False

        with self._lock:
            ok, _ = self._capture.read()
            if ok:
                self._last_read_ok = True
                return True

        # Okuma başarısız, kamerayı yeniden açmayı dene
        logger.warning("Kamera bağlantı hatası, yeniden deneniyor...")
        self._reconnect()
        return self._capture is not None

    def _reconnect(self) -> None:
        """Kamerayı yeniden bağlar."""
        # Eski kapat
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None

        # Yeniden aç
        if self._active_index is not None:
            try:
                cap = cv2.VideoCapture(self._active_index, cv2.CAP_DSHOW)
                if cap.isOpened():
                    ok, _ = cap.read()
                    if ok:
                        self._capture = cap
                        self._last_read_ok = True
                        logger.info("Kamera yeniden bağlandı (Index: %d)",
                                    self._active_index)
                        return
                    cap.release()
            except Exception as exc:
                logger.warning("Kamera yeniden bağlama hatası: %s", exc)

        self._capture = None
        self._last_read_ok = False
        logger.warning("Kamera yeniden bağlanamadı.")

    def close(self) -> None:
        with self._lock:
            if self._capture is not None:
                try:
                    self._capture.release()
                except Exception as exc:
                    logger.warning("Kamera kapatma hatası: %s", exc)
                self._capture = None
            self._active_index = None
            self._initialized = False


__all__ = ["CameraManager"]
