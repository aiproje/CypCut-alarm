"""RapidOCR ile ekran görüntüsünden metin çıkarma servisi."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..logging_setup import get_logger

logger = get_logger(__name__)


class OcrService:
    """RapidOCR tabanlı OCR servisi."""

    def __init__(self) -> None:
        self._engine = None
        self._available = self._init_engine()

    def _init_engine(self) -> bool:
        try:
            from rapidocr_onnxruntime import RapidOCR
            self._engine = RapidOCR()
            logger.info("RapidOCR motoru başarıyla başlatıldı.")
            return True
        except ImportError:
            logger.warning(
                "rapidocr-onnxruntime kurulu değil. "
                "Kurulum: pip install rapidocr-onnxruntime"
            )
            return False
        except Exception as exc:
            logger.warning("RapidOCR başlatılamadı: %s", exc)
            return False

    @property
    def is_available(self) -> bool:
        return self._available

    def recognize(self, image_path: Path) -> Optional[str]:
        """Verilen görüntü dosyasından metin çıkarır.

        Args:
            image_path: JPEG/PNG gibi bir görüntü dosyası yolu.

        Returns:
            Çıkarılan metin veya başarısızsa None.
        """
        if not self._available or self._engine is None:
            logger.warning("OCR motoru kullanılamıyor.")
            return None

        if not image_path.exists():
            logger.warning("Görüntü dosyası bulunamadı: %s", image_path)
            return None

        try:
            result, elapse = self._engine(str(image_path))
        except Exception as exc:
            logger.exception("OCR hatası: %s", exc)
            return None

        if result is None or len(result) == 0:
            logger.info("OCR sonucu boş (elapse: %s)", elapse)
            return ""

        lines = []
        for item in result:
            # RapidOCR sonucu: [(bbox, text, score), ...]
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                text = item[1]
                score = item[2] if len(item) >= 3 else 0.0
                if text and text.strip():
                    lines.append(text.strip())

        text = "\n".join(lines)
        logger.info("OCR tamamlandı (%d satır, elapse: %s)", len(lines), elapse)
        return text


__all__ = ["OcrService"]
