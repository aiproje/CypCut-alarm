"""Kamera yönetimi.

Sistem başlangıcında kullanılabilir kameraları tarar; ilk açılan index'i
seçer. Env'de CAMERA_INDEX tanımlıysa önce o denenir. Kamera bulunamazsa
``camera`` None döner, sistem çalışmaya devam eder.
"""
from __future__ import annotations

import threading
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
                logger.info("📷 Kamera bulundu (Index: %d)", index)
                return True

            self._capture = None
            self._active_index = None
            self._initialized = True
            logger.warning("⚠️ Kamera bulunamadı (0-%d aralığı tarandı)", self._max_index - 1)
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
            return None
        return frame

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
