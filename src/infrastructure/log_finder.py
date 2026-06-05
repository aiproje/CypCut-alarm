"""CypCut log dosyalarını bulur ve en güncelini seçer."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


# CypCut-20260605083823-5408-1.rtf
_CYPCUT_PATTERN = re.compile(r"^CypCut-\d{14}-\d+-\d+\.rtf$", re.IGNORECASE)


def is_cypcut_log(name: str) -> bool:
    """Verilen dosya adı CypCut log mu?"""
    return bool(_CYPCUT_PATTERN.match(name))


def list_logs(log_dir: Path) -> list[Path]:
    """log_dir içindeki tüm CypCut log dosyalarını (dosya adına göre) sıralı döner."""
    if not log_dir.exists() or not log_dir.is_dir():
        return []
    files = [p for p in log_dir.iterdir() if p.is_file() and is_cypcut_log(p.name)]
    files.sort(key=lambda p: p.name)
    return files


def find_latest_log(log_dir: Path) -> Optional[Path]:
    """En yeni CypCut log dosyasını döner (yoksa None)."""
    files = list_logs(log_dir)
    return files[-1] if files else None


__all__ = ["is_cypcut_log", "list_logs", "find_latest_log"]
