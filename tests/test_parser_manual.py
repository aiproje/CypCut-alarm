"""Parser ve RTF cleaner'ın örnek log üzerinde doğrulanması için hızlı test."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.event_parser import EventParser
from src.infrastructure.rtf_cleaner import clean as rtf_clean


def _safe(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


def test_sample_logs() -> None:
    parser = EventParser()
    sample_dir = PROJECT_ROOT / "ornek_log_dosyalari"
    log_files = sorted(sample_dir.glob("CypCut-*.rtf"))
    if not log_files:
        print("Örnek log dosyası bulunamadı.")
        return

    for log_file in log_files:
        print(f"\n=== {log_file.name} ===")
        content = log_file.read_text(encoding="utf-8", errors="replace")
        clean_text = rtf_clean(content)
        events_found = 0
        for line in clean_text.splitlines():
            if not line.strip():
                continue
            ev = parser.parse(line)
            if ev is not None:
                events_found += 1
                ts = ev.timestamp.strftime("%H:%M:%S") if ev.timestamp else "??:??:??"
                print(f"  [{ts}] {ev.kind.value:<12} -> {_safe(ev.text)}")
        print(f"  Toplam event: {events_found}")


if __name__ == "__main__":
    test_sample_logs()
