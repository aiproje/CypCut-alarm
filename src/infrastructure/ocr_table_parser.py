from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..logging_setup import get_logger

logger = get_logger(__name__)

_TIMESTAMP_RE = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}\s*(?:AM|PM))",
    re.IGNORECASE,
)

_SHORT_TIMESTAMP_RE = re.compile(
    r"(\d{2}/\d{2}\s+\d{1,2}:\d{2}:\d{2})"
)

_CYPCUT_TS_NOSPACE_RE = re.compile(r"(\d{2}/\d{2}\d{2}:\d{2}:\d{2})")

_TIMESTAMP_INSIDE_RE = re.compile(
    r"\b(\d{2}/\d{1,2}\s+\d{1,2}:\d{2}:\d{2})\b"
)

_KNOWN_HEADERS = {"time", "alarminformation", "id", "status", "operation", "alarm", "information"}

_ALARM_KEYWORDS = {
    "alarm", "error", "fault", "help", "timeout", "servo",
    "bcs100", "tip touch", "gantry", "laser", "capacitance",
    "pressure", "gas", "follow", "origin", "measure", "dock",
    "system", "alarmremove", "alarm remove", "resume",
}

_CHINESE_PATTERN = re.compile(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]')


# ---------------------------------------------------------------------------
# Alarm Code -> Turkish Açıklama Haritası
# ---------------------------------------------------------------------------

_ALARM_INTERPRETATIONS: list[tuple[re.Pattern, str, str]] = [
    # (regex, alarm_kodu, türkçe_açıklama)
    (re.compile(r'BCS100', re.IGNORECASE),
     "BCS100 Eksen Takip Hatası",
     "Eksen uzun süre istenen konuma ulaşamadı.\n"
     "• Eksen hız/ivme ayarlarını kontrol edin\n"
     "• Kapasitans kalibrasyonu yapın\n"
     "• Kılavuz ray ve bilyalı vida mekaniğini kontrol edin"),

    (re.compile(r'TipTouch|Tip\s*Touch', re.IGNORECASE),
     "TipTouch Sensör Hatası",
     "Kesim başlığı dokunma/temas sensörü tetiklendi.\n"
     "• Malzeme yüzeyindeki çarpıntıyı kontrol edin\n"
     "• Sensör kablosunu ve bağlantılarını kontrol edin\n"
     "• Kesim tablasının düzgün olduğundan emin olun"),

    (re.compile(r'Capacitance|Capacitancediminish', re.IGNORECASE),
     "Kapasitans Azalması",
     "Kesim başlığı kapasitans değeri kritik seviyede.\n"
     "• Nozulu ve kesim başlığını temizleyin\n"
     "• Başlık yükseklik ayarını kontrol edin\n"
     "• Kapasitans kalibrasyonu yapın"),

    (re.compile(r'gas\s*pressure|02\s*gas', re.IGNORECASE),
     "Gaz Basıncı Düşük",
     "Kesim gazı (Oksijen) basıncı yetersiz.\n"
     "• Gaz tüpü seviyesini kontrol edin\n"
     "• Basınç regülatör ayarını kontrol edin\n"
     "• Gaz hattı sızıntılarını kontrol edin"),

    (re.compile(r'(X|Y|Z|Axis\d*|Axis\s*\d*)Servo', re.IGNORECASE),
     "Eksen Servo Alarmı",
     "Eksen servo sürücüsü alarm verdi.\n"
     "• Motor kablolarını ve bağlantılarını kontrol edin\n"
     "• Sürücü hata kodunu kontrol edin\n"
     "• Eksen mekanik sıkışmayı kontrol edin"),

    (re.compile(r'NetworkTimeout|Network\s*Timeout', re.IGNORECASE),
     "Ağ Zaman Aşımı",
     "Kontrolör ile iletişim zaman aşımına uğradı.\n"
     "• Ethernet kablosunu kontrol edin\n"
     "• Kontrolör gücünü kontrol edin\n"
     "• Ağ switch/hub bağlantısını kontrol edin"),

    (re.compile(r'Gantry|Crossgirder', re.IGNORECASE),
     "Portal (Gantry) Hatası",
     "Portal/çapraz kiriş konumlandırma hatası.\n"
     "• Çift eksen senkronizasyonunu kontrol edin\n"
     "• Portal mekaniğini ve rayları kontrol edin\n"
     "• Referans alma işlemini tekrarlayın"),

    (re.compile(r'Origin|Errormeasure', re.IGNORECASE),
     "Referans Noktası Hatası",
     "Makine referans/başlangıç noktası bulunamadı.\n"
     "• Referans sensörlerini kontrol edin\n"
     "• Eksen limit switch'lerini kontrol edin\n"
     "• Referans alma işlemini tekrarlayın"),

    (re.compile(r'QCW', re.IGNORECASE),
     "QCW Lazer Modu",
     "Lazer QCW modunda çalışıyor.\n"
     "• Lazer güç parametrelerini kontrol edin\n"
     "• CW/QCW mod geçişini kontrol edin"),

    (re.compile(r'System', re.IGNORECASE),
     "Sistem Alarmı",
     "CypCut yazılımı sistem alarmı verdi.\n"
     "• Yazılımı yeniden başlatmayı deneyin\n"
     "• Windows olay günlüğünü kontrol edin"),

    (re.compile(r'Follow\s*Error', re.IGNORECASE),
     "Eksen Takip Hatası",
     "Eksen takip hatası oluştu.\n"
     "• Eksen hız ayarlarını kontrol edin\n"
     "• Motor sürücü parametrelerini kontrol edin"),
]

# ---------------------------------------------------------------------------
# Çince kalıplar → Türkçe çeviriler
# ---------------------------------------------------------------------------

_CN_TRANSLATIONS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'检查(\w+)轴速度是否设置过小', re.IGNORECASE),
     r'Eksen \1 hız ayarı çok düşük olabilir'),
    (re.compile(r'检查(\w+)轴速度是含设置过小', re.IGNORECASE),
     r'Eksen \1 hız ayarı çok düşük olabilir'),
    (re.compile(r'检查(\w+)轴速度是省收置过小', re.IGNORECASE),
     r'Eksen \1 hız ayarı çok düşük olabilir'),
    (re.compile(r'跟随异常', re.IGNORECASE), 'Takip hatası'),
    (re.compile(r'长时间内未跟随到位', re.IGNORECASE), 'Uzun süre konuma ulaşamadı'),
    (re.compile(r'跟随到位', re.IGNORECASE), 'Konum takibi'),
    (re.compile(r'电容标定', re.IGNORECASE), 'Kapasitans kalibrasyonu'),
    (re.compile(r'电睿标定|电答标定', re.IGNORECASE), 'Kapasitans kalibrasyonu'),
    (re.compile(r'重新进行', re.IGNORECASE), 'Yeniden yapılmalı'),
    (re.compile(r'无法跟随到位', re.IGNORECASE), 'Konum takibi başarısız'),
    (re.compile(r'解决方法', re.IGNORECASE), 'Çözüm'),
    (re.compile(r'板外跟随异常', re.IGNORECASE), 'Tezgah dışı takip hatası'),
    (re.compile(r'导数', re.IGNORECASE), 'Neden'),
    (re.compile(r'导致', re.IGNORECASE), 'Sebep'),
    (re.compile(r'停止', re.IGNORECASE), 'Durdurma'),
    (re.compile(r'异常', re.IGNORECASE), 'Anormallik'),
    (re.compile(r'未随到位', re.IGNORECASE), 'Konumlanamadı'),
    (re.compile(r'未翠随到位|未距随到位|未蹭随到位', re.IGNORECASE), 'Konumlanamadı'),
    (re.compile(r'减小', re.IGNORECASE), 'Azalma'),
    (re.compile(r'检查', re.IGNORECASE), 'Kontrol et'),
    (re.compile(r'设置', re.IGNORECASE), 'Ayar'),
    (re.compile(r'或者', re.IGNORECASE), 'veya'),
    (re.compile(r'进行', re.IGNORECASE), 'Yap'),
    (re.compile(r'解决', re.IGNORECASE), 'Çözüm'),
]


def _translate_chinese(text: str) -> str:
    """Çince metin parçalarını Türkçe'ye çevirir (basit pattern eşleme)."""
    if not text:
        return text
    for pattern, replacement in _CN_TRANSLATIONS:
        text = pattern.sub(replacement, text)
    return text


def _identify_alarm(text: str) -> tuple[str, str]:
    """Alarm metnini tanır, (kod, türkçe_açıklama) döndürür."""
    for pattern, code, desc in _ALARM_INTERPRETATIONS:
        if pattern.search(text):
            return code, desc
    return None, None


def _extract_error_code(text: str) -> Optional[str]:
    """Metinden alarm kodunu çıkarır (BCS100, 63, 1007, 7, vs)."""
    m = re.search(r'(?:ID[=:]\s*|#)?(\d{1,10})\b', text)
    if m:
        return m.group(1)

    known_codes = ["BCS100", "QCW", "BCS"]
    for code in known_codes:
        if code.lower() in text.lower():
            return code
    return None


def _extract_timestamp_from_text(text: str) -> Optional[str]:
    """Metnin içindeki timestamp'i bulur (örn: 06/11 20:51:30)."""
    m = re.search(r'(\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})', text)
    if m:
        return m.group(1)
    m = re.search(r'(\d{2}/\d{2}\d{2}:\d{2}:\d{2})', text)
    if m:
        raw = m.group(1)
        return raw[:5] + ' ' + raw[5:]
    return None


def _get_event_kind_from_text(text: str) -> str:
    """Metinden olay türünü belirler: alarm, alarm_clear, stop, start, resume, info."""
    lower = text.lower()
    if re.search(r'alarm\s*remove|alarmtemizle|alarmremov', lower):
        return 'alarm_clear'
    if re.search(r'alarm', lower):
        return 'alarm'
    if re.search(r'working\s*-->\s*pause|-->\s*pause', lower):
        return 'stop'
    if re.search(r'resume|pause\s*-->\s*resume', lower):
        return 'resume'
    if re.search(r'start\s*processing|stop\s*-->\s*working|start\b', lower):
        return 'start'
    if re.search(r'working\s*-->', lower):
        return 'start'
    if re.search(r'processing\s*end|lastworkend|go\s*dock\s*-->\s*stop', lower):
        return 'stop'
    return 'info'


# ---------------------------------------------------------------------------
# Ana veri sınıfları
# ---------------------------------------------------------------------------

@dataclass
class OcrAlarmRow:
    timestamp: Optional[str]
    alarm_info: Optional[str]
    alarm_id: Optional[str]
    status: Optional[str]
    operation: Optional[str]

    _interpreted_code: Optional[str] = field(default=None, repr=False)
    _interpreted_desc: Optional[str] = field(default=None, repr=False)

    @property
    def is_alarm_active(self) -> bool:
        if not self.status and not self.alarm_info:
            return False
        combined = ((self.alarm_info or '') + ' ' + (self.status or '') + ' ' + (self.operation or '')).lower()
        ek = _get_event_kind_from_text(combined)
        if ek in ('alarm_clear', 'stop', 'start', 'resume'):
            return False
        if ek == 'alarm':
            return True
        return any(kw in combined for kw in _ALARM_KEYWORDS)

    @property
    def is_alarm_clear(self) -> bool:
        combined = ((self.status or '') + ' ' + (self.alarm_info or '') + ' ' + (self.operation or '')).lower()
        return _get_event_kind_from_text(combined) == 'alarm_clear'

    @property
    def event_kind(self) -> str:
        """Olay türü: alarm, alarm_clear, stop, start, resume, info."""
        combined = ((self.alarm_info or '') + ' ' + (self.status or '') + ' ' + (self.operation or ''))
        return _get_event_kind_from_text(combined)

    @property
    def is_meaningful(self) -> bool:
        fields = [self.alarm_info, self.status, self.operation]
        for f in fields:
            if f and not _CHINESE_PATTERN.search(f):
                return True
        return False

    @property
    def timestamp_dt(self) -> Optional[datetime]:
        if not self.timestamp:
            return None
        try:
            ts = self.timestamp.strip()
            ts = re.sub(r"\s+", " ", ts)
            return datetime.strptime(ts, "%m/%d/%Y %I:%M:%S%p")
        except ValueError:
            try:
                return datetime.strptime(ts, "%m/%d/%Y %I:%M:%S %p")
            except ValueError:
                return None

    def get_alarm_code(self) -> Optional[str]:
        """Tanınan alarm kodunu döndürür."""
        combined = ((self.alarm_info or '') + ' ' + (self.status or ''))
        code, _ = _identify_alarm(combined)
        return code

    def get_turkish_description(self) -> str:
        """Alarm için Türkçe açıklama döndürür."""
        combined = ((self.alarm_info or '') + ' ' + (self.status or ''))
        cn_part = ''

        if _CHINESE_PATTERN.search(combined):
            cn_part = _translate_chinese(combined)
            if cn_part != combined:
                cn_part = f"\nÇince çeviri: {cn_part}"

        code, desc = _identify_alarm(combined)

        info = self.alarm_info or self.status or '?'
        info = _translate_chinese(info)
        info = _clean_text(info)

        eid = self.alarm_id or _extract_error_code(combined) or '?'

        if code:
            result = f"{code}"
            if desc:
                result += f"\n\n{desc}"
            if eid and eid != 'BCS100':
                result += f"\n\nHata kodu: {eid}"
            return result

        return f"Alarm: {info} (ID: {eid}){cn_part}"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp or "",
            "alarm_info": self.alarm_info or "",
            "id": self.alarm_id or "",
            "status": self.status or "",
            "operation": self.operation or "",
        }


# ---------------------------------------------------------------------------
# Temizlik / Dönüşüm yardımcıları
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """Full-width karakterleri yarı-genişliğe çevirir."""
    result = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            result.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            result.append(' ')
        elif code in (0xFF08, 0xFF09):
            result.append(chr(code - 0xFEE0))
        else:
            result.append(ch)
    return ''.join(result)


def _fix_timestamp_spacing(text: str) -> str:
    """Boşluksuz timestamp'leri düzeltir."""
    def _fix(m: re.Match) -> str:
        raw = m.group(1)
        if re.match(r'\d{2}/\d{2}\d{2}:\d{2}:\d{2}', raw):
            return raw[:5] + ' ' + raw[5:]
        return raw
    return _CYPCUT_TS_NOSPACE_RE.sub(_fix, text)


def _is_timestamp(text: str) -> bool:
    return bool(_TIMESTAMP_RE.match(text.strip()))


def _is_short_timestamp(text: str) -> bool:
    return bool(_SHORT_TIMESTAMP_RE.match(text.strip()))


def _contains_cypcut_timestamp(text: str) -> bool:
    return bool(_TIMESTAMP_INSIDE_RE.search(text))


def _has_any_alarm_keyword(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _ALARM_KEYWORDS)


def _extract_leading_timestamp(text: str) -> tuple[str, str]:
    """Metnin başındaki timestamp'i ayırır. (kalan_metin, timestamp) döndürür.
    
    Şu formatları tanır:
      (06/16 13:58:15)Metin    -> ('Metin', '06/16 13:58:15')
      06/16 13:58:15)Metin     -> ('Metin', '06/16 13:58:15')
      06/16 13:58:15 Metin     -> ('Metin', '06/16 13:58:15')
    """
    m = re.match(r'\(?(\d{2}/\d{1,2}\s+\d{1,2}:\d{2}:\d{2})\)?\s*(.*)', text.strip())
    if m:
        return m.group(2).strip(), m.group(1)
    return text, ''


def _has_operation_pattern(text: str) -> bool:
    lower = text.lower()
    # Regex ile boşluk toleranslı ok işareti kontrolü
    if re.search(r'working\s*-->|-->(\s*working|\s*pause|\s*stop)', lower):
        return True
    if re.search(r'frame\s*-->|-->\s*frame', lower):
        return True
    patterns = [
        "go dock", "dock-->", "laser enable",
        "red light", "nest completed", "rebuilding model",
        "edge searching",
        "processing end", "lastworkend",
    ]
    if any(p in lower for p in patterns):
        return True
    # "Alarm:XXX" pattern'leri operation değildir (aksine alarm bilgisidir)
    return False


def _estimate_chinese_ratio(text: str) -> float:
    if not text:
        return 0.0
    chinese_chars = len(_CHINESE_PATTERN.findall(text))
    return chinese_chars / max(len(text), 1)


# ---------------------------------------------------------------------------
# Ana ayrıştırma
# ---------------------------------------------------------------------------

def parse_ocr_text(raw_text: str) -> list[OcrAlarmRow]:
    """OCR ham çıktısını yapılandırılmış alarm satırlarına dönüştürür."""
    if not raw_text or not raw_text.strip():
        return []

    cleaned = _fix_timestamp_spacing(_clean_text(raw_text))
    lines = [line.strip() for line in cleaned.strip().split("\n") if line.strip()]
    if not lines:
        return []

    data_start = 0
    for i, line in enumerate(lines):
        if line.lower().strip() in _KNOWN_HEADERS:
            data_start = i + 1
        else:
            break

    data_lines = lines[data_start:]
    if not data_lines:
        return []

    logger.info("OCR parse: %d başlık atlandı, %d satır", data_start, len(data_lines))

    # Satır başındaki timestamp'leri ayır, her satırı content+ts çiftine dönüştür
    parsed_pairs: list[tuple[str, str]] = []  # (content, timestamp)
    for line in data_lines:
        content, ts = _extract_leading_timestamp(line)
        if not ts:
            # Klasik timestamp kontrolü (tam satır timestamp ise timestamp olarak kaydet)
            if _is_timestamp(line) or _is_short_timestamp(line):
                parsed_pairs.append(('', line))
            else:
                parsed_pairs.append((line, ''))
        else:
            parsed_pairs.append((content, ts))

    # Boş content'leri filtrele (sadece timestamp olan satırlar)
    filtered_pairs = [(c, t) for c, t in parsed_pairs if c]

    if not filtered_pairs:
        return []

    # Gruplama: içinde timestamp SATIRI olmayan ardışık content'leri birleştir
    rows: list[OcrAlarmRow] = []
    current_batch: list[str] = []
    current_ts: str = ''

    for content, ts in filtered_pairs:
        if ts:
            # Timestamp'i olan satır: yeni grup başlat
            if current_batch:
                row = _build_row(current_batch)
                if row is not None:
                    if not row.timestamp and current_ts:
                        row.timestamp = current_ts
                    rows.append(row)
            current_batch = [content]
            current_ts = ts
        else:
            # Timestamp'siz satır: mevcut gruba ekle
            current_batch.append(content)

    if current_batch:
        row = _build_row(current_batch)
        if row is not None:
            if not row.timestamp and current_ts:
                row.timestamp = current_ts
            rows.append(row)

    rows = _merge_similar_rows(rows)
    rows = _filter_noise_rows(rows)
    logger.info("OCR parse: %d satır çıkarıldı", len(rows))
    return rows


def _build_row(fields: list[str]) -> Optional[OcrAlarmRow]:
    """OCR alanlarından OcrAlarmRow inşa eder."""
    if not fields:
        return None

    timestamp = None
    alarm_info = None
    alarm_id = None
    status = None
    operation = None

    for field in fields:
        f = field.strip()
        if not f:
            continue

        # Baştaki timestamp'i ayır
        content, ts = _extract_leading_timestamp(f)
        if ts and timestamp is None:
            timestamp = ts
        if not content:
            continue
        if ts:
            f = content

        if _is_timestamp(f) or _is_short_timestamp(f):
            timestamp = f
            continue

        if f.isdigit() and len(f) <= 10:
            alarm_id = f
            continue

        ek = _get_event_kind_from_text(f)

        is_op = _has_operation_pattern(f) and ek != 'alarm'

        if is_op:
            operation = f
        elif ek in ('alarm', 'alarm_clear', 'resume', 'start', 'stop') or _has_any_alarm_keyword(f):
            if alarm_info is None:
                alarm_info = f
            elif status is None:
                status = f
            elif operation is None and _has_operation_pattern(f):
                operation = f
            elif operation is None:
                operation = f
        else:
            if alarm_info is None:
                alarm_info = f
            elif status is None:
                status = f
            elif operation is None:
                operation = f

    if timestamp is None and alarm_info is None and alarm_id is None:
        if not (operation and _get_event_kind_from_text(operation) != 'info'):
            return None

    cn_text = str(alarm_info or '') + str(status or '') + str(operation or '')
    # Çince oranı yüksekse ama içinde tanınan alarm pattern'i varsa filtreleme
    if _estimate_chinese_ratio(cn_text) > 0.3:
        has_known_pattern = any(
            p in cn_text.lower() for p in
            ["bcs100", "alarm", "servo", "timeout", "gantry",
             "capacitance", "pressure", "gas", "follow", "origin",
             "error", "fault", "help"]
        )
        if not has_known_pattern:
            logger.debug("Çince oranı yüksek, filtrelendi: %s", fields)
            return None
        logger.debug("Çince oranı yüksek ama tanınan pattern var, korundu: %s", fields)

    if alarm_info and alarm_id and not status and len(fields) <= 3:
        status = alarm_id
        alarm_id = None

    if alarm_info and _has_operation_pattern(str(alarm_info)) and operation is None:
        operation = alarm_info
        alarm_info = None

    return OcrAlarmRow(
        timestamp=timestamp,
        alarm_info=alarm_info,
        alarm_id=alarm_id,
        status=status,
        operation=operation,
    )


def _merge_similar_rows(rows: list[OcrAlarmRow]) -> list[OcrAlarmRow]:
    """Aynı alarm_info'ya sahip ardışık satırları birleştirir."""
    if not rows:
        return rows
    merged: list[OcrAlarmRow] = []
    for row in rows:
        if merged and merged[-1].alarm_info and row.alarm_info and merged[-1].alarm_info == row.alarm_info:
            if row.timestamp and not merged[-1].timestamp:
                merged[-1].timestamp = row.timestamp
            if row.status and not merged[-1].status:
                merged[-1].status = row.status
            if row.operation and not merged[-1].operation:
                merged[-1].operation = row.operation
        else:
            merged.append(row)
    return merged


def _filter_noise_rows(rows: list[OcrAlarmRow]) -> list[OcrAlarmRow]:
    """Anlamsız satırları filtreler."""
    filtered: list[OcrAlarmRow] = []
    for row in rows:
        if not row.is_meaningful and row.alarm_info is None and row.status is None:
            continue
        if row.alarm_info and len(row.alarm_info) <= 2 and not row.status:
            continue
        filtered.append(row)
    return filtered


# ---------------------------------------------------------------------------
# Tablo formatlama
# ---------------------------------------------------------------------------

def format_table(rows: list[OcrAlarmRow]) -> str:
    if not rows:
        return "Tabloda veri bulunamadı."
    lines = [
        f"{'Zaman':<25} {'Alarm':<30} {'ID':<6} {'Durum':<28} {'İşlem':<30}",
        "-" * 125,
    ]
    for row in rows:
        lines.append(
            f"{row.timestamp or '-':<25} "
            f"{(_translate_chinese(row.alarm_info or '-'))[:30]:<30} "
            f"{row.alarm_id or '-':<6} "
            f"{(_translate_chinese(row.status or '-'))[:28]:<28} "
            f"{(_translate_chinese(row.operation or '-'))[:30]:<30}"
        )
    return "\n".join(lines)


__all__ = [
    "OcrAlarmRow", "parse_ocr_text", "format_table",
    "_translate_chinese", "_identify_alarm",
    "_get_event_kind_from_text", "_extract_error_code",
]
