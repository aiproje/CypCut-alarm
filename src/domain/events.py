"""Domain event veri sınıfları."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enums import EventKind


@dataclass(frozen=True)
class ParsedEvent:
    """Log satırından parse edilen event.

    Attributes:
        kind: Olay türü.
        timestamp: Satır başındaki zaman damgası (yoksa None).
        text: Olayla ilgili metin (alarm içeriği, transition açıklaması).
        raw_line: Orijinal (RTF temizlenmiş) satır.
    """

    kind: EventKind
    timestamp: datetime | None
    text: str
    raw_line: str

    @property
    def is_state_change(self) -> bool:
        return self.kind in {EventKind.START, EventKind.STOP, EventKind.RESUME}

    @property
    def is_alarm(self) -> bool:
        return self.kind == EventKind.ALARM

    @property
    def is_alarm_clear(self) -> bool:
        return self.kind == EventKind.ALARM_CLEAR


__all__ = ["ParsedEvent"]
