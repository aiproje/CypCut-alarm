"""OCR çıktısını yapılandırılmış tablo satırlarına ayrıştırıcı.

OCR motoru ekran görüntüsünden dikey olarak metin çıkarır.
Bu modül dikey OCR çıktısını tablo satırlarına dönüştürür.

Örnek OCR çıktısı:
    Time
    Alarminformation
    ID
    Status
    Operation
    6/11/2026 10:09:37AM
    NetworkTimeout
    63
    Help
    6/11/2026 10:09:26AM
    Axis3Servoalarm
    30
    ...

Bu çıktı satır satır okunarak tabloya dönüştürülür.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..logging_setup import get_logger

logger = get_logger(__name__)

# Tarih formatı: 6/11/2026 10:09:37AM veya 6/11/2026 10:09:26 AM
_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s*(?:AM|PM))",
    re.IGNORECASE,
)

# Bilinen başlık metinleri
_KNOWN_HEADERS = {"time", "alarminformation", "id", "status", "operation"}

# Status alanında aranacak anahtar kelimeler
_ALARM_KEYWORDS = {"alarm", "error", "fault", "help", "timeout", "servo"}


@dataclass
class OcrAlarmRow:
    """OCR'dan çıkarılan tek bir alarm satırı."""

    timestamp: Optional[str]
    alarm_info: Optional[str]
    alarm_id: Optional[str]
    status: Optional[str]
    operation: Optional[str]

    @property
    def is_alarm_active(self) -> bool:
        """Status alanında alarm belirtisi var mı kontrol eder."""
        if not self.status:
            return False
        lower = self.status.lower()
        return any(kw in lower for kw in _ALARM_KEYWORDS)

    @property
    def timestamp_dt(self) -> Optional[datetime]:
        """Timestamp'ı datetime'e çevirir."""
        if not self.timestamp:
            return None
        try:
            # "6/11/2026 10:09:37AM" formatını parse et
            ts = self.timestamp.strip()
            # Boşluk sorunlarını düzelt
            ts = re.sub(r"\s+", " ", ts)
            return datetime.strptime(ts, "%m/%d/%Y %I:%M:%S%p")
        except ValueError:
            try:
                return datetime.strptime(ts, "%m/%d/%Y %I:%M:%S %p")
            except ValueError:
                return None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp or "",
            "alarm_info": self.alarm_info or "",
            "id": self.alarm_id or "",
            "status": self.status or "",
            "operation": self.operation or "",
        }


def _is_timestamp(text: str) -> bool:
    """Metnin zaman damgası formatında olup olmadığını kontrol eder."""
    return bool(_TIMESTAMP_RE.match(text.strip()))


def _is_header(text: str) -> bool:
    """Metnin bilinen bir başlık olup olmadığını kontrol eder."""
    return text.strip().lower() in _KNOWN_HEADERS


def parse_ocr_text(raw_text: str) -> list[OcrAlarmRow]:
    """OCR çıktısını OcrAlarmRow listesine dönüştürür.

    Args:
        raw_text: OCR motorundan gelen ham metin çıktısı.

    Returns:
        Ayrıştırılmış OcrAlarmRow listesi.
    """
    if not raw_text or not raw_text.strip():
        return []

    lines = [line.strip() for line in raw_text.strip().split("\n") if line.strip()]

    if not lines:
        return []

    # Başlık satırlarını atla
    data_start = 0
    for i, line in enumerate(lines):
        if _is_header(line):
            data_start = i + 1
        else:
            # İlk başlık olmayan satırda dur
            break

    data_lines = lines[data_start:]
    if not data_lines:
        return []

    logger.info("OCR parse: %d başlık satırı atlandı, %d veri satırı bulundu",
                data_start, len(data_lines))

    # Veri satırlarını grupla: her satır bir alan olarak okunur
    # Sıra: Time, Alarminformation, ID, Status, Operation (5'li gruplar)
    # Ancak eksik alanlar olabilir, esnek olmalıyız

    rows: list[OcrAlarmRow] = []
    current_row_data: list[str] = []
    expected_fields = 5  # Time, Alarminformation, ID, Status, Operation

    for line in data_lines:
        current_row_data.append(line)

        if len(current_row_data) == expected_fields:
            row = _build_row(current_row_data)
            if row is not None:
                rows.append(row)
            current_row_data = []

    # Kalan veri varsa (eksik alanlı son satır)
    if current_row_data:
        row = _build_row(current_row_data)
        if row is not None:
            rows.append(row)

    logger.info("OCR parse tamamlandı: %d satır çıkarıldı", len(rows))
    return rows


def _build_row(fields: list[str]) -> Optional[OcrAlarmRow]:
    """Alan listesinden OcrAlarmRow oluşturur.

    Alan sırası: Time, Alarminformation, ID, Status, Operation
    Ancak bazı alanlar eksik olabilir. Akıllı ayrıştırma yapar.
    """
    if not fields:
        return None

    timestamp = None
    alarm_info = None
    alarm_id = None
    status = None
    operation = None

    for field in fields:
        if _is_timestamp(field):
            timestamp = field
        elif field.isdigit():
            alarm_id = field
        elif _is_header(field):
            # Başlık satırı veri arasında göründüyse atla
            continue
        elif status is None and any(kw in field.lower() for kw in _ALARM_KEYWORDS):
            # Status alanı olarak değerlendirilebilecek alarm kelimesi içeren metin
            # Ancak alarm_info'dan önce kontrol et
            if alarm_info is None:
                alarm_info = field
            else:
                status = field
        elif alarm_info is None:
            alarm_info = field
        elif status is None:
            status = field
        elif operation is None:
            operation = field

    # Hiçbir anlamlı veri yoksa None dön
    if timestamp is None and alarm_info is None and alarm_id is None:
        return None

    return OcrAlarmRow(
        timestamp=timestamp,
        alarm_info=alarm_info,
        alarm_id=alarm_id,
        status=status,
        operation=operation,
    )


def format_table(rows: list[OcrAlarmRow]) -> str:
    """OcrAlarmRow listesini okunabilir tablo formatına çevirir."""
    if not rows:
        return "Tabloda veri bulunamadı."

    lines = [
        f"{'Zaman':<25} {'Alarm':<30} {'ID':<6} {'Durum':<15} {'İşlem':<10}",
        "-" * 90,
    ]
    for row in rows:
        lines.append(
            f"{row.timestamp or '-':<25} "
            f"{row.alarm_info or '-':<30} "
            f"{row.alarm_id or '-':<6} "
            f"{row.status or '-':<15} "
            f"{row.operation or '-':<10}"
        )
    return "\n".join(lines)


__all__ = ["OcrAlarmRow", "parse_ocr_text", "format_table"]
