"""RTF temizleyici.

CypCut log dosyaları RTF formatındadır. striprtf bu RTF'yi düz metne çevirir.
Ancak bazı CJK (Çince) karakterler bozuk ('?' karakterlerine dönüşmüş) olduğu
için son bir temizlik katmanı uygulanır.

Önemli: Bu modül kaynak dosyayı ASLA düzenlemez, yalnızca okur ve dönüştürür.
"""
from __future__ import annotations

import re
from functools import lru_cache

from striprtf.striprtf import rtf_to_text


_BROKEN_Q_RUNS = re.compile(r"(\?{2,})")
_BOM = "\ufeff"


@lru_cache(maxsize=512)
def clean(rtf_text: str) -> str:
    """RTF metnini düz metne çevirir.

    Args:
        rtf_text: RTF formatında ham metin.

    Returns:
        Düz metin (zaman damgası + içerik).
    """
    try:
        text = rtf_to_text(rtf_text)
    except Exception:
        return _fallback_clean(rtf_text)

    text = text.replace(_BOM, "").replace("\r", "").strip()

    text = _BROKEN_Q_RUNS.sub("", text)

    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text


def _fallback_clean(rtf_text: str) -> str:
    """striprtf başarısız olursa basit regex temizliği."""
    text = rtf_text
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", text)
    text = re.sub(r"[{}]", "", text)
    text = _BROKEN_Q_RUNS.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


__all__ = ["clean"]
