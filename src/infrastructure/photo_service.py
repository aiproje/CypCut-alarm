"""Fotoğraf ve video çekimi + geçici dosya yönetimi.

Geliştirilmiş:
  - capture_video() metodu eklendi
  - Kamera bağlantı kontrolü eklendi
"""
from __future__ import annotations

import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2

from ..logging_setup import get_logger
from .camera_manager import CameraManager

logger = get_logger(__name__)


class MediaService:
    """Kameradan JPEG fotoğraf ve MP4 video çekip geçici dosyaya yazar."""

    def __init__(self, camera: CameraManager, video_duration: float = 5.0) -> None:
        self._camera = camera
        self._video_duration = video_duration
        self._lock = threading.Lock()

    def capture_jpeg(self) -> Optional[Path]:
        """Kameradan tek kare okur, JPEG olarak temp dosyaya yazar, Path döner.

        Hata durumunda veya kamera yoksa None döner.
        """
        with self._lock:
            if not self._camera.is_available:
                return None

            frame = self._camera.read()
            if frame is None:
                logger.warning("Kameradan kare okunamadı.")
                return None

            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            tmp_path = Path(tempfile.gettempdir()) / f"cypcut_{ts}.jpg"

            try:
                ok = cv2.imwrite(str(tmp_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 100])
            except Exception as exc:
                logger.exception("JPEG encode hatası: %s", exc)
                return None

            if not ok:
                logger.warning("JPEG yazılamadı: %s", tmp_path)
                return None

            logger.info("Fotoğraf çekildi: %s", tmp_path)
            return tmp_path

    def capture_video(self) -> Optional[Path]:
        """Kameradan 5 saniyelik video çeker, MP4 olarak temp dosyaya yazar.

        Hata durumunda veya kamera yoksa None döner.
        """
        with self._lock:
            if not self._camera.is_available:
                return None

            tmp_path = self._camera.capture_video(
                duration=self._video_duration,
                fps=20.0,
            )
            return tmp_path

    def check_camera(self) -> bool:
        """Kamera bağlantısını kontrol eder."""
        return self._camera.check_connection()


# Eski isimle de erişilebilirlik
PhotoService = MediaService

__all__ = ["MediaService", "PhotoService"]
